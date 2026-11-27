"""Illumination Correction module: per-tile flat-field correction."""
from __future__ import annotations

from smage.runlog import runlog
from smage.widgets.panels import _FlowArrowBand

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRectF,
    QTimer,
    Qt,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.pipeline import _rename_applies
from smage.core.scale_math import _apply_scale_transforms
from smage.core.settings import (
    _default_palette,
    _plot_background,
)
from smage.data.columns import _illumination_stage_item
from smage.data.image_index import _image_geometry
from smage.data.workers import _ColumnSampleWorker, _IlluminationWorker
from smage.widgets.colormap import _figure_colormap
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from smage.widgets.plot_axes import _make_sci_axis
from smage.widgets.resources import _lock_pixmap
from uuid import uuid4
import copy
import pyqtgraph as pg
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.modules.compensation import page as _compensation_page
from smage.widgets import colormap as _widgets_colormap
from smage.widgets.gate_editor import editor as _widgets_gate_editor_editor
from smage.widgets import image_level as _widgets_image_level
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import renderer as _widgets_plot_renderer


def _build_illumination_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Illumination Correction page."""
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
    close_btn.clicked.connect(lambda _=False: _illum_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("submodules", [])
    page._illum_task_def = task_def
    page._illum_task_id = task_def.get("id")
    page._illum_params = params
    page._illum_submodules = params["submodules"]
    page._illum_selected = None
    page._illum_plot = None
    page._illum_locked = locked
    page._illum_save_btn = save_btn
    page._illum_dirty = False
    page._illum_snapshot = copy.deepcopy(params)
    page._illum_worker = _IlluminationWorker()
    page._illum_request_key = None
    page._illum_calc_running = False
    page._illum_hist_worker = _ColumnSampleWorker()
    page._illum_hist_cache = {}
    page._illum_hist_pending = {}
    page._illum_shared_levels = None
    page._illum_params_version = 0
    page._illum_poll_timer = QTimer(ctx.host)
    page._illum_poll_timer.setInterval(120)
    page._illum_poll_timer.timeout.connect(
        lambda: _illum_on_poll(ctx, page)
    )
    page._illum_poll_timer.start()
    page._illum_order_timer = QTimer(ctx.host)
    page._illum_order_timer.setSingleShot(True)
    page._illum_order_timer.setInterval(200)
    page._illum_order_timer.timeout.connect(
        lambda: _illum_reordered(ctx, page)
    )
    save_btn.clicked.connect(lambda _=False: _illum_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QHBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(12)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_illum_list_panel(ctx, page))
    right_host = QWidget()
    right_lay = QVBoxLayout(right_host)
    right_lay.setContentsMargins(0, 0, 0, 0)
    right_lay.setSpacing(0)
    splitter.addWidget(right_host)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 5)
    splitter.setSizes([220, 1100])
    row.addWidget(splitter, 1)
    layout.addWidget(content, 1)
    page._illum_right_host = right_host

    _illum_refresh_list(ctx, page)
    _illum_rebuild_right(ctx, page)
    _illum_update_save_btn(ctx, page)
    return page


def _illum_list_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    outer = QHBoxLayout(panel)
    outer.setContentsMargins(6, 10, 10, 10)
    outer.setSpacing(4)
    outer.addWidget(_FlowArrowBand())
    inner = QVBoxLayout()
    inner.setContentsMargins(0, 0, 0, 0)
    inner.setSpacing(6)
    add_btn = QPushButton("Add Submodule")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_btn.setEnabled(not page._illum_locked)
    add_btn.clicked.connect(lambda _=False: _illum_add_submodule(ctx, page))
    inner.addWidget(add_btn)
    sub_list = QListWidget()
    sub_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: none; }"
        "QListWidget::item:selected { background: #dbeafa; }"
    )
    sub_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    sub_list.setDefaultDropAction(Qt.DropAction.MoveAction)
    sub_list.itemDoubleClicked.connect(
        lambda item: _illum_select_submodule(ctx,
            page, item.data(Qt.ItemDataRole.UserRole)
        )
    )
    sub_list.itemClicked.connect(
        lambda item: _illum_select_submodule(ctx,
            page, item.data(Qt.ItemDataRole.UserRole)
        )
    )
    sub_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    sub_list.customContextMenuRequested.connect(
        lambda pos: _illum_list_menu(ctx, page, pos)
    )
    sub_list.model().rowsMoved.connect(
        lambda *_: page._illum_order_timer.start()
    )
    inner.addWidget(sub_list, 1)
    note = QLabel(
        "Data passes through the submodules above, top to bottom, "
        "and leaves the module after the last one."
    )
    note.setWordWrap(True)
    note.setStyleSheet("color: #6b7785;")
    inner.addWidget(note)
    outer.addLayout(inner, 1)
    page._illum_list = sub_list
    page._illum_add_btn = add_btn
    return panel


def _illum_refresh_list(ctx, page) -> None:
    sub_list = page._illum_list
    sub_list.blockSignals(True)
    sub_list.clear()
    for sub in page._illum_submodules:
        item = QListWidgetItem(sub.get("name", ""))
        item.setData(Qt.ItemDataRole.UserRole, sub.get("id"))
        if sub.get("mask") is None:
            item.setToolTip("Pattern not recognized yet")
        sub_list.addItem(item)
    sub_list.blockSignals(False)
    if page._illum_selected:
        for i in range(sub_list.count()):
            if sub_list.item(i).data(Qt.ItemDataRole.UserRole) == page._illum_selected:
                sub_list.setCurrentItem(sub_list.item(i))
                break


def _illum_selected_sub(ctx, page):
    for sub in page._illum_submodules:
        if sub.get("id") == page._illum_selected:
            return sub
    return None


def _illum_next_name(ctx, page) -> str:
    taken = {sub.get("name") for sub in page._illum_submodules}
    n = 1
    while f"Channel{n}" in taken:
        n += 1
    return f"Channel{n}"


def _illum_unique_name(ctx, page, base: str) -> str:
    taken = {sub.get("name") for sub in page._illum_submodules}
    if base not in taken:
        return base
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def _illum_default_axes(ctx, task_id=None):
    columns = []
    for rec in _data_files._available_files(ctx, task_id):
        for name in _data_columns._columns_for(ctx, rec.get("parquet"), task_id):
            if name not in columns:
                columns.append(name)
    return (
        "x" if "x" in columns else None,
        "y" if "y" in columns else None,
    )


def _illum_add_submodule(ctx, page) -> None:
    x_default, y_default = _illum_default_axes(ctx, page._illum_task_id)
    sub = {
        "id": uuid4().hex[:8],
        "name": _illum_next_name(ctx, page),
        "files": [],
        "channel": None,
        "x": x_default,
        "y": y_default,
        "tile_w": 256,
        "tile_h": 256,
        "bin": 5,
        "resolution": 10,
        "method": "median",
        "intensity": 1.0,
        "calc_files": [],
        "mask": None,
        "mask_source": None,
    }
    page._illum_submodules.append(sub)
    page._illum_selected = sub["id"]
    _illum_refresh_list(ctx, page)
    _illum_rebuild_right(ctx, page)
    _illum_mark_dirty(ctx, page)


def _illum_list_menu(ctx, page, pos) -> None:
    sub_list = page._illum_list
    item = sub_list.itemAt(pos)
    if item is None:
        return
    sub_id = item.data(Qt.ItemDataRole.UserRole)
    sub = next((s for s in page._illum_submodules if s["id"] == sub_id), None)
    if sub is None:
        return
    menu = QMenu(sub_list)
    ren = QAction("Rename", menu)
    ren.setEnabled(not page._illum_locked)
    ren.triggered.connect(lambda: _illum_rename_submodule(ctx, page, sub))
    menu.addAction(ren)
    dup = QAction("Duplicate", menu)
    dup.setEnabled(not page._illum_locked)
    dup.triggered.connect(lambda: _illum_duplicate_submodule(ctx, page, sub))
    menu.addAction(dup)
    menu.addSeparator()
    delete = QAction("Delete", menu)
    delete.setEnabled(not page._illum_locked)
    delete.triggered.connect(lambda: _illum_delete_submodule(ctx, page, sub))
    menu.addAction(delete)
    menu.exec(sub_list.viewport().mapToGlobal(pos))


def _illum_rename_submodule(ctx, page, sub) -> None:
    text, ok = QInputDialog.getText(
        ctx.host, "Rename submodule", "Name:", QLineEdit.EchoMode.Normal,
        sub.get("name", ""),
    )
    if not ok:
        return
    name = text.strip()
    if not name:
        return
    if any(
        other is not sub and other.get("name") == name
        for other in page._illum_submodules
    ):
        QMessageBox.information(
            ctx.host, "Rename submodule", "A submodule with this name already exists."
        )
        return
    sub["name"] = name
    _illum_refresh_list(ctx, page)
    _illum_mark_dirty(ctx, page)


def _illum_duplicate_submodule(ctx, page, sub) -> None:
    name = sub.get("name", "Channel") + " copy"
    while any(s.get("name") == name for s in page._illum_submodules):
        name += " copy"
    new_sub = copy.deepcopy(sub)
    new_sub["id"] = uuid4().hex[:8]
    new_sub["name"] = name
    # Keep the recognized pattern (and how it was made): the duplicate starts
    # from the same illumination profile. Saving writes its own mask file
    # under the new id.
    new_sub.pop("mask_file", None)
    new_sub.setdefault("intensity", 1.0)
    page._illum_submodules.append(new_sub)
    page._illum_selected = new_sub["id"]
    _illum_refresh_list(ctx, page)
    _illum_rebuild_right(ctx, page)
    _illum_mark_dirty(ctx, page)


def _illum_drop_mask_file(ctx, task_id, sub) -> None:
    """Delete a submodule's illumination mask file, if any."""
    if ctx.project is None:
        return
    try:
        ctx.project._mask_path(str(task_id), str(sub["id"])).unlink(missing_ok=True)
    except OSError:
        pass


