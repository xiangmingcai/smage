"""Segmentation module: Cellpose / StarDist / threshold masks."""
from __future__ import annotations

from smage.extensions_registry import _extension_def

from PySide6.QtCore import (
    QRectF,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from pathlib import Path
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.image_ops import (
    _otsu_threshold,
    _otsu_threshold_chunked,
    _relabel_sequential,
)
from smage.core.settings import (
    _default_palette,
    _plot_background,
)
from smage.data.image_index import (
    _build_label_index,
    _image_index_dir,
)
from smage.widgets.controls import (
    _LogStepSpinBox,
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from smage.widgets.plot_axes import _make_sci_axis
from smage.widgets.resources import _lock_pixmap
import copy
import queue
import threading
import time
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.data import scratch as _data_scratch
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import renderer as _widgets_plot_renderer


# Image batches: at most this many rasterized inputs and pending outputs are
# allowed on disk at once. Mask parquet is written in bounded row chunks so a
# large label grid never has to be duplicated as a DataFrame.
SEG_PREFETCH = 2
SEG_INPUT_TIMEOUT = 1800.0
SEG_WRITE_CHUNK = 2_000_000
SEG_EXPAND_TILE = 1024
SEG_UINT16_MAX = 65535


def _seg_params_for(ctx, task_def) -> dict:
    """Saved params of a segmentation task (its snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "segmentation":
        snap = getattr(reg["page"], "_seg_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _seg_mask_record(ctx, params, parquet_path):
    """The mask record of one file in a segmentation task's params."""
    masks = params.get("masks") or {}
    rec = masks.get(parquet_path)
    return rec if isinstance(rec, dict) else None


def _seg_mask_file(ctx, rec):
    """Absolute path of a mask record's parquet file, or None if missing."""
    if not rec or ctx.project is None:
        return None
    rel = rec.get("path")
    if not rel:
        return None
    path = ctx.project.path / rel
    return path if path.is_file() else None


def _seg_signature(ctx, path) -> str:
    """A cheap change signature (size + nanosecond mtime) for a mask file."""
    try:
        st = Path(path).stat()
    except OSError:
        return ""
    return f"{int(st.st_size)}-{int(st.st_mtime_ns)}"


def _seg_load_mask(ctx, mask_path):
    """Load a mask's full 2-D label grid (cached by path + signature)."""
    import numpy as np
    import pandas as pd

    cache = getattr(ctx, "_seg_mask_cache", None)
    if cache is None:
        cache = {}
        ctx._seg_mask_cache = cache
    key = str(mask_path)
    sign = _seg_signature(ctx, mask_path)
    hit = cache.get(key)
    if hit is not None and hit[0] == sign:
        return hit[1]
    man = _data_image_index._image_load_manifest(ctx, mask_path)
    if man is None:
        return None
    width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
    height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
    try:
        df = pd.read_parquet(mask_path, columns=["x", "y", "mask"])
    except (OSError, ValueError):
        return None
    xi = np.rint(
        df["x"].to_numpy(dtype="float64") - float(man["xmin"])
    ).astype(np.int64)
    yi = np.rint(
        df["y"].to_numpy(dtype="float64") - float(man["ymin"])
    ).astype(np.int64)
    mask = np.zeros((height, width), dtype=np.int32)
    ok = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
    mask[yi[ok], xi[ok]] = df["mask"].to_numpy(dtype=np.int32)[ok]
    if len(cache) > 4:
        cache.clear()
    cache[key] = (sign, mask)
    return mask


def _seg_temp_dir(ctx):
    """Disposable working files for segmentation."""
    return _data_scratch._scratch_area(ctx, "segmentation")


def _seg_edges_dir(ctx):
    return _seg_temp_dir(ctx) / "edges"


def _seg_runs_dir(ctx):
    return _seg_temp_dir(ctx) / "runs"


def _seg_clear_edge_cache(ctx) -> None:
    """Drop this project's cached mask-edge images (temp data only)."""
    cache = getattr(ctx, "_seg_edge_cache", None)
    if cache is not None:
        cache.clear()
    _data_scratch._scratch_remove(_seg_edges_dir(ctx))


def _seg_cleanup_runs(ctx) -> None:
    """Remove batch folders left behind by an interrupted process."""
    _data_scratch._scratch_remove(_seg_runs_dir(ctx))


def _seg_cleanup_temp(ctx) -> None:
    """Remove every disposable segmentation file for the current project."""
    _seg_clear_edge_cache(ctx)
    _seg_cleanup_runs(ctx)
    _data_scratch._scratch_cleanup_area(ctx, "segmentation")


def _seg_edge_map(ctx, mask_path):
    """Return (and disk-cache) the boolean edge map of a mask."""
    import numpy as np

    mask = _seg_load_mask(ctx, mask_path)
    if mask is None:
        return None
    sign = _seg_signature(ctx, mask_path)
    cache = getattr(ctx, "_seg_edge_cache", None)
    if cache is None:
        cache = {}
        ctx._seg_edge_cache = cache
    key = str(mask_path)
    hit = cache.get(key)
    if hit is not None and hit[0] == sign:
        return hit[1]
    path = _seg_edges_dir(ctx) / f"{Path(str(mask_path)).stem}.{sign}.npy"
    edge = None
    if path.is_file():
        try:
            edge = np.load(path)
        except (OSError, ValueError):
            edge = None
    if edge is None or edge.shape != mask.shape:
        edge = np.zeros(mask.shape, dtype=bool)
        if mask.size:
            edge[1:, :] |= mask[1:, :] != mask[:-1, :]
            edge[:, 1:] |= mask[:, 1:] != mask[:, :-1]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, edge)
        except OSError:
            pass
    if len(cache) > 4:
        cache.clear()
    cache[key] = (sign, edge)
    return edge


def _build_segmentation_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Segmentation page (files | method | preview | plot settings)."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    bar = QFrame()
    bar.setObjectName("moduleBar")
    tl = QHBoxLayout(bar)
    tl.setContentsMargins(12, 6, 12, 6)
    tl.setSpacing(4)
    tl.addWidget(QLabel(f"<b>{_data_files._module_display_title(ctx, task_def.get('id'), name)}</b>"))
    if locked:
        lock_icon = QLabel()
        lock_icon.setPixmap(_lock_pixmap())
        lock_icon.setFixedSize(14, 14)
        lock_icon.setToolTip("Locked — a downstream generate module has saved data")
        tl.addWidget(lock_icon)
        draft = QLabel("Draft (locked)")
        draft.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft)
    tl.addStretch()
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setEnabled(not locked)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(lambda _=False: _seg_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("method", "nuclear_expansion")
    # Migrate legacy method ids (Cellpose/StarDist) to their -cpu names.
    if params.get("method") == "cellpose":
        params["method"] = "cellpose-cpu"
    elif params.get("method") == "stardist":
        params["method"] = "stardist-cpu"
    params.setdefault("files", [])
    params.setdefault("expose", {})
    params.setdefault("column", "Mask")
    params.setdefault("method_params", {})
    params.setdefault("masks", {})
    params.setdefault("applied", {})
    params.setdefault("plot", {})
    params.setdefault("log", [])
    page._seg_task_def = task_def
    page._seg_task_id = task_def.get("id")
    page._seg_params = params
    page._seg_locked = locked
    page._seg_save_btn = save_btn
    page._seg_dirty = False
    page._seg_snapshot = copy.deepcopy(params)
    page._seg_applied_snapshot = dict(params.get("applied") or {})
    page._seg_overlay_item = None
    page._seg_mask_cache = {}
    page._seg_ext_running = False
    page._seg_calc_running = False
    page._seg_calc_cancel = False
    page._seg_calc_finished = False
    page._seg_nuclear_queue = None
    page._seg_nuclear_timer = None
    page._seg_ext_queue = None
    page._seg_ext_timer = None
    page._seg_ext_writer = None
    page._seg_ext_cancelled = False
    page._seg_ext_aborted = False
    page._seg_ext_proc_finished = False
    page._seg_ext_feeder_done = False
    page._seg_ext_pending_writes = 0
    page._seg_ext_prepared = 0
    page._seg_ext_written = 0
    page._seg_import_running = False
    page._seg_import_cancel = False
    page._seg_import_queue = None
    page._seg_import_timer = None
    # The calculation log belongs to the module: it is saved with the task
    # params, restored when the page reopens, and never cleared by a rerun.
    page._seg_log_lines = list(params.get("log") or [])
    save_btn.clicked.connect(lambda _=False: _seg_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QHBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(12)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_seg_files_panel(ctx, page))
    splitter.addWidget(_seg_method_panel(ctx, page))
    page._seg_preview_panel = _seg_preview_panel(ctx, page)
    page._seg_settings_panel = _seg_plot_settings_panel(ctx, page)
    splitter.addWidget(page._seg_preview_panel)
    splitter.addWidget(page._seg_settings_panel)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 2)
    splitter.setStretchFactor(3, 1)
    splitter.setSizes([260, 260, 520, 260])
    row.addWidget(splitter, 1)
    layout.addWidget(content, 1)
    page._seg_splitter = splitter

    _seg_refresh_files(ctx, page)
    _seg_update_preview_visibility(ctx, page)
    _seg_update_save_btn(ctx, page)
    return page


def _seg_default_plot(ctx, parquet_path, task_id=None) -> dict:
    """A fresh image plot config for the preview panel."""
    columns = []
    rec = _widgets_plot_renderer._figure_record(ctx, parquet_path)
    if rec is not None:
        columns = _data_columns._columns_for(ctx, rec.get("parquet"), task_id)
    channel = next((c for c in columns if c not in ("x", "y")), None)
    return {
        "type": "image",
        "file": parquet_path,
        "file_name": rec.get("name") if rec else None,
        "x": "x",
        "y": "y",
        "color_mode": "parameter",
        "color_param": channel,
        "last_color_param": channel,
        "palette": _default_palette(),
        "layers": (
            [{
                "parameter": channel,
                "color": (255, 255, 255),
                "name": str(channel),
                "visible": True,
            }] if channel else []
        ),
        "show_legend": True,
        "show_axes": True,
        "show_axis_names": True,
        "show_indices": True,
        "show_sample_name": True,
        "mask_mode": "shape",
        "mask_color": "random",
        "mask_alpha": 45,
    }


def _seg_file_rows(ctx, page) -> list:
    return list(page._seg_params.get("files") or [])


def _seg_file_status(ctx, page, parquet_path) -> str:
    """no mask / pending / applied for one file."""
    params = page._seg_params
    rec = _seg_mask_record(ctx, params, parquet_path)
    path = _seg_mask_file(ctx, rec)
    if path is None:
        return SEG_STATUS_NO_MASK
    saved = page._seg_applied_snapshot.get(parquet_path) if hasattr(
        page, "_seg_applied_snapshot"
    ) else None
    if saved and saved == _seg_signature(ctx, path):
        return SEG_STATUS_APPLIED
    return SEG_STATUS_PENDING


def _seg_any_mask(ctx, page) -> bool:
    for path in _seg_file_rows(ctx, page):
        rec = _seg_mask_record(ctx, page._seg_params, path)
        if _seg_mask_file(ctx, rec) is not None:
            return True
    return False


