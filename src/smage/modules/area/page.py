"""Area module: per-cell / per-image region area and histograms."""
from __future__ import annotations

from PySide6.QtCore import (
    QRectF,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.pipeline import (
    _evaluate_stages,
    _pipeline_dependencies,
    _pipeline_touches,
)
from smage.core.settings import _default_palette
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
)
from smage.widgets.resources import _lock_pixmap
import base64
import copy
import hashlib
import json
import math
import queue
import threading
import time
from functools import partial
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import sampling as _data_sampling
from smage.widgets import colormap as _widgets_colormap
from smage.widgets import image_level as _widgets_image_level
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import renderer as _widgets_plot_renderer


_AREA_LOW_DEFAULT = 100.0

_AREA_HIGH_DEFAULT = 10000.0

_AREA_DENSITY_DEFAULT = 10.0

class _AreaCancelled(Exception):
    """Raised inside a running Area job when its parameters changed."""


def _build_area_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Area page: files/batch (1) | per-file table (2) | plot (1)."""
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
    close_btn.clicked.connect(lambda _=False: _area_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("files", [])
    params.setdefault("apply", [])
    params.setdefault("rows", {})
    params.setdefault("results", {})
    params.setdefault("grid", _AREA_GRID_DEFAULT)
    params.setdefault("plot", {
        # "auto" picks Image for image files and Scatter for table files.
        "type": "auto",
        "palette": _default_palette(),
        "point_size": 4.0,
    })
    params.setdefault("selected", None)

    page._area_task_def = task_def
    page._area_task_id = task_def.get("id")
    page._area_params = params
    page._area_locked = locked
    page._area_save_btn = save_btn
    page._area_dirty = False
    page._area_snapshot = copy.deepcopy(params)
    page._area_paths = []
    page._area_status = {}
    page._area_latest = {}
    page._area_counts = {}
    page._area_cancel_events = {}
    page._area_job_queue = None
    page._area_thread = None
    page._area_timer = None
    page._area_updating = False
    page._area_closing = False
    save_btn.clicked.connect(lambda _=False: _area_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_area_left_panel(ctx, page))
    splitter.addWidget(_area_center_panel(ctx, page))
    splitter.addWidget(_area_right_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 2)
    splitter.setStretchFactor(2, 1)
    splitter.setSizes([300, 560, 340])
    row.addWidget(splitter, 1)
    layout.addWidget(content, 1)

    _area_refresh(ctx, page)
    return page


def _area_left_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Files</b>"))
    pick = QPushButton("Select files…")
    pick.setCursor(Qt.CursorShape.PointingHandCursor)
    pick.clicked.connect(lambda _=False: _area_select_files(ctx, page))
    lay.addWidget(pick)

    files = QListWidget()
    files.setAlternatingRowColors(True)
    files.setToolTip("Checked files receive the batch settings below.")
    files.itemChanged.connect(lambda item: _area_on_file_toggle(ctx, page, item))
    lay.addWidget(files, 1)
    page._area_file_list = files

    summary = QLabel("No files selected")
    summary.setStyleSheet("color: #5f6368;")
    summary.setWordWrap(True)
    lay.addWidget(summary)
    page._area_summary = summary

    lay.addWidget(QLabel("<b>Batch settings</b>"))
    hint = QLabel("Only columns shared by the checked files are listed.")
    hint.setStyleSheet("color: gray;")
    hint.setWordWrap(True)
    lay.addWidget(hint)
    page._area_batch_hint = hint

    box = QGroupBox()
    grid = QGridLayout(box)
    grid.setContentsMargins(8, 8, 8, 8)
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(4)

    def _combo(key):
        combo = _NoWheelComboBox()
        combo.setMinimumContentsLength(6)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        setattr(page, f"_area_batch_{key}", combo)
        return combo

    def _spin(key, value, lo, hi, decimals, step):
        spin = _NoWheelDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setValue(float(value))
        setattr(page, f"_area_batch_{key}", spin)
        return spin

    grid.addWidget(QLabel("X"), 0, 0)
    grid.addWidget(_combo("x"), 0, 1)
    grid.addWidget(QLabel("Y"), 1, 0)
    grid.addWidget(_combo("y"), 1, 1)
    grid.addWidget(QLabel("Signal"), 2, 0)
    grid.addWidget(_combo("signal"), 2, 1)
    grid.addWidget(QLabel("Low"), 3, 0)
    grid.addWidget(_spin("low", _AREA_LOW_DEFAULT, -1e12, 1e12, 4, 10.0), 3, 1)
    grid.addWidget(QLabel("High"), 4, 0)
    grid.addWidget(_spin("high", _AREA_HIGH_DEFAULT, -1e12, 1e12, 4, 100.0), 4, 1)
    grid.addWidget(QLabel("Density >"), 5, 0)
    grid.addWidget(_spin("density", _AREA_DENSITY_DEFAULT, 0.0, 1e9, 2, 1.0), 5, 1)

    apply_btn = QPushButton("Apply to checked files")
    apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    apply_btn.clicked.connect(lambda _=False: _area_apply_batch(ctx, page))
    grid.addWidget(apply_btn, 6, 0, 1, 2)
    page._area_batch_apply = apply_btn
    lay.addWidget(box)
    return panel


def _area_center_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    top = QHBoxLayout()
    top.addWidget(QLabel("<b>Per-file parameters</b>"))
    top.addStretch()
    top.addWidget(QLabel("Grid cells (longer axis)"))
    grid_spin = QSpinBox()
    grid_spin.setRange(20, 2000)
    grid_spin.setSingleStep(10)
    grid_spin.setValue(int(page._area_params.get("grid") or _AREA_GRID_DEFAULT))
    grid_spin.setToolTip(
        "Density grid used for every file. Finer grids give a more precise "
        "area but make the density threshold stricter and the calculation "
        "slower on large images."
    )
    grid_spin.valueChanged.connect(lambda value: _area_on_grid(ctx, page, value))
    top.addWidget(grid_spin)
    page._area_grid_spin = grid_spin
    lay.addLayout(top)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Density grid used for every file. Finer = more precise area, but "
        "each cell then holds fewer points — lower the density threshold "
        "when you raise this."
    ))

    table = QTableWidget(0, 9)
    table.setHorizontalHeaderLabels([
        "File", "X", "Y", "Signal", "Low", "High", "Density", "Area", "Status",
    ])
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for col in range(1, 9):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
    table.setColumnWidth(1, 90)
    table.setColumnWidth(2, 90)
    table.setColumnWidth(3, 110)
    table.setColumnWidth(4, 80)
    table.setColumnWidth(5, 80)
    table.setColumnWidth(6, 70)
    table.setColumnWidth(7, 110)
    table.setColumnWidth(8, 120)
    table.currentCellChanged.connect(
        lambda row, *_a: _area_on_row_selected(ctx, page, row)
    )
    page._area_table = table
    lay.addWidget(table, 1)

    footer = QHBoxLayout()
    copy_btn = QPushButton("Copy")
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.setToolTip("Copy the table to the clipboard (tab-separated)")
    copy_btn.clicked.connect(lambda _=False: _area_copy(ctx, page))
    footer.addWidget(copy_btn)
    footer.addStretch()
    progress = QLabel("")
    progress.setStyleSheet("color: #5f6368;")
    footer.addWidget(progress)
    page._area_progress = progress
    lay.addLayout(footer)
    return panel


def _area_right_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    plot_panel = _widgets_panels._build_panel_frame(ctx)
    pl = QVBoxLayout(plot_panel)
    pl.setContentsMargins(10, 10, 10, 10)
    pl.setSpacing(6)
    top = QHBoxLayout()
    top.addWidget(QLabel("<b>Preview</b>"))
    top.addStretch()
    type_combo = _NoWheelComboBox()
    type_combo.addItem("Auto", "auto")
    type_combo.addItem("Scatter", "scatter")
    type_combo.addItem("Image", "image")
    type_combo.setToolTip(
        "Auto shows image files as images and table files as scatter plots."
    )
    type_combo.currentIndexChanged.connect(
        lambda _=0: _area_on_plot_type(ctx, page)
    )
    top.addWidget(type_combo)
    pl.addLayout(top)
    pl.addWidget(_widgets_panels._param_hint(ctx,
        "Auto shows image files as images and table files as scatter plots."
    ))

    plot_widget = _widgets_plot_renderer._figure_make_plot_widget(ctx, {"type": "scatter"})
    plot_widget.setMinimumHeight(240)
    pl.addWidget(plot_widget, 1)
    page._area_plot = plot_widget
    page._area_type_combo = type_combo
    lay.addWidget(plot_panel, 1)

    settings = _widgets_panels._build_panel_frame(ctx)
    sl = QVBoxLayout(settings)
    sl.setContentsMargins(10, 10, 10, 10)
    sl.setSpacing(6)
    sl.addWidget(QLabel("<b>Plot settings</b>"))

    row = QHBoxLayout()
    row.addWidget(QLabel("Palette"))
    palette = _NoWheelComboBox()
    _widgets_colormap._populate_palette_combo(ctx,
        palette,
        (page._area_params.get("plot") or {}).get("palette") or _default_palette(),
    )
    palette.currentIndexChanged.connect(
        lambda _=0: _area_on_plot_param(ctx, page)
    )
    row.addWidget(palette)
    sl.addLayout(row)
    sl.addWidget(_widgets_panels._param_hint(ctx,
        "Colour map used for the signal variable."
    ))
    page._area_palette = palette

    row = QHBoxLayout()
    row.addWidget(QLabel("Point size"))
    size = _NoWheelDoubleSpinBox()
    size.setRange(1.0, 20.0)
    size.setSingleStep(0.5)
    size.setDecimals(1)
    size.valueChanged.connect(lambda _=0.0: _area_on_plot_param(ctx, page))
    row.addWidget(size)
    row.addStretch()
    sl.addLayout(row)
    sl.addWidget(_widgets_panels._param_hint(ctx,
        "Scatter dot size; ignored in the Image view."
    ))
    page._area_point_size = size

    # Image display levels (Min/Max, histogram, Auto set, Reset).
    levels_host = QWidget()
    levels_host.setStyleSheet("background: transparent;")
    levels_lay = QVBoxLayout(levels_host)
    levels_lay.setContentsMargins(0, 0, 0, 0)
    levels_lay.setSpacing(6)
    sl.addWidget(levels_host)
    page._area_levels_host = levels_host
    page._area_levels_lay = levels_lay
    page._area_levels_sig = None

    note = QLabel("Amber cells are the regions counted as tissue (density above the threshold).")
    note.setStyleSheet("color: gray;")
    note.setWordWrap(True)
    sl.addWidget(note)
    lay.addWidget(settings)
    return panel


def _area_select_files(ctx, page) -> None:
    params = page._area_params
    records = _data_files._available_files(ctx, page._area_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, p, page._area_task_id)
        for p in params.get("files") or []
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    files = [by_name[n] for n in chosen if n in by_name]
    params["files"] = files
    apply_list = [p for p in params.get("apply") or [] if p in files]
    params["apply"] = apply_list or list(files)
    if params.get("selected") not in files:
        params["selected"] = files[0] if files else None
    _area_refresh(ctx, page)
    _area_mark_dirty(ctx, page)


def _area_refresh(ctx, page) -> None:
    """Rebuild the file list, the batch pickers and the table."""
    params = page._area_params
    files = [p for p in params.get("files") or []]
    apply_list = [p for p in params.get("apply") or [] if p in files]
    if not apply_list and files:
        apply_list = list(files)
    params["apply"] = apply_list
    if params.get("selected") not in files:
        params["selected"] = files[0] if files else None

    lst = page._area_file_list
    lst.blockSignals(True)
    lst.clear()
    for path in files:
        item = QListWidgetItem(_data_naming._comp_file_name(ctx, path, page._area_task_id))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if path in apply_list else Qt.CheckState.Unchecked
        )
        lst.addItem(item)
    lst.blockSignals(False)

    _area_update_summary(ctx, page)
    _area_rebuild_batch_combos(ctx, page)
    _area_rebuild_table(ctx, page)
    _area_sync_plot_settings(ctx, page)
    _area_render_plot(ctx, page)
    for path in files:
        _area_enqueue(ctx, page, path)
    _area_update_progress(ctx, page)


def _area_update_summary(ctx, page) -> None:
    params = page._area_params
    files = params.get("files") or []
    checked = [p for p in files if p in set(params.get("apply") or [])]
    page._area_summary.setText(
        f"{len(files)} file(s) · {len(checked)} checked for Apply"
        if files else "No files selected"
    )


def _area_on_file_toggle(ctx, page, item) -> None:
    path = item.data(Qt.ItemDataRole.UserRole)
    if not path:
        return
    apply_list = page._area_params.setdefault("apply", [])
    checked = item.checkState() == Qt.CheckState.Checked
    if checked and path not in apply_list:
        apply_list.append(path)
    elif not checked and path in apply_list:
        apply_list.remove(path)
    _area_update_summary(ctx, page)
    _area_rebuild_batch_combos(ctx, page)


def _area_batch_common_columns(ctx, page) -> list:
    paths = [
        p for p in page._area_params.get("files") or []
        if p in set(page._area_params.get("apply") or [])
    ]
    if not paths:
        return []
    common = set(_data_columns._columns_for(ctx, paths[0], page._area_task_id))
    for path in paths[1:]:
        common &= set(_data_columns._columns_for(ctx, path, page._area_task_id))
    return sorted(common)


def _area_rebuild_batch_combos(ctx, page) -> None:
    options = _area_batch_common_columns(ctx, page)
    for key in ("x", "y", "signal"):
        combo = getattr(page, f"_area_batch_{key}", None)
        if combo is None:
            continue
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        if key == "signal":
            combo.addItem("(none)", "")
        for name in options:
            combo.addItem(str(name), str(name))
        idx = combo.findData(str(current or ""))
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)
    checked = bool(page._area_params.get("apply") or [])
    page._area_batch_apply.setEnabled(bool(options) and checked)
    if not checked:
        page._area_batch_hint.setText(
            "Check at least one file above to batch-edit its parameters."
        )
    elif not options:
        page._area_batch_hint.setText(
            "The checked files have no columns in common."
        )
    else:
        page._area_batch_hint.setText(
            f"Only columns shared by the checked files are listed ({len(options)})."
        )


def _area_apply_batch(ctx, page) -> None:
    paths = [
        p for p in page._area_params.get("files") or []
        if p in set(page._area_params.get("apply") or [])
    ]
    if not paths:
        return
    cfg = {
        "x": page._area_batch_x.currentData() or "",
        "y": page._area_batch_y.currentData() or "",
        "signal": page._area_batch_signal.currentData() or "",
        "low": float(page._area_batch_low.value()),
        "high": float(page._area_batch_high.value()),
        "density": float(page._area_batch_density.value()),
    }
    rows = page._area_params.setdefault("rows", {})
    for path in paths:
        row_cfg = _area_row_cfg(ctx, page, path)
        row_cfg.update(cfg)
        _area_sync_row(ctx, page, path)
        _area_enqueue(ctx, page, path)
    if page._area_params.get("selected") in paths:
        _area_render_plot(ctx, page)
    _area_mark_dirty(ctx, page)


def _area_default_cfg(ctx, page, path) -> dict:
    columns = list(_data_columns._columns_for(ctx, path, page._area_task_id))
    lower = {c.lower(): c for c in columns}
    x_name = lower.get("x") or (columns[0] if columns else "")
    y_name = lower.get("y") or next(
        (c for c in columns if c != x_name), ""
    )
    signal = next((c for c in columns if c not in (x_name, y_name)), "")
    return {
        "x": x_name,
        "y": y_name,
        "signal": signal,
        "low": _AREA_LOW_DEFAULT,
        "high": _AREA_HIGH_DEFAULT,
        "density": _AREA_DENSITY_DEFAULT,
    }


def _area_row_cfg(ctx, page, path) -> dict:
    rows = page._area_params.setdefault("rows", {})
    cfg = rows.get(path)
    defaults = _area_default_cfg(ctx, page, path)
    if not isinstance(cfg, dict):
        cfg = defaults
        rows[path] = cfg
    else:
        for key, value in defaults.items():
            cfg.setdefault(key, value)
    return cfg


def _area_row_index(ctx, page, path):
    try:
        return page._area_paths.index(path)
    except ValueError:
        return None


def _area_rebuild_table(ctx, page) -> None:
    table = page._area_table
    paths = list(page._area_params.get("files") or [])
    page._area_paths = paths
    page._area_updating = True
    table.blockSignals(True)
    table.setRowCount(0)
    table.setRowCount(len(paths))
    for row, path in enumerate(paths):
        cfg = _area_row_cfg(ctx, page, path)
        name_item = QTableWidgetItem(_data_naming._comp_file_name(ctx, path, page._area_task_id))
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        name_item.setToolTip(path)
        table.setItem(row, 0, name_item)

        columns = list(_data_columns._columns_for(ctx, path, page._area_task_id))
        for col, key in ((1, "x"), (2, "y"), (3, "signal")):
            combo = _NoWheelComboBox()
            combo.setMinimumContentsLength(6)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            if key == "signal":
                combo.addItem("(none)", "")
            for name in columns:
                combo.addItem(str(name), str(name))
            idx = combo.findData(str(cfg.get(key) or ""))
            if idx < 0 and cfg.get(key):
                combo.addItem(str(cfg[key]), str(cfg[key]))
                idx = combo.count() - 1
            combo.setCurrentIndex(max(0, idx))
            combo.currentIndexChanged.connect(
                lambda _=0, p=path, k=key, c=combo: _area_on_row_param(ctx,
                    page, p, k, c.currentData()
                )
            )
            table.setCellWidget(row, col, combo)

        for col, key, lo, hi, step in (
            (4, "low", -1e12, 1e12, 10.0),
            (5, "high", -1e12, 1e12, 100.0),
            (6, "density", 0.0, 1e9, 1.0),
        ):
            spin = _NoWheelDoubleSpinBox()
            spin.setDecimals(2 if key == "density" else 4)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setValue(float(cfg.get(key) or 0.0))
            spin.valueChanged.connect(
                lambda value, p=path, k=key: _area_on_row_param(ctx, page, p, k, value)
            )
            table.setCellWidget(row, col, spin)

        area_item = QTableWidgetItem("")
        area_item.setFlags(area_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        area_item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.setItem(row, 7, area_item)
        status_item = QTableWidgetItem("")
        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, 8, status_item)
    table.blockSignals(False)
    page._area_updating = False

    for row, path in enumerate(paths):
        _area_update_area_cell(ctx, page, path)
        text, color = page._area_status.get(path, ("", "#5f6368"))
        item = table.item(row, 8)
        if item is not None and text:
            item.setText(text)
            item.setForeground(QColor(color))

    selected = page._area_params.get("selected")
    row = _area_row_index(ctx, page, selected) if selected else None
    if row is not None:
        table.blockSignals(True)
        table.setCurrentCell(row, 0)
        table.selectRow(row)
        table.blockSignals(False)


def _area_sync_row(ctx, page, path) -> None:
    """Push a file's stored parameters into its table row widgets."""
    row = _area_row_index(ctx, page, path)
    if row is None:
        return
    cfg = _area_row_cfg(ctx, page, path)
    table = page._area_table
    previous = page._area_updating
    page._area_updating = True
    try:
        for col, key in ((1, "x"), (2, "y"), (3, "signal")):
            combo = table.cellWidget(row, col)
            if combo is None:
                continue
            combo.blockSignals(True)
            idx = combo.findData(str(cfg.get(key) or ""))
            if idx < 0 and cfg.get(key):
                combo.addItem(str(cfg[key]), str(cfg[key]))
                idx = combo.count() - 1
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)
        for col, key in ((4, "low"), (5, "high"), (6, "density")):
            spin = table.cellWidget(row, col)
            if spin is None:
                continue
            spin.blockSignals(True)
            spin.setValue(float(cfg.get(key) or 0.0))
            spin.blockSignals(False)
    finally:
        page._area_updating = previous