def _illum_delete_submodule(ctx, page, sub) -> None:
    reply = QMessageBox.question(
        ctx.host,
        "Delete submodule",
        f'Delete "{sub.get("name", "")}"?',
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return
    page._illum_submodules.remove(sub)
    _illum_drop_mask_file(ctx, page._illum_task_id, sub)
    if page._illum_selected == sub["id"]:
        page._illum_selected = (
            page._illum_submodules[0]["id"]
            if page._illum_submodules else None
        )
    _illum_refresh_list(ctx, page)
    _illum_rebuild_right(ctx, page)
    _illum_mark_dirty(ctx, page)


def _illum_select_submodule(ctx, page, sub_id) -> None:
    if page._illum_selected == sub_id:
        return
    page._illum_selected = sub_id
    _illum_rebuild_right(ctx, page)


def _illum_reordered(ctx, page) -> None:
    sub_list = page._illum_list
    order = [
        sub_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(sub_list.count())
    ]
    by_id = {sub["id"]: sub for sub in page._illum_submodules}
    reordered = [by_id[sid] for sid in order if sid in by_id]
    if len(reordered) != len(page._illum_submodules):
        return
    if [s["id"] for s in reordered] == [s["id"] for s in page._illum_submodules]:
        return
    page._illum_submodules[:] = reordered
    _illum_refresh_list(ctx, page)
    _illum_render_preview(ctx, page)
    _illum_mark_dirty(ctx, page)


def _illum_rebuild_right(ctx, page) -> None:
    host = page._illum_right_host
    _widgets_panels._clear_settings_layout(ctx, host.layout())
    sub = _illum_selected_sub(ctx, page)
    page._illum_plot = None
    if sub is None:
        hint = QLabel(
            "Add a submodule on the left, then double-click it to edit."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888888;")
        host.layout().addWidget(hint)
        return
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)
    top = QWidget()
    top_lay = QHBoxLayout(top)
    top_lay.setContentsMargins(0, 0, 0, 0)
    top_lay.setSpacing(12)
    top_lay.addWidget(_illum_channel_panel(ctx, page, sub), 1)
    calc_host = QWidget()
    calc_lay = QHBoxLayout(calc_host)
    calc_lay.setContentsMargins(0, 0, 0, 0)
    calc_lay.setSpacing(12)
    calc_lay.addWidget(_illum_tiles_panel(ctx, page, sub), 1)
    calc_lay.addWidget(_illum_correction_panel(ctx, page, sub), 1)
    top_lay.addWidget(calc_host, 2)
    top_lay.addWidget(_illum_mask_panel(ctx, page, sub), 2)
    bottom = QWidget()
    bottom_box = QVBoxLayout(bottom)
    bottom_box.setContentsMargins(0, 0, 0, 0)
    bottom_box.setSpacing(4)
    bottom_lay = QHBoxLayout()
    bottom_lay.setContentsMargins(0, 0, 0, 0)
    bottom_lay.setSpacing(12)
    previews = QVBoxLayout()
    previews.setContentsMargins(0, 0, 0, 0)
    previews.setSpacing(4)
    plots_row = QHBoxLayout()
    plots_row.setContentsMargins(0, 0, 0, 0)
    plots_row.setSpacing(12)
    plots_row.addWidget(_illum_plot_panel(ctx, page, sub, "Before"), 1)
    plots_row.addWidget(_illum_plot_panel(ctx, page, sub, "After"), 1)
    previews.addLayout(plots_row, 1)
    sync_row = QHBoxLayout()
    sync_row.setContentsMargins(0, 0, 0, 0)
    sync_row.addStretch()
    sync_btn = QPushButton("Sync view →")
    sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    sync_btn.setFixedWidth(100)
    sync_btn.clicked.connect(lambda _=False: _illum_sync_views(ctx, page))
    sync_row.addWidget(sync_btn)
    sync_row.addStretch()
    previews.addLayout(sync_row)
    bottom_lay.addLayout(previews, 4)
    bottom_lay.addWidget(_illum_plot_settings(ctx, page, sub), 1)
    bottom_box.addLayout(bottom_lay, 1)
    page._illum_sync_btn = sync_btn
    _illum_update_sync_label(page)
    splitter.addWidget(top)
    splitter.addWidget(bottom)
    splitter.setSizes([320, 520])
    host.layout().addWidget(splitter, 1)
    _illum_render_preview(ctx, page)
    _illum_render_mask(ctx, page, sub)


def _illum_channel_panel(ctx, page, sub) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    editable = not page._illum_locked
    columns = _illum_all_columns(ctx, page._illum_task_id)

    top = QWidget()
    top_lay = QVBoxLayout(top)
    top_lay.setContentsMargins(0, 0, 0, 0)
    top_lay.setSpacing(4)
    top_lay.addWidget(QLabel("<b>Channel &amp; axes</b>"))
    channel_combo = _NoWheelComboBox()
    channel_combo.addItem("", None)
    for name in columns:
        channel_combo.addItem(name, name)
    index = channel_combo.findData(sub.get("channel"))
    channel_combo.setCurrentIndex(index if index >= 0 else 0)
    channel_combo.setEnabled(editable)
    top_lay.addWidget(QLabel("Channel"))
    top_lay.addWidget(channel_combo)
    axes_row = QHBoxLayout()
    x_combo = _NoWheelComboBox()
    y_combo = _NoWheelComboBox()
    for combo, value in ((x_combo, sub.get("x")), (y_combo, sub.get("y"))):
        combo.addItem("", None)
        for name in columns:
            combo.addItem(name, name)
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.setEnabled(editable)
    axes_row.addWidget(QLabel("X"))
    axes_row.addWidget(x_combo, 1)
    axes_row.addWidget(QLabel("Y"))
    axes_row.addWidget(y_combo, 1)
    top_lay.addLayout(axes_row)

    files_host = QWidget()
    files_lay = QVBoxLayout(files_host)
    files_lay.setContentsMargins(0, 0, 0, 0)
    files_lay.setSpacing(4)
    set_btn = QPushButton("Set Files")
    set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_btn.setEnabled(editable and bool(sub.get("channel")))
    set_btn.setToolTip(
        "" if sub.get("channel") else "Select the channel first"
    )
    set_btn.clicked.connect(lambda _=False: _illum_set_files(ctx, page, sub))
    files_lay.addWidget(set_btn)
    files_list = QListWidget()
    files_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: none; }"
    )
    for path in sub.get("files") or []:
        files_list.addItem(QListWidgetItem(_data_naming._comp_file_name(ctx, path, page._illum_task_id)))
    page._illum_files_list = files_list
    files_lay.addWidget(files_list, 1)

    lay.addWidget(top, 1)
    lay.addWidget(files_host, 3)

    def on_channel():
        new_channel = channel_combo.currentData()
        if new_channel == sub.get("channel"):
            return
        files = sub.get("files") or []
        missing = [
            _data_naming._comp_file_name(ctx, path, page._illum_task_id) for path in files
            if new_channel and new_channel not in set(
                _data_columns._columns_for(ctx, path, page._illum_task_id)
            )
        ]
        if missing:
            _compensation_page._comp_show_missing(ctx,
                [(name, [new_channel]) for name in missing]
            )
            channel_combo.blockSignals(True)
            idx = channel_combo.findData(sub.get("channel"))
            channel_combo.setCurrentIndex(idx if idx >= 0 else 0)
            channel_combo.blockSignals(False)
            return
        sub["channel"] = new_channel
        set_btn.setEnabled(editable and bool(new_channel))
        set_btn.setToolTip("" if new_channel else "Select the channel first")
        _illum_reset_plot_defaults(ctx, page, sub)
        rebuild_levels = getattr(page, "_illum_rebuild_levels", None)
        if rebuild_levels is not None:
            rebuild_levels()
        _illum_render_preview(ctx, page)
        _illum_mark_dirty(ctx, page)

    def on_axis(key, combo):
        def handler():
            sub[key] = combo.currentData()
            _illum_reset_plot_defaults(ctx, page, sub)
            rebuild_levels = getattr(page, "_illum_rebuild_levels", None)
            if rebuild_levels is not None:
                rebuild_levels()
            _illum_render_preview(ctx, page)
            _illum_mark_dirty(ctx, page)
        return handler

    channel_combo.currentIndexChanged.connect(lambda _=0: on_channel())
    x_combo.currentIndexChanged.connect(lambda _=0: on_axis("x", x_combo)())
    y_combo.currentIndexChanged.connect(lambda _=0: on_axis("y", y_combo)())
    return panel


