"""Batch Alignment module: ComBat / CytoNorm batch correction."""
from __future__ import annotations

from PySide6.QtCore import (
    QTimer,
    QUrl,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
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
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.pipeline import _apply_batch_align
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelSpinBox,
)
from smage.widgets.resources import _lock_pixmap
import copy
import hashlib
import json
import queue
import threading
from functools import partial
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


_BA_MIN_CELLS = 50          # cells per batch per cluster for a spline

_BA_MODES = [
    ("CytoNorm (reference-based)", "cytonorm"),
    ("ComBat (reference-free)", "combat"),
]

_BA_REPORT_SAMPLE = 20000    # rows sampled per file

_BA_REPORT_CAP = 8000        # values kept per (batch, group, channel)

def _ba_config_sig(ctx, params) -> str:
    """Digest of the inputs a fitted model was trained from."""
    payload = {
        "method": params.get("method"),
        "files": list(params.get("files") or []),
        "batches": params.get("batches") or {},
        "references": sorted(params.get("references") or []),
        "columns": list(params.get("columns") or []),
        "cytonorm": params.get("cytonorm") or {},
        "combat": params.get("combat") or {},
    }
    if (params.get("method") or "cytonorm") == "cytonorm":
        # CytoNorm trains FlowSOM externally; a different FlowSOM version
        # can produce different clusters, so old models become stale.
        payload["flowsom_extension"] = (
            ctx._ext_entry("flowsom").get("version") or ""
        )
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _ba_load_model(ctx, task_def):
    """Load a fitted model's .npz (cached by file mtime)."""
    import numpy as np

    params = task_def.get("params") or {}
    model = params.get("model")
    if not isinstance(model, dict) or not model.get("npz"):
        return None
    if ctx.project is None:
        return None
    path = ctx.project.path / model["npz"]
    if not path.is_file():
        return None
    try:
        stat = path.stat()
        key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return None
    cache = getattr(ctx, "_ba_cache", None)
    if cache is None:
        cache = {}
        ctx._ba_cache = cache
    if key in cache:
        return cache[key]
    try:
        with np.load(path, allow_pickle=False) as data:
            arrays = {name: data[name] for name in data.files}
    except Exception:  # noqa: BLE001
        return None
    if len(cache) > 8:
        cache.clear()
    cache[key] = arrays
    return arrays


def _ba_stage(ctx, task_def):
    """The map stage for a Batch Alignment task, or None when not fitted."""
    params = task_def.get("params") or {}
    model = params.get("model")
    if not isinstance(model, dict):
        return None
    if model.get("config_sig") != _ba_config_sig(ctx, params):
        return None
    arrays = _ba_load_model(ctx, task_def)
    if arrays is None:
        return None
    stage = {
        "id": task_def.get("id"),
        "method": model.get("method"),
        "columns": list(model.get("columns") or []),
        "files": {
            str(path): int(index)
            for path, index in (model.get("file_batch") or {}).items()
        },
        "batches": list(model.get("batches") or []),
        "model_id": model.get("model_id"),
        "affine": arrays.get("affine"),
        "codes": arrays.get("codes"),
        "node_cluster": arrays.get("node_cluster"),
        "knots_x": arrays.get("knots_x"),
        "knots_y": arrays.get("knots_y"),
        "has_spline": arrays.get("has_spline"),
        "global_x": arrays.get("global_x"),
        "global_y": arrays.get("global_y"),
        "has_global": arrays.get("has_global"),
    }
    if stage["method"] == "combat" and stage["affine"] is None:
        return None
    if stage["method"] != "combat" and stage["codes"] is None:
        return None
    return stage


