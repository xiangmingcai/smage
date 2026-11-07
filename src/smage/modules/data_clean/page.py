"""Data Clean module: PeacoQC-based event cleaning."""
from __future__ import annotations

from PySide6.QtCore import (
    QTimer,
    Qt,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from pathlib import Path
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.data.image_index import _build_image_index_files
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from smage.widgets.resources import _lock_pixmap
import copy
import hashlib
import json
import math
import queue
import threading


_DC_MODES = [
    ("All (MAD + Isolation Tree)", "all"),
    ("MAD only", "mad"),
    ("Isolation Tree only", "isolation_tree"),
]

def _dc_signature(ctx, params, parquet_path, task_id=None) -> str:
    """Change signature for one file's Data Clean output."""
    try:
        st = Path(parquet_path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "method": params.get("method"),
        "time": params.get("time"),
        "columns": params.get("columns") or [],
        "mode": params.get("mode"),
        "mad": params.get("mad"),
        "it_limit": params.get("it_limit"),
        "consecutive_bins": params.get("consecutive_bins"),
        "min_cells": params.get("min_cells"),
        "remove_zeros": params.get("remove_zeros"),
        "name": params.get("name"),
        "source": str(parquet_path),
        "stat": stat,
        "stages": ctx._map_stage_token(task_id),
    }
    sample = ctx._subsample_sig(task_id, parquet_path)
    if sample:
        payload["sample"] = sample
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _dc_params_for(ctx, task_def) -> dict:
    """Saved params of a Data Clean task (its live snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "data_clean":
        snap = getattr(reg["page"], "_dc_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _dc_column_values(ctx, parquet_path, rec, name):
    """Row-aligned float64 values of one Data Clean column."""
    return ctx._cluster_column_values(parquet_path, rec, name)


def _build_dc_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Data Clean page: algorithm/files | variables | calculate."""
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
    close_btn.clicked.connect(lambda _=False: ctx._dc_close(page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("method", "peacoqc")
    params.setdefault("files", [])
    params.setdefault("time", "")
    params.setdefault("columns", [])
    params.setdefault("mode", "all")
    params.setdefault("mad", 6.0)
    params.setdefault("it_limit", 0.6)
    params.setdefault("consecutive_bins", 5)
    params.setdefault("min_cells", 150)
    params.setdefault("remove_zeros", False)
    params.setdefault("name", "PeacoQC_pass")
    params.setdefault("outputs", {})
    params.setdefault("log", [])

    page._dc_task_def = task_def
    page._dc_task_id = task_def.get("id")
    page._dc_params = params
    page._dc_locked = locked
    page._dc_save_btn = save_btn
    page._dc_dirty = False
    page._dc_snapshot = copy.deepcopy(params)
    page._dc_log_lines = list(params.get("log") or [])
    page._dc_running = False
    page._dc_cancel = False
    page._dc_updating = False
    save_btn.clicked.connect(lambda _=False: ctx._dc_save_now(page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(ctx._dc_left_panel(page))
    splitter.addWidget(ctx._dc_center_panel(page))
    splitter.addWidget(ctx._dc_right_panel(page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 1)
    splitter.setSizes([320, 320, 400])
    row.addWidget(splitter, 1)

    row.addWidget(ctx._dc_log_panel(page))
    layout.addWidget(content, 1)

    ctx._dc_refresh(page)
    return page


def _dc_log_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Log</b>"))
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Calculation output appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    log.setFixedHeight(130)
    for line in getattr(page, "_dc_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._dc_log = log
    return panel


def _dc_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_dc_log_lines", None)
    if lines is None:
        lines = []
        page._dc_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    log = getattr(page, "_dc_log", None)
    if log is not None:
        for line in str(text).splitlines():
            if line.strip():
                log.appendPlainText(f"[{ts}] {line}")


def _dc_left_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Algorithm</b>"))
    method = _NoWheelComboBox()
    method.addItem("PeacoQC", "peacoqc")
    method.setEnabled(not page._dc_locked)
    lay.addWidget(method)
    page._dc_method = method
    desc = QLabel(
        "Peak-based quality control: splits the acquisition into bins and "
        "removes bins whose signal drifts."
    )
    desc.setWordWrap(True)
    desc.setStyleSheet("color: #6b7785;")
    lay.addWidget(desc)

    lay.addWidget(QLabel("<b>Data files</b>"))
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(not page._dc_locked)
    lay.addWidget(files_btn)
    files_box = QListWidget()
    files_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(files_box, 1)
    page._dc_files_box = files_box
    files_count = QLabel("")
    files_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(files_count)
    page._dc_files_count = files_count

    files_btn.clicked.connect(lambda _=False: ctx._dc_select_files(page))
    return panel


def _dc_center_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Time parameter</b>"))
    hint_time = QLabel(
        "The column that holds the acquisition order. Required: the "
        "algorithm splits the file into time bins. A column named "
        "\"time\" is selected automatically."
    )
    hint_time.setWordWrap(True)
    hint_time.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint_time)
    time_combo = _NoWheelComboBox()
    time_combo.setStyleSheet(
        "QComboBox { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    time_combo.setEnabled(not page._dc_locked)
    lay.addWidget(time_combo)
    page._dc_time = time_combo
    time_state = QLabel("")
    time_state.setStyleSheet("color: #b58900;")
    time_state.setWordWrap(True)
    lay.addWidget(time_state)
    page._dc_time_state = time_state

    lay.addSpacing(6)
    lay.addWidget(QLabel("<b>Test variables</b>"))
    hint = QLabel(
        "Variables shared by every selected file. The algorithm checks "
        "these columns for signal drift."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    vars_btn = QPushButton("Select variables…")
    vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    vars_btn.setEnabled(not page._dc_locked)
    lay.addWidget(vars_btn)
    vars_box = QListWidget()
    vars_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(vars_box, 1)
    page._dc_vars_box = vars_box
    vars_count = QLabel("")
    vars_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(vars_count)
    page._dc_vars_count = vars_count

    vars_btn.clicked.connect(lambda _=False: ctx._dc_select_vars(page))
    time_combo.currentIndexChanged.connect(
        lambda _=0: ctx._dc_on_time(page)
    )
    return panel


def _dc_right_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Algorithm parameters</b>"))

    lay.addWidget(QLabel("Mode"))
    mode = _NoWheelComboBox()
    for label, value in _DC_MODES:
        mode.addItem(label, value)
    mode.setEnabled(not page._dc_locked)
    lay.addWidget(mode)
    lay.addWidget(ctx._param_hint(
        "All runs both checks and keeps the union of their flags; each "
        "single mode uses only that check."
    ))
    page._dc_mode = mode

    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(2)
    grid.setColumnStretch(1, 1)
    row = 0
    grid.addWidget(QLabel("MAD threshold"), row, 0)
    mad = _NoWheelDoubleSpinBox()
    mad.setRange(1.0, 50.0)
    mad.setDecimals(1)
    mad.setSingleStep(0.5)
    mad.setValue(6.0)
    grid.addWidget(mad, row, 1)
    row += 1
    grid.addWidget(ctx._param_hint(
        "How many MADs a bin may deviate before it is removed. Lower = "
        "stricter."
    ), row, 0, 1, 2)
    row += 1
    grid.addWidget(QLabel("IT limit"), row, 0)
    it_limit = _NoWheelDoubleSpinBox()
    it_limit.setRange(0.05, 1.0)
    it_limit.setDecimals(2)
    it_limit.setSingleStep(0.05)
    it_limit.setValue(0.6)
    grid.addWidget(it_limit, row, 1)
    row += 1
    grid.addWidget(ctx._param_hint(
        "Isolation Tree gain limit. Lower = stricter; skipped when there "
        "are too few bins."
    ), row, 0, 1, 2)
    row += 1
    grid.addWidget(QLabel("Consecutive bins"), row, 0)
    consecutive = _NoWheelSpinBox()
    consecutive.setRange(0, 100)
    consecutive.setValue(5)
    grid.addWidget(consecutive, row, 1)
    row += 1
    grid.addWidget(ctx._param_hint(
        "Also drop runs of up to this many consecutive bad bins."
    ), row, 0, 1, 2)
    row += 1
    grid.addWidget(QLabel("Min cells / bin"), row, 0)
    min_cells = _NoWheelSpinBox()
    min_cells.setRange(10, 100000)
    min_cells.setValue(150)
    grid.addWidget(min_cells, row, 1)
    row += 1
    grid.addWidget(ctx._param_hint(
        "Smallest bin the statistics are computed on; larger = more stable "
        "but fewer bins."
    ), row, 0, 1, 2)
    page._dc_mad = mad
    page._dc_it_limit = it_limit
    page._dc_consecutive = consecutive
    page._dc_min_cells = min_cells
    lay.addLayout(grid)

    remove_zeros = QCheckBox("Remove zeros before peak detection")
    remove_zeros.setEnabled(not page._dc_locked)
    lay.addWidget(remove_zeros)
    lay.addWidget(ctx._param_hint(
        "Ignore zero values when locating the peaks (useful for "
        "sparse/low-background channels)."
    ))
    page._dc_remove_zeros = remove_zeros

    lay.addWidget(QLabel("<b>New column name</b>"))
    name_row = QWidget()
    nh = QHBoxLayout(name_row)
    nh.setContentsMargins(0, 0, 0, 0)
    nh.setSpacing(4)
    name_edit = QLineEdit()
    nh.addWidget(name_edit, 1)
    warn = QLabel("⚠")
    warn.setToolTip("Duplicate column name")
    warn.setStyleSheet("color: #b58900; font-weight: bold;")
    nh.addWidget(warn)
    lay.addWidget(name_row)
    page._dc_name = name_edit
    page._dc_warn = warn

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(False)
    lay.addWidget(calc_btn)
    page._dc_calc_btn = calc_btn
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._dc_status = status
    lay.addStretch()

    for widget in (mad, it_limit, consecutive, min_cells):
        widget.setEnabled(not page._dc_locked)
    name_edit.setEnabled(not page._dc_locked)

    mode.currentIndexChanged.connect(lambda _=0: ctx._dc_on_mode(page))
    mad.valueChanged.connect(lambda _=0.0: ctx._dc_on_param(page))
    it_limit.valueChanged.connect(lambda _=0.0: ctx._dc_on_param(page))
    consecutive.valueChanged.connect(lambda _=0: ctx._dc_on_param(page))
    min_cells.valueChanged.connect(lambda _=0: ctx._dc_on_param(page))
    remove_zeros.toggled.connect(lambda _=0: ctx._dc_on_param(page))
    name_edit.textEdited.connect(lambda _=0: ctx._dc_on_name(page))
    calc_btn.clicked.connect(lambda _=False: ctx._dc_calculate(page))
    return panel


def _dc_common_columns(ctx, page) -> list:
    files = page._dc_params.get("files") or []
    if not files:
        return []
    common = set(ctx._columns_for(files[0], page._dc_task_id))
    for path in files[1:]:
        common &= set(ctx._columns_for(path, page._dc_task_id))
    return sorted(common)


def _dc_select_files(ctx, page) -> None:
    records = ctx._available_files(page._dc_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        ctx._comp_file_name(p, page._dc_task_id)
        for p in page._dc_params.get("files") or []
    ]
    chosen = ctx._pick_items_dialog(
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._dc_params["files"] = [by_name[n] for n in chosen if n in by_name]
    common = set(ctx._dc_common_columns(page))
    time_col = page._dc_params.get("time") or ""
    if time_col not in common:
        time_col = next((c for c in common if c.lower() == "time"), "")
        page._dc_params["time"] = time_col
    page._dc_params["columns"] = [
        c for c in page._dc_params.get("columns") or []
        if c in common and c != time_col
    ]
    ctx._dc_refresh(page)
    ctx._dc_mark_dirty(page)


def _dc_select_vars(ctx, page) -> None:
    options = [
        c for c in ctx._dc_common_columns(page)
        if c != (page._dc_params.get("time") or "")
    ]
    if not options:
        QMessageBox.information(
            ctx.host, "Select variables",
            "Select data files with common columns first."
        )
        return
    chosen = ctx._pick_items_dialog(
        "Select variables", options,
        page._dc_params.get("columns") or [], "Filter variables...",
    )
    if chosen is None:
        return
    page._dc_params["columns"] = list(chosen)
    ctx._dc_refresh(page)
    ctx._dc_mark_dirty(page)


def _dc_on_time(ctx, page) -> None:
    if getattr(page, "_dc_updating", False):
        return
    value = page._dc_time.currentData() or ""
    page._dc_params["time"] = value
    if value in (page._dc_params.get("columns") or []):
        page._dc_params["columns"] = [
            c for c in page._dc_params["columns"] if c != value
        ]
        ctx._dc_refresh(page)
    ctx._dc_mark_dirty(page)


def _dc_on_mode(ctx, page) -> None:
    if getattr(page, "_dc_updating", False):
        return
    page._dc_params["mode"] = page._dc_mode.currentData() or "all"
    ctx._dc_mark_dirty(page)


def _dc_on_param(ctx, page) -> None:
    if getattr(page, "_dc_updating", False):
        return
    page._dc_params["mad"] = float(page._dc_mad.value())
    page._dc_params["it_limit"] = float(page._dc_it_limit.value())
    page._dc_params["consecutive_bins"] = int(page._dc_consecutive.value())
    page._dc_params["min_cells"] = int(page._dc_min_cells.value())
    page._dc_params["remove_zeros"] = bool(page._dc_remove_zeros.isChecked())
    ctx._dc_mark_dirty(page)


def _dc_on_name(ctx, page) -> None:
    if getattr(page, "_dc_updating", False):
        return
    page._dc_params["name"] = page._dc_name.text().strip()
    ctx._dc_mark_dirty(page)
    ctx._dc_update_name(page)


def _dc_existing_columns(ctx, page) -> set:
    cols = set()
    for path in page._dc_params.get("files") or []:
        cols |= set(ctx._columns_for(path, page._dc_task_id))
    return cols


def _dc_name_conflict(ctx, page) -> bool:
    name = (page._dc_params.get("name") or "").strip()
    if not name:
        return False
    return name in ctx._dc_existing_columns(page)


def _dc_update_name(ctx, page) -> None:
    page._dc_warn.setVisible(ctx._dc_name_conflict(page))
    ctx._dc_update_calc_btn(page)


def _dc_update_calc_btn(ctx, page) -> None:
    params = page._dc_params
    name = (params.get("name") or "").strip()
    ok = bool(
        params.get("files")
        and params.get("time")
        and params.get("columns")
        and name
        and not ctx._dc_name_conflict(page)
    )
    page._dc_calc_btn.setEnabled(ok and not page._dc_locked)
    if not params.get("files"):
        page._dc_time_state.setText("")
    elif not params.get("time"):
        page._dc_time_state.setText("Choose the time parameter before calculating.")
    else:
        page._dc_time_state.setText("")


def _dc_refresh(ctx, page) -> None:
    params = page._dc_params
    page._dc_updating = True
    try:
        page._dc_mode.blockSignals(True)
        page._dc_mode.setCurrentIndex(
            max(0, page._dc_mode.findData(params.get("mode", "all")))
        )
        page._dc_mode.blockSignals(False)
        for widget, key, cast in (
            (page._dc_mad, "mad", float),
            (page._dc_it_limit, "it_limit", float),
            (page._dc_consecutive, "consecutive_bins", int),
            (page._dc_min_cells, "min_cells", int),
        ):
            widget.blockSignals(True)
            widget.setValue(cast(params.get(key) or 0))
            widget.blockSignals(False)
        page._dc_remove_zeros.blockSignals(True)
        page._dc_remove_zeros.setChecked(bool(params.get("remove_zeros")))
        page._dc_remove_zeros.blockSignals(False)

        page._dc_name.blockSignals(True)
        page._dc_name.setText(params.get("name", "PeacoQC_pass"))
        page._dc_name.blockSignals(False)

        files = params.get("files") or []
        page._dc_files_box.clear()
        for path in files:
            page._dc_files_box.addItem(
                ctx._comp_file_name(path, page._dc_task_id)
            )
        page._dc_files_count.setText(f"{len(files)} file(s) selected")

        common = ctx._dc_common_columns(page)
        time_col = params.get("time") or ""
        page._dc_time.blockSignals(True)
        page._dc_time.clear()
        if common:
            page._dc_time.addItem("(select a column)", "")
        for column in common:
            page._dc_time.addItem(str(column), str(column))
        index = page._dc_time.findData(time_col)
        page._dc_time.setCurrentIndex(index if index >= 0 else 0)
        page._dc_time.blockSignals(False)

        columns = params.get("columns") or []
        page._dc_vars_box.clear()
        for c in columns:
            page._dc_vars_box.addItem(str(c))
        page._dc_vars_count.setText(f"{len(columns)} variable(s) selected")
    finally:
        page._dc_updating = False
    ctx._dc_update_name(page)


def _dc_validate(ctx, page):
    params = page._dc_params
    try:
        import peacoqc  # noqa: F401
    except Exception:  # noqa: BLE001
        return False, (
            "The PeacoQC runtime is missing.\n"
            "Install it with:  pip install peacoqc-rs polars"
        )
    if not params.get("files"):
        return False, "Select at least one data file."
    if not params.get("time"):
        return False, "Choose the parameter that represents acquisition time."
    if not params.get("columns"):
        return False, "Select at least one variable to check."
    name = (params.get("name") or "").strip()
    if not name:
        return False, "Enter a name for the new column."
    if ctx._dc_name_conflict(page):
        return False, f"The column name '{name}' is already used."
    return True, ""


def _dc_analyze_file(ctx, page, path, q, index, total):
    """Run PeacoQC on one file; return (flags, row_index, detail)."""
    import numpy as np
    import peacoqc
    import polars as pl

    params = page._dc_params
    task_id = page._dc_task_id
    time_col = params.get("time") or ""
    columns = list(params.get("columns") or [])
    name = ctx._comp_file_name(path, task_id)
    q.put(("log", f"Reading {index}/{total}: {name}"))
    q.put(("status", f"Cleaning {index}/{total} — reading"))
    values = ctx._column_data(task_id, path, [time_col] + columns)
    missing = [c for c in [time_col] + columns if c not in values]
    if missing:
        raise ValueError("column(s) not available: " + ", ".join(missing))
    time_values = np.asarray(values[time_col], dtype="float64")
    matrix = np.column_stack(
        [np.asarray(values[c], dtype="float64") for c in columns]
    )
    n_rows = len(matrix)
    if n_rows == 0:
        raise ValueError("the file has no rows")
    up_index = ctx._subsample_row_index(task_id, path)
    row_index = (
        np.asarray(up_index, dtype=np.int64)
        if up_index is not None else np.arange(n_rows, dtype=np.int64)
    )
    if len(row_index) != n_rows:
        row_index = row_index[:n_rows]
    usable = np.isfinite(time_values) & np.isfinite(matrix).all(axis=1)
    n_usable = int(usable.sum())
    if n_usable == 0:
        raise ValueError("no rows have finite values in the selected columns")
    order = np.flatnonzero(usable)
    order = order[np.argsort(time_values[order], kind="stable")]

    ranges = {}
    for j, column in enumerate(columns):
        column_values = matrix[order, j]
        low = float(np.min(column_values))
        high = float(np.max(column_values))
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            high = low + 1.0
        ranges[column] = (low, high)

    q.put(("log", f"  {n_usable:,} row(s) in time order, "
                  f"{len(columns)} variable(s)"))
    q.put(("status", f"Cleaning {index}/{total} — running PeacoQC"))
    frame = pl.DataFrame({
        column: matrix[order, j].astype("float32")
        for j, column in enumerate(columns)
    })
    try:
        result = peacoqc.run_qc(
            frame,
            channels=columns,
            channel_ranges=ranges,
            mode=str(params.get("mode") or "all"),
            mad=float(params.get("mad") or 6.0),
            it_limit=float(params.get("it_limit") or 0.6),
            consecutive_bins=int(params.get("consecutive_bins") or 5),
            min_cells=int(params.get("min_cells") or 150),
            remove_zeros=bool(params.get("remove_zeros")),
        )
    except BaseException as exc:  # noqa: BLE001 - the Rust core can panic
        raise ValueError(f"PeacoQC failed: {exc}") from exc
    good = np.asarray(result.good_cells, dtype=bool)
    if good.size != n_usable:
        raise ValueError("PeacoQC returned an unexpected number of rows")

    flags = np.zeros(n_rows, dtype="float64")
    flags[order[good]] = 1.0
    detail = {
        "rows": n_usable,
        "skipped": int(n_rows - n_usable),
        "removed": float(getattr(result, "percentage_removed", 0.0) or 0.0),
        "bins": int(getattr(result, "n_bins", 0) or 0),
        "events_per_bin": int(getattr(result, "events_per_bin", 0) or 0),
        "mad": getattr(result, "mad_percentage", None),
        "it": getattr(result, "it_percentage", None),
    }
    return flags, row_index, detail


def _dc_write_file(ctx, page, path, flags, row_indices=None):
    """Write one file's 0/1 column; return (rel, columns, kind)."""
    import numpy as np
    import pandas as pd

    params = page._dc_params
    name = (params.get("name") or "PeacoQC_pass").strip()
    stem = Path(str(path)).stem
    rel = ctx._module_rel(page._dc_task_id, f"{stem}_dataclean.parquet")
    out_path = ctx.project.path / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ent = ctx._ensure_image_index(path)
    is_image = ent is not None and ent.get("manifest") is not None
    n_full = ctx._data_row_count(path)
    sel = (
        np.asarray(row_indices) if row_indices is not None
        else np.arange(len(flags))
    ).astype(np.int64)

    if is_image:
        man = ent["manifest"]
        xmin = int(round(float(man["xmin"])))
        ymin = int(round(float(man["ymin"])))
        width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
        height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
        src = pd.read_parquet(path, columns=["x", "y"])
        xi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        yi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        ok = (sel >= 0) & (sel < len(xi))
        grid = np.full(height * width, np.nan, dtype="float64")
        s = sel[ok]
        sx, sy = xi[s], yi[s]
        good = (sx >= 0) & (sx < width) & (sy >= 0) & (sy < height)
        flat = sy[good] * width + sx[good]
        grid[flat] = flags[ok][good]
        xx = np.tile(np.arange(width, dtype=np.int32), height) + xmin
        yy = np.repeat(np.arange(height, dtype=np.int32), width) + ymin
        pd.DataFrame({"x": xx, "y": yy, name: grid}).to_parquet(
            out_path, index=False
        )
        _build_image_index_files(str(out_path))
        kind = "image"
    else:
        full = np.full(n_full, np.nan, dtype="float64")
        if len(flags) == len(sel):
            full[sel] = flags
        else:
            n = min(len(flags), n_full)
            full[:n] = flags[:n]
        pd.DataFrame({name: full}).to_parquet(out_path, index=False)
        kind = "table"
    return rel, [name], kind


def _dc_run(ctx, page, q):
    """Run the PeacoQC cleaning in a background thread."""
    params = page._dc_params
    files = list(params.get("files") or [])
    n_files = len(files)
    failed = 0
    outputs = {}
    try:
        q.put(("log", "— Starting PeacoQC —"))
        mode = next(
            (label for label, value in _DC_MODES
             if value == params.get("mode")),
            params.get("mode"),
        )
        q.put(("log", f"Mode: {mode} · MAD {params.get('mad')} · "
                      f"IT limit {params.get('it_limit')} · "
                      f"{params.get('consecutive_bins')} consecutive bin(s)"))
        for i, path in enumerate(files):
            if getattr(page, "_dc_cancel", False):
                q.put(("log", "Calculation cancelled."))
                break
            name = ctx._comp_file_name(path, page._dc_task_id)
            try:
                flags, row_index, detail = ctx._dc_analyze_file(
                    page, path, q, i + 1, n_files
                )
                rel, cols, kind = ctx._dc_write_file(
                    page, path, flags, row_index
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                q.put(("log", f"✗ {name}: {exc}"))
                continue
            outputs[path] = {
                "path": rel,
                "columns": cols,
                "kind": kind,
                "signature": ctx._dc_signature(
                    params, path, page._dc_task_id
                ),
            }
            passed = int(round(detail["rows"] * (1.0 - detail["removed"] / 100.0)))
            extra = ""
            if detail["skipped"]:
                extra += f", {detail['skipped']:,} row(s) skipped (missing values)"
            q.put(("log", f"✓ {name}: kept {passed:,}/{detail['rows']:,} "
                          f"({100.0 - detail['removed']:.1f}%), "
                          f"{detail['bins']} bin(s) × {detail['events_per_bin']}"
                          f"{extra}"))
        q.put(("done", outputs, len(outputs), failed))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _dc_calculate(ctx, page) -> None:
    ok, message = ctx._dc_validate(page)
    if not ok:
        QMessageBox.warning(ctx.host, "Calculate", message)
        return
    existing = [
        p for p in page._dc_params.get("files") or []
        if isinstance((page._dc_params.get("outputs") or {}).get(p), dict)
    ]
    if existing:
        reply = QMessageBox.question(
            ctx.host, "Calculate",
            f"{len(existing)} file(s) already have results.\n"
            "Recalculate and overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    page._dc_running = True
    page._dc_cancel = False
    page._dc_status.setText("Calculating…")
    page._dc_calc_btn.setEnabled(False)

    q = queue.Queue()
    page._dc_queue = q
    page._dc_thread = threading.Thread(
        target=ctx._dc_run, args=(page, q), daemon=True
    )
    page._dc_thread.start()

    timer = QTimer(ctx.host)
    timer.setInterval(120)
    timer.timeout.connect(lambda: ctx._dc_poll(page))
    timer.start()
    page._dc_timer = timer


def _dc_poll(ctx, page) -> None:
    q = getattr(page, "_dc_queue", None)
    if q is None:
        return
    finished = False
    error = None
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "log":
            ctx._dc_log(page, msg[1])
        elif kind == "status":
            page._dc_status.setText(msg[1])
        elif kind == "done":
            outputs, done, failed = msg[1], msg[2], msg[3]
            params = page._dc_params
            params["outputs"] = outputs
            page._dc_task_def["params"] = params
            page._dc_task_def["output"] = {"saved": bool(done)}
            if ctx.project is not None:
                ctx.project.save()
            page._dc_snapshot = copy.deepcopy(params)
            page._dc_dirty = False
            ctx._dc_update_save_btn(page)
            page._dc_done = done
            page._dc_failed = failed
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    page._dc_running = False
    timer = getattr(page, "_dc_timer", None)
    if timer is not None:
        timer.stop()
        page._dc_timer = None
    ctx._dc_update_calc_btn(page)
    if error:
        ctx._dc_log(page, f"✗ {error}")
        QMessageBox.warning(ctx.host, "Calculate", error)
        page._dc_status.setText("Failed")
    else:
        summary = f"Cleaned {page._dc_done} file(s)."
        if getattr(page, "_dc_failed", 0):
            summary += f" {page._dc_failed} failed — see the log."
        page._dc_status.setText(summary)
        ctx._dc_log(page, summary)
        ctx.statusBar().showMessage(summary)
        ctx._module_saved(page)


def _dc_mark_dirty(ctx, page) -> None:
    page._dc_dirty = True
    ctx._dc_update_save_btn(page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _dc_update_save_btn(ctx, page) -> None:
    page._dc_save_btn.setEnabled(page._dc_dirty and not page._dc_locked)


def _dc_save_now(ctx, page) -> None:
    params = page._dc_params
    params["log"] = list(getattr(page, "_dc_log_lines", []) or [])
    page._dc_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._dc_snapshot = copy.deepcopy(params)
    page._dc_dirty = False
    ctx._dc_update_save_btn(page)
    ctx._dc_log(page, "Settings saved")
    ctx.statusBar().showMessage("Data Clean settings saved")
    ctx._module_saved(page)


def _dc_close(ctx, page) -> None:
    if getattr(page, "_dc_running", False):
        reply = QMessageBox.question(
            ctx.host, "Close Data Clean",
            "A calculation is still running.\nStop it and close? "
            "This run's results will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._dc_cancel = True
        ctx._dc_log(page, "Stopping after the current file…")
        return
    if getattr(page, "_dc_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Data Clean",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log_lines = list(getattr(page, "_dc_log_lines", []) or [])
        page._dc_task_def["params"] = page._dc_snapshot
        page._dc_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)