def _illum_all_columns(ctx, task_id=None) -> list:
    columns = []
    for rec in _data_files._available_files(ctx, task_id):
        for name in _data_columns._columns_for(ctx, rec.get("parquet"), task_id):
            if name not in columns:
                columns.append(name)
    return columns


def _illum_set_files(ctx, page, sub) -> None:
    channel = sub.get("channel")
    if not channel:
        QMessageBox.information(
            ctx.host, "Set Files", "Select the channel first."
        )
        return
    records = _data_files._available_files(ctx, page._illum_task_id)
    names = [rec.get("name", "") for rec in records]
    current = [
        _data_naming._comp_file_name(ctx, path, page._illum_task_id) for path in (sub.get("files") or [])
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    paths = [
        rec.get("parquet") for rec in records
        if rec.get("name", "") in chosen_set
    ]
    problems = []
    for path in paths:
        columns = set(_data_columns._columns_for(ctx, path, page._illum_task_id))
        if channel not in columns:
            problems.append((_data_naming._comp_file_name(ctx, path, page._illum_task_id), [channel]))
    if problems:
        _compensation_page._comp_show_missing(ctx, problems)
        return
    sub["files"] = paths
    sub["calc_files"] = [
        path for path in (sub.get("calc_files") or []) if path in paths
    ]
    _illum_rebuild_right(ctx, page)
    _illum_mark_dirty(ctx, page)


def _illum_intensity(sub) -> float:
    """The correction strength of one submodule (0 = no correction)."""
    try:
        value = float(sub.get("intensity", 1.0))
    except (TypeError, ValueError):
        value = 1.0
    return max(0.0, min(100.0, value))


def _illum_tiles_panel(ctx, page, sub) -> QWidget:
    """Tile size, grid and files used to recognize the illumination pattern."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Tiles calculation</b>"))
    editable = not page._illum_locked

    sizes = QHBoxLayout()
    sizes.addWidget(QLabel("Tile W"))
    tile_w = _NoWheelSpinBox()
    tile_w.setRange(1, 1_000_000)
    tile_w.setValue(int(sub.get("tile_w", 256)))
    tile_w.setEnabled(editable)
    sizes.addWidget(tile_w)
    sizes.addWidget(QLabel("Tile H"))
    tile_h = _NoWheelSpinBox()
    tile_h.setRange(1, 1_000_000)
    tile_h.setValue(int(sub.get("tile_h", 256)))
    tile_h.setEnabled(editable)
    sizes.addWidget(tile_h)
    sizes.addStretch()
    lay.addLayout(sizes)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Tile size the correction is estimated on. Tiles that would run "
        "past the right or bottom edge are excluded from the calculation."
    ))

    hint = QLabel(_illum_divisibility_hint(ctx, sub))
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #b58900;")
    lay.addWidget(hint)

    bins_row = QHBoxLayout()
    bins_row.addWidget(QLabel("Bin"))
    bin_spin = _NoWheelSpinBox()
    bin_spin.setRange(3, 10)
    bin_spin.setValue(int(sub.get("bin", 5)))
    bin_spin.setEnabled(editable)
    bins_row.addWidget(bin_spin)
    bins_row.addWidget(QLabel("Resolution"))
    res_spin = _NoWheelSpinBox()
    res_spin.setRange(max(3, int(sub.get("bin", 5))), 20)
    res_spin.setValue(max(int(sub.get("bin", 5)), int(sub.get("resolution", 10))))
    res_spin.setEnabled(editable)
    bins_row.addWidget(res_spin)
    bins_row.addStretch()
    lay.addLayout(bins_row)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Bin = tile size of the coarse grid; Resolution = number of bins "
        "across the image. The pattern is fitted on that grid."
    ))

    lay.addWidget(QLabel("Files for illumination pattern recognition"))
    calc_list = QListWidget()
    calc_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: none; }"
    )
    calc_list.setEnabled(editable)
    selected = set(sub.get("calc_files") or [])
    files = sub.get("files") or []
    calc_list.blockSignals(True)
    for path in files:
        item = QListWidgetItem(_data_naming._comp_file_name(ctx, path, page._illum_task_id))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if path in selected
            else Qt.CheckState.Unchecked
        )
        calc_list.addItem(item)
    calc_list.blockSignals(False)
    lay.addWidget(calc_list, 1)
    page._illum_calc_list = calc_list

    calc_row = QHBoxLayout()
    calc_btn = QPushButton("Recognize Illumination Pattern")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(editable and bool(sub.get("channel")))
    calc_btn.clicked.connect(
        lambda _=False: _illum_calc_clicked(ctx, page, sub, calc_btn)
    )
    calc_row.addWidget(calc_btn)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    calc_row.addWidget(status, 1)
    lay.addLayout(calc_row)
    page._illum_calc_btn = calc_btn
    page._illum_calc_status = status

    def on_sizes():
        sub["tile_w"] = int(tile_w.value())
        sub["tile_h"] = int(tile_h.value())
        hint.setText(_illum_divisibility_hint(ctx, sub))
        cleared = False
        if sub.get("mask"):
            sub["mask"] = None
            sub["mask_source"] = None
            _illum_drop_mask_file(ctx, page._illum_task_id, sub)
            status.setText("Parameters changed — re-recognize the pattern.")
            cleared = True
        _illum_mark_dirty(ctx, page)
        if cleared:
            # The correction is gone now; redraw the mask panel and both
            # previews (which also redraws the tile grid).
            _illum_render_mask(ctx, page, sub)
            _illum_render_preview(ctx, page)
        else:
            # Only the overlay changes: skip re-running the correction.
            _illum_update_tile_grid(ctx, page)

    def on_bin(value):
        sub["bin"] = int(value)
        res_spin.setMinimum(int(value))
        if res_spin.value() < int(value):
            res_spin.setValue(int(value))
        sub["resolution"] = int(res_spin.value())
        if sub.get("mask"):
            sub["mask"] = None
            sub["mask_source"] = None
            _illum_drop_mask_file(ctx, page._illum_task_id, sub)
            status.setText("Parameters changed — re-recognize the pattern.")
        _illum_mark_dirty(ctx, page)

    def on_resolution(value):
        sub["resolution"] = int(value)
        if sub.get("mask"):
            sub["mask"] = None
            sub["mask_source"] = None
            _illum_drop_mask_file(ctx, page._illum_task_id, sub)
            status.setText("Parameters changed — re-recognize the pattern.")
        _illum_mark_dirty(ctx, page)

    def on_calc_files(_item=None):
        sub["calc_files"] = [
            calc_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(calc_list.count())
            if calc_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        _illum_mark_dirty(ctx, page)

    tile_w.valueChanged.connect(lambda _=0: on_sizes())
    tile_h.valueChanged.connect(lambda _=0: on_sizes())
    bin_spin.valueChanged.connect(on_bin)
    res_spin.valueChanged.connect(on_resolution)
    calc_list.itemChanged.connect(on_calc_files)
    return panel


def _illum_correction_panel(ctx, page, sub) -> QWidget:
    """Method and strength applied to the recognized illumination pattern."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Correction parameters</b>"))
    editable = not page._illum_locked

    lay.addWidget(QLabel("Correction method"))
    method_combo = _NoWheelComboBox()
    method_combo.addItem("Down (darkest unchanged)", "down")
    method_combo.addItem("Up (brightest unchanged)", "up")
    method_combo.addItem("Median (default)", "median")
    idx = method_combo.findData(sub.get("method", "median"))
    method_combo.setCurrentIndex(idx if idx >= 0 else 2)
    method_combo.setEnabled(editable)
    lay.addWidget(method_combo)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Down keeps the darkest tile, Up the brightest; Median keeps the "
        "median level and is the safe default."
    ))

    lay.addWidget(QLabel("Correction intensity"))
    intensity = _NoWheelDoubleSpinBox()
    intensity.setRange(0.0, 100.0)
    intensity.setDecimals(1)
    intensity.setSingleStep(0.1)
    intensity.setValue(_illum_intensity(sub))
    intensity.setEnabled(editable)
    lay.addWidget(intensity)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Blends the correction: 0 = no correction, 0.5 = half, 1 = full "
        "and 2 = double strength. Press Update to apply; downstream "
        "channels update when the module is saved."
    ))

    update_btn = QPushButton("Update")
    update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    update_btn.setEnabled(editable)
    update_btn.setToolTip(
        "Apply the method and intensity to the illumination correction"
    )
    lay.addWidget(update_btn)
    applied = QLabel("")
    applied.setWordWrap(True)
    applied.setStyleSheet("color: #6b7785; font-size: 8pt;")
    lay.addWidget(applied)
    lay.addStretch(1)

    method_labels = {
        "down": "Down (darkest unchanged)",
        "up": "Up (brightest unchanged)",
        "median": "Median (default)",
    }

    def refresh_applied() -> None:
        method = str(sub.get("method") or "median")
        label = method_labels.get(method, method)
        applied.setText(
            f"In use: {label} · intensity {_illum_intensity(sub):.1f}"
        )

    def refresh_levels():
        rebuild = getattr(page, "_illum_rebuild_levels", None)
        if rebuild is not None:
            rebuild()

    def on_update():
        if page._illum_locked:
            return
        sub["method"] = method_combo.currentData() or "median"
        sub["intensity"] = round(float(intensity.value()), 1)
        refresh_applied()
        _illum_mark_dirty(ctx, page)
        _illum_render_mask(ctx, page, sub)
        _illum_render_preview(ctx, page)
        refresh_levels()

    update_btn.clicked.connect(on_update)
    refresh_applied()
    return panel