class _SegExposeHeaderCheckBox(QCheckBox):
    """Tristate checkbox pinned over one table header section.

    Clicking it checks or clears every file at once; the partially checked
    state mirrors a mixed selection.
    """

    def __init__(self, header, section: int, text: str = "") -> None:
        super().__init__(text, header)
        self._header = header
        self._section = int(section)
        self.setTristate(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "Check or clear every file whose mask can be exposed"
        )
        header.sectionResized.connect(self._place)
        header.geometriesChanged.connect(self._place)
        self._place()

    def nextCheckState(self) -> None:  # noqa: N802 - Qt override
        """Toggle between checked and unchecked (partial counts as checked)."""
        if self.checkState() == Qt.CheckState.Checked:
            self.setCheckState(Qt.CheckState.Unchecked)
        else:
            self.setCheckState(Qt.CheckState.Checked)

    def _place(self, *_args) -> None:
        header = self._header
        if (
            self._section >= header.count()
            or header.isSectionHidden(self._section)
        ):
            self.setVisible(False)
            return
        x = header.sectionViewportPosition(self._section)
        width = header.sectionSize(self._section)
        hint = self.sizeHint()
        y = max(0, (header.height() - hint.height()) // 2)
        self.move(x + max(0, (width - hint.width()) // 2), y)
        self.setVisible(
            width > 0 and x + width > 0 and x < header.width()
        )


def _seg_files_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    # Top: method chooser (small), bottom: files (large) — ratio 1:6.
    top = QWidget()
    tlay = QVBoxLayout(top)
    tlay.setContentsMargins(0, 0, 0, 0)
    tlay.setSpacing(4)
    tlay.addWidget(QLabel("Method"))
    method_combo = _NoWheelComboBox()
    for text, value in SEG_METHODS:
        method_combo.addItem(text, value)
    idx = method_combo.findData(page._seg_params.get("method"))
    method_combo.setCurrentIndex(idx if idx >= 0 else 0)
    method_combo.setEnabled(not page._seg_locked)
    method_combo.currentIndexChanged.connect(
        lambda _=0: _seg_on_method_changed(ctx, page)
    )
    tlay.addWidget(method_combo)
    method_hint = QLabel("")
    method_hint.setWordWrap(True)
    method_hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    method_hint.setVisible(False)
    tlay.addWidget(method_hint)
    page._seg_method_hint = method_hint
    page._seg_method_combo = method_combo
    _seg_update_method_hint(ctx, page)

    bottom = QWidget()
    blay = QVBoxLayout(bottom)
    blay.setContentsMargins(0, 0, 0, 0)
    blay.setSpacing(6)
    set_btn = QPushButton("Select Files")
    set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_btn.setEnabled(not page._seg_locked)
    set_btn.clicked.connect(lambda _=False: _seg_set_files(ctx, page))
    blay.addWidget(set_btn)
    page._seg_setfiles_btn = set_btn

    table = QTableWidget()
    table.setColumnCount(3)
    # The Expose header carries a checkbox instead of text.
    table.setHorizontalHeaderLabels(["File", "", "Status"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    table.itemChanged.connect(lambda item: _seg_expose_changed(ctx, page, item))
    blay.addWidget(table, 1)
    page._seg_files_table = table

    expose_all = _SegExposeHeaderCheckBox(header, 1, "Expose")
    expose_all.clicked.connect(
        lambda checked: _seg_set_all_exposed(ctx, page, checked)
    )
    table.horizontalScrollBar().valueChanged.connect(expose_all._place)
    table.setColumnWidth(1, expose_all.sizeHint().width() + 14)
    page._seg_expose_all = expose_all
    QTimer.singleShot(0, expose_all._place)

    blay.addWidget(QLabel("Mask column"))
    name_edit = QLineEdit(str(page._seg_params.get("column") or "Mask"))
    name_edit.setEnabled(not page._seg_locked)
    name_edit.editingFinished.connect(
        lambda: _seg_column_edited(ctx, page)
    )
    blay.addWidget(name_edit)
    page._seg_column_edit = name_edit
    hint = QLabel(
        "Checked files expose their mask as a parameter to downstream modules "
        "when this module is saved."
    )
    hint.setStyleSheet("color: #6b7785;")
    hint.setWordWrap(True)
    blay.addWidget(hint)

    split = QSplitter(Qt.Orientation.Vertical)
    split.setChildrenCollapsible(False)
    split.addWidget(top)
    split.addWidget(bottom)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 6)
    split.setSizes([90, 540])
    lay.addWidget(split, 1)
    return panel


def _seg_refresh_files(ctx, page) -> None:
    """Rebuild the file table and the mask-column status snapshot."""
    table = page._seg_files_table
    table.blockSignals(True)
    rows = _seg_file_rows(ctx, page)
    table.setRowCount(len(rows))
    expose = page._seg_params.get("expose") or {}
    editable = not page._seg_locked
    for i, path in enumerate(rows):
        name_item = QTableWidgetItem(_data_naming._comp_file_name(ctx, path, page._seg_task_id))
        name_item.setData(Qt.ItemDataRole.UserRole, path)
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(i, 0, name_item)

        has_mask = _seg_mask_file(ctx,
            _seg_mask_record(ctx, page._seg_params, path)
        ) is not None
        chk = QTableWidgetItem()
        chk.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        )
        chk.setCheckState(
            Qt.CheckState.Checked if expose.get(path) else Qt.CheckState.Unchecked
        )
        chk.setSelected(False)
        if not has_mask or not editable:
            chk.setFlags(Qt.ItemFlag.ItemIsSelectable)
        table.setItem(i, 1, chk)

        status = _seg_file_status(ctx, page, path)
        sitem = QTableWidgetItem(status)
        sitem.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if status == SEG_STATUS_APPLIED:
            sitem.setForeground(QColor("#1a7f37"))
        elif status == SEG_STATUS_PENDING:
            sitem.setForeground(QColor("#b58900"))
        else:
            sitem.setForeground(QColor("#9aa0a6"))
        table.setItem(i, 2, sitem)
    table.blockSignals(False)
    _seg_update_expose_all(ctx, page)
    _seg_rebuild_method_panel(ctx, page)
    _seg_refresh_plot_settings(ctx, page)
    _seg_update_preview_visibility(ctx, page)


def _seg_update_expose_all(ctx, page) -> None:
    """Mirror the row check states on the Expose header checkbox."""
    box = getattr(page, "_seg_expose_all", None)
    if box is None:
        return
    table = page._seg_files_table
    states = []
    for row in range(table.rowCount()):
        item = table.item(row, 1)
        if item is None:
            continue
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            states.append(item.checkState() == Qt.CheckState.Checked)
    if not states:
        box.blockSignals(True)
        box.setCheckState(Qt.CheckState.Unchecked)
        box.blockSignals(False)
        box.setEnabled(False)
        return
    checked = sum(1 for state in states if state)
    if checked == len(states):
        state = Qt.CheckState.Checked
    elif checked == 0:
        state = Qt.CheckState.Unchecked
    else:
        state = Qt.CheckState.PartiallyChecked
    box.blockSignals(True)
    box.setCheckState(state)
    box.blockSignals(False)
    box.setEnabled(not page._seg_locked)


def _seg_set_all_exposed(ctx, page, checked: bool) -> None:
    """Check or clear every row whose mask can be exposed."""
    if page._seg_locked:
        return
    table = page._seg_files_table
    rows = _seg_file_rows(ctx, page)
    expose = page._seg_params.setdefault("expose", {})
    table.blockSignals(True)
    try:
        for row, path in enumerate(rows):
            item = table.item(row, 1)
            if item is None:
                continue
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            expose[path] = bool(checked)
    finally:
        table.blockSignals(False)
    _seg_update_expose_all(ctx, page)
    _seg_refresh_status(ctx, page)
    _seg_mark_dirty(ctx, page)


def _seg_expose_changed(ctx, page, item) -> None:
    if page._seg_locked or item.column() != 1:
        return
    table = page._seg_files_table
    row = table.row(item)
    rows = _seg_file_rows(ctx, page)
    if not (0 <= row < len(rows)):
        return
    path = rows[row]
    expose = page._seg_params.setdefault("expose", {})
    expose[path] = item.checkState() == Qt.CheckState.Checked
    _seg_update_expose_all(ctx, page)
    _seg_refresh_status(ctx, page)
    _seg_mark_dirty(ctx, page)


def _seg_refresh_status(ctx, page) -> None:
    """Update only the status cells (no widget rebuild)."""
    table = page._seg_files_table
    rows = _seg_file_rows(ctx, page)
    table.blockSignals(True)
    for i, path in enumerate(rows):
        if i >= table.rowCount():
            break
        item = table.item(i, 2)
        if item is None:
            continue
        status = _seg_file_status(ctx, page, path)
        item.setText(status)
        if status == SEG_STATUS_APPLIED:
            item.setForeground(QColor("#1a7f37"))
        elif status == SEG_STATUS_PENDING:
            item.setForeground(QColor("#b58900"))
        else:
            item.setForeground(QColor("#9aa0a6"))
    table.blockSignals(False)


def _seg_set_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._seg_task_id)
    names = [rec.get("name", "") for rec in records]
    current = [_data_naming._comp_file_name(ctx, p, page._seg_task_id) for p in _seg_file_rows(ctx, page)]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    picks = [rec for rec in records if rec.get("name", "") in chosen_set]
    bad = [rec.get("name", "") for rec in picks if not _data_outputs._seg_is_image_file(ctx, rec)]
    if bad:
        QMessageBox.warning(
            ctx.host,
            "Segmentation",
            "Only image files can be used: segmentation works on pixels and "
            "the mask must line up row by row with the image.\n\n"
            "Not image files:\n" + "\n".join(f"· {n}" for n in bad),
        )
        return
    page._seg_params["files"] = [rec.get("parquet") for rec in picks]
    _seg_refresh_files(ctx, page)
    _seg_mark_dirty(ctx, page)


def _seg_column_edited(ctx, page) -> None:
    name = page._seg_column_edit.text().strip() or "Mask"
    page._seg_params["column"] = name
    _seg_refresh_status(ctx, page)
    _seg_mark_dirty(ctx, page)


def _seg_on_method_changed(ctx, page) -> None:
    page._seg_params["method"] = page._seg_method_combo.currentData()
    _seg_update_method_hint(ctx, page)
    _seg_rebuild_method_panel(ctx, page)
    _seg_mark_dirty(ctx, page)


def _seg_update_method_hint(ctx, page) -> None:
    """Explain the method under the chooser (e.g. CPU is slow)."""
    hint = getattr(page, "_seg_method_hint", None)
    if hint is None:
        return
    if page._seg_params.get("method") == "cellpose-cpu":
        hint.setText(
            "Cellpose CPU is much slower than Cellpose GPU — use it only "
            "when no NVIDIA GPU is available."
        )
        hint.setVisible(True)
    else:
        hint.setText("")
        hint.setVisible(False)


def _seg_method_label(method) -> str:
    """Display name of a segmentation method (matches the chooser)."""
    for text, value in SEG_METHODS:
        if value == method:
            return text
    entry = _extension_def(method)
    return entry["name"] if entry is not None else str(method or "")


def _seg_rebuild_method_panel(ctx, page) -> None:
    """Swap the second panel between method params and the mask-input table."""
    host = page._seg_method_host
    _widgets_panels._clear_settings_layout(ctx, host.layout())
    method = page._seg_params.get("method")
    if method == "mask_input":
        _seg_mask_input_panel(ctx, page, host)
    elif _extension_def(method) is not None:
        _seg_external_method_panel(ctx, page, host)
    else:
        _seg_method_params_panel(ctx, page, host)


def _seg_method_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    host = QWidget()
    host.setLayout(QVBoxLayout())
    host.layout().setContentsMargins(0, 0, 0, 0)
    host.layout().setSpacing(6)
    # Parameters (4) above, calculation log (1) below the Calculate button.
    lay.addWidget(host, 4)
    log = _widgets_panels._log_widget()
    # Replay the saved history so the log survives closing and reopening.
    for line in getattr(page, "_seg_log_lines", []):
        log.appendPlainText(line)
    log.verticalScrollBar().setValue(log.verticalScrollBar().maximum())
    lay.addWidget(log, 1)
    page._seg_method_host = host
    page._seg_calc_log = log
    return panel


def _seg_map_params(ctx, page) -> dict:
    method = page._seg_params.get("method") or "nuclear_expansion"
    store = page._seg_params.setdefault("method_params", {})
    params = store.setdefault(method, {})
    for key, value in SEG_DEFAULT_PARAMS.items():
        params.setdefault(key, value)
    return params


def _seg_otsu_value(ctx, page, parquet_path, channel):
    """Otsu threshold of one channel (cached per file + channel)."""
    import pandas as pd

    cache = getattr(page, "_seg_otsu_cache", None)
    if cache is None:
        cache = {}
        page._seg_otsu_cache = cache
    key = (str(parquet_path), str(channel))
    if key in cache:
        return cache[key]
    try:
        df = pd.read_parquet(parquet_path, columns=[channel])
    except Exception:  # noqa: BLE001
        return None
    values = pd.to_numeric(
        df[channel], errors="coerce"
    ).to_numpy(dtype="float64")
    value = _otsu_threshold(values)
    cache[key] = value
    return value


def _seg_run_controls(ctx, page, enabled: bool) -> QWidget:
    """Calculate + Cancel row shared by every segmentation method."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(
        enabled
        and not getattr(page, "_seg_calc_running", False)
        and not getattr(page, "_seg_import_running", False)
    )
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel_btn.setEnabled(getattr(page, "_seg_calc_running", False))
    cancel_btn.setToolTip("Stop after the current file (or kill the external run)")
    cancel_btn.clicked.connect(lambda _=False: _seg_cancel_run(ctx, page))
    lay.addWidget(calc_btn, 1)
    lay.addWidget(cancel_btn)
    page._seg_calc_btn = calc_btn
    page._seg_cancel_btn = cancel_btn
    return row


def _seg_method_params_panel(ctx, page, host) -> None:
    """Nuclear Expansion parameters + Calculate."""
    lay = host.layout()
    params = _seg_map_params(ctx, page)
    enabled = not page._seg_locked

    lay.addWidget(QLabel("<b>Nuclear Expansion</b>"))
    lay.addWidget(QLabel("Nucleus channel"))
    columns = []
    for path in _seg_file_rows(ctx, page):
        for name in _data_columns._columns_for(ctx, path, page._seg_task_id):
            if name not in columns:
                columns.append(name)
    channel_combo = _NoWheelComboBox()
    channel_combo.addItem("", None)
    for name in columns:
        if name in ("x", "y"):
            continue
        channel_combo.addItem(name, name)
    idx = channel_combo.findData(params.get("channel"))
    if idx >= 0:
        channel_combo.setCurrentIndex(idx)
    channel_combo.setEnabled(enabled)
    lay.addWidget(channel_combo)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Channel used to detect nuclei."
    ))

    lay.addWidget(QLabel("Threshold"))
    mode_combo = _NoWheelComboBox()
    mode_combo.addItem("Otsu (automatic)", "otsu")
    mode_combo.addItem("Manual value", "manual")
    idx = mode_combo.findData(params.get("threshold_mode", "otsu"))
    mode_combo.setCurrentIndex(max(0, idx))
    mode_combo.setEnabled(enabled)
    lay.addWidget(mode_combo)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Otsu picks the cut-off from the histogram; Manual uses the value "
        "below."
    ))

    def otsu_default():
        """The Otsu value of the chosen channel, used as the default."""
        channel = params.get("channel")
        rows = _seg_file_rows(ctx, page)
        if not channel or not rows:
            return None
        return _seg_otsu_value(ctx, page, rows[0], channel)

    initial = float(params.get("threshold", 0.0))
    if params.get("threshold_mode", "otsu") != "manual":
        otsu = otsu_default()
        if otsu is not None:
            initial = otsu
    thr_spin = _LogStepSpinBox(initial, lambda v: on_threshold(v))
    thr_spin.set_enabled(enabled)
    thr_spin.setToolTip(
        "Prefilled with the Otsu value of the chosen channel. Editing it "
        "switches to a manual threshold."
    )
    lay.addWidget(thr_spin)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Prefilled with the Otsu value. Editing it switches the mode to "
        "Manual."
    ))

    lay.addWidget(QLabel("Minimum nucleus area (px)"))
    area_spin = _NoWheelSpinBox()
    area_spin.setRange(1, 1000000)
    area_spin.setValue(int(params.get("min_area", 30)))
    area_spin.setEnabled(enabled)
    lay.addWidget(area_spin)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Objects smaller than this many pixels are discarded as debris."
    ))

    lay.addWidget(QLabel("Expansion radius (px)"))
    exp_spin = _NoWheelSpinBox()
    exp_spin.setRange(0, 500)
    exp_spin.setValue(int(params.get("expand", 5)))
    exp_spin.setEnabled(enabled)
    lay.addWidget(exp_spin)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "How far each nucleus grows to approximate the cell body."
    ))

    split_check = QCheckBox("Split touching nuclei")
    split_check.setChecked(bool(params.get("split", False)))
    split_check.setEnabled(enabled)
    lay.addWidget(split_check)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Seeds each nucleus from the maximum of its distance map and "
        "assigns every pixel to the nearest seed, so nuclei that touch get "
        "separate labels."
    ))
    page._seg_split_check = split_check

    lay.addWidget(QLabel("Split minimum separation"))
    sep_spin = _NoWheelSpinBox()
    sep_spin.setRange(0, 200)
    sep_spin.setValue(int(params.get("split_separation", 0) or 0))
    sep_spin.setSpecialValueText("Auto")
    sep_spin.setSuffix(" px")
    sep_spin.setEnabled(enabled and split_check.isChecked())
    sep_spin.setToolTip(
        "Minimum distance between two nucleus centres before they are split. "
        "Larger = fewer splits, smaller = more. Auto uses the radius of a "
        "nucleus with the minimum area."
    )
    lay.addWidget(sep_spin)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Split strength: how far apart two nucleus centres must be to count "
        "as separate objects."
    ))
    page._seg_split_separation = sep_spin

    lay.addWidget(_seg_run_controls(ctx, page, enabled))
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    lay.addStretch(1)
    page._seg_calc_status = status

    def refresh_otsu():
        if params.get("threshold_mode") == "manual":
            return
        value = otsu_default()
        if value is not None:
            thr_spin.set_value(value)

    def on_threshold(value):
        params["threshold"] = float(value)
        if params.get("threshold_mode") != "manual":
            params["threshold_mode"] = "manual"
            mode_combo.setCurrentIndex(mode_combo.findData("manual"))
        _seg_mark_dirty(ctx, page)

    def on_channel(_=0):
        params["channel"] = channel_combo.currentData()
        refresh_otsu()
        _seg_mark_dirty(ctx, page)

    def on_mode(_=0):
        params["threshold_mode"] = mode_combo.currentData()
        if params["threshold_mode"] == "otsu":
            refresh_otsu()
        _seg_mark_dirty(ctx, page)

    def on_area(value):
        params["min_area"] = int(value)
        _seg_mark_dirty(ctx, page)

    def on_expand(value):
        params["expand"] = int(value)
        _seg_mark_dirty(ctx, page)

    def on_split(checked):
        params["split"] = bool(checked)
        sep_spin.setEnabled(enabled and bool(checked))
        _seg_mark_dirty(ctx, page)

    def on_separation(value):
        params["split_separation"] = int(value)
        _seg_mark_dirty(ctx, page)

    channel_combo.currentIndexChanged.connect(on_channel)
    mode_combo.currentIndexChanged.connect(on_mode)
    area_spin.valueChanged.connect(on_area)
    exp_spin.valueChanged.connect(on_expand)
    split_check.toggled.connect(on_split)
    sep_spin.valueChanged.connect(on_separation)
    page._seg_calc_btn.clicked.connect(
        lambda _=False: _seg_calculate(ctx, page)
    )


def _seg_channel_columns(ctx, page) -> list:
    """Union of non-coordinate columns across the selected files."""
    columns = []
    for path in _seg_file_rows(ctx, page):
        for name in _data_columns._columns_for(ctx, path, page._seg_task_id):
            if name in ("x", "y"):
                continue
            if name not in columns:
                columns.append(name)
    return columns


def _seg_external_method_panel(ctx, page, host) -> None:
    """Cellpose / StarDist params: extension status, channel, Calculate."""
    method = page._seg_params.get("method")
    entry = _extension_def(method)
    lay = host.layout()
    params = _seg_map_params(ctx, page)
    enabled = not page._seg_locked
    ready = ctx._ext_ready(method)

    lay.addWidget(QLabel(f"<b>{_seg_method_label(method)}</b>"))
    lay.addWidget(QLabel(entry["desc"]))
    lay.addWidget(QLabel("Runs in an external Python environment."))

    banner = QLabel()
    banner.setWordWrap(True)
    if ready:
        banner.setText("Extension ready ✓")
        banner.setStyleSheet("color: #1a7f37; font-weight: bold;")
    else:
        banner.setText(
            f"{_seg_method_label(method)} extension is not configured.\n"
            "Open Extension → Manage Extension to set it up first.",
        )
        banner.setStyleSheet("color: #b58900; font-weight: bold;")
    lay.addWidget(banner)

    def param_label(title, desc=""):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(QLabel(title))
        if desc:
            d = QLabel(desc)
            d.setStyleSheet("color: #6b7785;")
            h.addWidget(d)
        h.addStretch()
        return row

    lay.addWidget(QLabel("Files to calculate"))
    files_list = QListWidget()
    files_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    files_list.setMaximumHeight(110)
    for path in _seg_file_rows(ctx, page):
        item = QListWidgetItem(_data_naming._comp_file_name(ctx, path, page._seg_task_id))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        has_mask = (
            _seg_mask_file(ctx, _seg_mask_record(ctx, page._seg_params, path))
            is not None
        )
        item.setCheckState(
            Qt.CheckState.Unchecked if has_mask else Qt.CheckState.Checked
        )
        files_list.addItem(item)
    lay.addWidget(files_list)
    page._seg_calc_files_list = files_list

    lay.addWidget(param_label("Nucleus channel"))
    columns = _seg_channel_columns(ctx, page)
    channel_combo = _NoWheelComboBox()
    channel_combo.addItem("", None)
    for name in columns:
        channel_combo.addItem(name, name)
    idx = channel_combo.findData(params.get("channel"))
    if idx >= 0:
        channel_combo.setCurrentIndex(idx)
    channel_combo.setEnabled(enabled)
    lay.addWidget(channel_combo)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Channel used to detect nuclei."
    ))

    cyto_combo = None
    if entry["family"] == "cellpose":
        lay.addWidget(param_label("Cytoplasm channel (optional)"))
        cyto_combo = _NoWheelComboBox()
        cyto_combo.addItem("(none)", None)
        for name in columns:
            cyto_combo.addItem(name, name)
        idx = cyto_combo.findData(params.get("cyto_channel"))
        if idx >= 0:
            cyto_combo.setCurrentIndex(idx)
        cyto_combo.setEnabled(enabled)
        lay.addWidget(cyto_combo)
        lay.addWidget(_widgets_panels._param_hint(ctx,
            "Optional second channel that helps Cellpose follow the cell "
            "body around each nucleus."
        ))

    prob_spin = None
    nms_spin = None
    tile_size_spin = None
    if entry["family"] == "stardist":
        params.setdefault("model", entry["model"])
        params.setdefault("prob", 0.5)
        params.setdefault("nms", 0.3)
        params.setdefault("tile_size", 0)
        lay.addWidget(param_label("Probability threshold"))
        prob_spin = _NoWheelDoubleSpinBox()
        prob_spin.setRange(0.01, 1.0)
        prob_spin.setSingleStep(0.05)
        prob_spin.setDecimals(2)
        prob_spin.setValue(float(params.get("prob", 0.5)))
        prob_spin.setEnabled(enabled)
        lay.addWidget(prob_spin)
        lay.addWidget(_widgets_panels._param_hint(ctx,
            "Lower values detect more cells, and more false positives."
        ))

        lay.addWidget(param_label("NMS threshold"))
        nms_spin = _NoWheelDoubleSpinBox()
        nms_spin.setRange(0.0, 1.0)
        nms_spin.setSingleStep(0.05)
        nms_spin.setDecimals(2)
        nms_spin.setValue(float(params.get("nms", 0.3)))
        nms_spin.setEnabled(enabled)
        lay.addWidget(nms_spin)
        lay.addWidget(_widgets_panels._param_hint(ctx,
            "Non-maximum suppression: higher removes more overlapping "
            "detections."
        ))

        lay.addWidget(param_label("Tile size (px)"))
        tile_size_spin = _NoWheelSpinBox()
        tile_size_spin.setRange(0, 20000)
        tile_size_spin.setSingleStep(64)
        tile_size_spin.setValue(int(params.get("tile_size", 0)))
        tile_size_spin.setEnabled(enabled)
        tile_size_spin.setToolTip(
            "Approximate edge length of each tile in pixels. Smage splits "
            "each image into roughly this-sized tiles (n × m), so the "
            "actual counts adapt to the image dimensions. 0 lets StarDist "
            "pick a good size automatically."
        )
        lay.addWidget(tile_size_spin)
        lay.addWidget(_widgets_panels._param_hint(ctx,
            "Approximate tile edge in pixels; Smage adapts the actual "
            "tiles to the image size. 0 = let StarDist choose."
        ))

    lay.addWidget(_seg_run_controls(ctx, page, enabled and ready))
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    # The log box is created once by _seg_method_panel (below the Calculate
    # button) and shared by every method.
    page._seg_calc_status = status

    def on_channel(_=0):
        params["channel"] = channel_combo.currentData()
        _seg_mark_dirty(ctx, page)

    def on_cyto(_=0):
        params["cyto_channel"] = cyto_combo.currentData()
        _seg_mark_dirty(ctx, page)

    def on_prob(value):
        params["prob"] = float(value)
        _seg_mark_dirty(ctx, page)

    def on_nms(value):
        params["nms"] = float(value)
        _seg_mark_dirty(ctx, page)

    def on_tile_size(value):
        params["tile_size"] = int(value)
        _seg_mark_dirty(ctx, page)

    channel_combo.currentIndexChanged.connect(on_channel)
    if cyto_combo is not None:
        cyto_combo.currentIndexChanged.connect(on_cyto)
    if prob_spin is not None:
        prob_spin.valueChanged.connect(on_prob)
    if nms_spin is not None:
        nms_spin.valueChanged.connect(on_nms)
    if tile_size_spin is not None:
        tile_size_spin.valueChanged.connect(on_tile_size)
    page._seg_calc_btn.clicked.connect(
        lambda _=False: _seg_calculate(ctx, page)
    )


def _seg_mask_input_panel(ctx, page, host) -> None:
    """Mask Input: one row per file with a mask-file picker + Import."""
    lay = host.layout()
    lay.addWidget(QLabel("<b>Mask Input</b>"))
    table = QTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["File", "Mask file", "Status"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(28)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
    )
    lay.addWidget(table, 1)
    page._seg_input_table = table

    button_row = QWidget()
    button_lay = QHBoxLayout(button_row)
    button_lay.setContentsMargins(0, 0, 0, 0)
    button_lay.setSpacing(6)
    import_btn = QPushButton("Import")
    import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    import_btn.setEnabled(
        not page._seg_locked
        and not getattr(page, "_seg_import_running", False)
        and not getattr(page, "_seg_calc_running", False)
    )
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel_btn.setEnabled(getattr(page, "_seg_import_running", False))
    cancel_btn.setToolTip("Stop after the current mask file")
    cancel_btn.clicked.connect(lambda _=False: _seg_cancel_run(ctx, page))
    button_lay.addWidget(import_btn, 1)
    button_lay.addWidget(cancel_btn)
    lay.addWidget(button_row)
    page._seg_import_btn = import_btn
    page._seg_import_cancel_btn = cancel_btn
    hint = QLabel(
        "Masks must be TIFF images whose width and height match the data file."
    )
    hint.setStyleSheet("color: #6b7785;")
    hint.setWordWrap(True)
    lay.addWidget(hint)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._seg_import_status = status
    import_btn.clicked.connect(lambda _=False: _seg_import_masks(ctx, page))

    _seg_refresh_input_table(ctx, page)


def _seg_mask_record_live(ctx, page, parquet_path, create=False) -> dict:
    masks = page._seg_params.setdefault("masks", {})
    rec = masks.get(parquet_path)
    if not isinstance(rec, dict):
        if not create:
            return {}
        rec = {"path": None, "source": None, "input": None,
               "state": SEG_IMPORT_SELECT}
        masks[parquet_path] = rec
    return rec


def _seg_refresh_input_table(ctx, page) -> None:
    table = getattr(page, "_seg_input_table", None)
    if table is None:
        return
    rows = _seg_file_rows(ctx, page)
    table.blockSignals(True)
    table.setRowCount(len(rows))
    enabled = not page._seg_locked
    for i, path in enumerate(rows):
        name_item = QTableWidgetItem(_data_naming._comp_file_name(ctx, path, page._seg_task_id))
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(i, 0, name_item)

        rec = _seg_mask_record(ctx, page._seg_params, path) or {}
        btn = QPushButton(Path(str(rec.get("input") or "")).name or "…")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(enabled)
        btn.setToolTip(rec.get("input") or "Choose a mask file")
        btn.clicked.connect(
            lambda _=False, p=path: _seg_pick_mask_file(ctx, page, p)
        )
        table.setCellWidget(i, 1, btn)

        state = rec.get("state") or (
            SEG_IMPORT_DONE if _seg_mask_file(ctx, rec) else SEG_IMPORT_SELECT
        )
        sitem = QTableWidgetItem(state)
        sitem.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if state == SEG_IMPORT_DONE:
            sitem.setForeground(QColor("#1a7f37"))
        elif state in (SEG_IMPORT_FORMAT, SEG_IMPORT_SIZE, SEG_IMPORT_UNKNOWN):
            sitem.setForeground(QColor("#c0392b"))
        else:
            sitem.setForeground(QColor("#9aa0a6"))
        table.setItem(i, 2, sitem)
    table.blockSignals(False)


def _seg_pick_mask_file(ctx, page, parquet_path) -> None:
    rec = _seg_mask_record_live(ctx, page, parquet_path, create=True)
    already = _seg_mask_file(ctx, rec) is not None
    if already:
        reply = QMessageBox.question(
            ctx.host,
            "Choose mask file",
            "This file's mask has already been imported.\n"
            "Choosing another one will delete the imported mask. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    start_dir = getattr(ctx, "_seg_last_mask_dir", None) or str(Path.home())
    if rec.get("input"):
        start_dir = str(Path(str(rec["input"])).parent)
    path, _ = QFileDialog.getOpenFileName(
        ctx.host, "Select mask file", start_dir, "All files (*)"
    )
    if not path:
        return
    ctx._seg_last_mask_dir = str(Path(path).parent)
    if already:
        _seg_delete_mask(ctx, page, parquet_path)
    rec["input"] = path
    rec["state"] = SEG_IMPORT_WAITING
    rec["path"] = rec.get("path") if rec.get("source") == "builtin" else None
    _seg_refresh_input_table(ctx, page)
    _seg_refresh_files(ctx, page)
    _seg_mark_dirty(ctx, page)


def _seg_delete_mask(ctx, page, parquet_path) -> None:
    """Remove a file's mask parquet, its raster pyramid and its record."""
    import shutil

    rec = _seg_mask_record(ctx, page._seg_params, parquet_path) or {}
    rel = rec.get("path")
    if rel and ctx.project is not None:
        try:
            (ctx.project.path / rel).unlink(missing_ok=True)
        except OSError:
            pass
        try:
            shutil.rmtree(
                _image_index_dir(ctx.project.path / rel), ignore_errors=True
            )
        except OSError:
            pass
    page._seg_mask_cache = {}
    ctx._seg_value_cache = {}


def _seg_import_masks(ctx, page) -> None:
    """Import queued mask TIFFs on a background thread."""
    if getattr(page, "_seg_import_running", False):
        status = getattr(page, "_seg_import_status", None)
        if status is not None:
            status.setText("A mask import is already running.")
        return
    if getattr(page, "_seg_calc_running", False):
        status = getattr(page, "_seg_import_status", None)
        if status is not None:
            status.setText("Wait for the running calculation to finish.")
        return

    rows = _seg_file_rows(ctx, page)
    if not rows:
        QMessageBox.information(
            ctx.host, "Import", "Select data files first."
        )
        return
    pending = []
    for path in rows:
        rec = _seg_mask_record_live(ctx, page, path, create=True)
        if rec.get("state") == SEG_IMPORT_DONE and _seg_mask_file(ctx, rec):
            continue
        if not rec.get("input"):
            continue
        pending.append((
            str(path),
            str(rec.get("input")),
            _data_naming._comp_file_name(ctx, path, page._seg_task_id),
        ))
    if not pending:
        QMessageBox.information(
            ctx.host, "Import",
            "All files have already been imported — nothing new to import.",
        )
        return

    page._seg_import_running = True
    page._seg_import_cancel = False
    page._seg_import_done = 0
    page._seg_import_errors = []
    import_btn = getattr(page, "_seg_import_btn", None)
    if import_btn is not None:
        import_btn.setEnabled(False)
    cancel_btn = getattr(page, "_seg_import_cancel_btn", None)
    if cancel_btn is not None:
        cancel_btn.setEnabled(True)
    status = getattr(page, "_seg_import_status", None)
    if status is not None:
        status.setText(f"Importing {len(pending)} mask(s)…")
    _seg_log(ctx, page, f"===== Mask import — {len(pending)} file(s) =====")

    q = queue.Queue()
    page._seg_import_queue = q
    thread = threading.Thread(
        target=_seg_run_mask_import,
        args=(ctx, page, q, pending),
        daemon=True,
    )
    page._seg_import_thread = thread
    thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _seg_poll_mask_import(ctx, page))
    timer.start()
    page._seg_import_timer = timer


def _seg_run_mask_import(ctx, page, q, pending) -> None:
    """Read and validate TIFF masks; write each one on this worker thread."""
    import numpy as np
    import tifffile

    done = 0
    errors = []
    cancelled = False
    total = len(pending)
    try:
        for i, (parquet_path, src, name) in enumerate(pending):
            if getattr(page, "_seg_import_cancel", False):
                cancelled = True
                q.put(("log", "Mask import cancelled."))
                break
            q.put(("status", f"Importing {i + 1}/{total}: {name}…"))
            q.put(("log", f"→ Importing {i + 1}/{total}: {name}…"))
            ent = _data_image_index._ensure_image_index(ctx, parquet_path)
            if ent is None or ent.get("manifest") is None:
                errors.append(f"{name}: no image index")
                q.put(("file_error", parquet_path,
                       SEG_IMPORT_UNKNOWN, name, "no image index"))
                continue
            man = ent["manifest"]
            height = int(round(man["ymax"] - man["ymin"])) + 1
            width = int(round(man["xmax"] - man["xmin"])) + 1
            suffix = Path(src).suffix.lower()
            if suffix not in (".tif", ".tiff", ".ome.tif", ".ome.tiff"):
                errors.append(f"{name}: not a TIFF file")
                q.put(("file_error", parquet_path,
                       SEG_IMPORT_FORMAT, name, "not a TIFF file"))
                continue
            try:
                arr = tifffile.imread(src)
            except Exception:  # noqa: BLE001
                errors.append(f"{name}: could not read the TIFF file")
                q.put(("file_error", parquet_path,
                       SEG_IMPORT_FORMAT, name,
                       "could not read the TIFF file"))
                continue
            arr = np.asarray(arr)
            while arr.ndim > 2:
                arr = arr[0]
            if arr.ndim != 2:
                errors.append(f"{name}: the mask is not a 2-D image")
                q.put(("file_error", parquet_path,
                       SEG_IMPORT_FORMAT, name,
                       "the mask is not a 2-D image"))
                continue
            if arr.shape[0] != height or arr.shape[1] != width:
                reason = (
                    f"mask is {arr.shape[1]}×{arr.shape[0]} but the "
                    f"image is {width}×{height}"
                )
                errors.append(f"{name}: {reason}")
                q.put(("file_error", parquet_path,
                       SEG_IMPORT_SIZE, name, reason))
                continue
            try:
                rel = _seg_write_mask(
                    ctx, page._seg_task_id, parquet_path, arr
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: could not save the mask")
                q.put(("file_error", parquet_path,
                       SEG_IMPORT_UNKNOWN, name, str(exc)))
                continue
            done += 1
            q.put(("file", parquet_path, rel, int(arr.max()), name))
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    finally:
        q.put(("done", done, errors, cancelled))


def _seg_poll_mask_import(ctx, page) -> None:
    """Apply background mask-import events on the UI thread."""
    q = getattr(page, "_seg_import_queue", None)
    if q is None:
        return
    status = getattr(page, "_seg_import_status", None)
    done = getattr(page, "_seg_import_done", 0)
    errors = getattr(page, "_seg_import_errors", [])
    finished = None
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "log":
            _seg_log(ctx, page, msg[1])
        elif kind == "status":
            if status is not None:
                status.setText(msg[1])
        elif kind == "file":
            parquet_path, rel, count, name = msg[1], msg[2], msg[3], msg[4]
            rec = _seg_mask_record_live(
                ctx, page, parquet_path, create=True
            )
            rec["path"] = rel
            rec["source"] = "import"
            rec["state"] = SEG_IMPORT_DONE
            done += 1
            _seg_log(ctx, page, f"✓ {name}: {count} object(s)")
        elif kind == "file_error":
            parquet_path, state, name, reason = (
                msg[1], msg[2], msg[3], msg[4]
            )
            rec = _seg_mask_record_live(
                ctx, page, parquet_path, create=True
            )
            rec["state"] = state
            _seg_log(ctx, page, f"✗ {name}: {reason}")
        elif kind == "done":
            done, errors, cancelled = msg[1], msg[2], msg[3]
            finished = (done, errors, bool(cancelled))
    page._seg_import_done = done
    page._seg_import_errors = errors
    if finished is None:
        return

    timer = getattr(page, "_seg_import_timer", None)
    if timer is not None:
        timer.stop()
    page._seg_import_queue = None
    page._seg_import_running = False
    done, errors, cancelled = finished
    _seg_log(ctx, page, f"Imported {done} mask(s).")
    _seg_after_masks_changed(ctx, page)
    _seg_refresh_input_table(ctx, page)
    _seg_refresh_files(ctx, page)
    import_btn = getattr(page, "_seg_import_btn", None)
    if import_btn is not None:
        import_btn.setEnabled(not page._seg_locked)
    cancel_btn = getattr(page, "_seg_import_cancel_btn", None)
    if cancel_btn is not None:
        cancel_btn.setEnabled(False)
    if cancelled:
        msg = f"Mask import cancelled after {done} file(s)."
    else:
        msg = f"Imported {done} mask(s)."
    if errors:
        msg += "\n" + "\n".join(errors)
    status = getattr(page, "_seg_import_status", None)
    if status is not None:
        status.setText(msg)
    ctx.statusBar().showMessage(msg.splitlines()[0])


def _seg_write_mask(ctx, task_id, parquet_path, array) -> str:
    """Save a mask as a ctx-contained ``[x, y, mask]`` parquet + label pyramid.

    The mask becomes an extra channel of the source image: a parquet with
    one row per pixel (x/y shared with the source) plus a mode-based raster
    pyramid so large masks zoom like the original image. Returns the
    relative path of the mask parquet.
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    if ctx.project is None:
        raise RuntimeError("no project")
    ent = _data_image_index._ensure_image_index(ctx, parquet_path)
    if ent is None or ent.get("manifest") is None:
        raise RuntimeError("source image has no index")
    man = ent["manifest"]
    xmin = int(round(float(man["xmin"])))
    ymin = int(round(float(man["ymin"])))
    width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
    height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
    arr = np.asarray(array, dtype=np.int32)
    if arr.ndim != 2 or arr.shape[0] != height or arr.shape[1] != width:
        # Trust the array's own shape if it disagrees with the manifest.
        height, width = arr.shape

    stem = Path(str(parquet_path)).stem
    rel = _data_files._module_rel(ctx, task_id, f"{stem}_mask.parquet")
    path = ctx.project.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(arr.size)
    max_label = int(arr.max()) if total else 0
    # Label counts normally fit in uint16; fall back for very large images.
    mask_dtype = np.uint16 if max_label <= SEG_UINT16_MAX else np.int32
    field_type = pa.uint16() if mask_dtype is np.uint16 else pa.int32()
    schema = pa.schema([
        ("x", pa.int32()),
        ("y", pa.int32()),
        ("mask", field_type),
    ])
    flat = arr.ravel()
    chunk = max(1, min(int(SEG_WRITE_CHUNK), max(total, 1)))
    with pq.ParquetWriter(
        path, schema, compression="zstd", compression_level=1
    ) as writer:
        for start in range(0, total, chunk):
            end = min(total, start + chunk)
            index = np.arange(start, end, dtype=np.int64)
            x = (index % width + xmin).astype(np.int32)
            y = (index // width + ymin).astype(np.int32)
            values = flat[start:end]
            if values.dtype != np.dtype(mask_dtype):
                values = values.astype(mask_dtype, copy=False)
            table = pa.Table.from_arrays(
                [
                    pa.array(x, type=pa.int32()),
                    pa.array(y, type=pa.int32()),
                    pa.array(values, type=field_type),
                ],
                schema=schema,
            )
            writer.write_table(table)
    _build_label_index(
        path, arr, xmin, xmin + width - 1, ymin, ymin + height - 1
    )
    # Cache invalidation happens on the UI thread when the writer reports the
    # finished file; _seg_write_mask itself may run on a worker.
    return rel


class _SegMaskWriter:
    """Serialize mask parquet + pyramid writes on a background thread.

    External segmentation produces masks in a subprocess; writing them on the
    UI thread froze the window for every finished file. This writer accepts
    either an in-memory mask or a temporary ``.npy`` output (Cellpose /
    StarDist), and reports results through ``poll()``. The caller can attach a
    ``release`` callback (the batch's input semaphore) so a finished write
    allows the feeder to prepare the next input.
    """

    def __init__(self, ctx, max_queue=3) -> None:
        self._ctx = ctx
        self._queue = queue.Queue(maxsize=max(1, int(max_queue)))
        self._results = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, task_id, parquet_path, *, array=None, npy_path=None,
               n_cells=0, meta=None, release=None) -> None:
        self._queue.put({
            "task_id": task_id,
            "parquet_path": str(parquet_path),
            "array": array,
            "npy_path": str(npy_path) if npy_path else None,
            "n_cells": int(n_cells or 0),
            "meta": dict(meta or {}),
            "release": release,
        })

    def poll(self) -> list:
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        """Wait for queued writes, then stop the worker."""
        self._queue.put(None)
        self._thread.join(timeout=30)

    def _run(self) -> None:
        import numpy as np

        while True:
            job = self._queue.get()
            if job is None:
                return
            result = None
            try:
                mask = job["array"]
                if mask is None and job["npy_path"]:
                    try:
                        mask = np.load(job["npy_path"])
                    finally:
                        Path(job["npy_path"]).unlink(missing_ok=True)
                if mask is None:
                    raise ValueError("no mask output was produced")
                rel = _seg_write_mask(
                    self._ctx, job["task_id"], job["parquet_path"], mask
                )
                result = (
                    "written", job["parquet_path"], rel,
                    job["n_cells"], job["meta"],
                )
            except Exception as exc:  # noqa: BLE001
                result = (
                    "write_error", job["parquet_path"], str(exc),
                    job["meta"],
                )
            finally:
                release = job.get("release")
                if release is not None:
                    try:
                        release()
                    except Exception:  # noqa: BLE001
                        pass
            self._results.put(result)


def _seg_after_masks_changed(ctx, page) -> None:
    """Persist mask records: masks are products, so save right away."""
    ctx._seg_mask_cache = {}
    ctx._seg_value_cache = {}
    page._seg_task_def["output"] = {"saved": True}
    page._seg_task_def["params"] = page._seg_params
    if ctx.project is not None:
        ctx.project.save()
    page._seg_snapshot = copy.deepcopy(page._seg_params)
    page._seg_dirty = False
    _seg_update_save_btn(ctx, page)
    ctx._refresh_workflow_tree()
    ctx._module_saved(page)


def _seg_calculate(ctx, page) -> None:
    """Run the selected method on every selected file."""
    method = page._seg_params.get("method")
    if _extension_def(method) is not None:
        _seg_calculate_external(ctx, page)
        return
    _seg_calculate_nuclear(ctx, page)


def _seg_calculate_nuclear(ctx, page) -> None:
    """Start Nuclear Expansion on every selected file, in the background.

    The mask computation runs on a worker thread so the window stays
    responsive; progress arrives through a queue and is applied by
    :func:`_seg_poll_nuclear` on the UI thread.
    """
    if getattr(page, "_seg_calc_running", False):
        ctx.statusBar().showMessage(
            "Segmentation: a calculation is already running"
        )
        return
    if getattr(page, "_seg_import_running", False):
        ctx.statusBar().showMessage(
            "Segmentation: wait for the mask import to finish"
        )
        return
    rows = _seg_file_rows(ctx, page)
    params = _seg_map_params(ctx, page)
    status = getattr(page, "_seg_calc_status", None)
    if not rows:
        QMessageBox.information(ctx.host, "Calculate", "Select data files first.")
        return
    if not params.get("channel"):
        QMessageBox.information(
            ctx.host, "Calculate", "Choose the nucleus channel first."
        )
        return

    page._seg_calc_running = True
    page._seg_calc_cancel = False
    page._seg_calc_finished = False
    btn = getattr(page, "_seg_calc_btn", None)
    if btn is not None:
        page._seg_calc_btn_prev_enabled = btn.isEnabled()
        btn.setEnabled(False)
    cancel = getattr(page, "_seg_cancel_btn", None)
    if cancel is not None:
        cancel.setEnabled(True)
    if status is not None:
        status.setText(f"Starting Nuclear Expansion on {len(rows)} file(s)…")

    q = queue.Queue()
    page._seg_nuclear_queue = q
    thread = threading.Thread(
        target=_seg_run_nuclear_bg,
        # A snapshot: parameter edits during the run must not mix two
        # configurations into one batch.
        args=(ctx, page, q, rows, copy.deepcopy(params)),
        daemon=True,
    )
    page._seg_nuclear_thread = thread
    thread.start()

    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _seg_poll_nuclear(ctx, page))
    timer.start()
    page._seg_nuclear_timer = timer


def _seg_run_nuclear_bg(ctx, page, q, rows, params) -> None:
    """Compute and write masks on a worker thread, posting progress events."""
    done = 0
    cells = 0
    errors = []
    try:
        q.put(("log",
               f"===== Nuclear Expansion run — {len(rows)} file(s) ====="))
        for i, parquet_path in enumerate(rows):
            if getattr(page, "_seg_calc_cancel", False):
                q.put(("log", "Calculation cancelled."))
                break
            name = _data_naming._comp_file_name(
                ctx, parquet_path, page._seg_task_id
            )
            q.put(("log", f"→ Calculating {i + 1}/{len(rows)}: {name}…"))
            q.put(("status",
                   f"Calculating {i + 1}/{len(rows)}: {name}…"))
            mask, err = _seg_nuclear_expansion(
                ctx, parquet_path, params, page._seg_task_id
            )
            if mask is None:
                errors.append(f"{name}: {err}")
                q.put(("log", f"✗ {name}: {err}"))
                continue
            try:
                rel = _seg_write_mask(
                    ctx, page._seg_task_id, parquet_path, mask
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                q.put(("log", f"✗ {name}: {exc}"))
                continue
            nucleus_count = int(mask.max())
            done += 1
            cells += nucleus_count
            q.put(("file", parquet_path, rel, nucleus_count))
            q.put(("log", f"✓ {name}: {nucleus_count} nucleus(s)"))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))
        return
    q.put(("done", done, cells, errors))


def _seg_poll_nuclear(ctx, page) -> None:
    """Apply a background Nuclear Expansion run's events on the UI thread."""
    q = getattr(page, "_seg_nuclear_queue", None)
    if q is None:
        return
    status = getattr(page, "_seg_calc_status", None)
    finished = False
    error = None
    done = 0
    cells = 0
    errors = []
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "log":
            _seg_log(ctx, page, msg[1])
        elif kind == "status":
            if status is not None:
                status.setText(msg[1])
        elif kind == "file":
            parquet_path, rel = msg[1], msg[2]
            # Mask records belong to the page, so they are updated here
            # rather than on the worker thread.
            rec = _seg_mask_record_live(ctx, page, parquet_path, create=True)
            rec["path"] = rel
            rec["source"] = "builtin"
            rec["state"] = SEG_IMPORT_DONE
        elif kind == "done":
            done, cells, errors = msg[1], msg[2], msg[3]
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return

    timer = getattr(page, "_seg_nuclear_timer", None)
    if timer is not None:
        timer.stop()
    page._seg_nuclear_queue = None
    page._seg_calc_running = False
    page._seg_calc_finished = True

    if error is not None:
        _seg_log(ctx, page, f"✗ Calculation failed: {error}")
        if status is not None:
            status.setText(f"Calculation failed: {error}")
        _seg_restore_calc_btn(ctx, page)
        return

    cancelled = getattr(page, "_seg_calc_cancel", False)
    if cancelled:
        summary = f"Cancelled after {done} mask(s) · {cells} nucleus(s)."
    else:
        summary = f"Calculated {done} mask(s) · {cells} nucleus(s)."
    _seg_log(ctx, page, summary)
    _seg_after_masks_changed(ctx, page)
    _seg_refresh_files(ctx, page)
    # The refresh rebuilds the parameter panel, so re-read its status label.
    status = getattr(page, "_seg_calc_status", status)
    if status is not None:
        msg = summary
        if errors:
            msg += "\n" + "\n".join(errors)
        status.setText(msg)


def _seg_cancel_run(ctx, page) -> None:
    """Request cancellation of whichever segmentation operation is running."""
    if getattr(page, "_seg_import_running", False):
        page._seg_import_cancel = True
        status = getattr(page, "_seg_import_status", None)
        if status is not None:
            status.setText("Cancelling mask import…")
        return
    if not getattr(page, "_seg_calc_running", False):
        return
    page._seg_calc_cancel = True
    if getattr(page, "_seg_ext_running", False):
        page._seg_ext_cancelled = True
        try:
            ctx._ext_worker().kill()
        except Exception:  # noqa: BLE001
            pass
    status = getattr(page, "_seg_calc_status", None)
    if status is not None:
        status.setText("Cancelling…")


def _seg_restore_calc_btn(ctx, page) -> None:
    """Re-enable Calculate after a background run that did not rebuild it."""
    btn = getattr(page, "_seg_calc_btn", None)
    if btn is not None:
        btn.setEnabled(
            bool(getattr(page, "_seg_calc_btn_prev_enabled", True))
            and not page._seg_locked
        )
    cancel = getattr(page, "_seg_cancel_btn", None)
    if cancel is not None:
        cancel.setEnabled(False)


def _seg_raster_stack(ctx, parquet_path, channels, task_id=None):
    """Reconstruct a (C, H, W) float stack of the given channels."""
    import numpy as np

    images = []
    shape = None
    for channel in channels:
        image, err = _seg_raster_channel(
            ctx, parquet_path, channel, task_id=task_id
        )
        if image is None:
            return None, err
        if shape is None:
            shape = image.shape
        elif image.shape != shape:
            return None, "channel shapes differ"
        images.append(image)
    return np.stack(images, axis=0).astype("float32"), None


def _seg_calculate_external(ctx, page) -> None:
    """Run Cellpose / StarDist on the selected files in one subprocess.

    Inputs are rasterized just ahead of the subprocess (bounded by
    :data:`SEG_PREFETCH`), the runner consumes each input as it appears, and
    finished masks are written by a separate writer thread. This keeps both
    the UI responsive and the project's temporary disk use bounded.
    """
    import json
    import os
    import uuid

    if getattr(page, "_seg_calc_running", False):
        ctx.statusBar().showMessage(
            "Segmentation: a calculation is already running"
        )
        return
    if getattr(page, "_seg_import_running", False):
        ctx.statusBar().showMessage(
            "Segmentation: wait for the mask import to finish"
        )
        return
    if ctx.project is None:
        QMessageBox.information(
            ctx.host, "Calculate", "Open a project before running segmentation."
        )
        return
    method = page._seg_params.get("method")
    entry = _extension_def(method)
    params = _seg_map_params(ctx, page)
    files_list = getattr(page, "_seg_calc_files_list", None)
    if files_list is not None:
        rows = []
        for i in range(files_list.count()):
            item = files_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                rows.append(item.data(Qt.ItemDataRole.UserRole))
    else:
        rows = _seg_file_rows(ctx, page)
    status = getattr(page, "_seg_calc_status", None)

    if not rows:
        QMessageBox.information(
            ctx.host, "Calculate",
            "Select at least one file in the list to calculate.",
        )
        return
    if not params.get("channel"):
        QMessageBox.information(
            ctx.host, "Calculate", "Choose the nucleus channel first."
        )
        return
    if not ctx._ext_ready(method):
        QMessageBox.information(
            ctx.host,
            "Calculate",
            f"{_seg_method_label(method)} extension is not configured.\n"
            "Open Extension → Manage Extension to set it up first.",
        )
        return

    run_id = (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    tmpdir = _seg_runs_dir(ctx) / run_id
    try:
        tmpdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.warning(
            ctx.host, "Calculate",
            f"Could not create the segmentation temporary folder:\n{exc}",
        )
        return
    nucleus = params.get("channel")
    cyto = params.get("cyto_channel") if entry["family"] == "cellpose" else None

    jobs = []
    manifest = []
    for i, parquet_path in enumerate(rows):
        image_npy = tmpdir / f"in_{i:04d}.npy"
        output_npy = tmpdir / f"out_{i:04d}.npy"
        name = _data_naming._comp_file_name(
            ctx, parquet_path, page._seg_task_id
        )
        jobs.append({
            "index": i,
            "parquet": str(parquet_path),
            "image": str(image_npy),
            "output": str(output_npy),
            "name": name,
        })
        manifest.append({
            "image": str(image_npy),
            "output": str(output_npy),
        })
    manifest_path = tmpdir / "jobs.json"
    try:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    except OSError as exc:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
        QMessageBox.warning(
            ctx.host, "Calculate",
            f"Could not write the segmentation job list:\n{exc}",
        )
        return

    page._seg_calc_running = True
    page._seg_calc_cancel = False
    page._seg_calc_finished = False
    page._seg_ext_running = True
    page._seg_ext_cancelled = False
    page._seg_ext_aborted = False
    page._seg_ext_entry = entry
    page._seg_ext_method = method
    page._seg_ext_params = copy.deepcopy(params)
    page._seg_ext_python = ctx._ext_python(method)
    page._seg_ext_runner = str(ctx._ext_runner_path(method))
    page._seg_ext_tmpdir = tmpdir
    page._seg_ext_nucleus_idx = 1 if cyto else 0
    page._seg_ext_cyto_idx = 0 if cyto else None
    page._seg_ext_jobs = jobs
    page._seg_ext_done = set()
    page._seg_ext_errors = []
    page._seg_ext_cells = 0
    page._seg_ext_report = []
    page._seg_ext_tile = None
    page._seg_ext_activity = ""
    page._seg_ext_proc_finished = False
    page._seg_ext_feeder_done = False
    page._seg_ext_pending_writes = 0
    page._seg_ext_prepared = 0
    page._seg_ext_written = 0
    page._seg_ext_slots = threading.Semaphore(SEG_PREFETCH)
    page._seg_ext_writer = _SegMaskWriter(
        ctx, max_queue=SEG_PREFETCH + 1
    )
    btn = getattr(page, "_seg_calc_btn", None)
    if btn is not None:
        page._seg_calc_btn_prev_enabled = btn.isEnabled()
        btn.setEnabled(False)
    cancel = getattr(page, "_seg_cancel_btn", None)
    if cancel is not None:
        cancel.setEnabled(True)
    if status is not None:
        status.setText(f"Preparing {len(rows)} file(s)…")

    q = queue.Queue()
    page._seg_ext_queue = q
    thread = threading.Thread(
        target=_seg_feed_external_inputs,
        args=(ctx, page, q, jobs, nucleus, cyto, page._seg_ext_slots),
        daemon=True,
    )
    page._seg_ext_thread = thread
    thread.start()

    # The runner waits for each input file, so it can start (and load its
    # model) while the first file is still being rasterized.
    _seg_ext_launch(ctx, page, jobs, str(manifest_path))

    old_timer = getattr(page, "_seg_ext_timer", None)
    if old_timer is not None:
        old_timer.stop()
    timer = QTimer(ctx.host)
    timer.setInterval(150)
    timer.timeout.connect(lambda: _seg_ext_poll(ctx, page))
    timer.start()
    page._seg_ext_timer = timer


def _seg_ext_failed_marker(image_path):
    """Marker written next to an input that could not be rasterized."""
    return Path(str(image_path)).with_suffix(".failed")


def _seg_slot_acquire(slots, page) -> bool:
    """Take an input slot, waking periodically to notice cancel/finish."""
    while (
        not getattr(page, "_seg_calc_cancel", False)
        and not getattr(page, "_seg_ext_aborted", False)
        and not getattr(page, "_seg_ext_proc_finished", False)
    ):
        if slots.acquire(timeout=0.2):
            return True
    return False


def _seg_feed_external_inputs(ctx, page, q, jobs, nucleus, cyto,
                              slots) -> None:
    """Rasterize inputs just ahead of the runner, bounded by ``slots``."""
    import numpy as np

    total = len(jobs)
    try:
        q.put(("log",
               f"Rasterizing up to {SEG_PREFETCH} file(s) ahead of "
               f"{_seg_method_label(page._seg_ext_method)}…"))
        for i, job in enumerate(jobs):
            if not _seg_slot_acquire(slots, page):
                break
            if getattr(page, "_seg_calc_cancel", False):
                slots.release()
                break
            name = job.get("name") or Path(job["parquet"]).name
            image_npy = Path(job["image"])
            q.put(("status", f"Rasterizing {i + 1}/{total}: {name}…"))
            q.put(("log", f"  rasterizing {name}…"))
            try:
                if cyto:
                    stack, err = _seg_raster_stack(
                        ctx, job["parquet"], [cyto, nucleus],
                        page._seg_task_id,
                    )
                else:
                    stack, err = _seg_raster_stack(
                        ctx, job["parquet"], [nucleus],
                        page._seg_task_id,
                    )
            except Exception as exc:  # noqa: BLE001
                stack, err = None, str(exc)
            if stack is None:
                marker = _seg_ext_failed_marker(image_npy)
                try:
                    marker.write_text(str(err or "rasterization failed"),
                                      encoding="utf-8")
                except OSError as exc:
                    q.put(("feeder_error",
                           f"could not write failure marker: {exc}"))
                    return
                q.put(("input_failed", i, name, str(err or "failed")))
                q.put(("log", f"✗ {name}: {err}"))
                continue
            try:
                part = Path(str(image_npy) + ".part")
                with open(part, "wb") as file:
                    np.save(file, stack)
                part.replace(image_npy)
            except Exception as exc:  # noqa: BLE001
                marker = _seg_ext_failed_marker(image_npy)
                try:
                    marker.write_text(str(exc), encoding="utf-8")
                except OSError as marker_exc:
                    q.put(("feeder_error",
                           f"could not write failure marker: {marker_exc}"))
                    return
                q.put(("input_failed", i, name, str(exc)))
                q.put(("log", f"✗ {name}: {exc}"))
                continue
            q.put(("prepared", i, name))
    except Exception as exc:  # noqa: BLE001
        q.put(("feeder_error", str(exc)))
    finally:
        q.put(("feeder_done",))


def _seg_ext_launch(ctx, page, jobs, manifest_path) -> None:
    """Start the Cellpose / StarDist subprocess (it already streams)."""
    import os

    entry = page._seg_ext_entry
    params = page._seg_ext_params
    threads = max(1, min(8, os.cpu_count() or 1))
    argv = [
        page._seg_ext_python, page._seg_ext_runner, "segment",
        "--manifest", manifest_path,
        "--nucleus", str(page._seg_ext_nucleus_idx),
        "--model", entry["model"],
        "--device", entry["device"],
        "--threads", str(threads),
        "--wait-inputs",
        "--input-timeout", str(SEG_INPUT_TIMEOUT),
    ]
    if page._seg_ext_cyto_idx is not None:
        argv += ["--cyto", str(page._seg_ext_cyto_idx)]
    if entry["family"] == "stardist":
        argv += [
            "--prob", str(params.get("prob", 0.5)),
            "--nms", str(params.get("nms", 0.3)),
            "--tile-size", str(params.get("tile_size", 0)),
        ]

    page._seg_ext_jobs = jobs
    page._seg_ext_done = set()
    page._seg_ext_errors = []
    page._seg_ext_cells = 0
    page._seg_ext_report = []
    page._seg_ext_tile = None
    page._seg_ext_activity = ""
    page._seg_ext_proc_finished = False
    page._seg_ext_start_time = time.monotonic()
    _seg_log(ctx,
        page,
        f"===== {_seg_method_label(page._seg_ext_method)} run — "
        f"{len(jobs)} file(s) =====",
    )
    worker = ctx._ext_worker()
    worker.clear()  # drop any stale results from earlier operations
    worker.run(("seg-batch",), argv)
    label = _seg_method_label(page._seg_ext_method)
    _seg_log(ctx, page, f"Running {label} on {len(jobs)} file(s)…")
    status = getattr(page, "_seg_calc_status", None)
    if status is not None:
        status.setText(f"Running {label} on {len(jobs)} file(s)…")


def _seg_store_log(ctx, page) -> None:
    """Mirror the in-memory log into the task params so it is saved.

    The snapshot is updated too: the log is a product of the module rather
    than an unsaved parameter edit, so it must survive "close without saving".
    """
    lines = list(getattr(page, "_seg_log_lines", []))
    params = getattr(page, "_seg_params", None)
    if params is not None:
        params["log"] = lines
    snapshot = getattr(page, "_seg_snapshot", None)
    if isinstance(snapshot, dict):
        snapshot["log"] = lines


def _seg_log(ctx, page, text) -> None:
    """Append a timestamped line to the calculation log.

    The log is a property of the module: lines are kept in ``params["log"]``
    so they survive closing/reopening the page, and a new calculation
    appends to the history instead of clearing it.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_seg_log_lines", None)
    if lines is None:
        lines = []
        page._seg_log_lines = lines
    for line in text.splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > SEG_LOG_LIMIT:
        del lines[:-SEG_LOG_LIMIT]
    _seg_store_log(ctx, page)
    box = getattr(page, "_seg_calc_log", None)
    if box is not None:
        for line in text.splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")
        bar = box.verticalScrollBar()
        bar.setValue(bar.maximum())
    page._seg_log_last_is_tile = False


def _seg_log_tile(ctx, page, i, total) -> None:
    """Update the in-progress tile line in the log (replace in place)."""
    ts = datetime.now().strftime("%H:%M:%S")
    text = f"[{ts}] tile {i}/{total}"
    lines = getattr(page, "_seg_log_lines", None)
    if lines is None:
        lines = []
        page._seg_log_lines = lines
    if getattr(page, "_seg_log_last_is_tile", False) and lines:
        lines[-1] = text
    else:
        lines.append(text)
        if len(lines) > SEG_LOG_LIMIT:
            del lines[:-SEG_LOG_LIMIT]
    _seg_store_log(ctx, page)
    box = getattr(page, "_seg_calc_log", None)
    if box is not None:
        if getattr(page, "_seg_log_last_is_tile", False):
            cursor = box.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(text)
        else:
            box.appendPlainText(text)
        bar = box.verticalScrollBar()
        bar.setValue(bar.maximum())
    page._seg_log_last_is_tile = True


def _seg_ext_poll(ctx, page) -> None:
    """Collect external segmentation events and finalize the masks."""
    import shutil

    status = getattr(page, "_seg_calc_status", None)
    writer = getattr(page, "_seg_ext_writer", None)
    jobs = getattr(page, "_seg_ext_jobs", None) or []
    done = getattr(page, "_seg_ext_done", None) or set()
    errors = getattr(page, "_seg_ext_errors", [])
    cells = getattr(page, "_seg_ext_cells", 0)
    report = getattr(page, "_seg_ext_report", None) or []
    tile = getattr(page, "_seg_ext_tile", None)
    activity = getattr(page, "_seg_ext_activity", "") or ""
    prepared = getattr(page, "_seg_ext_prepared", 0)
    written = getattr(page, "_seg_ext_written", 0)
    pending = getattr(page, "_seg_ext_pending_writes", 0)
    cancelled = getattr(page, "_seg_ext_cancelled", False)
    out_to_job = {job["output"]: job for job in jobs}
    slots = getattr(page, "_seg_ext_slots", None)
    if (cancelled or getattr(page, "_seg_ext_aborted", False)) and not getattr(
        page, "_seg_ext_proc_finished", False
    ):
        # Cancellation or a feeder failure can race the worker thread starting
        # the process; keep killing it until the process-done event arrives.
        try:
            ctx._ext_worker().kill()
        except Exception:  # noqa: BLE001
            pass

    # Input feeder events.
    q = getattr(page, "_seg_ext_queue", None)
    if q is not None:
        while True:
            try:
                msg = q.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "log":
                _seg_log(ctx, page, msg[1])
            elif kind == "status":
                if status is not None:
                    status.setText(msg[1])
            elif kind == "prepared":
                prepared += 1
            elif kind == "input_failed":
                _name, err = msg[2], msg[3]
                _seg_log(ctx, page, f"✗ {_name}: {err}")
            elif kind == "feeder_error":
                errors.append(str(msg[1]))
                _seg_log(ctx, page, f"✗ Input preparation failed: {msg[1]}")
                page._seg_ext_feeder_done = True
                page._seg_ext_aborted = True
                # The runner would otherwise wait for an input that will
                # never appear until its timeout.
                try:
                    ctx._ext_worker().kill()
                except Exception:  # noqa: BLE001
                    pass
            elif kind == "feeder_done":
                page._seg_ext_feeder_done = True

    # Mask writer events (parquet + pyramid completed off the UI thread).
    if writer is not None:
        for result in writer.poll():
            kind = result[0]
            parquet_path = result[1]
            meta = result[-1] if isinstance(result[-1], dict) else {}
            name = meta.get("name") or _data_naming._comp_file_name(
                ctx, parquet_path, page._seg_task_id
            )
            if kind == "written":
                rel, n_cells = result[2], result[3]
                rec = _seg_mask_record_live(
                    ctx, page, parquet_path, create=True
                )
                rec["path"] = rel
                rec["source"] = "builtin"
                rec["state"] = SEG_IMPORT_DONE
                written += 1
                cells += int(n_cells or 0)
                seconds = float(meta.get("seconds") or 0.0)
                report.append(f"{name}: {n_cells} cells, {seconds:.1f}s")
                _seg_log(
                    ctx, page, f"✓ {name}: {n_cells} cells, "
                    f"{seconds:.1f}s"
                )
                ctx._seg_mask_cache = {}
                ctx._seg_value_cache = {}
            else:
                errors.append(f"{name}: {result[2]}")
                _seg_log(ctx, page, f"✗ {name}: {result[2]}")
            pending = max(0, pending - 1)

    # Subprocess streaming events.
    worker = ctx._ext_worker()
    for item in worker.poll():
        if len(item) >= 3 and item[1] == "line":
            line = item[2]
            if line.startswith("tile: "):
                info = line[len("tile: "):].strip()
                if "/" in info:
                    try:
                        cur, tot = info.split("/", 1)
                        tile = (int(cur), int(tot))
                        _seg_log_tile(ctx, page, int(cur), int(tot))
                    except ValueError:
                        pass
            elif line.startswith("done:"):
                body = line[len("done:"):].strip()
                out_path, _sep, meta_text = body.partition("|")
                out_path = out_path.strip()
                n_cells = 0
                seconds = 0.0
                for part in meta_text.split("|"):
                    if part.startswith("cells="):
                        try:
                            n_cells = int(part[len("cells="):])
                        except ValueError:
                            pass
                    elif part.startswith("time="):
                        try:
                            seconds = float(part[len("time="):])
                        except ValueError:
                            pass
                job = out_to_job.get(out_path)
                if job is None or out_path in done:
                    continue
                done.add(out_path)
                pending += 1
                try:
                    writer.submit(
                        page._seg_task_id,
                        job["parquet"],
                        npy_path=out_path,
                        n_cells=n_cells,
                        meta={
                            "index": job.get("index"),
                            "name": job.get("name"),
                            "seconds": seconds,
                            "output": out_path,
                        },
                        release=slots.release if slots is not None else None,
                    )
                except Exception as exc:  # noqa: BLE001
                    pending = max(0, pending - 1)
                    errors.append(f"{job.get('name')}: {exc}")
                    _seg_log(
                        ctx, page, f"✗ {job.get('name')}: {exc}"
                    )
                    if slots is not None:
                        slots.release()
            elif line.startswith("failed:"):
                body = line[len("failed:"):].strip()
                out_path, _sep, meta_text = body.partition("|")
                out_path = out_path.strip()
                job = out_to_job.get(out_path)
                if job is None:
                    continue
                reason = "input could not be prepared"
                if meta_text.startswith("reason="):
                    reason = meta_text[len("reason="):].strip() or reason
                if out_path not in done:
                    done.add(out_path)
                    name = job.get("name") or _data_naming._comp_file_name(
                        ctx, job["parquet"], page._seg_task_id
                    )
                    errors.append(f"{name}: {reason}")
                    _seg_log(ctx, page, f"✗ {name}: {reason}")
                if slots is not None:
                    slots.release()
            else:
                # Non-progress output (e.g. TensorFlow/model loading lines):
                # remember the latest for the status, and record it in the
                # scrollable log so the user can see and copy the details.
                if line.strip():
                    activity = line.strip()[-120:]
                    _seg_log(ctx, page, line)
            continue
        if len(item) != 4 or item[1] != "done":
            continue
        key, _tag, code, text = item[0], item[1], item[2], item[3]
        if not (isinstance(key, tuple) and key[0] == "seg-batch"):
            continue
        page._seg_ext_proc_finished = True
        for job in jobs:
            if job["output"] in done:
                continue
            done.add(job["output"])
            if not cancelled:
                name = job.get("name") or _data_naming._comp_file_name(
                    ctx, job["parquet"], page._seg_task_id
                )
                if code != 0:
                    errors.append(
                        f"{name}: {text.strip()[-200:]}"
                    )
                else:
                    errors.append(f"{name}: no output produced")
        if text and code != 0 and not cancelled:
            _seg_log(ctx, page, text.rstrip())

    page._seg_ext_done = done
    page._seg_ext_errors = errors
    page._seg_ext_cells = cells
    page._seg_ext_report = report
    page._seg_ext_tile = tile
    page._seg_ext_activity = activity
    page._seg_ext_prepared = prepared
    page._seg_ext_written = written
    page._seg_ext_pending_writes = pending

    proc_finished = getattr(page, "_seg_ext_proc_finished", False)
    feeder_done = getattr(page, "_seg_ext_feeder_done", False)
    if not (proc_finished and feeder_done and pending == 0):
        if status is not None:
            start = getattr(page, "_seg_ext_start_time", None)
            elapsed = int(time.monotonic() - start) if start else 0
            minutes, seconds = divmod(elapsed, 60)
            elapsed_str = (
                f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
            )
            stamp = f"[{datetime.now().strftime('%H:%M:%S')} · {elapsed_str}]"
            msg = (
                f"{stamp} Prepared {prepared}/{len(jobs)} · "
                f"Segmented {len(done)}/{len(jobs)} · "
                f"Written {written}/{len(jobs)}"
            )
            if pending:
                msg += f" · Writing {pending}"
            if tile is not None:
                msg += f" — tile {tile[0]}/{tile[1]}"
            elif activity:
                msg += f" — {activity}"
            status.setText(msg)
        return

    timer = getattr(page, "_seg_ext_timer", None)
    if timer is not None:
        timer.stop()
    if writer is not None:
        writer.close()
    page._seg_ext_writer = None
    page._seg_ext_queue = None
    page._seg_ext_running = False
    page._seg_calc_running = False
    page._seg_calc_finished = True
    tmpdir = getattr(page, "_seg_ext_tmpdir", None)
    if tmpdir is not None:
        shutil.rmtree(tmpdir, ignore_errors=True)
    start = getattr(page, "_seg_ext_start_time", None)
    elapsed = int(time.monotonic() - start) if start else 0
    minutes, seconds = divmod(elapsed, 60)
    elapsed_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    aborted = getattr(page, "_seg_ext_aborted", False)
    if cancelled:
        summary = f"Cancelled after {written} mask(s) in {elapsed_str}."
    elif aborted:
        summary = (
            f"Stopped after {written} mask(s): input preparation failed."
        )
    else:
        summary = (
            f"Calculated {written} mask(s) · {cells} cell(s) in "
            f"{elapsed_str}."
        )
    _seg_log(ctx, page, summary)
    for err in errors:
        _seg_log(ctx, page, f"✗ {err}")
    _seg_after_masks_changed(ctx, page)
    _seg_refresh_files(ctx, page)
    status = getattr(page, "_seg_calc_status", None)
    if status is not None:
        msg = summary
        if report:
            msg += "\n" + "\n".join(report)
        if errors:
            msg += "\n" + "\n".join(errors)
        status.setText(msg)


def _seg_raster_channel(ctx, parquet_path, channel, task_id=None):
    """Reconstruct one channel as a 2-D float image (or (None, error)).

    Values go through the map pipeline (Unmix / Scale / Compensation …), so
    channels that only exist as module outputs (for example an unmixed
    ``DAPI``) can be segmented.  ``task_id`` selects the pipeline position.
    """
    import numpy as np

    ent = _data_image_index._ensure_image_index(ctx, parquet_path)
    if ent is None or ent.get("manifest") is None:
        return None, "no image index — re-import this file as an image"
    man = ent["manifest"]
    values = None
    if task_id:
        # A precomputed Unmix pyramid already holds the unmixed channel;
        # reading it avoids re-unmixing every source column again.  Only the
        # stages below the Unmix module are applied on top.
        unmix_cols, _stale = _data_outputs._unmix_image_outputs(
            ctx, task_id, parquet_path
        )
        rec = unmix_cols.get(channel)
        if rec is not None:
            import pandas as pd
            from smage.core.pipeline import _evaluate_stages, _stages_after

            df = pd.read_parquet(
                rec["parquet"], columns=["x", "y", channel]
            )
            x_values = df["x"].to_numpy(dtype="float64")
            y_values = df["y"].to_numpy(dtype="float64")
            data = pd.to_numeric(
                df[channel], errors="coerce"
            ).to_numpy(dtype="float64")
            post = _stages_after(
                ctx._map_stages(task_id), "unmix", rec.get("task_id")
            )
            if post:
                evaluated = _evaluate_stages(
                    post,
                    {"x": x_values, "y": y_values, channel: data},
                    [channel],
                    parquet_path=parquet_path,
                )
                data = evaluated.get(channel, data)
            values = {"x": x_values, "y": y_values, channel: data}
    if values is None:
        values = _data_columns._column_data(
            ctx, task_id, parquet_path, ["x", "y", channel]
        )
    if channel not in values:
        return None, f"'{channel}' is not available in this file"
    if "x" not in values or "y" not in values:
        return None, "x/y columns are not available in this file"
    xmin = float(man["xmin"])
    ymin = float(man["ymin"])
    width = int(round(float(man["xmax"]) - xmin)) + 1
    height = int(round(float(man["ymax"]) - ymin)) + 1
    xi = np.rint(
        np.asarray(values["x"], dtype="float64") - xmin
    ).astype(np.int64)
    yi = np.rint(
        np.asarray(values["y"], dtype="float64") - ymin
    ).astype(np.int64)
    data = np.asarray(values[channel], dtype="float64")
    image = np.zeros((height, width), dtype="float64")
    ok = (
        (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        & np.isfinite(data)
    )
    image[yi[ok], xi[ok]] = data[ok]
    if not ok.any():
        return None, "no finite pixel values"
    return image, None


def _seg_split_touching_nuclei(labels, min_area: int, separation: int = 0):
    """Split touching nuclei at distance-map maxima.

    Only connected components are ever split, so separate nuclei stay
    separate regardless of the strength setting. ``separation`` is the
    minimum distance in pixels between two nucleus centres for them to count
    as separate objects: larger values split less, smaller values split more.
    ``0`` uses ``sqrt(min_area / pi)`` — the radius of a nucleus with the
    minimum area. Each component with at least two seeds is partitioned by
    nearest seed (a constrained Voronoi split); fragments below ``min_area``
    are dropped afterwards.
    """
    import math

    import numpy as np
    from scipy import ndimage

    labels = np.asarray(labels)
    binary = labels > 0
    if not binary.any():
        return labels
    distance = ndimage.distance_transform_edt(binary)
    # Candidate maxima (3×3 neighbourhood); plateaus merge when labeled. A
    # light smoothing removes the distance-map ridge that runs along the
    # touching neck, which would otherwise register as a third "peak".
    smooth = ndimage.gaussian_filter(distance, 0.8)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=3)) & binary
    sep = (
        float(separation) if separation > 0
        else math.sqrt(max(int(min_area), 1) / math.pi)
    )
    sep = max(1.0, sep)
    sep2 = sep * sep

    out = labels.astype(np.int32, copy=True)
    next_label = int(out.max()) + 1
    slices = ndimage.find_objects(out)
    for index, sl in enumerate(slices, start=1):
        if sl is None:
            continue
        component = out[sl] == index
        if not component.any():
            continue
        peak_slice = peaks[sl] & component
        if not peak_slice.any():
            continue
        peak_labels, peak_count = ndimage.label(peak_slice)
        if peak_count <= 1:
            continue
        centers = ndimage.center_of_mass(
            peak_slice, peak_labels, range(1, peak_count + 1)
        )
        heights = [
            distance[sl][int(round(y)), int(round(x))] for y, x in centers
        ]
        order = np.argsort(np.asarray(heights))[::-1]
        # Greedy non-maximum suppression: keep the highest peaks that are at
        # least `sep` apart. This is the strength control — a large distance
        # merges nearby maxima and keeps the clump as one object.
        kept = []
        for index_peak in order:
            y, x = centers[index_peak]
            if any(
                (y - ky) ** 2 + (x - kx) ** 2 < sep2 for ky, kx in kept
            ):
                continue
            kept.append((y, x))
        if len(kept) <= 1:
            continue
        seeds = np.zeros(component.shape, dtype=np.int32)
        for label, (y, x) in enumerate(kept, start=1):
            seeds[int(round(y)), int(round(x))] = label
        # For every pixel of this component, the nearest seed.
        _distance, (iy, ix) = ndimage.distance_transform_edt(
            seeds == 0, return_indices=True
        )
        assigned = np.where(component, seeds[iy, ix], 0).astype(np.int32)
        block = out[sl]
        block[component] = next_label + assigned[component]
        out[sl] = block
        next_label += len(kept)
    return _relabel_sequential(out, int(min_area))


def _seg_expand_labels(labels, radius: int, tile_size: int = SEG_EXPAND_TILE):
    """Grow labels by ``radius`` pixels using tiles + a halo.

    The whole-image distance transform needs one float64 distance map plus two
    int32 index maps. Expansion is local (a pixel can only change if a label is
    within ``radius``), so each output tile is computed from a padded tile. The
    halo contains every label that can affect the tile, making the result
    identical to the full-image transform while keeping peak memory bounded.
    """
    import numpy as np
    from scipy import ndimage

    radius = max(0, int(radius))
    if radius <= 0 or labels.size == 0:
        return labels
    height, width = labels.shape
    out = labels.copy()
    tile = max(128, int(tile_size))
    for y0 in range(0, height, tile):
        y1 = min(height, y0 + tile)
        py0 = max(0, y0 - radius)
        py1 = min(height, y1 + radius)
        for x0 in range(0, width, tile):
            x1 = min(width, x0 + tile)
            px0 = max(0, x0 - radius)
            px1 = min(width, x1 + radius)
            block = labels[py0:py1, px0:px1]
            background = block == 0
            if not background.any():
                continue
            distance, (iy, ix) = ndimage.distance_transform_edt(
                background, return_indices=True
            )
            cy0, cy1 = y0 - py0, y1 - py0
            cx0, cx1 = x0 - px0, x1 - px0
            grown = distance[cy0:cy1, cx0:cx1] <= radius
            if not grown.any():
                continue
            nearest = block[
                iy[cy0:cy1, cx0:cx1], ix[cy0:cy1, cx0:cx1]
            ]
            patch = out[y0:y1, x0:x1]
            patch[grown] = nearest[grown]
    return out


def _seg_nuclear_expansion(ctx, parquet_path, params, task_id=None):
    """Threshold → label → filter → (split) → expand. Returns (mask, error)."""
    import numpy as np
    from scipy import ndimage

    channel = params.get("channel")
    if not channel:
        return None, "no nucleus channel"
    image, err = _seg_raster_channel(
        ctx, parquet_path, channel, task_id=task_id
    )
    if image is None:
        return None, err

    if params.get("threshold_mode") == "manual":
        threshold = float(params.get("threshold", 0.0))
    else:
        threshold = _otsu_threshold_chunked(image)
    binary = image > threshold
    if not binary.any():
        return None, "threshold produced an empty mask"
    binary = ndimage.binary_opening(binary, structure=np.ones((3, 3)))
    labels, _count = ndimage.label(binary)
    labels = _relabel_sequential(labels, int(params.get("min_area", 1)))
    if params.get("split"):
        labels = _seg_split_touching_nuclei(
            labels,
            int(params.get("min_area", 1)),
            int(params.get("split_separation", 0) or 0),
        )
    expand = int(params.get("expand", 0))
    if expand > 0 and labels.any():
        labels = _seg_expand_labels(labels, expand)
    return labels.astype(np.int32), None


def _seg_mark_dirty(ctx, page) -> None:
    page._seg_dirty = True
    _seg_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _seg_update_save_btn(ctx, page) -> None:
    page._seg_save_btn.setEnabled(page._seg_dirty and not page._seg_locked)


def _seg_save_now(ctx, page) -> None:
    params = page._seg_params
    column = (params.get("column") or "Mask").strip() or "Mask"
    expose = params.get("expose") or {}
    # The mask column must not collide with a real column name of a file
    # that is exposed (checked) in this module — unchecked files don't
    # publish a mask column, so their names don't matter here.
    taken = set()
    for path in _seg_file_rows(ctx, page):
        if not expose.get(path):
            continue
        for name in ctx._column_index.get(_data_files._rel_data_key(ctx, path), []):
            taken.add(name)
    if column in taken:
        QMessageBox.warning(
            ctx.host,
            "Mask column",
            f'"{column}" is already a column of the selected files.\n'
            "Choose another name.",
        )
        return
    params["column"] = column
    applied = {}
    for path in _seg_file_rows(ctx, page):
        if not expose.get(path):
            continue
        rec = _seg_mask_record(ctx, params, path)
        mask_path = _seg_mask_file(ctx, rec)
        if mask_path is None:
            continue
        applied[path] = _seg_signature(ctx, mask_path)
    params["applied"] = applied
    page._seg_applied_snapshot = dict(applied)
    page._seg_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._seg_snapshot = copy.deepcopy(params)
    page._seg_dirty = False
    _seg_update_save_btn(ctx, page)
    _seg_refresh_status(ctx, page)
    ctx._seg_value_cache = {}
    ctx.statusBar().showMessage("Segmentation saved")
    ctx._module_saved(page)


def _seg_close(ctx, page) -> None:
    if (
        getattr(page, "_seg_ext_running", False)
        or getattr(page, "_seg_calc_running", False)
        or getattr(page, "_seg_import_running", False)
    ):
        reply = QMessageBox.question(
            ctx.host,
            "Close Segmentation",
            "A calculation or import is still running.\n"
            "Closing the module now will interrupt it. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._seg_calc_cancel = True
        page._seg_import_cancel = True
        if getattr(page, "_seg_ext_running", False):
            page._seg_ext_cancelled = True
            ctx._ext_worker().kill()
    if page._seg_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Segmentation",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._seg_task_def["params"] = page._seg_snapshot
    for name in (
        "_seg_overlay_timer", "_seg_ext_timer", "_seg_nuclear_timer",
        "_seg_import_timer",
    ):
        timer = getattr(page, name, None)
        if timer is not None:
            timer.stop()
    # The module is closing: its batch folder is disposable.
    tmpdir = getattr(page, "_seg_ext_tmpdir", None)
    if tmpdir is not None:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
    _seg_clear_edge_cache(ctx)
    ctx._close_module_page(page)


def _seg_preview_panel(ctx, page) -> QWidget:
    import pyqtgraph as pg

    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Preview</b>"))
    body = QWidget()
    bh = QHBoxLayout(body)
    bh.setContentsMargins(0, 0, 0, 0)
    bh.setSpacing(4)
    plot = pg.PlotWidget(background=_plot_background())
    plot.showGrid(x=True, y=True, alpha=0.2)
    plot.getPlotItem().setAxisItems({
        "bottom": _make_sci_axis("bottom"),
        "left": _make_sci_axis("left"),
    })
    bh.addWidget(plot, 1)
    legend = _widgets_plot_renderer._figure_attach_legend(ctx, plot)
    bh.addWidget(legend, 0)
    lay.addWidget(body, 1)
    page._seg_preview_plot = plot
    # Re-render both the image (LOD raster for the new view) and the mask
    # overlay when the user zooms or pans.
    timer = QTimer(ctx.host)
    timer.setSingleShot(True)
    timer.setInterval(30)
    timer.timeout.connect(lambda: _seg_render_preview(ctx, page))
    page._seg_overlay_timer = timer
    plot.getPlotItem().getViewBox().sigRangeChanged.connect(
        lambda *_: timer.start()
    )
    return panel


def _seg_plot_settings_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Plot settings</b>"))
    host = QWidget()
    host.setLayout(QVBoxLayout())
    host.layout().setContentsMargins(0, 0, 0, 0)
    host.layout().setSpacing(6)
    lay.addWidget(host, 1)
    page._seg_settings_host = host
    return panel


def _seg_plot_dict(ctx, page) -> dict:
    """The preview's plot config, defaulted for the current files."""
    plot_dict = page._seg_params.setdefault("plot", {})
    rows = _seg_file_rows(ctx, page)
    if not rows:
        return plot_dict
    if plot_dict.get("file") not in rows:
        fresh = _seg_default_plot(ctx, rows[0], page._seg_task_id)
        fresh.update({k: v for k, v in plot_dict.items() if k != "file"})
        plot_dict.clear()
        plot_dict.update(fresh)
    if not plot_dict.get("type"):
        plot_dict["type"] = "image"
    if not plot_dict.get("layers") and plot_dict.get("color_param"):
        channel = plot_dict["color_param"]
        plot_dict["layers"] = [{
            "parameter": channel,
            "color": (255, 255, 255),
            "name": str(channel),
            "visible": True,
        }]
    plot_dict.setdefault("show_legend", True)
    return plot_dict


def _seg_refresh_plot_settings(ctx, page) -> None:
    """Rebuild part 4 (file/channel/mask options) from the current files."""
    host = getattr(page, "_seg_settings_host", None)
    if host is None:
        return
    _widgets_panels._clear_settings_layout(ctx, host.layout())
    lay = host.layout()
    enabled = not page._seg_locked
    plot_dict = _seg_plot_dict(ctx, page)
    rows = [p for p in _seg_file_rows(ctx, page)
            if _seg_mask_file(ctx, _seg_mask_record(ctx, page._seg_params, p))]

    lay.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    for path in rows:
        file_combo.addItem(_data_naming._comp_file_name(ctx, path, page._seg_task_id), path)
    idx = file_combo.findData(plot_dict.get("file"))
    if idx >= 0:
        file_combo.setCurrentIndex(idx)
    file_combo.setEnabled(enabled and bool(rows))
    lay.addWidget(file_combo)

    columns = []
    rec = _widgets_plot_renderer._figure_record(ctx, plot_dict.get("file"))
    if rec is not None:
        columns = _data_columns._columns_for(ctx, rec.get("parquet"), page._seg_task_id)

    _widgets_plot_renderer._figure_image_layer_settings(ctx,
        lay, plot_dict, columns, enabled,
        on_change=lambda: _seg_render_preview(ctx, page),
        on_rebuild=lambda: (
            _seg_refresh_plot_settings(ctx, page),
            _seg_render_preview(ctx, page),
        ),
        task_id=page._seg_task_id,
    )

    lay.addWidget(QLabel("Mask display"))
    mode_combo = _NoWheelComboBox()
    mode_combo.addItem("Don't show mask", "none")
    mode_combo.addItem("Show mask as shape", "shape")
    mode_combo.addItem("Show mask edges", "edges")
    idx = mode_combo.findData(plot_dict.get("mask_mode", "shape"))
    mode_combo.setCurrentIndex(max(0, idx))
    mode_combo.setEnabled(enabled)
    lay.addWidget(mode_combo)

    color_mode_combo = None
    alpha_spin = None
    if plot_dict.get("mask_mode", "shape") != "none":
        lay.addWidget(QLabel("Mask colors"))
        color_mode_combo = _NoWheelComboBox()
        color_mode_combo.addItem("Random colors", "random")
        color_mode_combo.addItem("Grayscale", "gray")
        idx = color_mode_combo.findData(plot_dict.get("mask_color", "random"))
        color_mode_combo.setCurrentIndex(max(0, idx))
        color_mode_combo.setEnabled(enabled)
        lay.addWidget(color_mode_combo)

        lay.addWidget(QLabel("Opacity (%)"))
        alpha_spin = _NoWheelSpinBox()
        alpha_spin.setRange(5, 100)
        alpha_spin.setValue(int(plot_dict.get("mask_alpha", 45)))
        alpha_spin.setEnabled(enabled)
        lay.addWidget(alpha_spin)

    _widgets_plot_renderer._figure_display_toggles(ctx,
        lay, plot_dict, enabled,
        on_change=lambda: (
            _widgets_plot_renderer._figure_apply_display_options(ctx,
                plot_dict, getattr(page, "_seg_preview_plot", None)
            )
            if getattr(page, "_seg_preview_plot", None) is not None
            else _seg_render_preview(ctx, page)
        ),
    )
    lay.addStretch(1)

    def on_file(_=0):
        path = file_combo.currentData()
        if not path:
            return
        plot_dict["file"] = path
        plot_dict["file_name"] = file_combo.currentText()
        _seg_refresh_plot_settings(ctx, page)
        _seg_render_preview(ctx, page)
        _seg_mark_dirty(ctx, page)

    def on_mode(_=0):
        plot_dict["mask_mode"] = mode_combo.currentData()
        _seg_refresh_plot_settings(ctx, page)
        _seg_render_preview(ctx, page)
        _seg_mark_dirty(ctx, page)

    def on_color_mode(_=0):
        plot_dict["mask_color"] = color_mode_combo.currentData()
        _seg_render_overlay(ctx, page)
        _seg_mark_dirty(ctx, page)

    def on_alpha(value):
        plot_dict["mask_alpha"] = int(value)
        _seg_render_overlay(ctx, page)
        _seg_mark_dirty(ctx, page)

    file_combo.currentIndexChanged.connect(on_file)
    mode_combo.currentIndexChanged.connect(on_mode)
    if color_mode_combo is not None:
        color_mode_combo.currentIndexChanged.connect(on_color_mode)
    if alpha_spin is not None:
        alpha_spin.valueChanged.connect(on_alpha)


def _seg_update_preview_visibility(ctx, page) -> None:
    """Parts 3 and 4 appear once at least one file has a mask."""
    has_mask = _seg_any_mask(ctx, page)
    panel3 = getattr(page, "_seg_preview_panel", None)
    panel4 = getattr(page, "_seg_settings_panel", None)
    for panel in (panel3, panel4):
        if panel is not None:
            panel.setVisible(has_mask)
    if has_mask:
        _seg_render_preview(ctx, page)


def _seg_render_preview(ctx, page) -> None:
    plot = getattr(page, "_seg_preview_plot", None)
    if plot is None:
        return
    plot_dict = _seg_plot_dict(ctx, page)
    rec = _widgets_plot_renderer._figure_record(ctx, plot_dict.get("file"))
    if rec is None:
        return
    _widgets_plot_renderer._figure_render(ctx, plot_dict, plot, task_id=page._seg_task_id)
    _seg_render_overlay(ctx, page)


def _seg_render_overlay(ctx, page) -> None:
    """Draw the mask overlay on top of the preview image."""
    import numpy as np
    import pyqtgraph as pg

    plot = getattr(page, "_seg_preview_plot", None)
    if plot is None:
        return
    plot_dict = _seg_plot_dict(ctx, page)
    old = getattr(page, "_seg_overlay_item", None)
    if old is not None:
        try:
            plot.removeItem(old)
        except Exception:  # noqa: BLE001
            pass
        page._seg_overlay_item = None
    mode = plot_dict.get("mask_mode", "shape")
    if mode == "none":
        return
    parquet_path = plot_dict.get("file")
    rec = _seg_mask_record(ctx, page._seg_params, parquet_path)
    mask_path = _seg_mask_file(ctx, rec)
    if mask_path is None:
        return
    ent = _data_image_index._ensure_image_index(ctx, parquet_path)
    if ent is None or ent.get("manifest") is None:
        return
    man = ent["manifest"]
    vb = plot.getPlotItem().getViewBox()
    xr = vb.viewRange()[0]
    yr = vb.viewRange()[1]
    x0, x1 = min(xr), max(xr)
    y0, y1 = min(yr), max(yr)
    if x1 <= x0 or y1 <= y0:
        return
    xmin, ymin = float(man["xmin"]), float(man["ymin"])
    i0 = max(0, int(np.floor(x0 - xmin)))
    i1 = min(int(round(float(man["xmax"]) - xmin)) + 1, int(np.ceil(x1 - xmin)))
    j0 = max(0, int(np.floor(y0 - ymin)))
    j1 = min(int(round(float(man["ymax"]) - ymin)) + 1, int(np.ceil(y1 - ymin)))
    if i1 <= i0 or j1 <= j0:
        return
    mask = _seg_load_mask(ctx, mask_path)
    if mask is None:
        return
    i1 = min(i1, mask.shape[1])
    j1 = min(j1, mask.shape[0])
    step = max(1, int(round((x1 - x0) / max(1, plot.width()))))
    crop = mask[j0:j1:step, i0:i1:step].astype(np.int64)
    if crop.size == 0:
        return
    max_label = int(crop.max())
    if max_label <= 0:
        return
    # Per-label colors: grayscale or a stable random palette.
    if plot_dict.get("mask_color") == "random":
        rng = np.random.default_rng(20240521)
        lut = rng.integers(60, 256, size=(max_label + 1, 3))
        lut[0] = 0
    else:
        ramp = np.linspace(0.35, 1.0, max(1, max_label + 1))
        lut = np.stack(
            [ramp * 255.0, ramp * 255.0, ramp * 255.0], axis=1
        ).astype(np.int64)
    if mode == "edges":
        edges_full = _seg_edge_map(ctx, mask_path)
        if edges_full is None:
            return
        edge = edges_full[j0:j1:step, i0:i1:step]
        neighbor = np.zeros_like(crop)
        neighbor[:-1, :] = np.maximum(neighbor[:-1, :], crop[1:, :])
        neighbor[1:, :] = np.maximum(neighbor[1:, :], crop[:-1, :])
        neighbor[:, :-1] = np.maximum(neighbor[:, :-1], crop[:, 1:])
        neighbor[:, 1:] = np.maximum(neighbor[:, 1:], crop[:, :-1])
        label_for_color = np.where(crop > 0, crop, neighbor)
        alpha = np.where(edge, 255, 0)
    else:
        label_for_color = crop
        alpha = np.where(crop > 0, 255, 0)
    opacity = max(0, min(100, int(plot_dict.get("mask_alpha", 45))))
    rgba = np.zeros((crop.shape[0], crop.shape[1], 4), dtype=np.uint8)
    clipped = np.clip(label_for_color, 0, max_label)
    rgba[..., 0:3] = lut[clipped]
    rgba[..., 3] = (alpha * opacity / 100.0).astype(np.uint8)
    item = pg.ImageItem(axisOrder="row-major")
    item.setImage(rgba, autoLevels=False)
    item.setRect(QRectF(
        xmin + i0 * step, ymin + j0 * step,
        crop.shape[1] * step, crop.shape[0] * step,
    ))
    item.setZValue(10)
    plot.addItem(item)
    page._seg_overlay_item = item


SEG_METHODS = [
    ("Nuclear Expansion", "nuclear_expansion"),
    ("StarDist", "stardist-cpu"),
    ("Cellpose GPU", "cellpose-gpu"),
    ("Cellpose CPU", "cellpose-cpu"),
    ("Mask Input", "mask_input"),
]


# Saved history is capped so the project file does not grow without bound.
SEG_LOG_LIMIT = 10000


SEG_STATUS_NO_MASK = "no mask"


SEG_STATUS_PENDING = "pending"


SEG_STATUS_APPLIED = "applied"


SEG_IMPORT_SELECT = "Select a file"


SEG_IMPORT_WAITING = "Waiting for import"


SEG_IMPORT_DONE = "Imported"


SEG_IMPORT_FORMAT = "Format error — choose another file"


SEG_IMPORT_SIZE = "Size mismatch — choose another file"


SEG_IMPORT_UNKNOWN = "Unknown error — try again"


SEG_DEFAULT_PARAMS = {
    "channel": None,
    "threshold_mode": "otsu",
    "threshold": 0.0,
    "min_area": 30,
    "expand": 5,
    "split": False,
    "split_separation": 0,
}