def _build_ba_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
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
    close_btn.clicked.connect(lambda _=False: _ba_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("method", "cytonorm")
    params.setdefault("files", [])
    params.setdefault("batches", {})
    params.setdefault("references", [])
    params.setdefault("columns", [])
    params.setdefault("cytonorm", {
        "nq": 99, "xdim": 10, "ydim": 10, "clusters": 10,
        "train_cells": 200000, "seed": 42,
    })
    params.setdefault("combat", {"eb": True, "adjust_variance": True})
    params.setdefault("model", None)
    params.setdefault("log", [])

    page._ba_task_def = task_def
    page._ba_task_id = task_def.get("id")
    page._ba_params = params
    page._ba_locked = locked
    page._ba_save_btn = save_btn
    page._ba_dirty = False
    page._ba_snapshot = copy.deepcopy(params)
    page._ba_log_lines = list(params.get("log") or [])
    page._ba_running = False
    page._ba_cancel = False
    page._ba_updating = False
    page._ba_reporting = False
    save_btn.clicked.connect(lambda _=False: _ba_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_ba_left_panel(ctx, page))
    splitter.addWidget(_ba_center_panel(ctx, page))
    splitter.addWidget(_ba_right_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 1)
    splitter.setSizes([340, 300, 400])
    row.addWidget(splitter, 1)

    row.addWidget(_ba_log_panel(ctx, page))
    layout.addWidget(content, 1)

    _ba_refresh(ctx, page)
    return page


def _ba_log_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
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
    for line in getattr(page, "_ba_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._ba_log = log
    return panel


def _ba_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_ba_log_lines", None)
    if lines is None:
        lines = []
        page._ba_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    log = getattr(page, "_ba_log", None)
    if log is not None:
        for line in str(text).splitlines():
            if line.strip():
                log.appendPlainText(f"[{ts}] {line}")


def _ba_left_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Algorithm</b>"))
    method = _NoWheelComboBox()
    for label, value in _BA_MODES:
        method.addItem(label, value)
    method.setEnabled(not page._ba_locked)
    lay.addWidget(method)
    page._ba_method = method
    desc = QLabel("")
    desc.setWordWrap(True)
    desc.setStyleSheet("color: #6b7785;")
    lay.addWidget(desc)
    page._ba_method_desc = desc

    lay.addWidget(QLabel("<b>Data files</b>"))
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(not page._ba_locked)
    lay.addWidget(files_btn)
    files_count = QLabel("")
    files_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(files_count)
    page._ba_files_count = files_count

    lay.addWidget(QLabel("<b>Batches</b>"))
    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["File", "Batch", "Reference"])
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.setColumnWidth(1, 90)
    table.itemChanged.connect(lambda item: _ba_on_table_item(ctx, page, item))
    lay.addWidget(table, 1)
    page._ba_table = table

    hint = QLabel(
        "Every file needs a batch. Mark the reference (control) sample of "
        "each batch for CytoNorm. Copy / paste to edit the table in Excel."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)

    btns = QHBoxLayout()
    copy_btn = QPushButton("Copy")
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    paste_btn = QPushButton("Paste")
    paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.clicked.connect(lambda _=False: _ba_copy(ctx, page))
    paste_btn.clicked.connect(lambda _=False: _ba_paste(ctx, page))
    btns.addWidget(copy_btn)
    btns.addWidget(paste_btn)
    btns.addStretch()
    lay.addLayout(btns)

    files_btn.clicked.connect(lambda _=False: _ba_select_files(ctx, page))
    method.currentIndexChanged.connect(lambda _=0: _ba_on_method(ctx, page))
    return panel


def _ba_center_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Channels</b>"))
    hint = QLabel(
        "Channels shared by every selected file. These are the channels "
        "that get aligned; other channels pass through unchanged."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    vars_btn = QPushButton("Select channels…")
    vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    vars_btn.setEnabled(not page._ba_locked)
    lay.addWidget(vars_btn)
    vars_box = QListWidget()
    vars_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(vars_box, 1)
    page._ba_vars_box = vars_box
    vars_count = QLabel("")
    vars_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(vars_count)
    page._ba_vars_count = vars_count

    vars_btn.clicked.connect(lambda _=False: _ba_select_vars(ctx, page))
    return panel


def _ba_right_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Algorithm parameters</b>"))

    _hint = partial(_widgets_panels._param_hint, ctx)

    cytonorm_host = QWidget()
    cg = QGridLayout(cytonorm_host)
    cg.setContentsMargins(0, 0, 0, 0)
    cg.setHorizontalSpacing(6)
    cg.setVerticalSpacing(2)
    cg.setColumnStretch(1, 1)
    row = 0
    cg.addWidget(QLabel("Quantiles (nQ)"), row, 0)
    nq = _NoWheelSpinBox()
    nq.setRange(11, 501)
    nq.setSingleStep(10)
    nq.setValue(99)
    cg.addWidget(nq, row, 1)
    row += 1
    cg.addWidget(_hint(
        "Quantiles read per channel and cluster. Higher = finer mapping."
    ), row, 0, 1, 2)
    row += 1
    cg.addWidget(QLabel("SOM grid"), row, 0)
    som = QWidget()
    sl = QHBoxLayout(som)
    sl.setContentsMargins(0, 0, 0, 0)
    sl.setSpacing(4)
    xdim = _NoWheelSpinBox()
    xdim.setRange(4, 30)
    xdim.setValue(10)
    ydim = _NoWheelSpinBox()
    ydim.setRange(4, 30)
    ydim.setValue(10)
    sl.addWidget(xdim)
    sl.addWidget(QLabel("×"))
    sl.addWidget(ydim)
    cg.addWidget(som, row, 1)
    row += 1
    cg.addWidget(_hint(
        "FlowSOM grid. Larger = more, smaller cell groups."
    ), row, 0, 1, 2)
    row += 1
    cg.addWidget(QLabel("Clusters"), row, 0)
    clusters = _NoWheelSpinBox()
    clusters.setRange(2, 100)
    clusters.setValue(10)
    cg.addWidget(clusters, row, 1)
    row += 1
    cg.addWidget(_hint(
        "Cell groups the splines are fitted in."
    ), row, 0, 1, 2)
    row += 1
    cg.addWidget(QLabel("Cells for training"), row, 0)
    cells = _NoWheelSpinBox()
    cells.setRange(1000, 2_000_000)
    cells.setSingleStep(10000)
    cells.setValue(200000)
    cg.addWidget(cells, row, 1)
    row += 1
    cg.addWidget(_hint(
        "Cap on reference cells used to train the SOM."
    ), row, 0, 1, 2)
    row += 1
    cg.addWidget(QLabel("Seed"), row, 0)
    seed = _NoWheelSpinBox()
    seed.setRange(0, 999999)
    seed.setValue(42)
    cg.addWidget(seed, row, 1)
    row += 1
    cg.addWidget(_hint(
        "Random seed for the SOM, so the fit is reproducible."
    ), row, 0, 1, 2)
    lay.addWidget(cytonorm_host)
    page._ba_cytonorm_host = cytonorm_host
    page._ba_nq = nq
    page._ba_xdim = xdim
    page._ba_ydim = ydim
    page._ba_clusters = clusters
    page._ba_train_cells = cells
    page._ba_seed = seed

    combat_host = QWidget()
    cb = QVBoxLayout(combat_host)
    cb.setContentsMargins(0, 0, 0, 0)
    cb.setSpacing(2)
    eb = QCheckBox("Empirical Bayes shrinkage")
    eb.setChecked(True)
    cb.addWidget(eb)
    cb.addWidget(_hint(
        "Pull each batch estimate towards the pooled value. Safer when a "
        "batch has few cells."
    ))
    adjust_var = QCheckBox("Adjust variance as well as location")
    adjust_var.setChecked(True)
    cb.addWidget(adjust_var)
    cb.addWidget(_hint(
        "Also equalise the spread, not just the median. Off = shift means "
        "only."
    ))
    lay.addWidget(combat_host)
    page._ba_combat_host = combat_host
    page._ba_eb = eb
    page._ba_adjust_var = adjust_var

    hint = QLabel("")
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    page._ba_param_hint = hint

    lay.addWidget(QLabel("<b>Run</b>"))
    # Shown only for CytoNorm without a configured FlowSOM extension; the
    # Calculate button is disabled in that state.
    flowsom_hint = QLabel(
        "FlowSOM extension is not configured. Install it in "
        "Extension → Manage Extensions before calculating."
    )
    flowsom_hint.setWordWrap(True)
    flowsom_hint.setStyleSheet(
        "color: #b58900; font-weight: bold; font-size: 8pt;"
    )
    flowsom_hint.setVisible(False)
    lay.addWidget(flowsom_hint)
    page._ba_flowsom_status = flowsom_hint
    buttons = QHBoxLayout()
    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    report_btn = QPushButton("Report")
    report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    report_btn.setToolTip(
        "Write an HTML/PDF report with the before/after distributions"
    )
    buttons.addWidget(calc_btn)
    buttons.addWidget(report_btn)
    lay.addLayout(buttons)
    page._ba_calc_btn = calc_btn
    page._ba_report_btn = report_btn

    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._ba_status = status
    lay.addStretch()

    for widget in (nq, xdim, ydim, clusters, cells, seed, eb, adjust_var):
        widget.setEnabled(not page._ba_locked)

    nq.valueChanged.connect(lambda _=0: _ba_on_param(ctx, page))
    xdim.valueChanged.connect(lambda _=0: _ba_on_param(ctx, page))
    ydim.valueChanged.connect(lambda _=0: _ba_on_param(ctx, page))
    clusters.valueChanged.connect(lambda _=0: _ba_on_param(ctx, page))
    cells.valueChanged.connect(lambda _=0: _ba_on_param(ctx, page))
    seed.valueChanged.connect(lambda _=0: _ba_on_param(ctx, page))
    eb.toggled.connect(lambda _=0: _ba_on_param(ctx, page))
    adjust_var.toggled.connect(lambda _=0: _ba_on_param(ctx, page))
    calc_btn.clicked.connect(lambda _=False: _ba_calculate(ctx, page))
    report_btn.clicked.connect(lambda _=False: _ba_report(ctx, page))
    return panel


def _ba_common_columns(ctx, page) -> list:
    files = page._ba_params.get("files") or []
    if not files:
        return []
    common = set(_data_columns._columns_for(ctx, files[0], page._ba_task_id))
    for path in files[1:]:
        common &= set(_data_columns._columns_for(ctx, path, page._ba_task_id))
    return sorted(common)


def _ba_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._ba_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, p, page._ba_task_id)
        for p in page._ba_params.get("files") or []
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    files = [by_name[n] for n in chosen if n in by_name]
    params = page._ba_params
    params["files"] = files
    batches = params.setdefault("batches", {})
    params["batches"] = {p: batches.get(p, "") for p in files}
    refs = set(params.get("references") or [])
    params["references"] = [p for p in files if p in refs]
    common = set(_ba_common_columns(ctx, page))
    params["columns"] = [c for c in params.get("columns") or [] if c in common]
    _ba_refresh(ctx, page)
    _ba_mark_dirty(ctx, page)


def _ba_select_vars(ctx, page) -> None:
    options = [
        c for c in _ba_common_columns(ctx, page)
        if c not in ("x", "y")
    ]
    if not options:
        QMessageBox.information(
            ctx.host, "Select channels",
            "Select data files with common channels first."
        )
        return
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select channels", options,
        page._ba_params.get("columns") or [], "Filter channels...",
    )
    if chosen is None:
        return
    page._ba_params["columns"] = list(chosen)
    _ba_refresh(ctx, page)
    _ba_mark_dirty(ctx, page)


def _ba_on_method(ctx, page) -> None:
    if getattr(page, "_ba_updating", False):
        return
    page._ba_params["method"] = page._ba_method.currentData() or "cytonorm"
    _ba_refresh(ctx, page)
    _ba_mark_dirty(ctx, page)