def _illum_divisibility_hint(ctx, sub) -> str:
    files = sub.get("files") or []
    tile_w = int(sub.get("tile_w", 256))
    tile_h = int(sub.get("tile_h", 256))
    if not files or tile_w <= 0 or tile_h <= 0:
        return ""
    try:
        geom = _image_geometry(files[0])
    except Exception:  # noqa: BLE001
        return ""
    width = int(round(geom[1] - geom[0] + 1))
    height = int(round(geom[3] - geom[2] + 1))
    messages = []
    if width < tile_w or height < tile_h:
        messages.append(
            f"The image is {width}×{height}, smaller than the tile "
            f"{tile_w}×{tile_h}: no complete tile fits. Reduce the tile size."
        )
    if width % tile_w:
        messages.append(
            f"Width {width} is not divisible by tile width {tile_w}: "
            "the edge strip is excluded from the pattern recognition."
        )
    if height % tile_h:
        messages.append(
            f"Height {height} is not divisible by tile height {tile_h}: "
            "the edge strip is excluded from the pattern recognition."
        )
    return "\n".join(messages)


def _illum_mask_panel(ctx, page, sub) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Illumination pattern</b>"))
    plot = pg.PlotWidget(background=_plot_background())
    plot.getPlotItem().hideAxis("bottom")
    plot.getPlotItem().hideAxis("left")
    plot.getPlotItem().getViewBox().setAspectLocked(True)
    image_item = pg.ImageItem(axisOrder="row-major")
    plot.addItem(image_item)
    lay.addWidget(plot, 1)
    source = QLabel("")
    source.setWordWrap(True)
    source.setStyleSheet("color: #6b7785;")
    lay.addWidget(source)
    page._illum_mask_plot = plot
    page._illum_mask_image = image_item
    page._illum_mask_source = source
    return panel


