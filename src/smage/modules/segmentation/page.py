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
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
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
import time


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
    sign = ctx._seg_signature(mask_path)
    hit = cache.get(key)
    if hit is not None and hit[0] == sign:
        return hit[1]
    man = ctx._image_load_manifest(mask_path)
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
    """Shared temp folder for this project's segmentation caches."""
    import hashlib
    import tempfile

    key = "no-project"
    if ctx.project is not None:
        key = hashlib.md5(
            str(ctx.project.path.resolve()).encode("utf-8")
        ).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "smage" / "segmentation" / key


def _seg_clear_edge_cache(ctx) -> None:
    """Drop this project's cached mask-edge images (temp data only)."""
    import shutil

    cache = getattr(ctx, "_seg_edge_cache", None)
    if cache is not None:
        cache.clear()
    try:
        shutil.rmtree(ctx._seg_temp_dir(), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _seg_edge_map(ctx, mask_path):
    """Return (and disk-cache) the boolean edge map of a mask."""
    import numpy as np

    mask = ctx._seg_load_mask(mask_path)
    if mask is None:
        return None
    sign = ctx._seg_signature(mask_path)
    cache = getattr(ctx, "_seg_edge_cache", None)
    if cache is None:
        cache = {}
        ctx._seg_edge_cache = cache
    key = str(mask_path)
    hit = cache.get(key)
    if hit is not None and hit[0] == sign:
        return hit[1]
    path = ctx._seg_temp_dir() / f"{Path(str(mask_path)).stem}.{sign}.npy"
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
    tl.addWidget(QLabel(f"<b>{ctx._module_display_title(task_def.get('id'), name)}</b>"))
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
    close_btn.clicked.connect(lambda _=False: ctx._seg_close(page))
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
    page._seg_log_lines = []
    save_btn.clicked.connect(lambda _=False: ctx._seg_save_now(page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QHBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(12)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(ctx._seg_files_panel(page))
    splitter.addWidget(ctx._seg_method_panel(page))
    page._seg_preview_panel = ctx._seg_preview_panel(page)
    page._seg_settings_panel = ctx._seg_plot_settings_panel(page)
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

    ctx._seg_refresh_files(page)
    ctx._seg_update_preview_visibility(page)
    ctx._seg_update_save_btn(page)
    return page


def _seg_default_plot(ctx, parquet_path, task_id=None) -> dict:
    """A fresh image plot config for the preview panel."""
    columns = []
    rec = ctx._figure_record(parquet_path)
    if rec is not None:
        columns = ctx._columns_for(rec.get("parquet"), task_id)
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
    rec = ctx._seg_mask_record(params, parquet_path)
    path = ctx._seg_mask_file(rec)
    if path is None:
        return SEG_STATUS_NO_MASK
    saved = page._seg_applied_snapshot.get(parquet_path) if hasattr(
        page, "_seg_applied_snapshot"
    ) else None
    if saved and saved == ctx._seg_signature(path):
        return SEG_STATUS_APPLIED
    return SEG_STATUS_PENDING


def _seg_any_mask(ctx, page) -> bool:
    for path in ctx._seg_file_rows(page):
        rec = ctx._seg_mask_record(page._seg_params, path)
        if ctx._seg_mask_file(rec) is not None:
            return True
    return False


def _seg_files_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
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
        lambda _=0: ctx._seg_on_method_changed(page)
    )
    tlay.addWidget(method_combo)
    page._seg_method_combo = method_combo

    bottom = QWidget()
    blay = QVBoxLayout(bottom)
    blay.setContentsMargins(0, 0, 0, 0)
    blay.setSpacing(6)
    set_btn = QPushButton("Set Files")
    set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_btn.setEnabled(not page._seg_locked)
    set_btn.clicked.connect(lambda _=False: ctx._seg_set_files(page))
    blay.addWidget(set_btn)
    page._seg_setfiles_btn = set_btn

    table = QTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["File", "Expose", "Status"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    table.itemChanged.connect(lambda item: ctx._seg_expose_changed(page, item))
    blay.addWidget(table, 1)
    page._seg_files_table = table

    blay.addWidget(QLabel("Mask column"))
    name_edit = QLineEdit(str(page._seg_params.get("column") or "Mask"))
    name_edit.setEnabled(not page._seg_locked)
    name_edit.editingFinished.connect(
        lambda: ctx._seg_column_edited(page)
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
    rows = ctx._seg_file_rows(page)
    table.setRowCount(len(rows))
    expose = page._seg_params.get("expose") or {}
    editable = not page._seg_locked
    for i, path in enumerate(rows):
        name_item = QTableWidgetItem(ctx._comp_file_name(path, page._seg_task_id))
        name_item.setData(Qt.ItemDataRole.UserRole, path)
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(i, 0, name_item)

        has_mask = ctx._seg_mask_file(
            ctx._seg_mask_record(page._seg_params, path)
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

        status = ctx._seg_file_status(page, path)
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
    ctx._seg_rebuild_method_panel(page)
    ctx._seg_refresh_plot_settings(page)
    ctx._seg_update_preview_visibility(page)


def _seg_expose_changed(ctx, page, item) -> None:
    if page._seg_locked or item.column() != 1:
        return
    table = page._seg_files_table
    row = table.row(item)
    rows = ctx._seg_file_rows(page)
    if not (0 <= row < len(rows)):
        return
    path = rows[row]
    expose = page._seg_params.setdefault("expose", {})
    expose[path] = item.checkState() == Qt.CheckState.Checked
    ctx._seg_refresh_status(page)
    ctx._seg_mark_dirty(page)


def _seg_refresh_status(ctx, page) -> None:
    """Update only the status cells (no widget rebuild)."""
    table = page._seg_files_table
    rows = ctx._seg_file_rows(page)
    table.blockSignals(True)
    for i, path in enumerate(rows):
        if i >= table.rowCount():
            break
        item = table.item(i, 2)
        if item is None:
            continue
        status = ctx._seg_file_status(page, path)
        item.setText(status)
        if status == SEG_STATUS_APPLIED:
            item.setForeground(QColor("#1a7f37"))
        elif status == SEG_STATUS_PENDING:
            item.setForeground(QColor("#b58900"))
        else:
            item.setForeground(QColor("#9aa0a6"))
    table.blockSignals(False)


def _seg_set_files(ctx, page) -> None:
    records = ctx._available_files(page._seg_task_id)
    names = [rec.get("name", "") for rec in records]
    current = [ctx._comp_file_name(p, page._seg_task_id) for p in ctx._seg_file_rows(page)]
    chosen = ctx._pick_items_dialog(
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    picks = [rec for rec in records if rec.get("name", "") in chosen_set]
    bad = [rec.get("name", "") for rec in picks if not ctx._seg_is_image_file(rec)]
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
    ctx._seg_refresh_files(page)
    ctx._seg_mark_dirty(page)


def _seg_column_edited(ctx, page) -> None:
    name = page._seg_column_edit.text().strip() or "Mask"
    page._seg_params["column"] = name
    ctx._seg_refresh_status(page)
    ctx._seg_mark_dirty(page)


def _seg_on_method_changed(ctx, page) -> None:
    page._seg_params["method"] = page._seg_method_combo.currentData()
    ctx._seg_rebuild_method_panel(page)
    ctx._seg_mark_dirty(page)


def _seg_rebuild_method_panel(ctx, page) -> None:
    """Swap the second panel between method params and the mask-input table."""
    host = page._seg_method_host
    ctx._clear_settings_layout(host.layout())
    method = page._seg_params.get("method")
    if method == "mask_input":
        ctx._seg_mask_input_panel(page, host)
    elif _extension_def(method) is not None:
        ctx._seg_external_method_panel(page, host)
    else:
        ctx._seg_method_params_panel(page, host)


def _seg_method_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    host = QWidget()
    host.setLayout(QVBoxLayout())
    host.layout().setContentsMargins(0, 0, 0, 0)
    host.layout().setSpacing(6)
    lay.addWidget(host, 1)
    page._seg_method_host = host
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


def _seg_method_params_panel(ctx, page, host) -> None:
    """Nuclear Expansion parameters + Calculate."""
    lay = host.layout()
    params = ctx._seg_map_params(page)
    enabled = not page._seg_locked

    lay.addWidget(QLabel("<b>Nuclear Expansion</b>"))
    lay.addWidget(QLabel("Nucleus channel"))
    columns = []
    for path in ctx._seg_file_rows(page):
        for name in ctx._columns_for(path, page._seg_task_id):
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
    lay.addWidget(ctx._param_hint(
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
    lay.addWidget(ctx._param_hint(
        "Otsu picks the cut-off from the histogram; Manual uses the value "
        "below."
    ))

    def otsu_default():
        """The Otsu value of the chosen channel, used as the default."""
        channel = params.get("channel")
        rows = ctx._seg_file_rows(page)
        if not channel or not rows:
            return None
        return ctx._seg_otsu_value(page, rows[0], channel)

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
    lay.addWidget(ctx._param_hint(
        "Prefilled with the Otsu value. Editing it switches the mode to "
        "Manual."
    ))

    lay.addWidget(QLabel("Minimum nucleus area (px)"))
    area_spin = _NoWheelSpinBox()
    area_spin.setRange(1, 1000000)
    area_spin.setValue(int(params.get("min_area", 30)))
    area_spin.setEnabled(enabled)
    lay.addWidget(area_spin)
    lay.addWidget(ctx._param_hint(
        "Objects smaller than this many pixels are discarded as debris."
    ))

    lay.addWidget(QLabel("Expansion radius (px)"))
    exp_spin = _NoWheelSpinBox()
    exp_spin.setRange(0, 500)
    exp_spin.setValue(int(params.get("expand", 5)))
    exp_spin.setEnabled(enabled)
    lay.addWidget(exp_spin)
    lay.addWidget(ctx._param_hint(
        "How far each nucleus grows to approximate the cell body."
    ))

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(enabled)
    lay.addWidget(calc_btn)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    lay.addStretch(1)
    page._seg_calc_btn = calc_btn
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
        ctx._seg_mark_dirty(page)

    def on_channel(_=0):
        params["channel"] = channel_combo.currentData()
        refresh_otsu()
        ctx._seg_mark_dirty(page)

    def on_mode(_=0):
        params["threshold_mode"] = mode_combo.currentData()
        if params["threshold_mode"] == "otsu":
            refresh_otsu()
        ctx._seg_mark_dirty(page)

    def on_area(value):
        params["min_area"] = int(value)
        ctx._seg_mark_dirty(page)

    def on_expand(value):
        params["expand"] = int(value)
        ctx._seg_mark_dirty(page)

    channel_combo.currentIndexChanged.connect(on_channel)
    mode_combo.currentIndexChanged.connect(on_mode)
    area_spin.valueChanged.connect(on_area)
    exp_spin.valueChanged.connect(on_expand)
    calc_btn.clicked.connect(lambda _=False: ctx._seg_calculate(page))


def _seg_channel_columns(ctx, page) -> list:
    """Union of non-coordinate columns across the selected files."""
    columns = []
    for path in ctx._seg_file_rows(page):
        for name in ctx._columns_for(path, page._seg_task_id):
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
    params = ctx._seg_map_params(page)
    enabled = not page._seg_locked
    ready = ctx._ext_ready(method)

    lay.addWidget(QLabel(f"<b>{entry['name']}</b>"))
    lay.addWidget(QLabel(entry["desc"]))
    lay.addWidget(QLabel("Runs in an external Python environment."))

    banner = QLabel()
    banner.setWordWrap(True)
    if ready:
        banner.setText("Extension ready ✓")
        banner.setStyleSheet("color: #1a7f37; font-weight: bold;")
    else:
        banner.setText(
            f"{entry['name']} extension is not configured.\n"
            "Open Extension → Manage Extension to set it up first."
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
    for path in ctx._seg_file_rows(page):
        item = QListWidgetItem(ctx._comp_file_name(path, page._seg_task_id))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        has_mask = (
            ctx._seg_mask_file(ctx._seg_mask_record(page._seg_params, path))
            is not None
        )
        item.setCheckState(
            Qt.CheckState.Unchecked if has_mask else Qt.CheckState.Checked
        )
        files_list.addItem(item)
    lay.addWidget(files_list)
    page._seg_calc_files_list = files_list

    lay.addWidget(param_label("Nucleus channel"))
    columns = ctx._seg_channel_columns(page)
    channel_combo = _NoWheelComboBox()
    channel_combo.addItem("", None)
    for name in columns:
        channel_combo.addItem(name, name)
    idx = channel_combo.findData(params.get("channel"))
    if idx >= 0:
        channel_combo.setCurrentIndex(idx)
    channel_combo.setEnabled(enabled)
    lay.addWidget(channel_combo)
    lay.addWidget(ctx._param_hint(
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
        lay.addWidget(ctx._param_hint(
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
        lay.addWidget(ctx._param_hint(
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
        lay.addWidget(ctx._param_hint(
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
        lay.addWidget(ctx._param_hint(
            "Approximate tile edge in pixels; Smage adapts the actual "
            "tiles to the image size. 0 = let StarDist choose."
        ))

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(enabled and ready)
    lay.addWidget(calc_btn)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Calculation output appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    for line in getattr(page, "_seg_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log, 1)
    page._seg_calc_btn = calc_btn
    page._seg_calc_status = status
    page._seg_calc_log = log

    def on_channel(_=0):
        params["channel"] = channel_combo.currentData()
        ctx._seg_mark_dirty(page)

    def on_cyto(_=0):
        params["cyto_channel"] = cyto_combo.currentData()
        ctx._seg_mark_dirty(page)

    def on_prob(value):
        params["prob"] = float(value)
        ctx._seg_mark_dirty(page)

    def on_nms(value):
        params["nms"] = float(value)
        ctx._seg_mark_dirty(page)

    def on_tile_size(value):
        params["tile_size"] = int(value)
        ctx._seg_mark_dirty(page)

    channel_combo.currentIndexChanged.connect(on_channel)
    if cyto_combo is not None:
        cyto_combo.currentIndexChanged.connect(on_cyto)
    if prob_spin is not None:
        prob_spin.valueChanged.connect(on_prob)
    if nms_spin is not None:
        nms_spin.valueChanged.connect(on_nms)
    if tile_size_spin is not None:
        tile_size_spin.valueChanged.connect(on_tile_size)
    calc_btn.clicked.connect(lambda _=False: ctx._seg_calculate(page))


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

    import_btn = QPushButton("Import")
    import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    import_btn.setEnabled(not page._seg_locked)
    lay.addWidget(import_btn)
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
    import_btn.clicked.connect(lambda _=False: ctx._seg_import_masks(page))

    ctx._seg_refresh_input_table(page)


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
    rows = ctx._seg_file_rows(page)
    table.blockSignals(True)
    table.setRowCount(len(rows))
    enabled = not page._seg_locked
    for i, path in enumerate(rows):
        name_item = QTableWidgetItem(ctx._comp_file_name(path, page._seg_task_id))
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(i, 0, name_item)

        rec = ctx._seg_mask_record(page._seg_params, path) or {}
        btn = QPushButton(Path(str(rec.get("input") or "")).name or "…")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(enabled)
        btn.setToolTip(rec.get("input") or "Choose a mask file")
        btn.clicked.connect(
            lambda _=False, p=path: ctx._seg_pick_mask_file(page, p)
        )
        table.setCellWidget(i, 1, btn)

        state = rec.get("state") or (
            SEG_IMPORT_DONE if ctx._seg_mask_file(rec) else SEG_IMPORT_SELECT
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
    rec = ctx._seg_mask_record_live(page, parquet_path, create=True)
    already = ctx._seg_mask_file(rec) is not None
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
        ctx._seg_delete_mask(page, parquet_path)
    rec["input"] = path
    rec["state"] = SEG_IMPORT_WAITING
    rec["path"] = rec.get("path") if rec.get("source") == "builtin" else None
    ctx._seg_refresh_input_table(page)
    ctx._seg_refresh_files(page)
    ctx._seg_mark_dirty(page)


def _seg_delete_mask(ctx, page, parquet_path) -> None:
    """Remove a file's mask parquet, its raster pyramid and its record."""
    import shutil

    rec = ctx._seg_mask_record(page._seg_params, parquet_path) or {}
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
    import numpy as np
    import tifffile

    rows = ctx._seg_file_rows(page)
    if not rows:
        QMessageBox.information(
            ctx.host, "Import", "Select data files first."
        )
        return
    pending = []
    for path in rows:
        rec = ctx._seg_mask_record_live(page, path, create=True)
        if rec.get("state") == SEG_IMPORT_DONE and ctx._seg_mask_file(rec):
            continue
        if not rec.get("input"):
            continue
        pending.append((path, rec))
    if not pending:
        QMessageBox.information(
            ctx.host, "Import",
            "All files have already been imported — nothing new to import.",
        )
        return

    done = 0
    errors = []
    for parquet_path, rec in pending:
        ent = ctx._ensure_image_index(parquet_path)
        if ent is None or ent.get("manifest") is None:
            rec["state"] = SEG_IMPORT_UNKNOWN
            errors.append(f"{ctx._comp_file_name(parquet_path, page._seg_task_id)}: no image index")
            continue
        man = ent["manifest"]
        height = int(round(man["ymax"] - man["ymin"])) + 1
        width = int(round(man["xmax"] - man["xmin"])) + 1
        src = str(rec.get("input") or "")
        suffix = Path(src).suffix.lower()
        if suffix not in (".tif", ".tiff", ".ome.tif", ".ome.tiff"):
            rec["state"] = SEG_IMPORT_FORMAT
            continue
        try:
            arr = tifffile.imread(src)
        except Exception:  # noqa: BLE001
            rec["state"] = SEG_IMPORT_FORMAT
            continue
        arr = np.asarray(arr)
        while arr.ndim > 2:
            arr = arr[0]
        if arr.ndim != 2:
            rec["state"] = SEG_IMPORT_FORMAT
            continue
        if arr.shape[0] != height or arr.shape[1] != width:
            rec["state"] = SEG_IMPORT_SIZE
            continue
        try:
            rel = ctx._seg_write_mask(page._seg_task_id, parquet_path, arr)
        except Exception:  # noqa: BLE001
            rec["state"] = SEG_IMPORT_UNKNOWN
            continue
        rec["path"] = rel
        rec["source"] = "import"
        rec["state"] = SEG_IMPORT_DONE
        done += 1

    ctx._seg_after_masks_changed(page)
    ctx._seg_refresh_input_table(page)
    ctx._seg_refresh_files(page)
    msg = f"Imported {done} mask(s)."
    if errors:
        msg += "\n" + "\n".join(errors)
    page._seg_import_status.setText(msg)
    ctx.statusBar().showMessage(msg.splitlines()[0])


def _seg_write_mask(ctx, task_id, parquet_path, array) -> str:
    """Save a mask as a ctx-contained ``[x, y, mask]`` parquet + label pyramid.

    The mask becomes an extra channel of the source image: a parquet with
    one row per pixel (x/y shared with the source) plus a mode-based raster
    pyramid so large masks zoom like the original image. Returns the
    relative path of the mask parquet.
    """
    import numpy as np
    import pandas as pd

    if ctx.project is None:
        raise RuntimeError("no project")
    ent = ctx._ensure_image_index(parquet_path)
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
    rel = ctx._module_rel(task_id, f"{stem}_mask.parquet")
    path = ctx.project.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    xx = np.tile(np.arange(width, dtype=np.int32), height) + xmin
    yy = np.repeat(np.arange(height, dtype=np.int32), width) + ymin
    pd.DataFrame({"x": xx, "y": yy, "mask": arr.ravel()}).to_parquet(
        path, index=False
    )
    _build_label_index(
        path, arr, xmin, xmin + width - 1, ymin, ymin + height - 1
    )
    ctx._seg_mask_cache = {}
    ctx._seg_value_cache = {}
    return rel


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
    ctx._seg_update_save_btn(page)
    ctx._refresh_workflow_tree()
    ctx._module_saved(page)


def _seg_calculate(ctx, page) -> None:
    """Run the selected method on every selected file."""
    method = page._seg_params.get("method")
    if _extension_def(method) is not None:
        ctx._seg_calculate_external(page)
        return
    ctx._seg_calculate_nuclear(page)


def _seg_calculate_nuclear(ctx, page) -> None:
    """Run Nuclear Expansion on every selected file."""
    rows = ctx._seg_file_rows(page)
    params = ctx._seg_map_params(page)
    status = getattr(page, "_seg_calc_status", None)
    if not rows:
        QMessageBox.information(ctx.host, "Calculate", "Select data files first.")
        return
    if not params.get("channel"):
        QMessageBox.information(
            ctx.host, "Calculate", "Choose the nucleus channel first."
        )
        return
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    done = 0
    cells = 0
    errors = []
    try:
        for i, parquet_path in enumerate(rows):
            if status is not None:
                status.setText(
                    f"Calculating {i + 1}/{len(rows)}: "
                    f"{ctx._comp_file_name(parquet_path, page._seg_task_id)}…"
                )
                QApplication.processEvents()
            mask, err = ctx._seg_nuclear_expansion(parquet_path, params)
            if mask is None:
                errors.append(f"{ctx._comp_file_name(parquet_path, page._seg_task_id)}: {err}")
                continue
            try:
                rel = ctx._seg_write_mask(page._seg_task_id, parquet_path, mask)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ctx._comp_file_name(parquet_path, page._seg_task_id)}: {exc}")
                continue
            rec = ctx._seg_mask_record_live(page, parquet_path, create=True)
            rec["path"] = rel
            rec["source"] = "builtin"
            rec["state"] = SEG_IMPORT_DONE
            done += 1
            cells += int(mask.max())
    finally:
        QApplication.restoreOverrideCursor()

    ctx._seg_after_masks_changed(page)
    ctx._seg_refresh_files(page)
    # The refresh rebuilds the parameter panel, so re-read its status label.
    status = getattr(page, "_seg_calc_status", status)
    if status is not None:
        msg = f"Calculated {done} mask(s) · {cells} cell(s)."
        if errors:
            msg += "\n" + "\n".join(errors)
        status.setText(msg)


def _seg_raster_stack(ctx, parquet_path, channels):
    """Reconstruct a (C, H, W) float stack of the given channels."""
    import numpy as np

    images = []
    shape = None
    for channel in channels:
        image, err = ctx._seg_raster_channel(parquet_path, channel)
        if image is None:
            return None, err
        if shape is None:
            shape = image.shape
        elif image.shape != shape:
            return None, "channel shapes differ"
        images.append(image)
    return np.stack(images, axis=0).astype("float32"), None


def _seg_calculate_external(ctx, page) -> None:
    """Run Cellpose / StarDist on the selected files in one subprocess."""
    import json
    import tempfile

    import numpy as np

    method = page._seg_params.get("method")
    entry = _extension_def(method)
    params = ctx._seg_map_params(page)
    files_list = getattr(page, "_seg_calc_files_list", None)
    if files_list is not None:
        rows = []
        for i in range(files_list.count()):
            item = files_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                rows.append(item.data(Qt.ItemDataRole.UserRole))
    else:
        rows = ctx._seg_file_rows(page)
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
            f"{entry['name']} extension is not configured.\n"
            "Open Extension → Manage Extension to set it up first.",
        )
        return

    python = ctx._ext_python(method)
    runner = str(ctx._ext_runner_path(method))
    tmpdir = Path(tempfile.mkdtemp(prefix="smage-seg-"))
    jobs = []
    manifest = []
    nucleus = params.get("channel")
    cyto = params.get("cyto_channel") if entry["family"] == "cellpose" else None
    # Reconstructing the channel image happens in the main app, before the
    # external subprocess starts, so log it (this is the silent phase).
    ctx._seg_log(page, f"Rasterizing {len(rows)} file(s)…")
    for i, parquet_path in enumerate(rows):
        ctx._seg_log(
            page, f"  rasterizing {ctx._comp_file_name(parquet_path, page._seg_task_id)}…"
        )
        if cyto:
            stack, err = ctx._seg_raster_stack(parquet_path, [cyto, nucleus])
            nucleus_idx, cyto_idx = 1, 0
        else:
            stack, err = ctx._seg_raster_stack(parquet_path, [nucleus])
            nucleus_idx, cyto_idx = 0, None
        if stack is None:
            continue
        image_npy = tmpdir / f"in_{i}.npy"
        output_npy = tmpdir / f"out_{i}.npy"
        np.save(image_npy, stack)
        manifest.append({"image": str(image_npy), "output": str(output_npy)})
        jobs.append({"parquet": parquet_path, "output": str(output_npy)})

    if not jobs:
        return

    manifest_path = tmpdir / "jobs.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    argv = [
        python, runner, "segment",
        "--manifest", str(manifest_path),
        "--nucleus", str(nucleus_idx),
        "--model", entry["model"],
        "--device", entry["device"],
    ]
    if cyto_idx is not None:
        argv += ["--cyto", str(cyto_idx)]
    if entry["family"] == "stardist":
        argv += [
            "--prob", str(params.get("prob", 0.5)),
            "--nms", str(params.get("nms", 0.3)),
            "--tile-size", str(params.get("tile_size", 0)),
        ]

    page._seg_ext_jobs = jobs
    page._seg_ext_tmpdir = tmpdir
    page._seg_ext_done = set()
    page._seg_ext_errors = []
    page._seg_ext_cells = 0
    page._seg_ext_report = []
    page._seg_ext_tile = None
    page._seg_ext_activity = ""
    page._seg_ext_finished = False
    page._seg_ext_running = True
    page._seg_ext_start_time = time.monotonic()
    # Keep appending to the existing log so each run's history is preserved.
    ctx._seg_log(
        page,
        f"===== {entry['name']} run — {len(jobs)} file(s) =====",
    )
    worker = ctx._ext_worker()
    worker.clear()  # drop any stale results from earlier operations
    # Stop any previous run's poll timer so it can't steal this run's events.
    old_timer = getattr(page, "_seg_ext_timer", None)
    if old_timer is not None:
        old_timer.stop()
    worker.run(("seg-batch",), argv)
    if status is not None:
        status.setText(f"Running {entry['name']} on {len(jobs)} file(s)…")

    timer = QTimer(ctx.host)
    timer.setInterval(150)
    timer.timeout.connect(lambda: ctx._seg_ext_poll(page))
    timer.start()
    page._seg_ext_timer = timer


def _seg_log(ctx, page, text) -> None:
    """Append a timestamped line to the segmentation calculation log."""
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_seg_log_lines", None)
    if lines is None:
        lines = []
        page._seg_log_lines = lines
    for line in text.splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    box = getattr(page, "_seg_calc_log", None)
    if box is not None:
        for line in text.splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")
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
    page._seg_log_last_is_tile = True


def _seg_ext_poll(ctx, page) -> None:
    """Collect finished external segmentations and finalize the masks."""
    import shutil

    import numpy as np

    worker = ctx._ext_worker()
    status = getattr(page, "_seg_calc_status", None)
    jobs = getattr(page, "_seg_ext_jobs", None) or []
    done = getattr(page, "_seg_ext_done", None) or set()
    errors = getattr(page, "_seg_ext_errors", [])
    cells = getattr(page, "_seg_ext_cells", 0)
    report = getattr(page, "_seg_ext_report", None) or []
    tile = getattr(page, "_seg_ext_tile", None)
    activity = getattr(page, "_seg_ext_activity", "") or ""
    finished = getattr(page, "_seg_ext_finished", False)
    out_to_job = {job["output"]: job for job in jobs}

    for item in worker.poll():
        if item[1] == "line":
            line = item[2]
            if line.startswith("tile: "):
                info = line[len("tile: "):].strip()
                if "/" in info:
                    try:
                        cur, tot = info.split("/", 1)
                        tile = (int(cur), int(tot))
                        ctx._seg_log_tile(page, int(cur), int(tot))
                    except ValueError:
                        pass
            elif line.startswith("done:"):
                body = line[len("done:"):].strip()
                out_path, sep, meta = body.partition("|")
                out_path = out_path.strip()
                n_cells = 0
                seconds = 0.0
                for part in meta.split("|"):
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
                parquet_path = job["parquet"]
                try:
                    arr = np.load(out_path)
                    if arr.ndim != 2:
                        raise ValueError("bad mask shape")
                    rel = ctx._seg_write_mask(
                        page._seg_task_id, parquet_path, arr
                    )
                    rec = ctx._seg_mask_record_live(
                        page, parquet_path, create=True
                    )
                    rec["path"] = rel
                    rec["source"] = "builtin"
                    rec["state"] = SEG_IMPORT_DONE
                    cells += int(arr.max())
                    report.append(
                        f"{ctx._comp_file_name(parquet_path, page._seg_task_id)}: "
                        f"{n_cells} cells, {seconds:.1f}s"
                    )
                    ctx._seg_log(
                        page,
                        f"✓ {ctx._comp_file_name(parquet_path, page._seg_task_id)}: "
                        f"{n_cells} cells, {seconds:.1f}s",
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{ctx._comp_file_name(parquet_path, page._seg_task_id)}: {exc}")
                    ctx._seg_log(page, f"✗ {ctx._comp_file_name(parquet_path, page._seg_task_id)}: {exc}")
                done.add(out_path)
            else:
                # Non-progress output (e.g. TensorFlow/model loading lines):
                # remember the latest for the status, and record it in the
                # scrollable log so the user can see and copy the details.
                if line.strip():
                    activity = line.strip()[-120:]
                    ctx._seg_log(page, line)
            continue
        if len(item) != 4 or item[1] != "done":
            continue
        key, _tag, code, text = item[0], item[1], item[2], item[3]
        if not (isinstance(key, tuple) and key[0] == "seg-batch"):
            continue
        finished = True
        for job in jobs:
            if job["output"] in done:
                continue
            if code != 0:
                errors.append(
                    f"{ctx._comp_file_name(job['parquet'], page._seg_task_id)}: "
                    f"{text.strip()[-200:]}"
                )
            else:
                errors.append(
                    f"{ctx._comp_file_name(job['parquet'], page._seg_task_id)}: no output produced"
                )
            done.add(job["output"])
        if text and code != 0:
            ctx._seg_log(page, text.rstrip())

    page._seg_ext_done = done
    page._seg_ext_errors = errors
    page._seg_ext_cells = cells
    page._seg_ext_report = report
    page._seg_ext_tile = tile
    page._seg_ext_activity = activity
    page._seg_ext_finished = finished
    if finished:
        page._seg_ext_running = False

    if not finished:
        if status is not None:
            start = getattr(page, "_seg_ext_start_time", None)
            elapsed = int(time.monotonic() - start) if start else 0
            minutes, seconds = divmod(elapsed, 60)
            elapsed_str = (
                f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
            )
            stamp = f"[{datetime.now().strftime('%H:%M:%S')} · {elapsed_str}]"
            msg = f"{stamp} Running… {len(done)}/{len(jobs)} file(s) done"
            if tile is not None:
                msg += f" — tile {tile[0]}/{tile[1]}"
            elif activity:
                msg += f" — {activity}"
            status.setText(msg)
        return

    timer = getattr(page, "_seg_ext_timer", None)
    if timer is not None:
        timer.stop()
    shutil.rmtree(page._seg_ext_tmpdir, ignore_errors=True)
    ok_count = len(jobs) - len(errors)
    start = getattr(page, "_seg_ext_start_time", None)
    elapsed = int(time.monotonic() - start) if start else 0
    minutes, seconds = divmod(elapsed, 60)
    elapsed_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    ctx._seg_log(
        page,
        f"Calculated {ok_count} mask(s) · {cells} cell(s) in {elapsed_str}.",
    )
    for err in errors:
        ctx._seg_log(page, f"✗ {err}")
    ctx._seg_after_masks_changed(page)
    ctx._seg_refresh_files(page)
    status = getattr(page, "_seg_calc_status", None)
    if status is not None:
        msg = f"Calculated {ok_count} mask(s) · {cells} cell(s) in {elapsed_str}."
        if report:
            msg += "\n" + "\n".join(report)
        if errors:
            msg += "\n" + "\n".join(errors)
        status.setText(msg)


def _seg_raster_channel(ctx, parquet_path, channel):
    """Reconstruct one channel as a 2-D float image (or (None, error))."""
    import numpy as np
    import pandas as pd

    ent = ctx._ensure_image_index(parquet_path)
    if ent is None or ent.get("manifest") is None:
        return None, "no image index — re-import this file as an image"
    man = ent["manifest"]
    try:
        df = pd.read_parquet(parquet_path, columns=["x", "y", channel])
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    xmin = float(man["xmin"])
    ymin = float(man["ymin"])
    width = int(round(float(man["xmax"]) - xmin)) + 1
    height = int(round(float(man["ymax"]) - ymin)) + 1
    xi = np.rint(df["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
    yi = np.rint(df["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
    values = pd.to_numeric(df[channel], errors="coerce").to_numpy(dtype="float64")
    image = np.zeros((height, width), dtype="float64")
    ok = (
        (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        & np.isfinite(values)
    )
    image[yi[ok], xi[ok]] = values[ok]
    if values[ok].size == 0:
        return None, "no finite pixel values"
    return image, None


def _seg_nuclear_expansion(ctx, parquet_path, params):
    """Threshold → label → filter → expand. Returns (mask, error)."""
    import numpy as np
    from scipy import ndimage

    channel = params.get("channel")
    if not channel:
        return None, "no nucleus channel"
    image, err = ctx._seg_raster_channel(parquet_path, channel)
    if image is None:
        return None, err

    if params.get("threshold_mode") == "manual":
        threshold = float(params.get("threshold", 0.0))
    else:
        threshold = _otsu_threshold(image)
    binary = image > threshold
    if not binary.any():
        return None, "threshold produced an empty mask"
    binary = ndimage.binary_opening(binary, structure=np.ones((3, 3)))
    labels, _count = ndimage.label(binary)
    labels = _relabel_sequential(labels, int(params.get("min_area", 1)))
    expand = int(params.get("expand", 0))
    if expand > 0 and labels.any():
        distance, (iy, ix) = ndimage.distance_transform_edt(
            labels == 0, return_indices=True
        )
        grown = distance <= expand
        labels = np.where(grown, labels[iy, ix], labels)
    return labels.astype(np.int32), None


def _seg_mark_dirty(ctx, page) -> None:
    page._seg_dirty = True
    ctx._seg_update_save_btn(page)
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
    for path in ctx._seg_file_rows(page):
        if not expose.get(path):
            continue
        for name in ctx._column_index.get(ctx._rel_data_key(path), []):
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
    for path in ctx._seg_file_rows(page):
        if not expose.get(path):
            continue
        rec = ctx._seg_mask_record(params, path)
        mask_path = ctx._seg_mask_file(rec)
        if mask_path is None:
            continue
        applied[path] = ctx._seg_signature(mask_path)
    params["applied"] = applied
    page._seg_applied_snapshot = dict(applied)
    page._seg_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._seg_snapshot = copy.deepcopy(params)
    page._seg_dirty = False
    ctx._seg_update_save_btn(page)
    ctx._seg_refresh_status(page)
    ctx._seg_value_cache = {}
    ctx.statusBar().showMessage("Segmentation saved")
    ctx._module_saved(page)


def _seg_close(ctx, page) -> None:
    if getattr(page, "_seg_ext_running", False):
        reply = QMessageBox.question(
            ctx.host,
            "Close Segmentation",
            "A calculation is still running.\n"
            "Closing the module now will interrupt it. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
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
    for name in ("_seg_overlay_timer", "_seg_ext_timer"):
        timer = getattr(page, name, None)
        if timer is not None:
            timer.stop()
    ctx._close_module_page(page)


def _seg_preview_panel(ctx, page) -> QWidget:
    import pyqtgraph as pg

    panel = ctx._build_panel_frame()
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
    legend = ctx._figure_attach_legend(plot)
    bh.addWidget(legend, 0)
    lay.addWidget(body, 1)
    page._seg_preview_plot = plot
    # Re-render both the image (LOD raster for the new view) and the mask
    # overlay when the user zooms or pans.
    timer = QTimer(ctx.host)
    timer.setSingleShot(True)
    timer.setInterval(30)
    timer.timeout.connect(lambda: ctx._seg_render_preview(page))
    page._seg_overlay_timer = timer
    plot.getPlotItem().getViewBox().sigRangeChanged.connect(
        lambda *_: timer.start()
    )
    return panel


def _seg_plot_settings_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
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
    rows = ctx._seg_file_rows(page)
    if not rows:
        return plot_dict
    if plot_dict.get("file") not in rows:
        fresh = ctx._seg_default_plot(rows[0], page._seg_task_id)
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
    ctx._clear_settings_layout(host.layout())
    lay = host.layout()
    enabled = not page._seg_locked
    plot_dict = ctx._seg_plot_dict(page)
    rows = [p for p in ctx._seg_file_rows(page)
            if ctx._seg_mask_file(ctx._seg_mask_record(page._seg_params, p))]

    lay.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    for path in rows:
        file_combo.addItem(ctx._comp_file_name(path, page._seg_task_id), path)
    idx = file_combo.findData(plot_dict.get("file"))
    if idx >= 0:
        file_combo.setCurrentIndex(idx)
    file_combo.setEnabled(enabled and bool(rows))
    lay.addWidget(file_combo)

    columns = []
    rec = ctx._figure_record(plot_dict.get("file"))
    if rec is not None:
        columns = ctx._columns_for(rec.get("parquet"), page._seg_task_id)

    ctx._figure_image_layer_settings(
        lay, plot_dict, columns, enabled,
        on_change=lambda: ctx._seg_render_preview(page),
        on_rebuild=lambda: (
            ctx._seg_refresh_plot_settings(page),
            ctx._seg_render_preview(page),
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

    ctx._figure_display_toggles(
        lay, plot_dict, enabled,
        on_change=lambda: (
            ctx._figure_apply_display_options(
                plot_dict, getattr(page, "_seg_preview_plot", None)
            )
            if getattr(page, "_seg_preview_plot", None) is not None
            else ctx._seg_render_preview(page)
        ),
    )
    lay.addStretch(1)

    def on_file(_=0):
        path = file_combo.currentData()
        if not path:
            return
        plot_dict["file"] = path
        plot_dict["file_name"] = file_combo.currentText()
        ctx._seg_refresh_plot_settings(page)
        ctx._seg_render_preview(page)
        ctx._seg_mark_dirty(page)

    def on_mode(_=0):
        plot_dict["mask_mode"] = mode_combo.currentData()
        ctx._seg_refresh_plot_settings(page)
        ctx._seg_render_preview(page)
        ctx._seg_mark_dirty(page)

    def on_color_mode(_=0):
        plot_dict["mask_color"] = color_mode_combo.currentData()
        ctx._seg_render_overlay(page)
        ctx._seg_mark_dirty(page)

    def on_alpha(value):
        plot_dict["mask_alpha"] = int(value)
        ctx._seg_render_overlay(page)
        ctx._seg_mark_dirty(page)

    file_combo.currentIndexChanged.connect(on_file)
    mode_combo.currentIndexChanged.connect(on_mode)
    if color_mode_combo is not None:
        color_mode_combo.currentIndexChanged.connect(on_color_mode)
    if alpha_spin is not None:
        alpha_spin.valueChanged.connect(on_alpha)


def _seg_update_preview_visibility(ctx, page) -> None:
    """Parts 3 and 4 appear once at least one file has a mask."""
    has_mask = ctx._seg_any_mask(page)
    panel3 = getattr(page, "_seg_preview_panel", None)
    panel4 = getattr(page, "_seg_settings_panel", None)
    for panel in (panel3, panel4):
        if panel is not None:
            panel.setVisible(has_mask)
    if has_mask:
        ctx._seg_render_preview(page)


def _seg_render_preview(ctx, page) -> None:
    plot = getattr(page, "_seg_preview_plot", None)
    if plot is None:
        return
    plot_dict = ctx._seg_plot_dict(page)
    rec = ctx._figure_record(plot_dict.get("file"))
    if rec is None:
        return
    ctx._figure_render(plot_dict, plot, task_id=page._seg_task_id)
    ctx._seg_render_overlay(page)


def _seg_render_overlay(ctx, page) -> None:
    """Draw the mask overlay on top of the preview image."""
    import numpy as np
    import pyqtgraph as pg

    plot = getattr(page, "_seg_preview_plot", None)
    if plot is None:
        return
    plot_dict = ctx._seg_plot_dict(page)
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
    rec = ctx._seg_mask_record(page._seg_params, parquet_path)
    mask_path = ctx._seg_mask_file(rec)
    if mask_path is None:
        return
    ent = ctx._ensure_image_index(parquet_path)
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
    mask = ctx._seg_load_mask(mask_path)
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
        edges_full = ctx._seg_edge_map(mask_path)
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
    ("Mask Input", "mask_input"),
    ("Cellpose-CPU", "cellpose-cpu"),
    ("Cellpose-GPU", "cellpose-gpu"),
    ("StarDist-CPU", "stardist-cpu"),
]


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
}