def _ba_update_flowsom_ext(ctx, page) -> None:
    """Warn (for CytoNorm) when the external FlowSOM is not configured.

    There is no setup shortcut: the user installs the extension from
    Extension → Manage Extensions, and Calculate stays disabled until it
    is ready.
    """
    method = page._ba_params.get("method") or "cytonorm"
    ready = ctx._ext_ready("flowsom")
    status = getattr(page, "_ba_flowsom_status", None)
    if status is not None:
        status.setVisible(method == "cytonorm" and not ready)
    _ba_update_calc_btn(ctx, page)


def _ba_on_param(ctx, page) -> None:
    if getattr(page, "_ba_updating", False):
        return
    params = page._ba_params
    params["cytonorm"] = {
        "nq": int(page._ba_nq.value()),
        "xdim": int(page._ba_xdim.value()),
        "ydim": int(page._ba_ydim.value()),
        "clusters": int(page._ba_clusters.value()),
        "train_cells": int(page._ba_train_cells.value()),
        "seed": int(page._ba_seed.value()),
    }
    params["combat"] = {
        "eb": bool(page._ba_eb.isChecked()),
        "adjust_variance": bool(page._ba_adjust_var.isChecked()),
    }
    _ba_mark_dirty(ctx, page)


def _ba_on_table_item(ctx, page, item) -> None:
    if getattr(page, "_ba_updating", False) or page._ba_locked:
        return
    row = item.row()
    files = page._ba_params.get("files") or []
    if row < 0 or row >= len(files):
        return
    path = files[row]
    params = page._ba_params
    batches = params.setdefault("batches", {})
    if item.column() == 1:
        value = item.text().strip()
        if batches.get(path, "") == value:
            return
        batches[path] = value
    elif item.column() == 2:
        refs = list(params.get("references") or [])
        checked = item.checkState() == Qt.CheckState.Checked
        if checked and path not in refs:
            refs.append(path)
        elif not checked and path in refs:
            refs.remove(path)
        params["references"] = refs
    else:
        return
    _ba_mark_dirty(ctx, page)
    _ba_update_calc_btn(ctx, page)
    _ba_paint_table(ctx, page)


def _ba_paint_table(ctx, page) -> None:
    """Colour rows whose batch is still empty."""
    table = page._ba_table
    files = page._ba_params.get("files") or []
    batches = page._ba_params.get("batches") or {}
    table.blockSignals(True)
    for row, path in enumerate(files):
        item = table.item(row, 1)
        if item is None:
            continue
        missing = not (batches.get(path) or "").strip()
        item.setBackground(QColor("#fdecea") if missing else QColor("#ffffff"))
        item.setToolTip(
            "Every file must belong to a batch." if missing else ""
        )
    table.blockSignals(False)


def _ba_copy(ctx, page) -> None:
    params = page._ba_params
    refs = set(params.get("references") or [])
    batches = params.get("batches") or {}
    lines = ["File\tBatch\tReference"]
    for path in params.get("files") or []:
        lines.append("\t".join([
            _data_naming._comp_file_name(ctx, path, page._ba_task_id),
            str(batches.get(path, "") or ""),
            "yes" if path in refs else "no",
        ]))
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Batch table copied to the clipboard")


def _ba_paste(ctx, page) -> None:
    text = QApplication.clipboard().text()
    lines = [line.rstrip("\r") for line in text.split("\n") if line.strip()]
    if not lines:
        return
    header = [cell.strip().lower() for cell in lines[0].split("\t")]
    if header[:3] != ["file", "batch", "reference"]:
        QMessageBox.warning(
            ctx.host, "Paste",
            "The pasted table must start with the header "
            "'File\\tBatch\\tReference' (copy the table first).",
        )
        return
    rows = [line.split("\t") for line in lines[1:]]
    files = page._ba_params.get("files") or []
    current = [_data_naming._comp_file_name(ctx, p, page._ba_task_id) for p in files]
    if len(rows) != len(current):
        QMessageBox.warning(
            ctx.host, "Paste",
            f"The pasted table has {len(rows)} row(s) but this module has "
            f"{len(current)} file(s). Copy the table again after changing "
            "the file selection.",
        )
        return
    for index, (expected, row) in enumerate(zip(current, rows)):
        if not row or row[0].strip() != expected:
            QMessageBox.warning(
                ctx.host, "Paste",
                f"Row {index + 1}: expected file '{expected}' but found "
                f"'{row[0].strip() if row else ''}'.",
            )
            return
    truthy = {"yes", "y", "true", "1", "x"}
    batches = {}
    refs = []
    for path, row in zip(files, rows):
        batches[path] = (row[1].strip() if len(row) > 1 else "")
        flag = (row[2].strip().lower() if len(row) > 2 else "")
        if flag in truthy:
            refs.append(path)
    page._ba_params["batches"] = batches
    page._ba_params["references"] = refs
    _ba_refresh(ctx, page)
    _ba_mark_dirty(ctx, page)


def _ba_existing_columns(ctx, page) -> set:
    prefix = page._ba_params.get("model") or {}
    names = set(prefix.get("columns") or [])
    return names


def _ba_model_current(ctx, page) -> bool:
    model = page._ba_params.get("model")
    return (
        isinstance(model, dict)
        and model.get("config_sig") == _ba_config_sig(ctx, page._ba_params)
    )


def _ba_update_calc_btn(ctx, page) -> None:
    params = page._ba_params
    files = params.get("files") or []
    batches = params.get("batches") or {}
    name = str(params.get("method") or "cytonorm")
    ok = bool(files and params.get("columns"))
    if ok:
        ok = all((batches.get(path) or "").strip() for path in files)
    flowsom_missing = False
    if ok and name == "cytonorm":
        refs = [p for p in params.get("references") or [] if p in files]
        ok = bool(refs)
        # CytoNorm trains FlowSOM externally; without the extension the
        # fit cannot run, so Calculate stays disabled.
        if not ctx._ext_ready("flowsom"):
            flowsom_missing = True
            ok = False
    page._ba_calc_btn.setEnabled(ok and not page._ba_locked
                                 and not page._ba_running)
    if flowsom_missing:
        page._ba_calc_btn.setToolTip(
            "Install the FlowSOM extension first "
            "(Extension → Manage Extensions)."
        )
    else:
        page._ba_calc_btn.setToolTip("")
    page._ba_report_btn.setEnabled(
        _ba_model_current(ctx, page) and not page._ba_locked
        and not page._ba_reporting
    )