def _illum_render_mask(ctx, page, sub) -> None:
    import numpy as np

    image_item = getattr(page, "_illum_mask_image", None)
    if image_item is None:
        return
    source = getattr(page, "_illum_mask_source", None)
    mask_data = sub.get("mask")
    if not mask_data:
        image_item.clear()
        if source is not None:
            source.setText("No pattern yet. Click Recognize Illumination Pattern.")
        return
    mask = np.asarray(mask_data, dtype="float64")
    finite = mask[np.isfinite(mask)]
    if finite.size == 0:
        image_item.clear()
        return
    lo = float(finite.min())
    hi = float(finite.max())
    if hi <= lo:
        hi = lo + 1.0
    lut = _figure_colormap("viridis").getLookupTable(0.0, 1.0, 256)
    image_item.setImage(mask, autoLevels=False, levels=(lo, hi), lut=lut)
    image_item.setRect(QRectF(0, 0, mask.shape[1], mask.shape[0]))
    source_params = sub.get("mask_source") or {}
    if source is not None:
        source.setText(
            f"tile {source_params.get('tile_w', sub.get('tile_w'))}"
            f" × {source_params.get('tile_h', sub.get('tile_h'))}"
            f" · bin {source_params.get('bin', sub.get('bin'))}"
            f" · resolution {source_params.get('resolution', sub.get('resolution'))}\n"
            f"channel {sub.get('channel')} · method {sub.get('method')}\n"
            f"pattern range {lo:.4g} – {hi:.4g}"
        )


class _IllumPointerFilter(QObject):
    """Remember which preview plot the pointer was last over."""

    def __init__(self, parent, page, plot) -> None:
        super().__init__(parent)
        self._page = page
        self._plot = plot

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (
            QEvent.Type.Enter,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
        ):
            self._page._illum_sync_source = self._plot
            _illum_update_sync_label(self._page)
        return False


def _illum_update_sync_label(page) -> None:
    """Show which direction the Sync views button will copy."""
    button = getattr(page, "_illum_sync_btn", None)
    if button is None:
        return
    if getattr(page, "_illum_sync_source", None) is getattr(
        page, "_illum_after_plot", None
    ):
        button.setText("Sync view ←")
        button.setToolTip(
            "Copy the zoom and position of the After preview to Before"
        )
    else:
        button.setText("Sync view →")
        button.setToolTip(
            "Copy the zoom and position of the Before preview to After"
        )


def _illum_sync_views(ctx, page) -> None:
    """Copy the active preview's zoom/position to the other one, once.

    A one-shot copy avoids the feedback and repaint problems of a live
    range link (which could crash while wheel-zooming).
    """
    before = getattr(page, "_illum_before_plot", None)
    after = getattr(page, "_illum_after_plot", None)
    if before is None or after is None or before is after:
        return
    source = getattr(page, "_illum_sync_source", None)
    if source not in (before, after):
        source = before
    target = after if source is before else before
    xr, yr = source.getPlotItem().getViewBox().viewRange()
    target.getPlotItem().getViewBox().setRange(
        xRange=xr, yRange=yr, padding=0
    )
    runlog.action(
        "illum.sync_views",
        source="After" if source is after else "Before",
    )
    _illum_update_sync_label(page)


def _illum_plot_panel(ctx, page, sub, title: str) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel(f"<b>{title}</b>"))
    plot = pg.PlotWidget(background=_plot_background())
    plot.showGrid(x=True, y=True, alpha=0.2)
    plot.getPlotItem().setAxisItems({
        "bottom": _make_sci_axis("bottom"),
        "left": _make_sci_axis("left"),
    })
    lay.addWidget(plot, 1)
    # Re-sample the image at the current zoom so magnified views stay sharp.
    lod = getattr(page, "_illum_lod_timer", None)
    if lod is None:
        lod = QTimer(ctx.host)
        lod.setSingleShot(True)
        lod.setInterval(80)
        lod.timeout.connect(lambda: _illum_render_preview(ctx, page))
        page._illum_lod_timer = lod

    def on_range(*_args) -> None:
        import time

        if getattr(page, "_illum_rendering", False):
            return
        if time.monotonic() < getattr(
            page, "_illum_ignore_range_until", 0.0
        ):
            return
        lod.start()

    plot.getPlotItem().getViewBox().sigRangeChanged.connect(on_range)
    pointer = _IllumPointerFilter(plot, page, plot)
    plot.installEventFilter(pointer)
    viewport = plot.viewport()
    if viewport is not None:
        viewport.installEventFilter(pointer)
    if title == "Before":
        page._illum_before_plot = plot
    else:
        page._illum_after_plot = plot
    return panel