def _area_on_row_param(ctx, page, path, key, value) -> None:
    if getattr(page, "_area_updating", False):
        return
    cfg = _area_row_cfg(ctx, page, path)
    if key in ("x", "y", "signal"):
        value = "" if value is None else str(value)
    else:
        value = float(value)
    if cfg.get(key) == value:
        return
    cfg[key] = value
    page._area_params["selected"] = path
    row = _area_row_index(ctx, page, path)
    if row is not None:
        table = page._area_table
        table.blockSignals(True)
        table.setCurrentCell(row, 0)
        table.selectRow(row)
        table.blockSignals(False)
    _area_enqueue(ctx, page, path)
    _area_render_plot(ctx, page)
    _area_mark_dirty(ctx, page)


def _area_on_row_selected(ctx, page, row) -> None:
    if row is None or row < 0 or row >= len(page._area_paths):
        return
    path = page._area_paths[row]
    if page._area_params.get("selected") == path:
        return
    page._area_params["selected"] = path
    _area_render_plot(ctx, page)


def _area_on_grid(ctx, page, value) -> None:
    value = max(2, int(value))
    if int(page._area_params.get("grid") or 0) == value:
        return
    page._area_params["grid"] = value
    # Every area depends on the grid, and the cached counts are grid-sized.
    page._area_params["results"] = {}
    page._area_counts.clear()
    for path in page._area_params.get("files") or []:
        _area_update_area_cell(ctx, page, path)
        _area_enqueue(ctx, page, path)
    _area_mark_dirty(ctx, page)