def _ba_refresh(ctx, page) -> None:
    params = page._ba_params
    page._ba_updating = True
    try:
        page._ba_method.blockSignals(True)
        page._ba_method.setCurrentIndex(
            max(0, page._ba_method.findData(params.get("method", "cytonorm")))
        )
        page._ba_method.blockSignals(False)
        method = params.get("method", "cytonorm")
        page._ba_cytonorm_host.setVisible(method == "cytonorm")
        _ba_update_flowsom_ext(ctx, page)
        page._ba_combat_host.setVisible(method == "combat")
        page._ba_method_desc.setText(
            "Aligns every file to the reference samples of its batch: "
            "quantile normalisation inside each FlowSOM cluster, fitted "
            "against the mean reference distribution."
            if method == "cytonorm" else
            "Corrects each batch/channel by a location and scale "
            "difference estimated with empirical Bayes. Reference samples "
            "are not used."
        )
        # Each parameter carries its own note; this line only covers the
        # points that apply to the algorithm as a whole.
        page._ba_param_hint.setText(
            "Reference samples drive the fit — files from a batch without "
            "one are left unchanged. Put a Scale (log10) module upstream "
            "to align on a log scale."
            if method == "cytonorm" else
            "Reference samples are not used. ComBat assumes an "
            "approximately log-normal distribution — add a Scale (log10) "
            "module upstream if needed."
        )

        cytonorm = params.get("cytonorm") or {}
        for widget, key, default in (
            (page._ba_nq, "nq", 99),
            (page._ba_xdim, "xdim", 10),
            (page._ba_ydim, "ydim", 10),
            (page._ba_clusters, "clusters", 10),
            (page._ba_train_cells, "train_cells", 200000),
            (page._ba_seed, "seed", 42),
        ):
            widget.blockSignals(True)
            widget.setValue(int(cytonorm.get(key, default)))
            widget.blockSignals(False)
        combat = params.get("combat") or {}
        page._ba_eb.blockSignals(True)
        page._ba_eb.setChecked(bool(combat.get("eb", True)))
        page._ba_eb.blockSignals(False)
        page._ba_adjust_var.blockSignals(True)
        page._ba_adjust_var.setChecked(bool(combat.get("adjust_variance", True)))
        page._ba_adjust_var.blockSignals(False)

        files = params.get("files") or []
        batches = params.get("batches") or {}
        refs = set(params.get("references") or [])
        table = page._ba_table
        table.blockSignals(True)
        table.setRowCount(len(files))
        for row, path in enumerate(files):
            name_item = QTableWidgetItem(
                _data_naming._comp_file_name(ctx, path, page._ba_task_id)
            )
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name_item.setToolTip(path)
            table.setItem(row, 0, name_item)
            batch_item = QTableWidgetItem(str(batches.get(path, "") or ""))
            batch_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable
            )
            table.setItem(row, 1, batch_item)
            ref_item = QTableWidgetItem("")
            ref_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            ref_item.setCheckState(
                Qt.CheckState.Checked if path in refs
                else Qt.CheckState.Unchecked
            )
            table.setItem(row, 2, ref_item)
        table.blockSignals(False)
        page._ba_files_count.setText(f"{len(files)} file(s) selected")

        columns = params.get("columns") or []
        page._ba_vars_box.clear()
        for column in columns:
            page._ba_vars_box.addItem(str(column))
        page._ba_vars_count.setText(f"{len(columns)} channel(s) selected")
    finally:
        page._ba_updating = False
    _ba_paint_table(ctx, page)
    _ba_update_calc_btn(ctx, page)


def _ba_validate(ctx, page):
    params = page._ba_params
    if not params.get("files"):
        return False, "Select at least one data file."
    if not params.get("columns"):
        return False, "Select at least one channel to align."
    batches = params.get("batches") or {}
    missing = [
        _data_naming._comp_file_name(ctx, path, page._ba_task_id)
        for path in params["files"]
        if not (batches.get(path) or "").strip()
    ]
    if missing:
        return False, (
            "Every file needs a batch. Missing: " + ", ".join(missing[:5])
            + ("…" if len(missing) > 5 else "")
        )
    if params.get("method") == "cytonorm":
        refs = [p for p in params.get("references") or [] if p in params["files"]]
        if not refs:
            return False, (
                "CytoNorm needs at least one reference sample "
                "(tick the Reference column)."
            )
    return True, ""