def _illum_plot_settings(ctx, page, sub) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Plot settings</b>"))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    # Scoped selectors keep the transparent rule from reaching QToolTip,
    # whose palette would otherwise turn black.
    scroll.setObjectName("illumSettingsScroll")
    scroll.setStyleSheet(
        "QScrollArea#illumSettingsScroll { background: transparent; }"
    )
    host = QWidget()
    host.setObjectName("illumSettingsHost")
    host.setStyleSheet(
        "QWidget#illumSettingsHost { background: transparent; }"
    )
    settings = QVBoxLayout(host)
    settings.setContentsMargins(0, 0, 0, 0)
    settings.setSpacing(6)
    scroll.setWidget(host)
    lay.addWidget(scroll, 1)
    page._illum_settings_layout = settings

    plot_dict = {
        "type": sub.get("plot_type") or "scatter",
        "file": (sub.get("files") or [None])[0],
        "file_name": None,
        "x": sub.get("x"),
        "y": sub.get("y"),
        "color_mode": "parameter",
        "color_param": sub.get("channel"),
        "palette": _default_palette(),
        "show_axes": True,
        "show_axis_names": True,
        "show_indices": True,
        "show_sample_name": True,
        "show_tile_grid": True,
        # Image display levels, shared by the Before and After plots.
        "image_min": sub.get("plot_image_min"),
        "image_max": sub.get("plot_image_max"),
    }
    page._illum_plot = plot_dict
    page._illum_plot_settings_built = True

    records = _data_files._available_files(ctx, page._illum_task_id)
    enabled = not page._illum_locked

    def refresh():
        _illum_render_preview(ctx, page)
        _illum_mark_dirty(ctx, page)

    settings.addWidget(QLabel("Plot type"))
    type_combo = _NoWheelComboBox()
    type_combo.addItem("Scatter", "scatter")
    type_combo.addItem("Image", "image")
    idx = type_combo.findData(plot_dict.get("type"))
    type_combo.setCurrentIndex(idx if idx >= 0 else 0)
    type_combo.setEnabled(enabled)
    settings.addWidget(type_combo)

    settings.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    files = sub.get("files") or []
    for path in files:
        file_combo.addItem(_data_naming._comp_file_name(ctx, path, page._illum_task_id), path)
    idx = file_combo.findData(plot_dict.get("file"))
    file_combo.setCurrentIndex(idx if idx >= 0 else 0)
    file_combo.setEnabled(enabled)
    settings.addWidget(file_combo)
    plot_dict["file_name"] = file_combo.currentText()

    # Image display levels (Min/Max, signal histogram, Auto set). The same
    # bounds drive the Before and After plots, which share `plot_dict`.
    levels_host = QWidget()
    levels_host.setObjectName("illumLevelsHost")
    levels_host.setStyleSheet(
        "QWidget#illumLevelsHost { background: transparent; }"
    )
    levels_lay = QVBoxLayout(levels_host)
    levels_lay.setContentsMargins(0, 0, 0, 0)
    levels_lay.setSpacing(6)
    settings.addWidget(levels_host)

    def clear_levels() -> None:
        while levels_lay.count():
            entry = levels_lay.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.deleteLater()

    def on_levels(lo, hi) -> None:
        sub["plot_image_min"] = lo
        sub["plot_image_max"] = hi
        plot_dict["image_min"] = lo
        plot_dict["image_max"] = hi
        _illum_mark_dirty(ctx, page)
        _illum_render_preview(ctx, page)

    def rebuild_levels() -> None:
        import numpy as np

        clear_levels()
        is_image = plot_dict.get("type") == "image"
        levels_host.setVisible(is_image)
        if not is_image:
            return
        rec = _widgets_plot_renderer._figure_record(ctx, plot_dict.get("file"))
        path = rec.get("parquet") if rec else None
        try:
            index = page._illum_submodules.index(sub)
        except ValueError:
            index = len(page._illum_submodules)
        channel = plot_dict.get("color_param")
        if not path or not channel:
            return
        before_stages = _illum_preview_stages(
            ctx, page, page._illum_submodules[:index]
        )
        after_stages = _illum_preview_stages(
            ctx, page, page._illum_submodules[: index + 1]
        )
        configs = (
            ctx._figure_axis_scale_configs(
                page._illum_task_id, path, channel
            )
            if channel else None
        )
        cache = page._illum_hist_cache
        before_key = _illum_hist_cache_key(
            page, path, channel, "before", index
        )
        after_key = _illum_hist_cache_key(
            page, path, channel, "after", index
        )
        if channel:
            _illum_hist_request(
                ctx, page, before_key, path, channel, before_stages
            )
            _illum_hist_request(
                ctx, page, after_key, path, channel, after_stages
            )
        before_values = cache.get(before_key)
        after_values = cache.get(after_key)
        if before_values is None or after_values is None:
            # The distributions are read in the background: show both
            # sections with a placeholder instead of reading the whole
            # channel on the UI thread.
            for caption in ("Before", "After"):
                cap = QLabel(caption)
                cap.setStyleSheet(
                    "color: #5a6673; font-size: 8pt; font-weight: bold;"
                )
                levels_lay.addWidget(cap)
                note = QLabel("Computing…")
                note.setStyleSheet("color: #6b7785; font-size: 8pt;")
                levels_lay.addWidget(note)
            return
        # One display range shared by the Before and After images, so a
        # correction (including a constant gain from the method) shows up
        # instead of being normalized away by two independent auto ranges.
        if before_values.size and after_values.size:
            before_display = _apply_scale_transforms(before_values, configs)
            after_display = _apply_scale_transforms(after_values, configs)
            lo = min(
                float(np.percentile(before_display, 0.5)),
                float(np.percentile(after_display, 0.5)),
            )
            hi = max(
                float(np.percentile(before_display, 99.5)),
                float(np.percentile(after_display, 99.5)),
            )
            page._illum_shared_levels = (lo, hi) if hi > lo else None
        else:
            page._illum_shared_levels = None
        # Before on top (read-only distribution), then the shared Min/Max
        # controls with the corrected After distribution below them.
        before_caption = QLabel("Before")
        before_caption.setStyleSheet(
            "color: #5a6673; font-size: 8pt; font-weight: bold;"
        )
        levels_lay.addWidget(before_caption)
        _widgets_image_level._image_signal_histogram(
            ctx, levels_lay, channel, page._illum_task_id, path,
            extra_stages=before_stages, configs=configs,
            values=before_values,
        )
        _widgets_image_level._image_level_controls(ctx,
            levels_lay,
            channel,
            sub.get("plot_image_min"),
            sub.get("plot_image_max"),
            enabled,
            on_levels,
            page._illum_task_id,
            path,
            extra_stages=after_stages,
            configs=configs,
            hist_caption="After",
            values=after_values,
        )

    page._illum_rebuild_levels = rebuild_levels

    columns = []
    rec = _widgets_plot_renderer._figure_record(ctx, plot_dict.get("file"))
    if rec is not None:
        columns = _data_columns._columns_for(ctx, rec.get("parquet"), page._illum_task_id)

    settings.addWidget(QLabel("X parameter"))
    x_combo = _widgets_gate_editor_editor._gating_param_combo(ctx, columns, plot_dict.get("x"), enabled)
    settings.addWidget(x_combo)
    settings.addWidget(QLabel("Y parameter"))
    y_combo = _widgets_gate_editor_editor._gating_param_combo(ctx, columns, plot_dict.get("y"), enabled)
    settings.addWidget(y_combo)

    settings.addWidget(QLabel("Color parameter (corrected channel)"))
    color_label = QLabel(str(plot_dict.get("color_param") or "—"))
    color_label.setStyleSheet("color: #37474f; font-weight: bold;")
    settings.addWidget(color_label)
    page._illum_color_label = color_label

    settings.addWidget(QLabel("Palette"))
    palette_combo = _NoWheelComboBox()
    _widgets_colormap._populate_palette_combo(ctx,
        palette_combo, plot_dict.get("palette") or _default_palette()
    )
    palette_combo.setEnabled(enabled)
    settings.addWidget(palette_combo)

    grid_box = QCheckBox("Show tile grid")
    grid_box.setChecked(bool(plot_dict.get("show_tile_grid")))
    grid_box.setEnabled(enabled)
    settings.addWidget(grid_box)
    settings.addStretch()

    def on_type():
        plot_dict["type"] = type_combo.currentData()
        sub["plot_type"] = plot_dict["type"]
        rebuild_levels()
        refresh()

    def on_file():
        plot_dict["file"] = file_combo.currentData()
        plot_dict["file_name"] = file_combo.currentText()
        rebuild_levels()
        refresh()

    def on_param(key, combo):
        def handler(value):
            plot_dict[key] = value or None
            refresh()
        return handler

    def on_palette():
        plot_dict["palette"] = palette_combo.currentData()
        refresh()

    def on_grid(checked):
        plot_dict["show_tile_grid"] = bool(checked)
        # Only toggle the overlay; re-rendering the data would re-run the
        # whole correction pipeline and make the checkbox feel sluggish.
        _illum_update_tile_grid(ctx, page)
        _illum_mark_dirty(ctx, page)

    type_combo.currentIndexChanged.connect(lambda _=0: on_type())
    file_combo.currentIndexChanged.connect(lambda _=0: on_file())
    x_combo.currentTextChanged.connect(on_param("x", x_combo))
    y_combo.currentTextChanged.connect(on_param("y", y_combo))
    palette_combo.currentIndexChanged.connect(lambda _=0: on_palette())
    grid_box.toggled.connect(on_grid)
    rebuild_levels()
    return panel


def _illum_sub_stage(ctx, sub):
    return _illumination_stage_item(sub)


def _illum_preview_stages(ctx, page, subs):
    items = [
        item for item in (_illum_sub_stage(ctx, s) for s in subs)
        if item is not None
    ]
    if not items:
        return None
    return [("illumination", {
        "task_id": page._illum_task_id,
        "submodules": items,
    })]


_ILLUM_HIST_ROWS = 200_000


def _illum_hist_cache_key(page, path, channel, side, index):
    """Cache key for one sampled distribution.

    The parameter version drops every entry when a submodule changes; the
    index keeps two submodules of the same channel apart while nothing is
    dirty.
    """
    return (
        str(path), str(channel), side, int(index),
        int(getattr(page, "_illum_params_version", 0)),
    )


def _illum_hist_source(ctx, page, path, channel, stages):
    """(read path, columns, names) for a sampled histogram, or None."""
    if not path or not channel:
        return None
    needed = _data_columns._pipeline_dependencies(
        list(stages or []), [channel], path
    )
    generated = _illum_generated_column(
        ctx, page._illum_task_id, path, channel
    )
    try:
        import pyarrow.parquet as pq
    except Exception:  # noqa: BLE001
        return None
    if generated is not None:
        # A generated channel lives in its own row-aligned output; sample
        # that file (it carries x/y for image data).
        if generated["column"] != channel:
            return None
        read_path = generated["path"]
        try:
            available = set(pq.ParquetFile(read_path).schema.names)
        except Exception:  # noqa: BLE001
            return None
        if not all(name in available for name in needed):
            return None
        return read_path, sorted(needed), [channel]
    base = set(
        ctx._column_index.get(_data_files._rel_data_key(ctx, path), [])
    )
    if not base:
        try:
            base = set(pq.ParquetFile(path).schema.names)
        except Exception:  # noqa: BLE001
            base = set()
    columns = [name for name in needed if name in base]
    if not all(name in columns for name in needed):
        return None
    return path, columns, [channel]