def _area_format(ctx, value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    return f"{number:,.6g}"


def _area_update_area_cell(ctx, page, path) -> None:
    row = _area_row_index(ctx, page, path)
    if row is None:
        return
    item = page._area_table.item(row, 7)
    if item is None:
        return
    cfg = _area_row_cfg(ctx, page, path)
    result = (page._area_params.get("results") or {}).get(path)
    if (
        isinstance(result, dict)
        and result.get("area") is not None
        and result.get("sig") == _area_signature(ctx, page, path, cfg)
    ):
        item.setText(_area_format(ctx, result["area"]))
        item.setToolTip(
            f"{int(result.get('cells') or 0)} cell(s) × "
            f"{_area_format(ctx, result.get('cell'))}² "
            f"(grid {result.get('nx')}×{result.get('ny')}, "
            f"{int(result.get('points') or 0)} point(s) in the signal range)"
        )
    else:
        item.setText("—")
        item.setToolTip("")


def _area_set_status(ctx, page, path, text, color) -> None:
    page._area_status[path] = (text, color)
    row = _area_row_index(ctx, page, path)
    if row is None:
        return
    item = page._area_table.item(row, 8)
    if item is not None:
        item.setText(text)
        item.setForeground(QColor(color))


def _area_update_progress(ctx, page) -> None:
    pending = sum(
        1 for text, _color in page._area_status.values()
        if text.startswith("Queued") or text.startswith("Computing")
    )
    label = getattr(page, "_area_progress", None)
    if label is None:
        return
    label.setText(f"{pending} file(s) pending" if pending else "")


def _area_signature(ctx, page, path, cfg, include_density=True) -> str:
    """Change signature for one file's area result."""
    try:
        st = Path(path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "grid": int(page._area_params.get("grid") or _AREA_GRID_DEFAULT),
        "x": cfg.get("x"),
        "y": cfg.get("y"),
        "signal": cfg.get("signal"),
        "low": float(cfg.get("low") or 0.0),
        "high": float(cfg.get("high") or 0.0),
        "source": str(path),
        "stat": stat,
        "stages": _data_columns._map_stage_token(ctx, page._area_task_id),
        "columns": sorted(_data_columns._columns_for(ctx, path, page._area_task_id)),
    }
    if include_density:
        payload["density"] = float(cfg.get("density") or 0.0)
    sample = _data_sampling._subsample_sig(ctx, page._area_task_id, path)
    if sample:
        payload["sample"] = sample
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _area_start_worker(ctx, page) -> None:
    if getattr(page, "_area_job_queue", None) is not None:
        return
    jobs = queue.Queue()
    results = queue.Queue()
    page._area_job_queue = jobs
    page._area_result_queue = results
    page._area_thread = threading.Thread(
        target=partial(_area_run, ctx), args=(page, jobs, results), daemon=True
    )
    page._area_thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(150)
    timer.timeout.connect(lambda: _area_poll(ctx, page))
    timer.start()
    page._area_timer = timer


def _area_enqueue(ctx, page, path) -> None:
    """Queue a row for calculation when its stored result is out of date."""
    if getattr(page, "_area_closing", False):
        return
    cfg = _area_row_cfg(ctx, page, path)
    sig = _area_signature(ctx, page, path, cfg)
    page._area_latest[path] = sig
    results = page._area_params.setdefault("results", {})
    result = results.get(path)
    if isinstance(result, dict) and result.get("sig") == sig:
        _area_set_status(ctx, page, path, "Done", "#2e7d32")
        _area_update_area_cell(ctx, page, path)
        return
    if not (cfg.get("x") and cfg.get("y") and cfg.get("signal")):
        _area_set_status(ctx, page, path, "Set parameters", "#808080")
        _area_update_area_cell(ctx, page, path)
        return

    _area_start_worker(ctx, page)
    events = page._area_cancel_events
    previous = events.get(path)
    if previous is not None:
        previous.set()
    event = threading.Event()
    events[path] = event
    page._area_job_queue.put({
        "path": path,
        "cfg": dict(cfg),
        "sig": sig,
        "cancel": event,
    })
    _area_set_status(ctx, page, path, "Queued", "#b58900")
    _area_update_progress(ctx, page)


def _area_run(ctx, page, jobs, results) -> None:
    """Worker loop: calculate one queued file at a time."""
    while True:
        job = jobs.get()
        if job is None:
            return
        if job["cancel"].is_set():
            continue
        path = job["path"]
        sig = job["sig"]
        try:
            result = _area_compute(ctx, page, job, results)
        except _AreaCancelled:
            continue
        except Exception as exc:  # noqa: BLE001
            results.put(("error", path, sig, str(exc)))
            continue
        results.put(("done", path, sig, result))


def _area_compute(ctx, page, job, q):
    """Turn a job into an area result, reusing cached density counts."""
    import numpy as np

    path = job["path"]
    cfg = job["cfg"]
    base_sig = _area_signature(ctx, page, path, cfg, include_density=False)
    cached = page._area_counts.get(path)
    if isinstance(cached, dict) and cached.get("sig") == base_sig:
        hist = cached
    else:
        hist = _area_histogram(ctx, page, job, q)
        if job["cancel"].is_set():
            raise _AreaCancelled()
        hist["sig"] = base_sig
        page._area_counts[path] = hist
        while len(page._area_counts) > 24:
            page._area_counts.pop(next(iter(page._area_counts)))
    counts = hist["counts"]
    threshold = float(cfg.get("density") or 0.0)
    qual = counts > threshold
    cells = int(qual.sum())
    cell = float(hist["cell"])
    return {
        "sig": job["sig"],
        "area": float(cells) * cell * cell,
        "cells": cells,
        "nx": int(hist["nx"]),
        "ny": int(hist["ny"]),
        "cell": cell,
        "xmin": float(hist["xmin"]),
        "ymin": float(hist["ymin"]),
        "rows": int(hist["rows"]),
        "points": int(hist.get("points") or 0),
        "seconds": round(float(hist.get("seconds") or 0.0), 2),
        "mask": base64.b64encode(np.packbits(qual.ravel())).decode("ascii"),
    }


def _area_histogram(ctx, page, job, q):
    """Count the points of one file on the density grid (streaming)."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq

    path = job["path"]
    cfg = job["cfg"]
    cancel = job["cancel"]
    task_id = page._area_task_id
    x_name, y_name, sig_name = cfg.get("x"), cfg.get("y"), cfg.get("signal")
    low = float(cfg.get("low") or 0.0)
    high = float(cfg.get("high") or 0.0)
    names = [x_name, y_name, sig_name]
    grid = max(2, int(page._area_params.get("grid") or _AREA_GRID_DEFAULT))
    started = time.time()

    pf = pq.ParquetFile(path)
    try:
        schema_names = set(pf.schema_arrow.names)
    except Exception:  # noqa: BLE001
        schema_names = set()
    stages = (
        _data_columns._map_stages(ctx, task_id, parquet_path=path)
        if task_id else []
    )
    needed = _pipeline_dependencies(stages, names, path) if stages else set(names)
    needed = set(needed) | set(names)
    if not needed <= schema_names:
        # A virtual column (Segment mask, Cluster label, …) is not stored in
        # the data parquet: fall back to the materialised arrays.
        return _area_histogram_loaded(ctx, page, job, q)

    bounds = _area_bounds(ctx, pf, x_name, y_name, stages)
    if bounds is None:
        return _area_histogram_loaded(ctx, page, job, q)
    xmin, xmax, ymin, ymax = bounds
    cell = max(xmax - xmin, ymax - ymin) / float(grid)
    if not math.isfinite(cell) or cell <= 0:
        cell = 1.0
    nx = max(1, int(math.ceil((xmax - xmin) / cell - 1e-9)))
    ny = max(1, int(math.ceil((ymax - ymin) / cell - 1e-9)))

    total = int(pf.metadata.num_rows or 0)
    counts = np.zeros(nx * ny, dtype=np.int64)
    points = 0
    keep_index = _data_sampling._subsample_row_index(ctx, task_id, path)
    origin = None
    if any(kind == "illumination" for kind, _stage in stages):
        origin = (float(xmin), float(ymin))
    cols = sorted(needed)
    offset = 0
    last_report = 0.0
    for batch in pf.iter_batches(batch_size=500_000, columns=cols):
        if cancel.is_set():
            raise _AreaCancelled()
        n = batch.num_rows
        df = batch.to_pandas()
        values = {
            c: pd.to_numeric(df[c], errors="coerce").to_numpy(dtype="float64")
            for c in cols if c in df.columns
        }
        if stages:
            values = _evaluate_stages(
                stages, values, names, path, cache=None, origin=origin
            )
        x = values.get(x_name)
        y = values.get(y_name)
        v = values.get(sig_name)
        if x is None or y is None or v is None:
            raise ValueError("A selected column is not available in this file")
        if keep_index is not None:
            i0 = int(np.searchsorted(keep_index, offset, side="left"))
            i1 = int(np.searchsorted(keep_index, offset + n, side="left"))
            sub = np.asarray(keep_index[i0:i1], dtype=np.int64) - offset
            x, y, v = x[sub], y[sub], v[sub]
        offset += n
        keep = (
            np.isfinite(x) & np.isfinite(y) & np.isfinite(v)
            & (v >= low) & (v <= high)
        )
        if keep.any():
            bi = np.floor((x[keep] - xmin) / cell).astype(np.int64)
            bj = np.floor((y[keep] - ymin) / cell).astype(np.int64)
            np.clip(bi, 0, nx - 1, out=bi)
            np.clip(bj, 0, ny - 1, out=bj)
            counts += np.bincount(
                bj * nx + bi, minlength=nx * ny
            ).astype(np.int64, copy=False)
            points += int(keep.sum())
        now = time.time()
        if total and now - last_report > 0.25:
            last_report = now
            q.put(("progress", path, job["sig"], min(1.0, offset / total)))
    return {
        "counts": counts.reshape(ny, nx),
        "cell": cell,
        "nx": nx,
        "ny": ny,
        "xmin": float(xmin),
        "ymin": float(ymin),
        "rows": int(offset),
        "points": int(points),
        "seconds": time.time() - started,
    }


def _area_histogram_loaded(ctx, page, job, q):
    """Density grid from fully materialised columns (virtual columns)."""
    import numpy as np

    path = job["path"]
    cfg = job["cfg"]
    cancel = job["cancel"]
    names = [cfg.get("x"), cfg.get("y"), cfg.get("signal")]
    grid = max(2, int(page._area_params.get("grid") or _AREA_GRID_DEFAULT))
    started = time.time()
    q.put(("progress", path, job["sig"], 0.05))
    values = _data_columns._column_data(ctx, page._area_task_id, path, names)
    if cancel.is_set():
        raise _AreaCancelled()
    if any(name not in values for name in names):
        raise ValueError("A selected column is not available in this file")
    x = np.asarray(values[names[0]], dtype="float64")
    y = np.asarray(values[names[1]], dtype="float64")
    v = np.asarray(values[names[2]], dtype="float64")
    if not (len(x) == len(y) == len(v)):
        raise ValueError("The selected columns have different row counts")
    q.put(("progress", path, job["sig"], 0.5))
    finite = np.isfinite(x) & np.isfinite(y)
    if not finite.any():
        raise ValueError("The file has no finite X/Y values")
    xmin, xmax = float(x[finite].min()), float(x[finite].max())
    ymin = float(y[finite].min())
    ymax = float(y[finite].max())
    cell = max(xmax - xmin, ymax - ymin) / float(grid)
    if not math.isfinite(cell) or cell <= 0:
        cell = 1.0
    nx = max(1, int(math.ceil((xmax - xmin) / cell - 1e-9)))
    ny = max(1, int(math.ceil((ymax - ymin) / cell - 1e-9)))
    counts = np.zeros(nx * ny, dtype=np.int64)
    low = float(cfg.get("low") or 0.0)
    high = float(cfg.get("high") or 0.0)
    keep = finite & np.isfinite(v) & (v >= low) & (v <= high)
    points = int(keep.sum())
    if keep.any():
        bi = np.floor((x[keep] - xmin) / cell).astype(np.int64)
        bj = np.floor((y[keep] - ymin) / cell).astype(np.int64)
        np.clip(bi, 0, nx - 1, out=bi)
        np.clip(bj, 0, ny - 1, out=bj)
        counts += np.bincount(bj * nx + bi, minlength=nx * ny)
    return {
        "counts": counts.reshape(ny, nx),
        "cell": cell,
        "nx": nx,
        "ny": ny,
        "xmin": xmin,
        "ymin": ymin,
        "rows": int(len(x)),
        "points": points,
        "seconds": time.time() - started,
    }


def _area_bounds(ctx, pf, x_name, y_name, stages):
    """(xmin, xmax, ymin, ymax) from parquet statistics, or None."""
    if _pipeline_touches(stages, x_name) or _pipeline_touches(stages, y_name):
        return None
    try:
        names = list(pf.schema.names)
        indices = (names.index(x_name), names.index(y_name))
    except (ValueError, AttributeError):
        return None
    bounds = []
    metadata = pf.metadata
    for index in indices:
        low, high = math.inf, -math.inf
        for group_index in range(metadata.num_row_groups):
            try:
                stats = metadata.row_group(group_index).column(index).statistics
            except Exception:  # noqa: BLE001
                return None
            if stats is None or not getattr(stats, "has_min_max", True):
                return None
            try:
                low = min(low, float(stats.min))
                high = max(high, float(stats.max))
            except (TypeError, ValueError):
                return None
        if not (math.isfinite(low) and math.isfinite(high)):
            return None
        bounds.append((low, high))
    return bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]


def _area_poll(ctx, page) -> None:
    q = getattr(page, "_area_result_queue", None)
    if q is None:
        return
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        path = msg[1]
        sig = msg[2]
        if kind == "progress":
            if page._area_latest.get(path) == sig:
                _area_set_status(ctx,
                    page, path, f"Computing… {msg[3] * 100:.0f}%", "#b58900"
                )
        elif kind == "done":
            page._area_cancel_events.pop(path, None)
            if page._area_latest.get(path) != sig:
                continue
            page._area_params.setdefault("results", {})[path] = msg[3]
            _area_set_status(ctx, page, path, "Done", "#2e7d32")
            _area_update_area_cell(ctx, page, path)
            if page._area_params.get("selected") == path:
                _area_render_plot(ctx, page)
            _area_mark_dirty(ctx, page)
        elif kind == "error":
            page._area_cancel_events.pop(path, None)
            if page._area_latest.get(path) != sig:
                continue
            page._area_params.setdefault("results", {}).pop(path, None)
            _area_set_status(ctx, page, path, f"Error: {msg[3]}", "#c0392b")
            _area_update_area_cell(ctx, page, path)
    _area_update_progress(ctx, page)


def _area_sync_plot_settings(ctx, page) -> None:
    plot_cfg = page._area_params.get("plot") or {}
    palette = page._area_palette
    palette.blockSignals(True)
    idx = palette.findData(plot_cfg.get("palette", "viridis"))
    palette.setCurrentIndex(max(0, idx))
    palette.blockSignals(False)
    size = page._area_point_size
    size.blockSignals(True)
    size.setValue(float(plot_cfg.get("point_size", 4.0)))
    size.blockSignals(False)
    combo = page._area_type_combo
    combo.blockSignals(True)
    idx = combo.findData(plot_cfg.get("type", "scatter"))
    combo.setCurrentIndex(max(0, idx))
    combo.blockSignals(False)


def _area_on_plot_type(ctx, page) -> None:
    plot_cfg = page._area_params.setdefault("plot", {})
    value = page._area_type_combo.currentData() or "scatter"
    if plot_cfg.get("type") == value:
        return
    plot_cfg["type"] = value
    _area_render_plot(ctx, page)
    _area_mark_dirty(ctx, page)


def _area_on_plot_param(ctx, page) -> None:
    plot_cfg = page._area_params.setdefault("plot", {})
    plot_cfg["palette"] = page._area_palette.currentData() or "viridis"
    plot_cfg["point_size"] = float(page._area_point_size.value())
    _area_render_plot(ctx, page)
    _area_mark_dirty(ctx, page)


def _area_resolved_plot_type(ctx, page, path=None):
    params = page._area_params
    plot_cfg = params.get("plot") or {}
    path = path if path is not None else params.get("selected")
    if not path:
        return None
    is_image = _data_image_index._image_load_manifest(ctx, path) is not None
    ptype = plot_cfg.get("type") or "auto"
    if ptype == "auto":
        ptype = "image" if is_image else "scatter"
    if ptype == "image" and not is_image:
        ptype = "scatter"
    return ptype


def _area_set_image_levels(ctx, page, lo, hi) -> None:
    path = page._area_params.get("selected")
    if not path:
        return
    cfg = _area_row_cfg(ctx, page, path)
    cfg["image_min"] = lo
    cfg["image_max"] = hi
    _area_render_plot(ctx, page)
    _area_mark_dirty(ctx, page)


def _area_rebuild_levels(ctx, page) -> None:
    """Min/Max / histogram / Auto set / Reset for an image preview."""
    lay = getattr(page, "_area_levels_lay", None)
    host = getattr(page, "_area_levels_host", None)
    if lay is None or host is None:
        return
    params = page._area_params
    path = params.get("selected")
    ptype = _area_resolved_plot_type(ctx, page, path)
    cfg = _area_row_cfg(ctx, page, path) if path else {}
    sig = (cfg or {}).get("signal")
    locked = bool(getattr(page, "_area_locked", False))
    signature = (path, sig if ptype == "image" else None, ptype, locked)
    if signature == getattr(page, "_area_levels_sig", None):
        return
    page._area_levels_sig = signature
    while lay.count():
        entry = lay.takeAt(0)
        widget = entry.widget()
        if widget is not None:
            widget.deleteLater()
    if ptype != "image" or not path or not sig:
        host.setVisible(False)
        return
    host.setVisible(True)
    _widgets_image_level._image_level_controls(ctx,
        lay, sig, cfg.get("image_min"), cfg.get("image_max"),
        not locked,
        on_minmax=lambda lo, hi: _area_set_image_levels(ctx, page, lo, hi),
        task_id=page._area_task_id,
        parquet_path=path,
    )


def _area_render_plot(ctx, page) -> None:
    pw = page._area_plot
    params = page._area_params
    plot_cfg = params.get("plot") or {}
    path = params.get("selected")
    _area_rebuild_levels(ctx, page)
    if not path:
        pw.clear()
        pw.setTitle("Select a file row to preview", color="#808080", size="10pt")
        return
    cfg = _area_row_cfg(ctx, page, path)
    x_name, y_name, sig_name = cfg.get("x"), cfg.get("y"), cfg.get("signal")
    if not (x_name and y_name):
        pw.clear()
        pw.setTitle("Set X and Y for this row", color="#b58900", size="10pt")
        return
    is_image = _data_image_index._image_load_manifest(ctx, path) is not None
    ptype = plot_cfg.get("type") or "auto"
    if ptype == "auto":
        ptype = "image" if is_image else "scatter"
    if ptype == "image" and not is_image:
        ptype = "scatter"
    plot_dict = {
        "type": ptype,
        "file": path,
        "palette": plot_cfg.get("palette", "viridis"),
        "file_name": _data_naming._comp_file_name(ctx, path, page._area_task_id),
        "xmin": None,
        "xmax": None,
        "ymin": None,
        "ymax": None,
        "show_legend": False,
        "point_size": float(plot_cfg.get("point_size", 4.0)),
    }
    try:
        if ptype == "image":
            if not sig_name:
                pw.clear()
                pw.setTitle(
                    "Set a signal variable to render the image",
                    color="#b58900", size="10pt",
                )
                return
            plot_dict["layers"] = [{
                "parameter": sig_name,
                "color": (255, 255, 255),
                "name": str(sig_name),
                "min": cfg.get("image_min"),
                "max": cfg.get("image_max"),
            }]
            _widgets_plot_renderer._figure_render_image(ctx, plot_dict, pw, task_id=page._area_task_id)
        else:
            plot_dict["x"] = x_name
            plot_dict["y"] = y_name
            plot_dict["color_mode"] = "parameter" if sig_name else "density"
            if sig_name:
                plot_dict["color_param"] = sig_name
            _widgets_plot_renderer._figure_render_scatter(ctx,
                plot_dict, pw, task_id=page._area_task_id
            )
    except Exception as exc:  # noqa: BLE001
        pw.clear()
        pw.setTitle(f"Preview failed: {exc}", color="#c0392b", size="10pt")
        return
    _area_draw_overlay(ctx, page, path)


def _area_draw_overlay(ctx, page, path) -> None:
    """Shade the grid cells that were counted as tissue."""
    import numpy as np
    import pyqtgraph as pg

    pw = getattr(page, "_area_plot", None)
    if pw is None:
        return
    item = getattr(pw, "_area_overlay", None)
    result = (page._area_params.get("results") or {}).get(path)
    if not isinstance(result, dict) or not result.get("mask"):
        if item is not None:
            try:
                item.setVisible(False)
            except RuntimeError:
                pw._area_overlay = None
        return
    try:
        nx = int(result["nx"])
        ny = int(result["ny"])
        bits = np.frombuffer(
            base64.b64decode(result["mask"]), dtype=np.uint8
        )
        qual = np.unpackbits(bits)[: nx * ny].reshape(ny, nx).astype(bool)
    except Exception:  # noqa: BLE001
        return
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = 176
    rgba[..., 2] = 32
    rgba[..., 3] = np.where(qual, 70, 0).astype(np.uint8)

    view = pw.getPlotItem().getViewBox()
    if item is None or item not in view.addedItems:
        item = pg.ImageItem(axisOrder="row-major")
        item.setZValue(30)
        pw.addItem(item)
        pw._area_overlay = item
    item.setImage(rgba, autoLevels=False)
    item.setRect(QRectF(
        float(result["xmin"]),
        float(result["ymin"]),
        nx * float(result["cell"]),
        ny * float(result["cell"]),
    ))
    item.setVisible(True)


def _area_copy(ctx, page) -> None:
    params = page._area_params
    headers = [
        "File", "X", "Y", "Signal", "Low", "High", "Density", "Area", "Status",
    ]
    lines = ["\t".join(headers)]
    for path in page._area_paths:
        cfg = _area_row_cfg(ctx, page, path)
        result = (params.get("results") or {}).get(path) or {}
        status = page._area_status.get(path, ("", ""))[0]
        lines.append("\t".join([
            _data_naming._comp_file_name(ctx, path, page._area_task_id),
            str(cfg.get("x") or ""),
            str(cfg.get("y") or ""),
            str(cfg.get("signal") or ""),
            _area_format(ctx, cfg.get("low")),
            _area_format(ctx, cfg.get("high")),
            _area_format(ctx, cfg.get("density")),
            _area_format(ctx, result.get("area")),
            status,
        ]))
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Area table copied to the clipboard")


def _area_mark_dirty(ctx, page) -> None:
    page._area_dirty = True
    _area_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _area_update_save_btn(ctx, page) -> None:
    page._area_save_btn.setEnabled(
        page._area_dirty and not page._area_locked
    )


def _area_save_now(ctx, page) -> None:
    page._area_task_def["params"] = page._area_params
    if ctx.project is not None:
        ctx.project.save()
    page._area_snapshot = copy.deepcopy(page._area_params)
    page._area_dirty = False
    _area_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Area settings saved")
    ctx._module_saved(page)


def _area_close(ctx, page) -> None:
    if getattr(page, "_area_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Area",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._area_task_def["params"] = page._area_snapshot
    page._area_closing = True
    for event in list((page._area_cancel_events or {}).values()):
        event.set()
    q = getattr(page, "_area_job_queue", None)
    if q is not None:
        q.put(None)
    timer = getattr(page, "_area_timer", None)
    if timer is not None:
        timer.stop()
        page._area_timer = None
    ctx._close_module_page(page)


_AREA_GRID_DEFAULT = 200