def _ba_calculate(ctx, page) -> None:
    ok, message = _ba_validate(ctx, page)
    if not ok:
        QMessageBox.warning(ctx.host, "Calculate", message)
        return
    if (page._ba_params.get("method") or "cytonorm") == "cytonorm" and (
        not ctx._ext_ready("flowsom")
    ):
        QMessageBox.information(
            ctx.host, "Calculate",
            "FlowSOM extension is not configured.\n"
            "CytoNorm trains FlowSOM in a separate Python environment. "
            "Open Extension → Manage Extensions and install FlowSOM "
            "first.",
        )
        return
    if _ba_model_current(ctx, page):
        reply = QMessageBox.question(
            ctx.host, "Calculate",
            "A model is already fitted for this configuration.\n"
            "Fit it again and overwrite?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    page._ba_running = True
    page._ba_cancel = False
    page._ba_status.setText("Calculating…")
    _ba_update_calc_btn(ctx, page)

    q = queue.Queue()
    page._ba_queue = q
    page._ba_thread = threading.Thread(
        target=partial(_ba_run, ctx), args=(page, q), daemon=True
    )
    page._ba_thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(150)
    timer.timeout.connect(lambda: _ba_poll(ctx, page))
    timer.start()
    page._ba_timer = timer


def _ba_run(ctx, page, q) -> None:
    params = page._ba_params
    method = params.get("method", "cytonorm")
    try:
        label = dict(_BA_MODES).get(method, method)
        q.put(("log", f"— Starting {label} —"))
        q.put(("status", "Fitting the batch model…"))
        if method == "combat":
            arrays, meta = _ba_fit_combat(ctx, page, q)
        else:
            arrays, meta = _ba_fit_cytonorm(ctx, page, q)
        if getattr(page, "_ba_cancel", False):
            q.put(("error", "Calculation cancelled."))
            return
        q.put(("status", "Writing the model…"))
        model = _ba_write_model(ctx, page, arrays, meta)
        q.put(("done", model))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _ba_read_matrix(ctx, page, path, columns):
    import numpy as np

    values = _data_columns._column_data(ctx, page._ba_task_id, path, columns)
    missing = [c for c in columns if c not in values]
    if missing:
        raise ValueError(
            f"{_data_naming._comp_file_name(ctx, path, page._ba_task_id)} is missing "
            f"column(s): {', '.join(missing[:4])}"
        )
    matrix = np.column_stack(
        [np.asarray(values[c], dtype="float64") for c in columns]
    )
    if matrix.size == 0:
        return matrix
    return matrix[np.isfinite(matrix).all(axis=1)]


def _ba_fit_cytonorm(ctx, page, q):
    """Train FlowSOM on the references, then fit per-cluster splines.

    FlowSOM runs in the user's external Python environment (the same
    extension the Cluster module uses), so Smage never imports it.
    """
    import numpy as np
    from scipy.interpolate import PchipInterpolator

    params = page._ba_params
    cfg = params.get("cytonorm") or {}
    files = list(params.get("files") or [])
    columns = list(params.get("columns") or [])
    batches = params.get("batches") or {}
    refs = [path for path in files if path in set(params.get("references") or [])]
    if not refs:
        raise ValueError("CytoNorm needs at least one reference sample")
    batch_names = sorted({str(batches.get(path) or "") for path in files})
    batch_index = {name: i for i, name in enumerate(batch_names)}
    file_batch = {path: batch_index[str(batches.get(path) or "")] for path in files}
    ref_set = set(refs)

    nq = max(11, int(cfg.get("nq") or 99))
    xdim = max(4, int(cfg.get("xdim") or 10))
    ydim = max(4, int(cfg.get("ydim") or 10))
    want_clusters = max(2, int(cfg.get("clusters") or 10))
    cap = max(1000, int(cfg.get("train_cells") or 200000))
    seed = int(cfg.get("seed") if cfg.get("seed") is not None else 42)
    per_file = max(2000, cap // max(1, len(refs)))
    rng = np.random.default_rng(seed)

    blocks = []
    block_batch = []
    for index, path in enumerate(refs):
        if getattr(page, "_ba_cancel", False):
            raise ValueError("Calculation cancelled.")
        name = _data_naming._comp_file_name(ctx, path, page._ba_task_id)
        q.put(("log", f"Reading reference {index + 1}/{len(refs)}: {name}"))
        matrix = _ba_read_matrix(ctx, page, path, columns)
        if matrix.shape[0] < _BA_MIN_CELLS:
            q.put(("log", f"  {name}: too few cells, skipped"))
            continue
        if matrix.shape[0] > per_file:
            pick = np.sort(rng.choice(matrix.shape[0], per_file, replace=False))
            matrix = matrix[pick]
        blocks.append(matrix.astype("float32"))
        block_batch.append(np.full(matrix.shape[0], file_batch[path], dtype=np.int32))
        q.put(("log", f"  {name}: {matrix.shape[0]:,} cell(s) used for training"))
    if not blocks:
        raise ValueError("No reference sample produced usable cells")
    train = np.vstack(blocks)
    train_batch = np.concatenate(block_batch)

    q.put(("status", f"Training FlowSOM ({xdim}×{ydim})…"))
    q.put(("log", f"FlowSOM on {train.shape[0]:,} cells, grid {xdim}×{ydim}, "
                  f"{want_clusters} cluster(s)"))
    result = ctx._flowsom_external(
        page, q, train, xdim, ydim, want_clusters, seed,
        cancel_flag="_ba_cancel", area="batch_align",
    )
    codes = result["codes"]
    node_cluster = result["node_cluster"]
    # Metacluster of every training cell (the unit the splines are fit on).
    cell_cluster = result["cell_cluster"]
    n_clusters = int(result["n_clusters"])
    if cell_cluster.size != train.shape[0]:
        raise ValueError("FlowSOM returned an unexpected number of labels")

    q.put(("status", "Fitting per-cluster splines…"))
    probs = np.arange(1, nq + 1) / (nq + 1)
    n_batches = len(batch_names)
    grid_points = 256
    knots_x = np.zeros(
        (n_batches, n_clusters, len(columns), grid_points), dtype="float32"
    )
    knots_y = np.zeros_like(knots_x)
    has_spline = np.zeros((n_batches, n_clusters), dtype=bool)
    n_identity = 0
    for cluster in range(n_clusters):
        in_cluster = cell_cluster == cluster
        quantiles = {}
        for batch in range(n_batches):
            mask = in_cluster & (train_batch == batch)
            count = int(mask.sum())
            if count < _BA_MIN_CELLS:
                continue
            quantiles[batch] = np.percentile(
                train[mask], probs * 100.0, axis=0
            ).T  # (channels, nq)
        if len(quantiles) < 2:
            n_identity += 1
            continue
        goal = np.mean(list(quantiles.values()), axis=0)
        for batch, batch_quantiles in quantiles.items():
            for j in range(len(columns)):
                source = np.asarray(batch_quantiles[j], dtype="float64")
                target = np.asarray(goal[j], dtype="float64")
                unique, first = np.unique(source, return_index=True)
                if unique.size < 2:
                    continue
                fitted = PchipInterpolator(unique, target[first])
                grid = np.linspace(unique[0], unique[-1], grid_points)
                knots_x[batch, cluster, j] = grid
                # PCHIP is monotone between the knots, so the evaluated
                # values need no clipping; clipping to the *source* range
                # would cut off a genuine shift.
                knots_y[batch, cluster, j] = fitted(grid)
            has_spline[batch, cluster] = True
    q.put(("log", f"{n_clusters} cluster(s); "
                  f"{n_identity} kept the identity mapping (too few cells)"))
    trained = int(has_spline.sum())
    q.put(("log", f"{trained} cluster × batch spline(s) fitted"))

    # A per-batch spline over all reference cells. It is the fallback for
    # cells whose cluster has no batch-specific spline — for example when a
    # strong batch effect splits the SOM into batch-specific clusters.
    global_x = np.zeros((n_batches, len(columns), grid_points), dtype="float32")
    global_y = np.zeros((n_batches, len(columns), grid_points), dtype="float32")
    has_global = np.zeros(n_batches, dtype=bool)
    overall = {}
    for batch in range(n_batches):
        mask = train_batch == batch
        if int(mask.sum()) < _BA_MIN_CELLS:
            continue
        overall[batch] = np.percentile(
            train[mask], probs * 100.0, axis=0
        ).T
    if len(overall) >= 2:
        goal = np.mean(list(overall.values()), axis=0)
        for batch, batch_quantiles in overall.items():
            for j in range(len(columns)):
                source = np.asarray(batch_quantiles[j], dtype="float64")
                target = np.asarray(goal[j], dtype="float64")
                unique, first = np.unique(source, return_index=True)
                if unique.size < 2:
                    continue
                fitted = PchipInterpolator(unique, target[first])
                grid = np.linspace(unique[0], unique[-1], grid_points)
                global_x[batch, j] = grid
                global_y[batch, j] = fitted(grid)
            has_global[batch] = True
    q.put(("log", f"Fallback spline(s) for {int(has_global.sum())} batch(es)"))

    arrays = {
        "codes": codes,
        "node_cluster": node_cluster,
        "knots_x": knots_x,
        "knots_y": knots_y,
        "has_spline": has_spline,
        "global_x": global_x,
        "global_y": global_y,
        "has_global": has_global,
    }
    meta = {
        "method": "cytonorm",
        "columns": columns,
        "batches": batch_names,
        "file_batch": file_batch,
        "references": [p for p in files if p in ref_set],
        "clusters": n_clusters,
        "splines": trained,
        "train_cells": int(train.shape[0]),
    }
    return arrays, meta


def _ba_fit_combat(ctx, page, q):
    """Estimate a location/scale correction per batch and channel."""
    import numpy as np

    params = page._ba_params
    cfg = params.get("combat") or {}
    files = list(params.get("files") or [])
    columns = list(params.get("columns") or [])
    batches = params.get("batches") or {}
    refs = set(params.get("references") or [])
    batch_names = sorted({str(batches.get(path) or "") for path in files})
    batch_index = {name: i for i, name in enumerate(batch_names)}
    file_batch = {path: batch_index[str(batches.get(path) or "")] for path in files}
    n_batches = len(batch_names)
    n_channels = len(columns)

    counts = np.zeros((n_batches, n_channels), dtype="float64")
    sums = np.zeros((n_batches, n_channels), dtype="float64")
    squares = np.zeros((n_batches, n_channels), dtype="float64")
    for index, path in enumerate(files):
        if getattr(page, "_ba_cancel", False):
            raise ValueError("Calculation cancelled.")
        name = _data_naming._comp_file_name(ctx, path, page._ba_task_id)
        q.put(("log", f"Reading {index + 1}/{len(files)}: {name}"))
        matrix = _ba_read_matrix(ctx, page, path, columns)
        if matrix.shape[0] == 0:
            q.put(("log", f"  {name}: no finite rows, skipped"))
            continue
        batch = file_batch[path]
        counts[batch] += matrix.shape[0]
        sums[batch] += matrix.sum(axis=0)
        squares[batch] += np.einsum("ij,ij->j", matrix, matrix)
        q.put(("log", f"  {name}: {matrix.shape[0]:,} cell(s)"))

    usable = counts >= 2
    if not usable.all(axis=0).any():
        raise ValueError("Not enough cells to estimate a Correction")
    mean = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    var = np.divide(
        squares - counts * mean * mean,
        np.maximum(counts - 1.0, 1.0),
        out=np.zeros_like(sums), where=counts > 1,
    )
    var = np.clip(var, 0.0, None)
    total = counts.sum(axis=0)
    grand = (sums.sum(axis=0) / np.maximum(total, 1.0))
    pooled = (
        (np.maximum(counts - 1.0, 0.0) * var).sum(axis=0)
        + (counts * (mean - grand[None, :]) ** 2).sum(axis=0)
    ) / np.maximum(total, 1.0)
    pooled = np.clip(pooled, 1e-12, None)

    scale_var = np.sqrt(pooled)[None, :]
    standard_mean = (mean - grand[None, :]) / scale_var
    standard_var = var / pooled[None, :]

    gamma_bar = np.mean(standard_mean, axis=0)
    t2 = np.var(standard_mean, axis=0, ddof=1) if n_batches > 1 else np.zeros(n_channels)
    t2 = np.where(np.isfinite(t2), t2, 0.0)
    prior_mean = np.mean(standard_var, axis=0)
    prior_var = np.var(standard_var, axis=0, ddof=1) if n_batches > 1 else np.zeros(n_channels)
    prior_var = np.where(np.isfinite(prior_var) & (prior_var > 1e-12), prior_var, 1e-12)
    a_prior = (2.0 * prior_var + prior_mean ** 2) / prior_var
    b_prior = (prior_mean * prior_var + prior_mean ** 3) / prior_var

    gamma = standard_mean.copy()
    delta = standard_var.copy()
    if bool(cfg.get("eb", True)):
        for _ in range(200):
            new_gamma = (
                t2[None, :] * counts * gamma + delta * gamma_bar[None, :]
            ) / (t2[None, :] * counts + delta + 1e-12)
            sum2 = (
                np.maximum(counts - 1.0, 0.0) * standard_var
                + counts * (standard_mean - new_gamma) ** 2
            )
            new_delta = (0.5 * sum2 + b_prior[None, :]) / (
                counts / 2.0 + a_prior[None, :] - 1.0
            )
            new_delta = np.clip(new_delta, 1e-8, None)
            change = np.max(np.abs(new_gamma - gamma) / (np.abs(gamma) + 1e-8))
            change = max(change, float(
                np.max(np.abs(new_delta - delta) / (np.abs(delta) + 1e-8))
            ))
            gamma, delta = new_gamma, new_delta
            if change < 1e-4:
                break
    if not bool(cfg.get("adjust_variance", True)):
        delta = np.ones_like(delta)

    divisor = np.sqrt(delta)
    affine = np.zeros((n_batches, n_channels, 2), dtype="float32")
    for batch in range(n_batches):
        a = 1.0 / divisor[batch]
        shift = grand * (1.0 - a) - a * gamma[batch] * np.sqrt(pooled)
        affine[batch, :, 0] = a
        affine[batch, :, 1] = shift
    # Degenerate channels stay unchanged.
    bad = (~np.isfinite(affine[:, :, 0])) | (~np.isfinite(affine[:, :, 1]))
    affine[:, :, 0] = np.where(bad, 1.0, affine[:, :, 0])
    affine[:, :, 1] = np.where(bad, 0.0, affine[:, :, 1])

    q.put(("log", f"ComBat fitted on {int(total.max()):,} cell(s) per channel "
                  f"(max), {n_batches} batch(es)"))
    arrays = {"affine": affine}
    meta = {
        "method": "combat",
        "columns": columns,
        "batches": batch_names,
        "file_batch": file_batch,
        "references": [p for p in files if p in refs],
        "cells": int(total.sum()),
    }
    return arrays, meta


def _ba_write_model(ctx, page, arrays, meta):
    """Save the fitted arrays into the module folder and describe them."""
    import numpy as np

    folder = _data_files._module_dir(ctx, page._ba_task_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "alignment.npz"
    np.savez_compressed(path, **arrays)
    stat = path.stat()
    model_id = f"{int(stat.st_mtime_ns)}-{int(stat.st_size)}"
    model = {
        "npz": _data_files._module_rel(ctx, page._ba_task_id, "alignment.npz"),
        "model_id": model_id,
        "config_sig": _ba_config_sig(ctx, page._ba_params),
        "method": meta.get("method"),
        "columns": list(meta.get("columns") or []),
        "batches": list(meta.get("batches") or []),
        "file_batch": dict(meta.get("file_batch") or {}),
        "references": list(meta.get("references") or []),
        "created": datetime.now().isoformat(timespec="seconds"),
        "clusters": int(meta.get("clusters") or 0),
        "splines": int(meta.get("splines") or 0),
        "train_cells": int(meta.get("train_cells") or 0),
    }
    return model


def _ba_poll(ctx, page) -> None:
    q = getattr(page, "_ba_queue", None)
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
            _ba_log(ctx, page, msg[1])
        elif kind == "status":
            page._ba_status.setText(msg[1])
        elif kind == "done":
            model = msg[1]
            params = page._ba_params
            params["model"] = model
            page._ba_task_def["params"] = params
            if ctx.project is not None:
                ctx.project.save()
            page._ba_snapshot = copy.deepcopy(params)
            page._ba_dirty = False
            _ba_update_save_btn(ctx, page)
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    page._ba_running = False
    timer = getattr(page, "_ba_timer", None)
    if timer is not None:
        timer.stop()
        page._ba_timer = None
    if error:
        _ba_log(ctx, page, f"✗ {error}")
        page._ba_status.setText("Failed")
        QMessageBox.warning(ctx.host, "Calculate", error)
    else:
        method = page._ba_params.get("method")
        if method == "cytonorm":
            summary = (
                f"CytoNorm fitted: {page._ba_params['model'].get('clusters')} "
                f"cluster(s), {page._ba_params['model'].get('splines')} "
                "cluster × batch correction(s)."
            )
        else:
            summary = (
                f"ComBat fitted for "
                f"{len(page._ba_params['model'].get('batches') or [])} batch(es)."
            )
        page._ba_status.setText(summary)
        _ba_log(ctx, page, summary)
        ctx.statusBar().showMessage(summary)
        ctx._module_saved(page)
    _ba_update_calc_btn(ctx, page)


def _ba_mark_dirty(ctx, page) -> None:
    page._ba_dirty = True
    _ba_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _ba_update_save_btn(ctx, page) -> None:
    page._ba_save_btn.setEnabled(page._ba_dirty and not page._ba_locked)


def _ba_save_now(ctx, page) -> None:
    params = page._ba_params
    params["log"] = list(getattr(page, "_ba_log_lines", []) or [])
    page._ba_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._ba_snapshot = copy.deepcopy(params)
    page._ba_dirty = False
    _ba_update_save_btn(ctx, page)
    _ba_log(ctx, page, "Settings saved")
    ctx.statusBar().showMessage("Batch Alignment settings saved")
    ctx._module_saved(page)


def _ba_close(ctx, page) -> None:
    if getattr(page, "_ba_running", False) or getattr(page, "_ba_reporting", False):
        reply = QMessageBox.question(
            ctx.host, "Close Batch Alignment",
            "A calculation is still running.\nStop it and close? "
            "This run's results will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._ba_cancel = True
        return
    if getattr(page, "_ba_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Batch Alignment",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log_lines = list(getattr(page, "_ba_log_lines", []) or [])
        page._ba_task_def["params"] = page._ba_snapshot
        page._ba_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)


def _ba_report(ctx, page) -> None:
    if not _ba_model_current(ctx, page):
        QMessageBox.information(
            ctx.host, "Report", "Calculate first, then run the report."
        )
        return
    if getattr(page, "_ba_reporting", False):
        return
    page._ba_reporting = True
    page._ba_cancel = False
    page._ba_status.setText("Building report…")
    _ba_update_calc_btn(ctx, page)

    q = queue.Queue()
    page._ba_report_queue = q
    page._ba_report_thread = threading.Thread(
        target=partial(_ba_report_run, ctx), args=(page, q), daemon=True
    )
    page._ba_report_thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(150)
    timer.timeout.connect(lambda: _ba_report_poll(ctx, page))
    timer.start()
    page._ba_report_timer = timer


def _ba_report_run(ctx, page, q) -> None:
    import numpy as np

    try:
        params = page._ba_params
        stage = _ba_stage(ctx, page._ba_task_def)
        if stage is None:
            raise ValueError("The fitted model is no longer available")
        columns = list(stage["columns"])
        files = list(params.get("files") or [])
        batches = params.get("batches") or {}
        refs = set(params.get("references") or [])
        use_groups = stage["method"] == "cytonorm"
        names = list(stage.get("batches") or [])
        batch_of = {
            str(path): int(index)
            for path, index in (stage.get("files") or {}).items()
        }
        rng = np.random.default_rng(20240517)
        curves = {}
        cell_counts = {}
        file_rows = []
        n_files = len(files)
        for index, path in enumerate(files):
            if getattr(page, "_ba_cancel", False):
                q.put(("error", "Report cancelled."))
                return
            name = _data_naming._comp_file_name(ctx, path, page._ba_task_id)
            q.put(("progress", index + 1, n_files, name))
            if path not in batch_of:
                continue
            values = _data_columns._column_data(ctx, page._ba_task_id, path, columns)
            missing = [c for c in columns if c not in values]
            if missing:
                q.put(("log", f"Skipped {name}: missing {missing[0]}"))
                continue
            matrix = np.column_stack(
                [np.asarray(values[c], dtype="float64") for c in columns]
            )
            rows = matrix.shape[0]
            if rows == 0:
                continue
            pick = (
                np.sort(rng.choice(rows, _BA_REPORT_SAMPLE, replace=False))
                if rows > _BA_REPORT_SAMPLE else np.arange(rows)
            )
            sample = matrix[pick]
            corrected = _apply_batch_align(
                stage,
                {column: sample[:, j] for j, column in enumerate(columns)},
                path,
            )
            batch = batch_of[path]
            group = "ref" if (use_groups and path in refs) else "all"
            key_group = group
            cell_counts[(batch, key_group)] = (
                cell_counts.get((batch, key_group), 0) + rows
            )
            file_rows.append({
                "name": name,
                "batch": names[batch] if batch < len(names) else str(
                    batches.get(path) or ""
                ),
                "reference": "yes" if path in refs else "no",
                "rows": rows,
                "used": int(len(pick)),
            })
            cap = _BA_REPORT_CAP
            for j, column in enumerate(columns):
                before = sample[:, j]
                after = np.asarray(
                    corrected.get(column, before), dtype="float64"
                )
                entry = curves.get((batch, key_group, j))
                if entry is None:
                    entry = {"before": before, "after": after}
                    curves[(batch, key_group, j)] = entry
                else:
                    entry["before"] = np.concatenate([entry["before"], before])
                    entry["after"] = np.concatenate([entry["after"], after])
                if entry["before"].size > cap:
                    keep = np.sort(
                        rng.choice(entry["before"].size, cap, replace=False)
                    )
                    entry["before"] = entry["before"][keep]
                    entry["after"] = entry["after"][keep]
        if not curves:
            raise ValueError("No data could be read for the report")
        payload = {
            "method": stage["method"],
            "columns": columns,
            "batches": names,
            "curves": curves,
            "cell_counts": cell_counts,
            "file_rows": file_rows,
            "use_groups": use_groups,
            "params": copy.deepcopy(params),
        }
        q.put(("done", payload))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _ba_report_poll(ctx, page) -> None:
    q = getattr(page, "_ba_report_queue", None)
    if q is None:
        return
    finished = False
    error = None
    payload = None
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "progress":
            page._ba_status.setText(
                f"Reading files for the report: {msg[1]}/{msg[2]} ({msg[3]})"
            )
        elif kind == "log":
            _ba_log(ctx, page, msg[1])
        elif kind == "done":
            payload = msg[1]
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    timer = getattr(page, "_ba_report_timer", None)
    if timer is not None:
        timer.stop()
        page._ba_report_timer = None
    page._ba_reporting = False
    if error:
        page._ba_status.setText("Report failed")
        _ba_log(ctx, page, f"✗ {error}")
        QMessageBox.warning(ctx.host, "Report", error)
        _ba_update_calc_btn(ctx, page)
        return
    try:
        page._ba_status.setText("Writing the report…")
        QApplication.processEvents()
        target = _ba_write_report(ctx, page, payload)
    except Exception as exc:  # noqa: BLE001
        page._ba_status.setText("Report failed")
        _ba_log(ctx, page, f"✗ {exc}")
        QMessageBox.warning(ctx.host, "Report", str(exc))
        _ba_update_calc_btn(ctx, page)
        return
    page._ba_status.setText("Report written")
    _ba_log(ctx, page, f"Report written to {target}")
    ctx.statusBar().showMessage("Batch Alignment report written")
    _ba_update_calc_btn(ctx, page)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def _ba_density(ctx, values, low, high, log_axis, bins=256, sigma=1.6):
    """A smooth density curve on a common grid."""
    import numpy as np
    from scipy.ndimage import gaussian_filter1d

    values = np.asarray(values, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size == 0 or not (high > low):
        return np.array([low, high]), np.array([0.0, 0.0])
    if log_axis:
        values = np.log10(np.clip(values, 1e-12, None))
    counts, edges = np.histogram(values, bins=bins, range=(low, high))
    smooth = gaussian_filter1d(counts.astype("float64"), sigma)
    peak = smooth.max() if smooth.size else 0.0
    if peak <= 0:
        peak = 1.0
    centers = 0.5 * (edges[1:] + edges[:-1])
    return centers, smooth / peak


def _ba_write_report(ctx, page, payload):
    """Render the figures with matplotlib and write HTML + PDF."""
    import base64
    import io

    import numpy as np
    from matplotlib import colormaps
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    folder = _data_files._module_dir(ctx, page._ba_task_id) / "report"
    folder.mkdir(parents=True, exist_ok=True)
    columns = payload["columns"]
    batches = payload["batches"]
    curves = payload["curves"]
    cell_counts = payload["cell_counts"]
    file_rows = payload["file_rows"]
    params = payload["params"]
    method = payload["method"]
    cytonorm = params.get("cytonorm") or {}
    combat = params.get("combat") or {}
    groups = ["ref", "all"] if payload["use_groups"] else ["all"]

    palette = colormaps["tab10"]
    colors = [palette(i % 10) for i in range(max(1, len(batches)))]
    group_label = {
        "ref": "Reference samples", "all": "Other samples"
        if payload["use_groups"] else "Samples",
    }

    def stat_pair(values):
        values = np.asarray(values, dtype="float64")
        values = values[np.isfinite(values)]
        if values.size == 0:
            return float("nan"), float("nan")
        median = float(np.median(values))
        return median, float(np.median(np.abs(values - median)))

    summary_rows = []
    channel_svg = []
    pdf_path = folder / "batch_report.pdf"
    with PdfPages(pdf_path) as pdf:
        for j, channel in enumerate(columns):
            rows_here = []
            for group in groups:
                for batch in range(len(batches)):
                    entry = curves.get((batch, group, j))
                    if entry is None or entry["before"].size == 0:
                        continue
                    before_median, before_mad = stat_pair(entry["before"])
                    after_median, after_mad = stat_pair(entry["after"])
                    rows_here.append({
                        "batch": batch,
                        "group": group,
                        "n": int(cell_counts.get((batch, group), 0)),
                        "median_before": before_median,
                        "median_after": after_median,
                        "mad_before": before_mad,
                        "mad_after": after_mad,
                    })
            if not rows_here:
                continue
            # Batch spread: references first, all samples as a fallback.
            spread_group = None
            if payload["use_groups"] and any(
                r["group"] == "ref" for r in rows_here
            ) and len({r["batch"] for r in rows_here if r["group"] == "ref"}) > 1:
                spread_group = "ref"
            else:
                spread_group = "all"
            spread_rows = [r for r in rows_here if r["group"] == spread_group]
            spread_rows = spread_rows or rows_here
            medians_before = [r["median_before"] for r in spread_rows]
            medians_after = [r["median_after"] for r in spread_rows]
            pooled_before = float(np.median(medians_before))
            pooled_after = float(np.median(medians_after))
            spread_before = max(
                abs(value - pooled_before) for value in medians_before
            )
            spread_after = max(
                abs(value - pooled_after) for value in medians_after
            )
            reduction = (
                100.0 * (spread_before - spread_after) / spread_before
                if spread_before > 0 else 0.0
            )
            summary_rows.append({
                "channel": channel, "before": spread_before,
                "after": spread_after, "reduction": reduction,
                "group": spread_group,
            })

            all_values = []
            for (batch, group, channel_index), entry in curves.items():
                if channel_index != j:
                    continue
                all_values.append(entry["before"])
                all_values.append(entry["after"])
            stacked = np.concatenate(all_values) if all_values else np.array([])
            stacked = stacked[np.isfinite(stacked)]
            if stacked.size == 0:
                continue
            low = float(np.min(stacked))
            high = float(np.max(stacked))
            log_axis = low > 0 and high / max(low, 1e-12) > 100.0
            if log_axis:
                low, high = np.log10(low), np.log10(high)
            if not (high > low):
                high = low + 1.0

            n_rows = len(rows_here)
            height = 2.0 + 0.42 * n_rows
            fig = Figure(figsize=(11.0, height), dpi=150)
            FigureCanvasAgg(fig)
            axes = fig.subplots(1, 2, sharex=True)
            for ax, panel, title in zip(
                axes, ("before", "after"), ("Before correction", "After correction")
            ):
                baseline = 0.0
                previous_group = None
                ticks = []
                tick_labels = []
                for row in rows_here:
                    batch = row["batch"]
                    group = row["group"]
                    entry = curves.get((batch, group, j))
                    if entry is None:
                        continue
                    if previous_group is not None and group != previous_group:
                        ax.axhline(baseline + 0.45, color="#c9ced6",
                                   linewidth=0.6, linestyle=(0, (2, 2)))
                        baseline -= 0.55
                    previous_group = group
                    centers, density = _ba_density(ctx,
                        entry[panel], low, high, log_axis
                    )
                    values = np.power(10.0, centers) if log_axis else centers
                    color = colors[batch % len(colors)]
                    reference = group == "ref"
                    ax.fill_between(
                        values, baseline, baseline + density * 0.8,
                        color=color, alpha=0.30 if reference else 0.16,
                        linewidth=0,
                    )
                    ax.plot(
                        values, baseline + density * 0.8, color=color,
                        linewidth=1.5 if reference else 1.0,
                    )
                    ticks.append(baseline)
                    tick_labels.append(
                        f"{batches[batch]} · {row['n']:,} cells"
                        + (" · ref" if reference else "")
                    )
                    baseline -= 1.0
                ax.set_ylim(baseline + 0.2, 1.05)
                ax.set_yticks(ticks)
                ax.set_yticklabels(tick_labels, fontsize=6.5, color="#333333")
                ax.tick_params(axis="y", length=0)
                ax.set_title(title, fontsize=9, loc="left")
                for side in ("top", "right", "left"):
                    ax.spines[side].set_visible(False)
                ax.spines["bottom"].set_color("#9aa0a6")
                ax.tick_params(axis="x", labelsize=7, colors="#444444")
                if log_axis:
                    ax.set_xscale("log")
                ax.set_xlabel(
                    f"{channel}" + (" (log10)" if log_axis else ""),
                    fontsize=8,
                )
            axes[0].set_xlim(low if not log_axis else 10 ** low,
                             high if not log_axis else 10 ** high)
            fig.suptitle(
                f"{channel} — batch effect before / after", fontsize=10,
                x=0.01, ha="left",
            )
            fig.tight_layout(rect=(0, 0, 1, 0.96))
            safe = "".join(
                ch if (ch.isalnum() or ch in "-_") else "_" for ch in channel
            )
            fig.savefig(folder / f"{safe}.svg", format="svg")
            fig.savefig(folder / f"{safe}.png", dpi=300)
            buffer = io.BytesIO()
            fig.savefig(buffer, format="svg")
            channel_svg.append({
                "channel": channel,
                "svg": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "rows": rows_here,
                "log": log_axis,
            })
            pdf.savefig(fig)

    overall = (
        sum(row["reduction"] for row in summary_rows) / len(summary_rows)
        if summary_rows else 0.0
    )

    def fmt(value):
        if value is None or not np.isfinite(value):
            return "—"
        absolute = abs(value)
        if absolute >= 1000 or (0 < absolute < 0.01):
            return f"{value:.3g}"
        return f"{value:,.4g}"

    method_line = (
        f"CytoNorm · nQ {cytonorm.get('nq')} · SOM {cytonorm.get('xdim')}×"
        f"{cytonorm.get('ydim')} · {cytonorm.get('clusters')} cluster(s) · "
        f"{int(cytonorm.get('train_cells') or 0):,} training cells"
        if method == "cytonorm" else
        f"ComBat · empirical Bayes "
        f"{'on' if combat.get('eb', True) else 'off'} · variance "
        f"{'adjusted' if combat.get('adjust_variance', True) else 'kept'}"
    )

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Batch alignment report</title><style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;",
        "margin:28px auto;max-width:1080px;color:#222;line-height:1.45}",
        "h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 6px}",
        "table{border-collapse:collapse;width:100%;font-size:12.5px}",
        "th,td{border-bottom:1px solid #e3e7ee;padding:5px 8px;text-align:right}",
        "th:first-child,td:first-child{text-align:left}",
        "th{background:#f5f8fb;font-weight:600}",
        ".muted{color:#6b7785;font-size:12px}",
        ".good{color:#1b7f3b}.bad{color:#b3261e}",
        "img{max-width:100%;border:1px solid #e3e7ee;border-radius:4px}",
        "figure{margin:8px 0 12px}",
        "</style></head><body>",
        f"<h1>Batch alignment report — {ctx.project.name}</h1>",
        f"<div class='muted'>Module: {page._ba_task_id} · "
        f"generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>",
        f"<p><b>Method:</b> {method_line}<br>",
        f"<b>Channels ({len(columns)}):</b> {', '.join(columns)}</p>",
        "<h2>1 · Batch spread per channel</h2>",
        "<p class='muted'>Largest difference between a batch median and the "
        "median across batches, before and after correction. "
        "Computed on "
        + ("the reference samples (the same control measured in every batch)."
           if any(row.get("group") == "ref" for row in summary_rows)
           else "all sampled cells.")
        + "</p>",
        "<table><tr><th>Channel</th><th>Before</th><th>After</th>"
        "<th>Reduced</th></tr>",
    ]
    for row in summary_rows:
        cls = "good" if row["reduction"] > 0 else "bad"
        parts.append(
            f"<tr><td>{row['channel']}</td><td>{fmt(row['before'])}</td>"
            f"<td>{fmt(row['after'])}</td>"
            f"<td class='{cls}'>{row['reduction']:.1f}%</td></tr>"
        )
    parts.append(
        f"<tr><th>Mean over channels</th><th></th><th></th>"
        f"<th class='{'good' if overall > 0 else 'bad'}'>{overall:.1f}%</th></tr>"
        "</table>"
    )

    parts.append("<h2>2 · Distributions per channel</h2>")
    parts.append(
        "<p class='muted'>Each curve is a normalised density (peak = 1) of "
        "one batch; references are drawn above, other samples below, both "
        "vertically offset so they stay readable.</p>"
    )
    for block in channel_svg:
        parts.append(f"<h3 style='font-size:13px;margin:18px 0 4px'>"
                     f"{block['channel']}</h3>")
        parts.append(
            f"<figure><img alt='{block['channel']}' src='data:image/svg+xml;"
            f"base64,{block['svg']}'></figure>"
        )
        parts.append(
            "<table><tr><th>Batch</th><th>Group</th><th>Cells</th>"
            "<th>Median before</th><th>Median after</th>"
            "<th>MAD before</th><th>MAD after</th></tr>"
        )
        for row in block["rows"]:
            parts.append(
                f"<tr><td>{batches[row['batch']]}</td>"
                f"<td>{group_label.get(row['group'], row['group'])}</td>"
                f"<td>{row['n']:,}</td>"
                f"<td>{fmt(row['median_before'])}</td>"
                f"<td>{fmt(row['median_after'])}</td>"
                f"<td>{fmt(row['mad_before'])}</td>"
                f"<td>{fmt(row['mad_after'])}</td></tr>"
            )
        parts.append("</table>")

    parts.append("<h2>3 · Files</h2>")
    parts.append(
        "<table><tr><th>File</th><th>Batch</th><th>Reference</th>"
        "<th>Rows</th></tr>"
    )
    for row in file_rows:
        parts.append(
            f"<tr><td>{row['name']}</td><td>{row['batch']}</td>"
            f"<td>{row['reference']}</td><td>{row['rows']:,}</td></tr>"
        )
    parts.append("</table>")
    parts.append(
        "<p class='muted'>Statistics are estimated from up to "
        f"{_BA_REPORT_SAMPLE:,} randomly sampled rows per file "
        f"(≤ {_BA_REPORT_CAP:,} values per batch and group). "
        "The after-correction values are the ones the pipeline applies to "
        "every downstream module; they are not a second analysis.</p>"
        "</body></html>"
    )
    target = folder / "batch_report.html"
    target.write_text("".join(parts), encoding="utf-8")
    return target