def _illum_hist_request(ctx, page, key, path, channel, stages) -> bool:
    """Ask the worker for one distribution; False when it is already cached."""
    cache = page._illum_hist_cache
    if key in cache:
        return True
    worker = getattr(page, "_illum_hist_worker", None)
    source = (
        _illum_hist_source(ctx, page, path, channel, stages)
        if worker is not None else None
    )
    if source is not None:
        if key in page._illum_hist_pending:
            return False
        read_path, columns, names = source
        page._illum_hist_pending[key] = names[0]
        worker.request(
            key, read_path, columns, list(stages or []), names, path,
            max_rows=_ILLUM_HIST_ROWS,
        )
        return False
    # Fallback: the values cannot be read from a sample (an unusual stored
    # column); read them the old way, once, and cache the result.
    try:
        values = ctx._figure_channel_values(
            page._illum_task_id, path, channel, extra_stages=stages
        )
    except Exception:  # noqa: BLE001
        values = None
    import numpy as np
    cache[key] = (
        np.asarray(values, dtype="float64")
        if values is not None and values.size
        else np.empty(0, dtype="float64")
    )
    return True


def _illum_hist_poll(ctx, page) -> bool:
    """Collect finished histogram reads; True when a redraw is due."""
    worker = getattr(page, "_illum_hist_worker", None)
    if worker is None:
        return False
    import numpy as np

    changed = False
    for key, values, _error in worker.poll():
        channel = page._illum_hist_pending.pop(key, None)
        page._illum_hist_cache[key] = (
            np.asarray(values.get(channel), dtype="float64")
            if values is not None and channel in values
            else np.empty(0, dtype="float64")
        )
        changed = True
    return changed


def _illum_schedule_preview(ctx, page, delay: int = 40) -> None:
    """Coalesce preview redraws triggered by several UI handlers at once."""
    timer = getattr(page, "_illum_render_timer", None)
    if timer is None:
        timer = QTimer(ctx.host)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: _illum_render_preview(ctx, page))
        page._illum_render_timer = timer
    timer.start(delay)


def _illum_render_preview(ctx, page) -> None:
    """Render both previews without letting their own range changes loop."""
    import time

    page._illum_rendering = True
    try:
        _illum_render_preview_now(ctx, page)
    finally:
        page._illum_rendering = False
        # Ignore the layout/range signals emitted while the render settled;
        # a user wheel event still arrives well after this window.
        page._illum_ignore_range_until = time.monotonic() + 0.05


def _illum_render_preview_now(ctx, page) -> None:
    sub = _illum_selected_sub(ctx, page)
    plot_dict = getattr(page, "_illum_plot", None)
    before = getattr(page, "_illum_before_plot", None)
    after = getattr(page, "_illum_after_plot", None)
    if sub is None or plot_dict is None or before is None or after is None:
        return
    try:
        index = page._illum_submodules.index(sub)
    except ValueError:
        return
    earlier = page._illum_submodules[:index]
    before_stages = _illum_preview_stages(ctx, page, earlier)
    after_stages = _illum_preview_stages(ctx, page, earlier + [sub])
    runlog.action(
        "illum.preview", type=str(plot_dict.get("type")),
        file=str(plot_dict.get("file")), channel=str(plot_dict.get("color_param")),
    )
    for plot_widget in (before, after):
        _illum_clear_tile_grid(ctx, plot_widget)
    # Both previews use one display range so the correction is visible
    # instead of being normalized away by two independent auto ranges.
    shared_levels = None
    if (
        plot_dict.get("type") == "image"
        and plot_dict.get("image_min") is None
        and plot_dict.get("image_max") is None
    ):
        shared_levels = getattr(page, "_illum_shared_levels", None)
    _widgets_plot_renderer._figure_render(ctx,
        plot_dict, before, task_id=page._illum_task_id,
        extra_stages=before_stages, image_levels=shared_levels,
    )
    _widgets_plot_renderer._figure_render(ctx,
        plot_dict, after, task_id=page._illum_task_id,
        extra_stages=after_stages, image_levels=shared_levels,
    )
    if plot_dict.get("show_tile_grid"):
        before._illum_grid_item = _illum_draw_tile_grid(ctx,
            before, plot_dict, sub
        )
        after._illum_grid_item = _illum_draw_tile_grid(ctx,
            after, plot_dict, sub
        )


def _illum_clear_tile_grid(ctx, plot_widget) -> None:
    item = getattr(plot_widget, "_illum_grid_item", None)
    if item is None:
        return
    try:
        plot_widget.removeItem(item)
    except Exception:  # noqa: BLE001
        pass
    plot_widget._illum_grid_item = None


def _illum_draw_tile_grid(ctx, plot_widget, plot_dict, sub):
    import numpy as np

    tile_w = int(sub.get("tile_w", 0) or 0)
    tile_h = int(sub.get("tile_h", 0) or 0)
    if tile_w <= 0 or tile_h <= 0:
        return None
    if plot_dict.get("type") == "image":
        extent = getattr(plot_widget, "_img_extent", None)
        if not extent:
            return None
        xmin, xmax, ymin, ymax = extent
    else:
        xmin = getattr(plot_widget, "_data_xmin", None)
        xmax = getattr(plot_widget, "_data_xmax", None)
        ymin = getattr(plot_widget, "_data_ymin", None)
        ymax = getattr(plot_widget, "_data_ymax", None)
        if None in (xmin, xmax, ymin, ymax):
            return None
    xs = []
    ys = []
    x = xmin
    while x <= xmax:
        xs.extend([x, x, np.nan])
        ys.extend([ymin, ymax, np.nan])
        x += tile_w
        if len(xs) > 20000:
            break
    y = ymin
    while y <= ymax:
        xs.extend([xmin, xmax, np.nan])
        ys.extend([y, y, np.nan])
        y += tile_h
        if len(xs) > 20000:
            break
    if not xs:
        return None
    item = pg.PlotDataItem(
        np.asarray(xs), np.asarray(ys),
        pen=pg.mkPen((120, 140, 170, 140), width=1),
        connect="finite",
    )
    plot_widget.addItem(item)
    return item


def _illum_update_tile_grid(ctx, page) -> None:
    """Add/remove the tile-grid overlay without re-rendering the data."""
    sub = _illum_selected_sub(ctx, page)
    plot_dict = getattr(page, "_illum_plot", None)
    before = getattr(page, "_illum_before_plot", None)
    after = getattr(page, "_illum_after_plot", None)
    if sub is None or plot_dict is None or before is None or after is None:
        return
    for plot_widget in (before, after):
        _illum_clear_tile_grid(ctx, plot_widget)
    if plot_dict.get("show_tile_grid"):
        before._illum_grid_item = _illum_draw_tile_grid(ctx,
            before, plot_dict, sub
        )
        after._illum_grid_item = _illum_draw_tile_grid(ctx,
            after, plot_dict, sub
        )


def _illum_reset_plot_defaults(ctx, page, sub) -> None:
    plot_dict = getattr(page, "_illum_plot", None)
    if plot_dict is None:
        return
    plot_dict["x"] = sub.get("x")
    plot_dict["y"] = sub.get("y")
    plot_dict["color_param"] = sub.get("channel")
    label = getattr(page, "_illum_color_label", None)
    if label is not None:
        label.setText(str(sub.get("channel") or "—"))


def _illum_generated_column(ctx, task_id, path, channel):
    """The stored output that holds a generated channel, or None.

    Unmix channels are not columns of the source parquet: their values live
    in a companion output that is row-aligned to the source, so the tile
    calculation reads the channel from there.
    """
    if not channel or not path:
        return None
    source = channel
    for stage in reversed(_data_columns._column_rename_stages(ctx, task_id)):
        if not _rename_applies(stage, path):
            continue
        reverse = {v: k for k, v in stage["map"].items()}
        source = reverse.get(source, source)
    records = ctx._unmix_applied_columns(task_id, path) or {}
    rec = records.get(source) or records.get(channel)
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None
    return {
        "path": str(rec["parquet"]),
        "column": str(rec.get("channel") or source),
    }


def _illum_calculate(ctx, page, sub, button) -> None:
    channel = sub.get("channel")
    x_name = sub.get("x")
    y_name = sub.get("y")
    if not channel or not x_name or not y_name:
        QMessageBox.information(
            ctx.host,
            "Recognize Illumination Pattern",
            "Select the channel and the X/Y parameters first.",
        )
        return
    files = list(sub.get("calc_files") or [])
    if not files:
        QMessageBox.information(
            ctx.host,
            "Recognize Illumination Pattern",
            "Select at least one file to recognize the pattern from.",
        )
        return
    if int(sub.get("resolution", 10)) < int(sub.get("bin", 5)):
        QMessageBox.information(
            ctx.host,
            "Recognize Illumination Pattern",
            "Resolution must be greater than or equal to bin.",
        )
        return
    try:
        index = page._illum_submodules.index(sub)
    except ValueError:
        return
    earlier = page._illum_submodules[:index]
    stages = _data_columns._map_stages(ctx, page._illum_task_id) or []
    stages = list(stages)
    earlier_items = [
        item for item in (_illum_sub_stage(ctx, s) for s in earlier)
        if item is not None
    ]
    if earlier_items:
        stages.append(("illumination", {
            "task_id": page._illum_task_id,
            "submodules": earlier_items,
        }))
    base_columns = {}
    for path in files:
        base_columns[path] = set(
            ctx._column_index.get(_data_files._rel_data_key(ctx, path), [])
        )
    generated = {}
    for path in files:
        rec = _illum_generated_column(
            ctx, page._illum_task_id, path, channel
        )
        if rec is not None:
            generated[path] = rec
    page._illum_request_key = sub["id"]
    page._illum_calc_running = True
    button.setText("Cancel")
    status = getattr(page, "_illum_calc_status", None)
    if status is not None:
        status.setText(f"Recognizing pattern: 0/{len(files)} files")
    page._illum_worker.request(
        sub["id"], stages, base_columns, files, channel, x_name, y_name,
        int(sub.get("tile_w", 256)), int(sub.get("tile_h", 256)),
        int(sub.get("bin", 5)), int(sub.get("resolution", 10)),
        generated=generated,
    )


def _illum_calc_clicked(ctx, page, sub, button) -> None:
    """Start the calculation, or cancel the one that is running."""
    if getattr(page, "_illum_calc_running", False):
        key = page._illum_request_key
        if key:
            page._illum_worker.cancel(key)
            status = getattr(page, "_illum_calc_status", None)
            if status is not None:
                status.setText("Cancelling…")
        return
    _illum_calculate(ctx, page, sub, button)


def _illum_on_poll(ctx, page) -> None:
    worker = getattr(page, "_illum_worker", None)
    if worker is None:
        return
    if _illum_hist_poll(ctx, page):
        previous = getattr(page, "_illum_shared_levels", None)
        rebuild_levels = getattr(page, "_illum_rebuild_levels", None)
        if rebuild_levels is not None:
            rebuild_levels()
        if getattr(page, "_illum_shared_levels", None) != previous:
            # The shared display range moved: redraw both previews on it.
            _illum_schedule_preview(ctx, page)
    status = getattr(page, "_illum_calc_status", None)
    for message in worker.poll():
        if message and message[0] == "progress":
            _, key, done, total, name = message
            if key == page._illum_request_key and status is not None:
                status.setText(
                    f"Recognizing pattern: {done}/{total} files · {name}"
                )
            continue
        if message and message[0] == "cancelled":
            _, key, _ = message
            if key != page._illum_request_key:
                continue
            page._illum_request_key = None
            page._illum_calc_running = False
            button = getattr(page, "_illum_calc_btn", None)
            if button is not None:
                button.setText("Recognize Illumination Pattern")
                button.setEnabled(not page._illum_locked)
            if status is not None:
                status.setText("Cancelled; the previous pattern was kept.")
            continue
        key, result, error = message
        if key != page._illum_request_key:
            continue
        page._illum_request_key = None
        page._illum_calc_running = False
        button = getattr(page, "_illum_calc_btn", None)
        if button is not None:
            button.setText("Recognize Illumination Pattern")
            button.setEnabled(not page._illum_locked)
        if error:
            if status is not None:
                status.setText(error)
            QMessageBox.warning(ctx.host, "Recognize Illumination Pattern", error)
            continue
        sub = next(
            (s for s in page._illum_submodules if s["id"] == key), None
        )
        if sub is None:
            continue
        sub["mask"] = result["mask"]
        sub["mask_source"] = {
            "tile_w": result["tile_w"],
            "tile_h": result["tile_h"],
            "bin": result["bin"],
            "resolution": result["resolution"],
            "files": result["files"],
            "channel": sub.get("channel"),
            "x": sub.get("x"),
            "y": sub.get("y"),
        }
        for entry in result.get("timing") or []:
            runlog.action(
                "illum.recognize",
                file=str(entry.get("file")),
                rows=int(entry.get("rows") or 0),
                seconds=float(entry.get("seconds") or 0.0),
            )
        if status is not None:
            count = len(result.get("timing") or [])
            status.setText(
                f"Mask calculated from {count} file(s) in "
                f"{float(result.get('seconds') or 0.0):.1f} s."
            )
        _illum_render_mask(ctx, page, sub)
        _illum_refresh_list(ctx, page)
        # Bump the parameter version before the histograms are rebuilt so the
        # After distribution is re-read from the new mask, then redraw.
        _illum_mark_dirty(ctx, page)
        if sub.get("id") == page._illum_selected:
            rebuild_levels = getattr(page, "_illum_rebuild_levels", None)
            if rebuild_levels is not None:
                rebuild_levels()
            _illum_schedule_preview(ctx, page)


def _illum_mark_dirty(ctx, page) -> None:
    page._illum_dirty = True
    # Histogram samples depend on the parameters and on the masks; bumping
    # the version drops the cached distributions of every submodule.
    page._illum_params_version = getattr(page, "_illum_params_version", 0) + 1
    cache = getattr(page, "_illum_hist_cache", None)
    if cache is not None and len(cache) > 16:
        cache.clear()
    _illum_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _illum_save_now(ctx, page) -> None:
    page._illum_task_def["params"] = page._illum_params
    if ctx.project is not None:
        ctx.project.save()
    page._illum_snapshot = copy.deepcopy(page._illum_params)
    page._illum_dirty = False
    _illum_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Illumination correction saved")
    ctx._module_saved(page)


def _illum_update_save_btn(ctx, page) -> None:
    page._illum_save_btn.setEnabled(page._illum_dirty and not page._illum_locked)


def _illum_close(ctx, page) -> None:
    if page._illum_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Illumination Correction",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._illum_task_def["params"] = page._illum_snapshot
    for name in (
        "_illum_poll_timer", "_illum_order_timer", "_illum_lod_timer",
        "_illum_render_timer",
    ):
        timer = getattr(page, name, None)
        if timer is not None:
            timer.stop()
    for name in ("_illum_worker", "_illum_hist_worker"):
        worker = getattr(page, name, None)
        if worker is not None:
            worker.stop()
    ctx._close_module_page(page)
