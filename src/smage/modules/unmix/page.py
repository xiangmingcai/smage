"""Unmix module: spectral unmixing and generated unmix outputs."""
from __future__ import annotations

import queue

from smage.widgets.controls import _ZoomPixmapLabel

from PySide6.QtCore import (
    QRect,
    QRectF,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_needed_columns,
)
from smage.core.pipeline import (
    _evaluate_stages,
    _pipeline_dependencies,
)
from smage.core.settings import (
    _default_palette,
    _plot_background,
)
from smage.data.image_index import _build_image_index_files
from smage.runlog import runlog
from smage.widgets.colormap import _figure_colormap
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelSpinBox,
)
from smage.widgets.gate_editor.canvas import _GatingState
from smage.widgets.plot_axes import (
    _make_fixed_axis,
    _make_rotated_axis,
    _make_sci_axis,
)
from smage.widgets.resources import (
    _drag_handle_pixmap,
    _lock_pixmap,
)
from uuid import uuid4
import copy
import hashlib
import json
import math
import shutil
import pyqtgraph as pg
import threading
from functools import partial
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import project_paths as _project_paths
from smage.widgets import canvas_export as _widgets_canvas_export
from smage.widgets import colormap as _widgets_colormap
from smage.widgets.gate_editor import editor as _widgets_gate_editor_editor
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import export as _widgets_plot_export


def _unmix_output_names(ctx, task_id, parquet_path) -> list:
    """Generated Unmix columns exposed to a downstream module."""
    if not task_id or not parquet_path:
        return []
    return list(ctx._unmix_applied_columns(task_id, parquet_path))


def _unmix_flow_config(ctx, task_def) -> dict | None:
    """Build (and cache) the unmixing matrix of a saved Unmix task.

    Returns ``{"task_id", "names", "columns", "matrix", "method"}`` with the
    matrix at full precision, or None when there is nothing to apply.
    """
    import numpy as np

    task_id = task_def.get("id")
    cached = ctx._unmix_flow_cache.get(task_id)
    if cached is not None:
        return cached or None
    # Downstream modules only see the saved configuration: when the module
    # is open, use its last-saved snapshot rather than live edits.
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "unmix":
        params = getattr(reg["page"], "_unmix_snapshot", None)
        if not isinstance(params, dict):
            params = task_def.get("params") or {}
    else:
        params = task_def.get("params") or {}
    settings = params.get("spectrum_settings") or {}
    enabled = set(settings.get("enabled") or [])
    columns = [p for p in settings.get("params", []) if p in enabled]
    stat = settings.get("stat", "median")
    if (params.get("signature_source") or "spectra") == "manual":
        # A pasted matrix replaces the spectra-built one.  Its column order
        # is kept; only the channel set must match the spectrum selection.
        manual = params.get("manual_matrix") or {}
        pasted_columns = [str(c) for c in manual.get("columns") or []]
        if not pasted_columns or set(pasted_columns) != set(columns):
            ctx._unmix_flow_cache[task_id] = False
            return None
        names = []
        rows = []
        for row in manual.get("rows") or []:
            values = list(row.get("values") or [])
            if len(values) != len(pasted_columns):
                continue
            array = np.asarray(values, dtype="float64")
            if not np.all(np.isfinite(array)):
                continue
            name = str(row.get("name") or "").strip()
            if not name or name in names:
                ctx._unmix_flow_cache[task_id] = False
                return None
            names.append(name)
            rows.append(array.tolist())
        if not rows:
            ctx._unmix_flow_cache[task_id] = False
            return None
        config = {
            "task_id": task_id,
            "names": names,
            "columns": pasted_columns,
            "matrix": np.asarray(rows, dtype="float64"),
            "method": params.get("method", "ols"),
            "samples": set(params.get("samples") or []),
        }
        ctx._unmix_flow_cache[task_id] = config
        return config
    spectra = {s["id"]: s for s in params.get("spectra", [])}
    names = []
    rows = []
    for row in params.get("matrix", []):
        positive = spectra.get(row.get("positive"))
        if positive is None:
            continue
        pos = _unmix_params_vector(ctx, task_id, params, positive, columns, stat)
        if pos is None:
            continue
        values = []
        negative = spectra.get(row.get("negative"))
        neg = {}
        if negative is not None:
            neg = _unmix_params_vector(ctx,
                task_id, params, negative, columns, stat
            ) or {}
        for column in columns:
            p = pos.get(column, float("nan"))
            n = neg.get(column, 0.0) if negative is not None else 0.0
            values.append(p - n if np.isfinite(p) else float("nan"))
        if row.get("normalize", True):
            finite = [v for v in values if np.isfinite(v)]
            peak = max(finite) if finite else None
            if peak is not None and peak > 0:
                values = [
                    (v / peak) if np.isfinite(v) else v for v in values
                ]
        if not all(np.isfinite(v) for v in values):
            continue  # a signature with missing data cannot unmix
        names.append(row.get("name", f"Signature {len(names) + 1}"))
        rows.append(values)
    if not rows or not columns:
        ctx._unmix_flow_cache[task_id] = False
        return None
    config = {
        "task_id": task_id,
        "names": names,
        "columns": columns,
        "matrix": np.asarray(rows, dtype="float64"),
        "method": params.get("method", "ols"),
        "samples": set(params.get("samples") or []),
    }
    ctx._unmix_flow_cache[task_id] = config
    return config


def _unmix_params_vector(ctx, task_id, params, spectrum, columns, stat):
    """Statistic per parameter for a spectrum stored in task params."""
    import numpy as np

    names, arrays, error = _unmix_spectrum_arrays_for(ctx,
        task_id, params, spectrum, columns
    )
    if error:
        return None
    values = {}
    for name, arr in zip(names, arrays):
        if arr.size == 0:
            values[name] = float("nan")
        elif stat == "mean":
            values[name] = float(arr.mean())
        else:
            values[name] = float(np.median(arr))
    return values


def _build_unmix_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Unmix page: two fixed sub-pages (extraction / matrix)."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Toolbar with Save/Close.
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
    close_btn.clicked.connect(lambda _=False: _unmix_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    page._unmix_task_def = task_def
    page._unmix_task_id = task_def.get("id")
    page._unmix_params = task_def.setdefault("params", {})
    page._unmix_params.setdefault("spectra", [])
    page._unmix_params.setdefault("matrix", [])
    page._unmix_params.setdefault("method", "ols")
    page._unmix_params.setdefault("trees", [])
    page._unmix_params.setdefault("next_spectrum_no", 1)
    # Both signature sources are kept, so switching modes loses nothing.
    page._unmix_params.setdefault("signature_source", "spectra")
    manual = page._unmix_params.setdefault("manual_matrix", {})
    if not isinstance(manual, dict):
        manual = {}
        page._unmix_params["manual_matrix"] = manual
    manual.setdefault("columns", [])
    manual.setdefault("rows", [])
    spectrum_cfg = page._unmix_params.setdefault("spectrum_settings", {})
    spectrum_cfg.setdefault("params", [])
    spectrum_cfg.setdefault("enabled", None)
    spectrum_cfg.setdefault("stat", "median")
    spectrum_cfg.setdefault("colormap", "viridis")
    spectrum_cfg.setdefault("line_color", "#2b6cb0")
    spectrum_cfg.setdefault("decimals", 2)
    page._unmix_spectrum_cfg = spectrum_cfg
    page._unmix_snapshot = copy.deepcopy(page._unmix_params)
    page._unmix_locked = locked
    page._unmix_save_btn = save_btn
    page._unmix_dirty = False
    page._unmix_spectra = page._unmix_params["spectra"]
    page._unmix_active_spectrum = None
    # One embedded gate tree per spectrum (each is a full Gating pane).
    page._unmix_trees = []
    page._unmix_active_tree = None
    # Debounced recompute of the displayed spectrum after gate edits.
    page._unmix_spectrum_timer = QTimer(ctx.host)
    page._unmix_spectrum_timer.setSingleShot(True)
    page._unmix_spectrum_timer.timeout.connect(
        lambda: _unmix_start_spectrum_refresh(ctx, page)
    )
    # Debounced apply of a signature-table row drag.
    page._unmix_sig_order_timer = QTimer(ctx.host)
    page._unmix_sig_order_timer.setSingleShot(True)
    page._unmix_sig_order_timer.setInterval(200)
    page._unmix_sig_order_timer.timeout.connect(
        lambda: _unmix_signature_reordered(ctx, page)
    )
    page._unmix_manual_order_timer = QTimer(ctx.host)
    page._unmix_manual_order_timer.setSingleShot(True)
    page._unmix_manual_order_timer.setInterval(200)
    page._unmix_manual_order_timer.timeout.connect(
        lambda: _unmix_manual_reordered(ctx, page)
    )
    page._unmix_manual_editing = False
    page._unmix_manual_bad_cells = set()
    page._unmix_matrix = page._unmix_params["matrix"]
    if "samples" not in page._unmix_params:
        page._unmix_params["samples"] = [
            r.get("parquet") for r in _data_files._available_files(ctx, page._unmix_task_id) if r.get("parquet")
        ]
    # Per-spectrum value cache, cleared whenever anything changes.
    page._unmix_spectrum_cache = {}
    # Guards the cross-thread column cache used by background spectrum
    # recomputation (gate edits).
    if getattr(ctx, "_unmix_column_lock", None) is None:
        ctx._unmix_column_lock = threading.Lock()

    tabs = QTabWidget()
    tabs.addTab(_unmix_extraction_subpage(ctx, page), "Spectrum Extraction")
    tabs.addTab(_unmix_matrix_subpage(ctx, page), "Matrix & Unmixing")
    layout.addWidget(tabs, 1)

    save_btn.clicked.connect(lambda _=False: _unmix_save_now(ctx, page))
    _unmix_restore_trees(ctx, page)
    _unmix_rebuild_spectrum_list(ctx, page)
    _unmix_rebuild_spectrum_settings(ctx, page)
    _unmix_rebuild_signature_tables(ctx, page)
    _unmix_rebuild_sample_list(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    active_spectrum = page._unmix_params.get("active_spectrum")
    if any(s["id"] == active_spectrum for s in page._unmix_spectra):
        _unmix_select_spectrum(ctx, page, active_spectrum)
    _unmix_update_save_btn(ctx, page)
    return page


def _unmix_extraction_subpage(ctx, page) -> QWidget:
    """Sub-page 1: spectrum area (1) above the embedded gate tree (2)."""
    w = QWidget()
    outer = QVBoxLayout(w)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    split = QSplitter(Qt.Orientation.Vertical)
    split.setChildrenCollapsible(False)
    split.addWidget(_unmix_spectrum_area(ctx, page))
    page._unmix_tree_stack = QStackedWidget()
    page._unmix_tree_stack.addWidget(_unmix_tree_placeholder(ctx))
    split.addWidget(page._unmix_tree_stack)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 1)
    split.setSizes([380, 380])
    outer.addWidget(split, 1)
    return w


def _unmix_spectrum_area(ctx, page) -> QWidget:
    """Spectrum settings (left) | spectra list (middle) | plot (right)."""
    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QHBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(12)

    # Left: settings shared by every spectrum in this module.
    settings_panel = _widgets_panels._build_panel_frame(ctx)
    settings_lay = QVBoxLayout(settings_panel)
    settings_lay.setContentsMargins(10, 10, 10, 10)
    settings_lay.setSpacing(6)
    settings_lay.addWidget(QLabel("<b>Spectrum settings</b>"))
    settings_scroll = QScrollArea()
    settings_scroll.setWidgetResizable(True)
    settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
    # Scoped selectors keep the transparent rule from reaching QToolTip,
    # whose palette would otherwise turn black.
    settings_scroll.setObjectName("unmixSettingsScroll")
    settings_scroll.setStyleSheet(
        "QScrollArea#unmixSettingsScroll { background: transparent; }"
    )
    settings_host = QWidget()
    settings_host.setObjectName("unmixSettingsHost")
    settings_host.setStyleSheet(
        "QWidget#unmixSettingsHost { background: transparent; }"
    )
    settings_layout = QVBoxLayout(settings_host)
    settings_layout.setContentsMargins(0, 0, 0, 0)
    settings_layout.setSpacing(6)
    settings_scroll.setWidget(settings_host)
    settings_lay.addWidget(settings_scroll, 1)
    page._unmix_spectrum_settings_layout = settings_layout

    # Middle: extracted spectra + the "New gate tree" entry point.
    list_panel = _widgets_panels._build_panel_frame(ctx)
    list_lay = QVBoxLayout(list_panel)
    list_lay.setContentsMargins(10, 10, 10, 10)
    list_lay.setSpacing(6)
    list_lay.addWidget(QLabel("<b>Spectra</b>"))
    spectra_list = QListWidget()
    spectra_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: none; }"
        "QListWidget::item:selected { background: #dbeafa; }"
    )
    # Drag rows to change the order the spectra are quantified in.
    spectra_list.setDragDropMode(
        QAbstractItemView.DragDropMode.InternalMove
    )
    spectra_list.setDefaultDropAction(Qt.DropAction.MoveAction)
    spectra_list.setDragEnabled(not page._unmix_locked)
    spectra_list.model().rowsMoved.connect(
        lambda *_: _unmix_reorder_spectra(ctx, page)
    )
    list_lay.addWidget(spectra_list, 1)
    spectra_list.itemClicked.connect(
        lambda item: _unmix_select_spectrum(ctx,
            page, item.data(Qt.ItemDataRole.UserRole)
        )
    )
    spectra_list.itemDoubleClicked.connect(
        lambda item: _unmix_select_spectrum(ctx,
            page, item.data(Qt.ItemDataRole.UserRole), open_tree=True
        )
    )
    spectra_list.setContextMenuPolicy(
        Qt.ContextMenuPolicy.CustomContextMenu
    )
    spectra_list.customContextMenuRequested.connect(
        lambda pos: _unmix_spectrum_context_menu(ctx, page, pos)
    )
    new_tree_btn = QPushButton("New gate tree")
    new_tree_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    new_tree_btn.setEnabled(not page._unmix_locked)
    new_tree_btn.clicked.connect(lambda _=False: _unmix_new_gate_tree(ctx, page))
    list_lay.addWidget(new_tree_btn)
    page._unmix_spectra_list = spectra_list
    page._unmix_new_tree_btn = new_tree_btn

    # Middle: spectrum curve (empty until a spectrum is extracted).
    plot_panel = _widgets_panels._build_panel_frame(ctx)
    plot_lay = QVBoxLayout(plot_panel)
    plot_lay.setContentsMargins(10, 10, 10, 10)
    plot_lay.setSpacing(6)
    plot_lay.addWidget(QLabel("<b>Spectrum</b>"))
    spectrum_plot = pg.PlotWidget()
    spectrum_plot.setBackground(_plot_background())
    spectrum_plot.showGrid(x=True, y=True, alpha=0.2)
    spectrum_plot.getPlotItem().setAxisItems({
        "bottom": _make_rotated_axis("bottom"),
        "left": _make_sci_axis("left"),
    })
    plot_lay.addWidget(spectrum_plot, 1)
    page._unmix_spectrum_plot = spectrum_plot
    _unmix_plot_export_setup(ctx, page, spectrum_plot, "Unmix spectrum")

    row.addWidget(settings_panel, 1)
    row.addWidget(list_panel, 1)
    row.addWidget(plot_panel, 2)
    return content


def _unmix_tree_placeholder(ctx) -> QWidget:
    """Shown in the gate-tree area until the first tree is created."""
    w = QWidget()
    l = QVBoxLayout(w)
    hint = QLabel('Click "New gate tree" to start drawing gates.')
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hint.setStyleSheet("color: #888888;")
    l.addStretch()
    l.addWidget(hint)
    l.addStretch()
    return w


def _unmix_make_tree(ctx, page, store: dict, spectrum_id=None) -> dict:
    """Register one gate tree; its widgets are built lazily when opened."""
    tree = {
        "id": store["id"],
        "store": store,
        "holder": None,
        "content": None,
        "spectrum_id": spectrum_id,
    }
    page._unmix_trees.append(tree)
    return tree


def _unmix_build_tree_widgets(ctx, page, tree: dict) -> None:
    """Create the embedded Gating pane for a tree (once)."""
    if tree.get("holder") is not None:
        return
    holder = _GatingState()
    holder._module_page = page
    holder._gating_on_dirty = lambda: _unmix_on_gating_dirty(ctx, page, holder)
    holder._gating_spectrum_menu = (
        lambda menu, h, idx: _unmix_spectrum_menu(ctx, page, h, idx, menu)
    )
    _widgets_gate_editor_editor._gating_state_init(ctx, holder, tree["store"], page._unmix_locked)
    holder._gating_task_id = page._unmix_task_id
    holder._gating_save_btn = None
    spectrum = _unmix_spectrum_for_tree(ctx, page, tree)
    holder._gating_protected_gate_id = spectrum["gate_id"] if spectrum else None
    content = _widgets_gate_editor_editor._gating_build_content(ctx, holder, page._unmix_locked)
    tree["holder"] = holder
    tree["content"] = content
    page._unmix_tree_stack.addWidget(content)


def _unmix_activate_tree(ctx, page, tree: dict) -> None:
    """Show a gate tree and refresh its scatter (it may have been hidden)."""
    _unmix_build_tree_widgets(ctx, page, tree)
    page._unmix_active_tree = tree
    page._unmix_tree_stack.setCurrentWidget(tree["content"])
    _widgets_gate_editor_editor._gating_render(ctx, tree["holder"])
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, tree["holder"])


def _unmix_destroy_tree(ctx, page, tree: dict) -> None:
    """Tear down a gate tree (worker, timer and plot items)."""
    if tree.get("holder") is not None:
        _widgets_gate_editor_editor._gating_cleanup(ctx, tree["holder"])
    if tree.get("content") is not None:
        page._unmix_tree_stack.removeWidget(tree["content"])
        tree["content"].deleteLater()
    if tree in page._unmix_trees:
        page._unmix_trees.remove(tree)
    if page._unmix_active_tree is tree:
        page._unmix_active_tree = None
    if not page._unmix_trees:
        page._unmix_tree_stack.setCurrentIndex(0)


def _unmix_new_gate_tree(ctx, page) -> None:
    """Start a fresh gate tree (discarding an unused scratch tree)."""
    active = page._unmix_active_tree
    if active is not None and active["spectrum_id"] is None:
        gates = active["store"].get("params", {}).get("gates", [])
        if gates:
            reply = QMessageBox.question(
                ctx.host,
                "New gate tree",
                "The current gate tree is not used by any spectrum.\n"
                "Discard it and start a new one?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        _unmix_destroy_tree(ctx, page, active)
    tree = _unmix_make_tree(ctx, page, {"id": uuid4().hex[:8], "params": {}})
    _unmix_activate_tree(ctx, page, tree)
    _unmix_clear_spectrum_selection(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_restore_trees(ctx, page) -> None:
    """Recreate the gate trees stored in the project file."""
    for rec in page._unmix_params.get("trees", []):
        store = {
            "id": rec.get("id") or uuid4().hex[:8],
            "params": rec.get("params") or {},
        }
        _unmix_make_tree(ctx, page, store, rec.get("spectrum_id"))
    active_id = page._unmix_params.get("active_tree")
    tree = next((t for t in page._unmix_trees if t["id"] == active_id), None)
    if tree is None and page._unmix_trees:
        tree = page._unmix_trees[0]
    if tree is not None:
        _unmix_activate_tree(ctx, page, tree)


def _unmix_on_gating_dirty(ctx, page, holder) -> None:
    """A gate tree changed: mark the module dirty and refresh its spectrum."""
    if page._unmix_locked:
        return
    tree = _unmix_tree_for_holder(ctx, page, holder)
    if tree is None:
        _unmix_mark_dirty(ctx, page, data=False)
        return
    # Gate edits only change the spectra that read this tree; keep the
    # vectors of the other spectra cached.
    cache = getattr(page, "_unmix_spectrum_cache", None)
    if cache is not None:
        for spectrum in page._unmix_spectra:
            if spectrum.get("tree_id") == tree["id"]:
                cache.pop(spectrum["id"], None)
    # Any in-flight background result is now stale.
    page._unmix_spectrum_gen = getattr(page, "_unmix_spectrum_gen", 0) + 1
    _unmix_mark_dirty(ctx, page, data=False)
    # If the displayed spectrum comes from this tree, recompute it soon.
    spectrum = page._unmix_active_spectrum
    if spectrum is not None and spectrum.get("tree_id") == tree["id"]:
        page._unmix_spectrum_timer.start(250)


def _unmix_cleanup(ctx, page) -> None:
    """Stop every embedded gate tree (workers and timers)."""
    for name in (
        "_unmix_spectrum_timer", "_unmix_sig_order_timer",
        "_unmix_spectrum_poll_timer",
    ):
        timer = getattr(page, name, None)
        if timer is not None:
            timer.stop()
    # Discard any result a background spectrum worker still posts.
    page._unmix_spectrum_gen = getattr(page, "_unmix_spectrum_gen", 0) + 1
    page._unmix_spectrum_queue = None
    # Release the column arrays cached for fast gate edits.
    ctx._unmix_column_cache = {}
    for tree in list(page._unmix_trees):
        if tree.get("holder") is not None:
            _widgets_gate_editor_editor._gating_cleanup(ctx, tree["holder"])
    page._unmix_trees = []
    page._unmix_active_tree = None


def _unmix_tree_by_id(ctx, page, tree_id):
    return next((t for t in page._unmix_trees if t["id"] == tree_id), None)


def _unmix_tree_for_holder(ctx, page, holder):
    return next((t for t in page._unmix_trees if t.get("holder") is holder), None)


def _unmix_spectrum_for_tree(ctx, page, tree):
    if tree is None or not tree.get("spectrum_id"):
        return None
    return next(
        (s for s in page._unmix_spectra if s["id"] == tree["spectrum_id"]),
        None,
    )


def _unmix_spectrum_parameters(ctx, page) -> list:
    """Every parameter available in the module (union of imported files).

    The settings are set up before any spectrum exists, so they cannot be
    tied to one gate tree's file.
    """
    columns = []
    for rec in _data_files._available_files(ctx, page._unmix_task_id):
        for name in _data_columns._columns_for(ctx, rec.get("parquet"), page._unmix_task_id):
            if name not in columns:
                columns.append(name)
    return columns


def _unmix_selected_parameters(ctx, page) -> list:
    """The parameters a spectrum contains, in left-to-right plot order."""
    cfg = page._unmix_spectrum_cfg
    enabled = set(cfg.get("enabled") or [])
    return [p for p in cfg.get("params", []) if p in enabled]


def _unmix_apply_parameter_order(ctx, page, new_order) -> None:
    """Apply a drag-reordered selection without moving unselected items."""
    cfg = page._unmix_spectrum_cfg
    current = list(cfg.get("params") or [])
    chosen = set(new_order)
    slots = [i for i, name in enumerate(current) if name in chosen]
    if len(slots) != len(new_order):
        return
    for slot, name in zip(slots, new_order):
        current[slot] = name
    cfg["params"] = current
    _unmix_render_spectrum_plot(ctx, page)
    _unmix_update_manual_summary(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_sync_spectrum_params(ctx, page) -> list:
    """Keep the module-wide parameter order and selection in sync."""
    cfg = page._unmix_spectrum_cfg
    columns = _unmix_spectrum_parameters(ctx, page)
    stored = [p for p in cfg.get("params", []) if p in columns]
    for col in columns:
        if col not in stored:
            stored.append(col)
    cfg["params"] = stored
    enabled = cfg.get("enabled")
    if not isinstance(enabled, list):
        # Unset: default to every parameter except the image coordinates.
        enabled = [c for c in columns if c not in ("x", "y")]
    cfg["enabled"] = [c for c in enabled if c in stored]
    return stored


def _unmix_next_spectrum_name(ctx, page) -> str:
    """Spectrum N — numbering keeps increasing and never reuses gaps."""
    while True:
        n = int(page._unmix_params.get("next_spectrum_no", 1))
        page._unmix_params["next_spectrum_no"] = n + 1
        name = f"Spectrum {n}"
        if not any(s["name"] == name for s in page._unmix_spectra):
            return name


def _unmix_rebuild_spectrum_list(ctx, page) -> None:
    lst = page._unmix_spectra_list
    active = page._unmix_active_spectrum
    lst.blockSignals(True)
    lst.clear()
    for spectrum in page._unmix_spectra:
        item = QListWidgetItem(spectrum["name"])
        item.setData(Qt.ItemDataRole.UserRole, spectrum["id"])
        lst.addItem(item)
    lst.blockSignals(False)
    if active is not None:
        _unmix_highlight_spectrum(ctx, page, active["id"])


def _unmix_reorder_spectra(ctx, page) -> None:
    """Adopt the order the spectra list was dragged into."""
    lst = page._unmix_spectra_list
    order = [
        lst.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(lst.count())
    ]
    by_id = {s["id"]: s for s in page._unmix_spectra}
    reordered = [by_id[sid] for sid in order if sid in by_id]
    if len(reordered) != len(page._unmix_spectra):
        return  # a partial view: keep the stored order
    page._unmix_spectra[:] = reordered
    active = page._unmix_active_spectrum
    if active is not None:
        _unmix_highlight_spectrum(ctx, page, active["id"])
    # The signature editor's dropdowns list the spectra in this order.
    if getattr(page, "_unmix_signature_table", None) is not None:
        _unmix_rebuild_signature_table(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_log(ctx, page, text: str) -> None:
    """Append a line to the Unmix run log (saved with the module)."""
    log = getattr(page, "_unmix_image_log", None)
    if log is not None:
        log.appendPlainText(text)
    lines = page._unmix_params.setdefault("image_log", [])
    lines.append(str(text))
    del lines[:-500]


def _unmix_output_signature(ctx, task_id, parquet_path, config) -> str:
    """Fingerprint of the config and inputs an Unmix output was made from."""
    import numpy as np

    try:
        st = Path(str(parquet_path)).stat()
        source_stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        source_stat = ""
    payload = {
        "task": task_id,
        "file": _project_paths._stable_path_key(ctx, parquet_path),
        "source": source_stat,
        "kind": (
            "image"
            if _data_image_index._image_load_manifest(
                ctx, parquet_path
            ) is not None else "table"
        ),
        "columns": list(config.get("columns") or []),
        "names": list(config.get("names") or []),
        "matrix": np.round(
            np.asarray(config.get("matrix"), dtype="float64"), 6
        ).tolist(),
        "method": config.get("method"),
        # Upstream map stages (Compensation, Illumination, previous Unmix,
        # Batch Align, Rename, Calculation) change the source values without
        # changing the source file, so they must invalidate the output.
        "stages": _data_columns._map_stage_token(
            ctx, task_id, parquet_path
        ),
    }
    return hashlib.md5(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _unmix_write_output(ctx, task_id, parquet_path, config) -> dict:
    """Write one file's unmixed channels (and image pyramid for images)."""
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    names = list(config.get("names") or [])
    columns = list(config.get("columns") or [])
    matrix = np.asarray(config.get("matrix"), dtype="float64")
    if not names or not columns:
        raise ValueError("no unmixing signatures")
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(names)
        or matrix.shape[1] != len(columns)
    ):
        raise ValueError("the unmixing matrix does not match its columns")
    # The same least-squares solution the pipeline uses for table files.
    pinv = config.get("_pinv")
    if pinv is None:
        pinv = np.linalg.pinv(matrix)
        config["_pinv"] = pinv

    manifest = _data_image_index._image_load_manifest(ctx, parquet_path)
    is_image = manifest is not None
    origin = (
        (float(manifest["xmin"]), float(manifest["ymin"]))
        if is_image else None
    )
    stem = Path(str(parquet_path)).stem
    rel = _data_files._module_rel(ctx, task_id, f"{stem}_unmix.parquet")
    out_path = ctx.project.path / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    stages = ctx._map_stages(task_id, parquet_path=parquet_path)
    needed = (
        _pipeline_dependencies(stages, columns, parquet_path)
        if stages else set(columns)
    )
    pf = pq.ParquetFile(parquet_path)
    available = set(pf.schema_arrow.names)
    virtual = sorted(c for c in needed if c not in available)
    # Virtual columns (Gate2Col, masks, ...) cannot be read from the source
    # parquet in chunks.  The full pipeline is the fallback and applies the
    # same stages plus those generated columns.
    fallback = bool(virtual) or ctx._feature_output(parquet_path) is not None
    if not is_image and any(kind == "illumination" for kind, _ in stages):
        # The illumination factor uses the file-wide x/y origin; loading the
        # full table is the only way to reproduce that for non-image data.
        fallback = True

    writer = None

    def write_frame(x_values, y_values, unmixed) -> None:
        nonlocal writer
        data = {}
        if is_image:
            data["x"] = np.asarray(x_values, dtype="float64")
            data["y"] = np.asarray(y_values, dtype="float64")
        for j, name in enumerate(names):
            data[name] = unmixed[:, j]
        table = pa.Table.from_pandas(
            pd.DataFrame(data), preserve_index=False
        )
        if writer is None:
            writer = pq.ParquetWriter(str(out_path), table.schema)
        writer.write_table(table)

    try:
        if fallback:
            requested = list(dict.fromkeys(
                (["x", "y"] if is_image else []) + columns
            ))
            full = ctx._column_data(
                task_id, parquet_path, requested, row_filter=False
            )
            if is_image and ("x" not in full or "y" not in full):
                raise ValueError("the source file has no x/y columns")
            missing = [c for c in columns if c not in full]
            if missing:
                raise ValueError(
                    "channel(s) could not be computed: " + ", ".join(missing)
                )
            stacked = np.column_stack([
                np.asarray(full[c], dtype="float64") for c in columns
            ])
            unmixed = stacked @ pinv
            for start in range(0, len(unmixed), 500_000):
                end = min(start + 500_000, len(unmixed))
                write_frame(
                    full.get("x", [])[start:end] if is_image else None,
                    full.get("y", [])[start:end] if is_image else None,
                    unmixed[start:end],
                )
        else:
            read_cols = list(dict.fromkeys(
                (["x", "y"] if is_image else []) + list(needed)
            ))
            for batch in pf.iter_batches(
                batch_size=500_000, columns=read_cols
            ):
                df = batch.to_pandas()
                values = {
                    c: pd.to_numeric(df[c], errors="coerce").to_numpy(
                        dtype="float64"
                    )
                    for c in read_cols if c in df.columns
                }
                if stages:
                    evaluated = _evaluate_stages(
                        stages, values, columns,
                        parquet_path=parquet_path,
                        cache=None,
                        origin=origin,
                    )
                else:
                    evaluated = {
                        c: values[c] for c in columns if c in values
                    }
                missing = [c for c in columns if c not in evaluated]
                if missing:
                    raise ValueError(
                        "channel(s) could not be computed: "
                        + ", ".join(missing)
                    )
                stacked = np.column_stack([
                    np.asarray(evaluated[c], dtype="float64")
                    for c in columns
                ])
                unmixed = stacked @ pinv
                if is_image and ("x" not in values or "y" not in values):
                    raise ValueError("the source file has no x/y columns")
                write_frame(
                    values.get("x") if is_image else None,
                    values.get("y") if is_image else None,
                    unmixed,
                )
    finally:
        if writer is not None:
            writer.close()
    if is_image:
        _build_image_index_files(str(out_path))
    else:
        # A table output may reuse the path of an older image output.
        shutil.rmtree(str(out_path) + ".imgidx", ignore_errors=True)
    return {
        "path": rel,
        "columns": names,
        "kind": "image" if is_image else "table",
        "signature": _unmix_output_signature(ctx,
            task_id, parquet_path, config
        ),
    }


def _unmix_removed_outputs(ctx, page, samples) -> list:
    """Saved outputs whose source file is no longer selected."""
    selected = {str(path) for path in samples or [] if path}
    removed = []
    for path, rec in (page._unmix_params.get("outputs") or {}).items():
        if str(path) in selected:
            continue
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        try:
            label = _data_naming._comp_file_name(
                ctx, path, page._unmix_task_id
            )
        except Exception:  # noqa: BLE001 - a removed file may not resolve
            label = Path(str(path)).name
        removed.append({"source": path, "rec": rec, "label": label})
    return removed


def _unmix_confirm_remove_outputs(ctx, page, samples):
    """Ask before dropping outputs of deselected files.

    Returns the removed records (possibly an empty list) when the run may
    continue, or None when the user cancels.
    """
    removed = _unmix_removed_outputs(ctx, page, samples)
    if not removed:
        return []
    shown = "\n".join(f"  - {item['label']}" for item in removed[:8])
    if len(removed) > 8:
        shown += f"\n  … and {len(removed) - 8} more"
    text = (
        f"{len(removed)} previously unmixed file(s) are no longer selected:\n\n"
        f"{shown}\n\n"
        "Continuing will delete their unmixed outputs and remove these "
        "channels from downstream modules.\n\nContinue?"
    )
    reply = QMessageBox.question(
        ctx.host, "Run unmix", text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return None
    outputs = dict(page._unmix_params.get("outputs") or {})
    for item in removed:
        outputs.pop(item["source"], None)
    page._unmix_params["outputs"] = outputs
    page._unmix_task_def["params"] = page._unmix_params
    # With no outputs left, the module no longer locks its inputs.
    page._unmix_task_def["output"] = {"saved": bool(outputs)}
    _unmix_save_now(ctx, page)
    return removed


def _unmix_output_abs_path(ctx, rec):
    """A generated output path, only when it stays inside the project."""
    if ctx.project is None or not isinstance(rec, dict):
        return None
    rel = rec.get("path")
    if not rel:
        return None
    try:
        root = Path(ctx.project.path).resolve()
        path = (Path(ctx.project.path) / rel).resolve()
    except (OSError, ValueError):
        return None
    if path == root or not path.is_relative_to(root):
        return None
    if path.suffix.lower() != ".parquet":
        return None
    return path


def _unmix_delete_output(ctx, rec) -> tuple[bool, str]:
    """Delete one unmix parquet and, if present, its image pyramid."""
    path = _unmix_output_abs_path(ctx, rec)
    if path is None:
        return False, "invalid output path"
    errors = []
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            errors.append(str(exc))
    index_dir = Path(str(path) + ".imgidx")
    if index_dir.exists():
        try:
            shutil.rmtree(index_dir)
        except OSError as exc:
            errors.append(str(exc))
    return (not errors), "; ".join(errors)


def _unmix_start_run(ctx, page) -> None:
    """Save, then generate the unmixed channels of every selected sample."""
    import queue as queue_module

    if getattr(page, "_unmix_running", False):
        return
    params = page._unmix_params
    samples = [p for p in (params.get("samples") or []) if p]
    if not samples:
        QMessageBox.information(
            ctx.host, "Run unmix",
            "Select at least one sample under “Samples to be unmixed”.",
        )
        return
    problem = _unmix_manual_problem(ctx, page)
    if problem:
        QMessageBox.information(ctx.host, "Run unmix", problem)
        return
    # The matrix that gets applied is the saved one.
    _unmix_save_now(ctx, page)
    config = _unmix_flow_config(ctx, page._unmix_task_def)
    if config is None:
        QMessageBox.information(
            ctx.host, "Run unmix",
            _unmix_manual_problem(ctx, page)
            or "Add at least one complete signature to the matrix first.",
        )
        return
    removed = _unmix_confirm_remove_outputs(ctx, page, samples)
    if removed is None:
        return
    page._unmix_running = True
    page._unmix_cancel = False
    if getattr(page, "_unmix_run_btn", None) is not None:
        page._unmix_run_btn.setEnabled(False)
    _unmix_log(ctx,
        page, f"— Run unmix: {len(samples)} file(s) selected —"
    )
    # Resolve the user-facing names here (the worker runs off the UI thread).
    labels = {
        path: _data_naming._comp_file_name(ctx, path, page._unmix_task_id)
        for path in samples
    }
    q = queue_module.Queue()
    page._unmix_queue = q
    page._unmix_thread = threading.Thread(
        target=partial(_unmix_output_worker, ctx),
        args=(page, q, config, samples, labels, removed),
        daemon=True,
    )
    page._unmix_thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _unmix_output_poll(ctx, page))
    timer.start()
    page._unmix_timer = timer


def _unmix_output_is_current(ctx, task_id, parquet_path, config, rec) -> bool:
    """Whether a saved unmix output still matches the current matrix."""
    if not isinstance(rec, dict) or not rec.get("path"):
        return False
    if rec.get("signature") != _unmix_output_signature(ctx,
        task_id, parquet_path, config
    ):
        return False
    path = ctx.project.path / rec["path"]
    if not path.is_file():
        return False
    if rec.get("kind", "image") == "image":
        return _data_image_index._image_load_manifest(
            ctx, str(path)
        ) is not None
    return True


def _unmix_output_worker(ctx, page, q, config, files, labels=None,
                        removed=None) -> None:
    """Background: delete stale outputs, then generate the selected files.

    Both table and image samples are written; image files additionally get
    an image pyramid.
    """
    task_id = page._unmix_task_id
    labels = labels or {}
    removed = list(removed or [])
    outputs = dict(page._unmix_params.get("outputs") or {})
    done = failed = skipped = unchanged = 0
    try:
        for item in removed:
            label = item.get("label") or str(item.get("source") or "")
            ok, error = _unmix_delete_output(ctx, item.get("rec"))
            if ok:
                q.put(("log", f"✓ removed: {label}"))
            else:
                q.put((
                    "log",
                    f"✗ could not delete unmix output: {label}: {error}",
                ))
        for i, path in enumerate(files):
            if getattr(page, "_unmix_cancel", False):
                q.put(("log", "Cancelled."))
                break
            label = labels.get(path) or Path(str(path)).name
            if _unmix_output_is_current(ctx,
                task_id, path, config, outputs.get(path)
            ):
                # Nothing about the matrix changed: keep the saved output.
                q.put(("log", f"• already up to date, skipped: {label}"))
                unchanged += 1
                continue
            try:
                outputs[path] = _unmix_write_output(ctx,
                    task_id, path, config
                )
                done += 1
                q.put(("log", f"✓ {label}  ({i + 1}/{len(files)})"))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                q.put(("log", f"✗ {label}: {exc}"))
        q.put(("done", outputs, done, failed, skipped, unchanged,
               len(removed)))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _unmix_output_poll(ctx, page) -> None:
    q = getattr(page, "_unmix_queue", None)
    if q is None:
        return
    finished = False
    error = None
    counts = (0, 0, 0, 0, 0)
    outputs = None
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "log":
            _unmix_log(ctx, page, msg[1])
        elif kind == "done":
            outputs = msg[1]
            counts = (msg[2], msg[3], msg[4], msg[5], msg[6])
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    page._unmix_running = False
    timer = getattr(page, "_unmix_timer", None)
    if timer is not None:
        timer.stop()
        page._unmix_timer = None
    if getattr(page, "_unmix_run_btn", None) is not None:
        page._unmix_run_btn.setEnabled(not page._unmix_locked)
    if outputs is not None:
        page._unmix_params["outputs"] = outputs
        page._unmix_task_def["params"] = page._unmix_params
        # Generated outputs lock the upstream modules; removing the last one
        # releases that lock.
        page._unmix_task_def["output"] = {"saved": bool(outputs)}
        if ctx.project is not None:
            ctx.project.save()
        page._unmix_snapshot = copy.deepcopy(page._unmix_params)
        page._unmix_dirty = False
        _unmix_update_save_btn(ctx, page)
        ctx._refresh_workflow_tree()
        ctx._module_saved(page)
    if error:
        _unmix_log(ctx, page, f"✗ {error}")
        QMessageBox.warning(ctx.host, "Run unmix", error)
    else:
        done, failed, _skipped, unchanged, removed = counts
        summary = (
            f"— Done: {done} generated, {unchanged} already up to date, "
            f"{failed} failed"
        )
        if removed:
            summary += f", {removed} removed"
        _unmix_log(ctx, page, summary + " —")


def _unmix_highlight_spectrum(ctx, page, spectrum_id) -> None:
    lst = page._unmix_spectra_list
    lst.blockSignals(True)
    lst.clearSelection()
    for i in range(lst.count()):
        item = lst.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == spectrum_id:
            lst.setCurrentItem(item)
            break
    lst.blockSignals(False)


def _unmix_select_spectrum(ctx, page, spectrum_id, open_tree=False) -> None:
    spectrum = next(
        (s for s in page._unmix_spectra if s["id"] == spectrum_id), None
    )
    page._unmix_active_spectrum = spectrum
    if spectrum is None:
        lst = page._unmix_spectra_list
        lst.blockSignals(True)
        lst.clearSelection()
        lst.setCurrentItem(None)
        lst.blockSignals(False)
    else:
        _unmix_highlight_spectrum(ctx, page, spectrum["id"])
    _unmix_render_spectrum_plot(ctx, page)
    if open_tree and spectrum is not None:
        tree = _unmix_tree_by_id(ctx, page, spectrum.get("tree_id"))
        if tree is not None:
            _unmix_activate_tree(ctx, page, tree)


def _unmix_clear_spectrum_selection(ctx, page) -> None:
    _unmix_select_spectrum(ctx, page, None, open_tree=False)


def _unmix_spectrum_context_menu(ctx, page, pos) -> None:
    lst = page._unmix_spectra_list
    item = lst.itemAt(pos)
    if item is None:
        return
    spectrum = next(
        (s for s in page._unmix_spectra
         if s["id"] == item.data(Qt.ItemDataRole.UserRole)),
        None,
    )
    if spectrum is None:
        return
    enabled = not page._unmix_locked
    menu = QMenu(lst)
    ren_act = QAction("Rename", menu)
    ren_act.setEnabled(enabled)
    ren_act.triggered.connect(lambda: _unmix_rename_spectrum(ctx, page, spectrum))
    menu.addAction(ren_act)
    copy_act = QAction("Duplicate", menu)
    copy_act.setEnabled(enabled)
    copy_act.triggered.connect(lambda: _unmix_copy_spectrum(ctx, page, spectrum))
    menu.addAction(copy_act)
    menu.addSeparator()
    del_act = QAction("Delete", menu)
    del_act.setEnabled(enabled)
    del_act.triggered.connect(lambda: _unmix_delete_spectrum(ctx, page, spectrum))
    menu.addAction(del_act)
    menu.exec(lst.viewport().mapToGlobal(pos))


def _unmix_set_spectrum(ctx, page, tree, gate_id) -> None:
    """Bind a gate tree to a new spectrum (a tree can only be set once)."""
    if tree.get("spectrum_id"):
        return
    cfg = page._unmix_spectrum_cfg
    enabled = list(cfg.get("enabled") or [])
    if not enabled:
        QMessageBox.information(
            ctx.host,
            "Set as spectrum",
            "No spectrum parameters are selected.\n"
            'Choose them with "Set" in Spectrum settings first.',
        )
        return
    preview = tree["store"].get("params", {}).get("preview") or {}
    if not preview.get("file"):
        QMessageBox.warning(
            ctx.host,
            "Set as spectrum",
            "This gate tree has no data file selected.",
        )
        return
    columns = set(_data_columns._columns_for(ctx, preview.get("file"), page._unmix_task_id))
    missing = [p for p in enabled if p not in columns]
    if missing:
        shown = ", ".join(str(m) for m in missing[:12])
        if len(missing) > 12:
            shown += ", ..."
        QMessageBox.warning(
            ctx.host,
            "Set as spectrum",
            f"The data file of this gate tree is missing "
            f"{len(missing)} selected spectrum parameter(s):\n\n{shown}"
            "\n\nChoose a data file that contains these parameters.",
        )
        return
    spectrum = {
        "id": uuid4().hex[:8],
        "name": _unmix_next_spectrum_name(ctx, page),
        "tree_id": tree["id"],
        "gate_id": gate_id,
    }
    page._unmix_spectra.append(spectrum)
    tree["spectrum_id"] = spectrum["id"]
    if tree.get("holder") is not None:
        tree["holder"]._gating_protected_gate_id = gate_id
    _unmix_rebuild_spectrum_list(ctx, page)
    _unmix_select_spectrum(ctx, page, spectrum["id"], open_tree=False)
    _unmix_rebuild_signature_table(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_reset_spectrum_source(ctx, page, tree) -> None:
    """Move a spectrum's source to another gate of its bound tree."""
    holder = tree.get("holder")
    spectrum = _unmix_spectrum_for_tree(ctx, page, tree)
    if holder is None or spectrum is None:
        return
    others = [
        g for g in holder._gating_gates if g["id"] != spectrum.get("gate_id")
    ]
    if not others:
        QMessageBox.information(
            ctx.host,
            "Set other gate as spectrum",
            "This gate tree has no other gates.",
        )
        return
    dialog = QDialog(ctx.host)
    dialog.setWindowTitle("Set other gate as spectrum")
    dlg = QVBoxLayout(dialog)
    dlg.addWidget(QLabel("Use another gate as the source of this spectrum:"))
    combo = QComboBox()
    for gate in others:
        combo.addItem(gate.get("name", gate["id"]), gate["id"])
    dlg.addWidget(combo)
    row = QHBoxLayout()
    row.addStretch()
    ok_btn = QPushButton("OK")
    cancel_btn = QPushButton("Cancel")
    row.addWidget(ok_btn)
    row.addWidget(cancel_btn)
    dlg.addLayout(row)
    ok_btn.clicked.connect(dialog.accept)
    cancel_btn.clicked.connect(dialog.reject)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    gate_id = combo.currentData()
    if not gate_id:
        return
    spectrum["gate_id"] = gate_id
    holder._gating_protected_gate_id = gate_id
    _unmix_select_spectrum(ctx, page, spectrum["id"], open_tree=False)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_spectrum_menu(ctx, page, holder, idx, menu) -> None:
    """Add Unmix actions to a gate tree's context menu."""
    if idx is None or idx < 0 or idx >= len(holder._gating_gates):
        return
    tree = _unmix_tree_for_holder(ctx, page, holder)
    if tree is None:
        return
    gate_id = holder._gating_gates[idx]["id"]
    spectrum = _unmix_spectrum_for_tree(ctx, page, tree)
    if spectrum is None and not page._unmix_locked:
        act = QAction("Set as spectrum", menu)
        act.triggered.connect(
            lambda _=False: _unmix_set_spectrum(ctx, page, tree, gate_id)
        )
        menu.addAction(act)
    elif spectrum is not None and spectrum.get("gate_id") == gate_id:
        act = QAction("Set other gate as spectrum", menu)
        act.setEnabled(not page._unmix_locked)
        act.triggered.connect(
            lambda _=False: _unmix_reset_spectrum_source(ctx, page, tree)
        )
        menu.addAction(act)


def _unmix_rename_spectrum(ctx, page, spectrum) -> None:
    text, ok = QInputDialog.getText(
        ctx.host, "Rename spectrum", "Name:", QLineEdit.EchoMode.Normal,
        spectrum.get("name", ""),
    )
    if not ok:
        return
    name = text.strip()
    if not name:
        return
    if any(s is not spectrum and s["name"] == name for s in page._unmix_spectra):
        QMessageBox.information(
            ctx.host, "Rename spectrum", "A spectrum with this name already exists."
        )
        return
    spectrum["name"] = name
    _unmix_rebuild_spectrum_list(ctx, page)
    _unmix_render_spectrum_plot(ctx, page)
    _unmix_rebuild_signature_table(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_copy_spectrum(ctx, page, spectrum) -> None:
    """Duplicate a spectrum, including an independent copy of its gate tree."""
    name = spectrum["name"] + " copy"
    while any(s["name"] == name for s in page._unmix_spectra):
        name += " copy"
    new_spectrum = copy.deepcopy(spectrum)
    new_spectrum["id"] = uuid4().hex[:8]
    new_spectrum["name"] = name
    source_tree = _unmix_tree_by_id(ctx, page, spectrum.get("tree_id"))
    if source_tree is not None:
        store = {
            "id": uuid4().hex[:8],
            "params": copy.deepcopy(source_tree["store"].get("params") or {}),
        }
        new_tree = _unmix_make_tree(ctx, page, store, new_spectrum["id"])
        new_spectrum["tree_id"] = new_tree["id"]
    page._unmix_spectra.append(new_spectrum)
    _unmix_rebuild_spectrum_list(ctx, page)
    _unmix_select_spectrum(ctx, page, new_spectrum["id"], open_tree=False)
    _unmix_rebuild_signature_table(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_delete_spectrum(ctx, page, spectrum) -> None:
    reply = QMessageBox.question(
        ctx.host,
        "Delete spectrum",
        f'Delete "{spectrum["name"]}"?\n'
        "Its bound gate tree will be destroyed.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return
    tree = _unmix_tree_by_id(ctx, page, spectrum.get("tree_id"))
    if tree is not None:
        _unmix_destroy_tree(ctx, page, tree)
    if spectrum in page._unmix_spectra:
        page._unmix_spectra.remove(spectrum)
    if page._unmix_active_spectrum is spectrum:
        page._unmix_active_spectrum = None
    _unmix_rebuild_spectrum_list(ctx, page)
    _unmix_select_spectrum(ctx,
        page,
        page._unmix_active_spectrum["id"] if page._unmix_active_spectrum else None,
        open_tree=False,
    )
    _unmix_rebuild_signature_table(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_spectrum_arrays(ctx, page, spectrum):
    """Return (names, arrays, error) for a spectrum's gated subset."""
    cfg = page._unmix_spectrum_cfg
    ordered = _unmix_sync_spectrum_params(ctx, page)
    enabled = set(cfg.get("enabled") or [])
    columns = [p for p in ordered if p in enabled]
    live_trees = [
        {"id": t["id"], "params": t["store"].get("params", {})}
        for t in page._unmix_trees
    ]
    return _unmix_spectrum_arrays_for(ctx,
        page._unmix_task_id, page._unmix_params, spectrum, columns,
        trees=live_trees,
    )


_UNMIX_COLUMN_CACHE_ENTRIES = 2


def _unmix_cached_columns(ctx, task_id, parquet, names):
    """Column values for a gate tree, cached across gate moves.

    Moving a gate changes the mask, not the channel values, so re-reading a
    multi-million-row parquet on every release is wasted work. The cache is
    keyed by the pipeline revision, the file and the column set, and keeps
    at most two entries so a large image does not pin unbounded memory.
    """
    key = (task_id, str(parquet), tuple(sorted(names)))
    revision = getattr(ctx, "_stages_revision", 0)
    lock = getattr(ctx, "_unmix_column_lock", None)
    if lock is None:
        lock = threading.Lock()
        ctx._unmix_column_lock = lock
    with lock:
        cache = getattr(ctx, "_unmix_column_cache", None)
        if (
            cache is None
            or getattr(ctx, "_unmix_column_revision", None) != revision
        ):
            cache = {}
            ctx._unmix_column_cache = cache
            ctx._unmix_column_revision = revision
        cached = cache.get(key)
    if cached is not None:
        return cached
    data = _data_columns._column_data(ctx, task_id, parquet, list(names))
    with lock:
        cache[key] = data
        while len(cache) > _UNMIX_COLUMN_CACHE_ENTRIES:
            cache.pop(next(iter(cache)))
    return data


def _unmix_spectrum_arrays_for(ctx, task_id, params, spectrum, columns, trees=None):
    """Return (names, arrays, error) for a spectrum from task parameters.

    Each array holds the transformed, finite values of one parameter inside
    the source gate, in `columns` order (parameters missing from the gate
    tree's file are skipped). `trees` overrides the saved tree list when
    the module is open and has unsaved edits.
    """
    import numpy as np

    if trees is None:
        trees = params.get("trees") or []
    tree = next((t for t in trees if t.get("id") == spectrum.get("tree_id")), None)
    if tree is None:
        return None, None, "The gate tree for this spectrum no longer exists."
    tree_params = tree.get("params") or {}
    gates = tree_params.get("gates") or []
    preview = tree_params.get("preview") or {}
    gate = next((g for g in gates if g.get("id") == spectrum.get("gate_id")), None)
    if gate is None:
        return None, None, "The source gate no longer exists."
    parquet = preview.get("file")
    if not parquet:
        return None, None, "The gate tree has no data file."
    # Gate columns and spectrum columns are evaluated through the same
    # ordered pipeline, so upstream Scale/Compensation modules apply.
    by_id = {g["id"]: g for g in gates}
    needed = set()
    for g in gates:
        needed |= _gate_needed_columns(g, by_id)
    # One read serves both the gate mask and the spectrum columns.
    wanted = sorted(needed | set(columns))
    all_values = _unmix_cached_columns(ctx, task_id, parquet, wanted)
    gate_values = {
        name: all_values[name] for name in needed if name in all_values
    }
    try:
        mask = _compute_gate_filter_mask(
            gates, gate["id"], parquet, {},
            existing=set(gate_values), extra=gate_values,
        )
    except Exception as exc:  # noqa: BLE001
        return None, None, f"Could not compute the gate: {exc}"
    if mask is None:
        return None, None, "Could not compute the gate (missing parameters)."

    data = all_values
    names = [p for p in columns if p in data]
    if not names:
        return None, None, "No parameters selected."
    mask = np.asarray(mask, dtype=bool)
    arrays = []
    for name in names:
        arr = np.asarray(data[name], dtype="float64")
        if arr.size != mask.size:
            return None, None, "Gate mask size mismatch."
        arr = arr[mask]
        arr = arr[np.isfinite(arr)]
        arrays.append(arr)
    return names, arrays, None


def _unmix_spectrum_values(ctx, page, spectrum):
    """Compute a spectrum from its source gate.

    Returns ``(names, values, hist, y_range, error)`` where `values` is the
    median/mean per parameter, `hist` is a (bins, parameters) density
    heatmap of the gated subset and `y_range` is the raw value range the
    heatmap spans.
    """
    names, arrays, error = _unmix_spectrum_arrays(ctx, page, spectrum)
    if error:
        return None, None, None, None, error
    stat = page._unmix_spectrum_cfg.get("stat", "median")
    names, values, hist, y_range = _unmix_values_from_arrays(names, arrays, stat)
    return names, values, hist, y_range, None


def _unmix_values_from_arrays(names, arrays, stat: str):
    """Reduce the gated arrays to per-channel statistics and a heatmap."""
    import numpy as np

    values = []
    for arr in arrays:
        if arr.size == 0:
            values.append(float("nan"))
        elif stat == "mean":
            values.append(float(arr.mean()))
        else:
            values.append(float(np.median(arr)))

    # Distribution heatmap: one column per parameter, shared raw value axis.
    bins = 128
    pooled = (
        np.concatenate([a for a in arrays if a.size])
        if any(a.size for a in arrays) else np.array([])
    )
    if pooled.size:
        lo, hi = np.percentile(pooled, [0.5, 99.5])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(pooled.min()), float(pooled.max())
        if hi <= lo:
            hi = lo + 1.0
    else:
        lo, hi = 0.0, 1.0
    hist = np.zeros((bins, len(names)), dtype="float32")
    for i, arr in enumerate(arrays):
        if arr.size == 0:
            continue
        counts, _ = np.histogram(
            np.clip(arr, lo, hi), bins=bins, range=(lo, hi)
        )
        peak = counts.max()
        if peak > 0:
            hist[:, i] = (counts / float(peak)).astype("float32")
    return names, values, hist, (lo, hi)


def _unmix_start_spectrum_refresh(ctx, page) -> None:
    """Recompute the active spectrum off the UI thread after gate edits."""
    spectrum = page._unmix_active_spectrum
    if spectrum is None:
        _unmix_refresh_outputs(ctx, page)
        return
    cfg = page._unmix_spectrum_cfg
    ordered = _unmix_sync_spectrum_params(ctx, page)
    enabled = set(cfg.get("enabled") or [])
    columns = [p for p in ordered if p in enabled]
    trees = [
        {"id": t["id"], "params": copy.deepcopy(t["store"].get("params", {}))}
        for t in page._unmix_trees
    ]
    page._unmix_spectrum_gen = getattr(page, "_unmix_spectrum_gen", 0) + 1
    gen = page._unmix_spectrum_gen
    q = queue.Queue()
    page._unmix_spectrum_queue = q
    thread = threading.Thread(
        target=_unmix_spectrum_worker,
        args=(ctx, page._unmix_task_id, q, gen, copy.deepcopy(spectrum),
              columns, trees, cfg.get("stat", "median")),
        daemon=True,
    )
    page._unmix_spectrum_thread = thread
    thread.start()
    timer = getattr(page, "_unmix_spectrum_poll_timer", None)
    if timer is None:
        timer = QTimer(ctx.host)
        timer.setInterval(100)
        timer.timeout.connect(lambda: _unmix_poll_spectrum(ctx, page))
        page._unmix_spectrum_poll_timer = timer
    timer.start()


def _unmix_spectrum_worker(ctx, task_id, q, gen, spectrum, columns, trees,
                           stat) -> None:
    """Background: gate mask, masked channel values and statistics."""
    try:
        names, arrays, error = _unmix_spectrum_arrays_for(
            ctx, task_id, {}, spectrum, columns, trees=trees
        )
        if error:
            q.put(("done", gen, spectrum.get("id"), None, error))
            return
        names, values, hist, y_range = _unmix_values_from_arrays(
            names, arrays, stat
        )
        q.put(("done", gen, spectrum.get("id"),
               (names, values, hist, y_range), None))
    except Exception as exc:  # noqa: BLE001
        q.put(("done", gen, spectrum.get("id"), None, str(exc)))


def _unmix_poll_spectrum(ctx, page) -> None:
    """Apply a background spectrum result on the UI thread."""
    q = getattr(page, "_unmix_spectrum_queue", None)
    if q is None:
        return
    msg = None
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
    if msg is None:
        return
    timer = getattr(page, "_unmix_spectrum_poll_timer", None)
    if timer is not None:
        timer.stop()
    page._unmix_spectrum_queue = None
    _kind, gen, spectrum_id, payload, error = msg
    if gen != getattr(page, "_unmix_spectrum_gen", 0):
        return
    spectrum = page._unmix_active_spectrum
    if spectrum is None or spectrum.get("id") != spectrum_id:
        return
    if error:
        _unmix_show_spectrum_error(ctx, page, error)
        return
    names, values, hist, y_range = payload
    cache = getattr(page, "_unmix_spectrum_cache", None)
    if cache is None:
        cache = {}
        page._unmix_spectrum_cache = cache
    cache[spectrum_id] = dict(zip(names, values))
    _unmix_paint_spectrum(ctx, page, spectrum, names, values, hist, y_range)
    _unmix_refresh_matrix_views(ctx, page)


def _unmix_render_spectrum_plot(ctx, page) -> None:
    """Compute and draw the active spectrum (synchronous path)."""
    plot_item = page._unmix_spectrum_plot.getPlotItem()
    spectrum = page._unmix_active_spectrum
    if spectrum is None:
        plot_item.clear()
        axis = plot_item.getAxis("bottom")
        axis.setTicks(None)
        axis.setHeight(None)
        plot_item.setTitle("")
        return
    names, values, hist, y_range, error = _unmix_spectrum_values(ctx,
        page, spectrum
    )
    if error:
        _unmix_show_spectrum_error(ctx, page, error)
        return
    _unmix_paint_spectrum(ctx, page, spectrum, names, values, hist, y_range)


def _unmix_show_spectrum_error(ctx, page, error) -> None:
    """Show a computation error in place of the spectrum plot."""
    plot_item = page._unmix_spectrum_plot.getPlotItem()
    plot_item.clear()
    axis = plot_item.getAxis("bottom")
    axis.setTicks(None)
    axis.setHeight(None)
    plot_item.setTitle(f"<span style='color:#b00000'>{error}</span>")


def _unmix_paint_spectrum(ctx, page, spectrum, names, values, hist,
                          y_range) -> None:
    """Draw precomputed spectrum values and their distribution heatmap."""
    import numpy as np

    plot_item = page._unmix_spectrum_plot.getPlotItem()
    plot_item.clear()
    axis = plot_item.getAxis("bottom")
    cfg = page._unmix_spectrum_cfg
    plot_item.setTitle(spectrum.get("name", ""))
    xs = list(range(len(names)))

    lo, hi = y_range
    finite = [v for v in values if np.isfinite(v)]
    y0 = min([lo] + finite)
    y1 = max([hi] + finite)
    if y1 <= y0:
        y1 = y0 + 1.0

    # Distribution heatmap drawn behind the spectrum line. Empty bins stay
    # transparent so the plot background shows through.
    cmap = _figure_colormap(cfg.get("colormap", "viridis"))
    rgb = np.array(cmap.getLookupTable(0.0, 1.0, 256), dtype="ubyte")
    lut = np.zeros((256, 4), dtype="ubyte")
    lut[:, :3] = rgb[:, :3]
    ramp = np.linspace(0.0, 1.0, 256) ** 0.45
    lut[:, 3] = np.clip(ramp * 255.0, 0, 255).astype("ubyte")
    lut[0, 3] = 0
    img = pg.ImageItem(axisOrder="row-major")
    img.setImage(hist, autoLevels=False, levels=(0.0, 1.0), lut=lut)
    img.setRect(QRectF(-0.5, lo, max(1, len(names)), hi - lo))
    img.setZValue(-10)
    plot_item.addItem(img)

    line_color = cfg.get("line_color", "#2b6cb0")
    plot_item.plot(
        xs, values,
        pen=pg.mkPen(line_color, width=2),
        symbol="o", symbolSize=6,
        symbolBrush=line_color, symbolPen=None,
    )
    axis.setTicks([[(i, name) for i, name in enumerate(names)]])
    # Rotated labels need as much height as the widest parameter name.
    font = axis.style.get("tickFont") or axis.font()
    metrics = QFontMetrics(font)
    widest = max((metrics.horizontalAdvance(str(n)) for n in names), default=0)
    axis.setHeight(int(widest) + 26)
    plot_item.setXRange(-0.5, max(0.5, len(names) - 0.5), padding=0)
    plot_item.setYRange(y0, y1, padding=0.02)


def _unmix_rebuild_spectrum_settings(ctx, page) -> None:
    """Build the module-wide spectrum settings (one set for all spectra)."""
    layout = page._unmix_spectrum_settings_layout
    _widgets_panels._clear_settings_layout(ctx, layout)
    enabled = not page._unmix_locked
    cfg = page._unmix_spectrum_cfg
    _unmix_sync_spectrum_params(ctx, page)

    layout.addWidget(QLabel("Statistic"))
    stat_combo = QComboBox()
    stat_combo.addItem("Median", "median")
    stat_combo.addItem("Mean", "mean")
    stat_index = stat_combo.findData(cfg.get("stat", "median"))
    stat_combo.setCurrentIndex(stat_index if stat_index >= 0 else 0)
    stat_combo.setEnabled(enabled)
    layout.addWidget(stat_combo)

    layout.addWidget(QLabel("Distribution colormap"))
    cmap_combo = _NoWheelComboBox()
    _widgets_colormap._populate_palette_combo(ctx,
        cmap_combo, cfg.get("colormap") or _default_palette()
    )
    cmap_combo.setEnabled(enabled)
    layout.addWidget(cmap_combo)

    params_row = QHBoxLayout()
    params_row.addWidget(QLabel("Parameters"))
    params_row.addStretch()
    selected_count = len(_unmix_selected_parameters(ctx, page))
    count_label = QLabel(f"{selected_count} selected")
    count_label.setStyleSheet("color: #5b6b82;")
    params_row.addWidget(count_label)
    layout.addLayout(params_row)
    params_btn = QPushButton("Set")
    params_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    params_btn.setEnabled(enabled)
    layout.addWidget(params_btn)

    def update_params_button():
        params_btn.setToolTip(
            f"{len(cfg.get('enabled') or [])}/{len(cfg.get('params') or [])}"
        )

    update_params_button()
    page._unmix_params_btn = params_btn
    params_btn.clicked.connect(
        lambda _=False: _unmix_edit_spectrum_parameters(ctx, page)
    )

    # The selected parameters, in plotting order; drag a row to reorder.
    selected_names = _unmix_selected_parameters(ctx, page)
    if selected_names:
        order_list = QListWidget()
        order_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        order_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        order_list.setDropIndicatorShown(True)
        order_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        order_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        handle_icon = QIcon(_drag_handle_pixmap())
        for name in selected_names:
            item = QListWidgetItem(handle_icon, name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip("Drag to reorder")
            order_list.addItem(item)
        order_list.setEnabled(enabled)

        def on_rows_moved(*_args):
            order = [
                order_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(order_list.count())
            ]
            _unmix_apply_parameter_order(ctx, page, order)

        order_list.model().rowsMoved.connect(on_rows_moved)
        layout.addWidget(order_list, 1)
    else:
        hint = QLabel("No parameters selected. Click Set to choose.")
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

    layout.addWidget(QLabel("Spectrum line color"))
    color_btn = QPushButton()
    color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    color_btn.setEnabled(enabled)
    color_btn.setFixedHeight(26)
    layout.addWidget(color_btn)

    layout.addWidget(QLabel("Matrix decimal places"))
    decimals_spin = _NoWheelSpinBox()
    decimals_spin.setRange(0, 6)
    decimals_spin.setValue(int(cfg.get("decimals", 2)))
    decimals_spin.setEnabled(enabled)
    layout.addWidget(decimals_spin)

    def refresh():
        _unmix_refresh_outputs(ctx, page)
        _unmix_mark_dirty(ctx, page)

    def update_color_button():
        color = QColor(cfg.get("line_color", "#2b6cb0"))
        text_color = "#ffffff" if color.lightness() < 128 else "#000000"
        color_btn.setText(color.name())
        color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {color.name()}; "
            f"color: {text_color}; border: 1px solid #999; }}"
        )

    def pick_line_color():
        color = QColorDialog.getColor(
            QColor(cfg.get("line_color", "#2b6cb0")), ctx.host,
            "Spectrum line color",
        )
        if color.isValid():
            cfg["line_color"] = color.name()
            update_color_button()
            refresh()

    def on_stat_changed():
        cfg["stat"] = stat_combo.currentData()
        refresh()

    def on_colormap_changed():
        cfg["colormap"] = cmap_combo.currentData()
        refresh()

    def on_decimals_changed(value):
        cfg["decimals"] = int(value)
        _unmix_refresh_matrix_views(ctx, page)
        _unmix_mark_dirty(ctx, page)

    update_color_button()
    stat_combo.currentIndexChanged.connect(lambda _=0: on_stat_changed())
    cmap_combo.currentIndexChanged.connect(lambda _=0: on_colormap_changed())
    color_btn.clicked.connect(lambda _=False: pick_line_color())
    decimals_spin.valueChanged.connect(on_decimals_changed)


def _unmix_edit_spectrum_parameters(ctx, page) -> None:
    """Pick which parameters a spectrum contains (shared picker dialog)."""
    cfg = page._unmix_spectrum_cfg
    ordered = _unmix_sync_spectrum_params(ctx, page)
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Spectrum parameters", ordered, set(cfg.get("enabled") or []),
        "Filter parameters...",
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    cfg["enabled"] = [p for p in cfg.get("params", []) if p in chosen_set]
    _unmix_rebuild_spectrum_settings(ctx, page)
    _unmix_render_spectrum_plot(ctx, page)
    _unmix_update_manual_summary(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_matrix_subpage(ctx, page) -> QWidget:
    """Sub-page 2: signatures + samples (left) | matrix image + settings (right)."""
    w = QWidget()
    w.setStyleSheet("background: #f5f8fb;")
    row = QHBoxLayout(w)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(12)

    left = QSplitter(Qt.Orientation.Vertical)
    left.setChildrenCollapsible(False)
    left.addWidget(_unmix_signature_panel(ctx, page))
    left.addWidget(_unmix_sample_panel(ctx, page))
    left.setStretchFactor(0, 3)
    left.setStretchFactor(1, 2)
    left.setSizes([420, 240])

    right = QSplitter(Qt.Orientation.Vertical)
    right.setChildrenCollapsible(False)
    right.addWidget(_unmix_matrix_panel(ctx, page))
    lower = QSplitter(Qt.Orientation.Horizontal)
    lower.setChildrenCollapsible(False)
    lower.addWidget(_unmix_image_panel(ctx, page))
    lower.addWidget(_unmix_matrix_settings_panel(ctx, page))
    lower.setStretchFactor(0, 1)
    lower.setStretchFactor(1, 1)
    lower.setSizes([400, 400])
    right.addWidget(lower)
    right.setStretchFactor(0, 3)
    right.setStretchFactor(1, 1)
    right.setSizes([560, 260])

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 2)
    splitter.setSizes([400, 800])
    row.addWidget(splitter, 1)
    return w


def _unmix_matrix_panel(ctx, page) -> QWidget:
    """The matrix area: spectral matrix / curve / similarity sub-pages."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(6)

    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    tabs.addTab(_unmix_matrix_tab_page(ctx, page), "Signature matrix")
    tabs.addTab(_unmix_curve_tab_page(ctx, page), "Signature curves")
    tabs.addTab(_unmix_similarity_tab_page(ctx, page), "Similarity matrix")
    lay.addWidget(tabs, 1)

    page._unmix_matrix_tabs = tabs
    page._unmix_matrix_tab_keys = ("matrix", "curve", "similarity")
    key = page._unmix_params.get("matrix_tab")
    if key not in page._unmix_matrix_tab_keys:
        key = "matrix"
    page._unmix_matrix_tab = key
    tabs.blockSignals(True)
    tabs.setCurrentIndex(page._unmix_matrix_tab_keys.index(key))
    tabs.blockSignals(False)
    tabs.currentChanged.connect(lambda index: _unmix_on_matrix_tab(ctx, page, index))
    return panel


def _unmix_matrix_tab_page(ctx, page) -> QWidget:
    """Sub-page 1: the matrix heatmap (rendered to an image)."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
    scroll.setStyleSheet("background: #ffffff; border: none;")
    label = _ZoomPixmapLabel(
        on_zoom=lambda f, pos: _unmix_zoom_matrix(ctx, page, f, pos)
    )
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("background: #ffffff; color: #888888;")
    label.setText(_unmix_empty_matrix_text(
        page,
        "Add a signature to build the matrix.",
        "Paste a matrix to build the matrix.",
    ))
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    label.customContextMenuRequested.connect(
        lambda pos: _unmix_heatmap_menu(ctx, page, "matrix", pos)
    )
    scroll.setWidget(label)
    page._unmix_matrix_label = label
    page._unmix_matrix_image = None
    page._unmix_matrix_scroll = scroll
    return scroll


def _unmix_curve_tab_page(ctx, page) -> QWidget:
    """Sub-page 2: one normalized curve per signature."""
    host = QWidget()
    host.setStyleSheet("background: #ffffff;")
    lay = QVBoxLayout(host)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(6)

    legend = QWidget()
    legend.setStyleSheet("background: transparent;")
    legend_lay = QGridLayout(legend)
    legend_lay.setContentsMargins(0, 0, 0, 0)
    legend_lay.setHorizontalSpacing(12)
    legend_lay.setVerticalSpacing(4)
    lay.addWidget(legend)

    plot = pg.PlotWidget(background=_plot_background())
    plot.showGrid(x=True, y=True, alpha=0.2)
    plot.getPlotItem().setAxisItems({
        "bottom": _make_rotated_axis("bottom"),
        # Plain decimals: normalized intensities are small values and a
        # scientific axis makes them hard to read.
        "left": _make_fixed_axis("left", decimals=1),
    })
    plot.getPlotItem().setLabel("left", "Normalized intensity")
    plot.getPlotItem().setLabel("bottom", "Channel")
    lay.addWidget(plot, 1)

    page._unmix_curve_legend = legend
    page._unmix_curve_legend_lay = legend_lay
    page._unmix_curve_plot = plot
    # The signature names sit in a widget above the plot; keep them in exports.
    plot._export_top_widget = legend
    _unmix_plot_export_setup(ctx, page, plot, "Unmix signature curves")
    return host


def _unmix_similarity_tab_page(ctx, page) -> QWidget:
    """Sub-page 3: pairwise cosine similarity between the signatures."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
    scroll.setStyleSheet("background: #ffffff; border: none;")
    label = _ZoomPixmapLabel(
        on_zoom=lambda f, pos: _unmix_zoom_matrix(ctx, page, f, pos)
    )
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("background: #ffffff; color: #888888;")
    label.setText(_unmix_empty_matrix_text(
        page,
        "Add at least two signatures to compare them.",
        "Paste at least two signatures to compare them.",
    ))
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    label.customContextMenuRequested.connect(
        lambda pos: _unmix_heatmap_menu(ctx, page, "similarity", pos)
    )
    scroll.setWidget(label)
    page._unmix_similarity_label = label
    page._unmix_similarity_image = None
    page._unmix_similarity_scroll = scroll
    return scroll


def _unmix_on_matrix_tab(ctx, page, index) -> None:
    keys = getattr(page, "_unmix_matrix_tab_keys", ("matrix", "curve", "similarity"))
    key = keys[index] if 0 <= index < len(keys) else "matrix"
    page._unmix_matrix_tab = key
    page._unmix_params["matrix_tab"] = key
    _unmix_rebuild_matrix_settings(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)


def _unmix_matrix_settings_panel(ctx, page) -> QWidget:
    """Settings for the active matrix sub-page (content swaps with the tab)."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    title = QLabel("<b>Matrix settings</b>")
    lay.addWidget(title)

    # Scrollable so a long signature list (Curve settings) does not raise
    # the panel's minimum height and freeze the splitter above it.
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setObjectName("unmixCurveScroll")
    scroll.setStyleSheet(
        "QScrollArea#unmixCurveScroll { background: transparent; }"
    )
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    host = QWidget()
    host.setObjectName("unmixCurveHost")
    host.setStyleSheet(
        "QWidget#unmixCurveHost { background: transparent; }"
    )
    host_lay = QVBoxLayout(host)
    host_lay.setContentsMargins(0, 0, 6, 0)
    host_lay.setSpacing(6)
    scroll.setWidget(host)
    lay.addWidget(scroll, 1)

    page._unmix_matrix_settings_title = title
    page._unmix_matrix_settings_lay = host_lay
    _unmix_rebuild_matrix_settings(ctx, page)
    return panel


def _unmix_rebuild_matrix_settings(ctx, page) -> None:
    lay = getattr(page, "_unmix_matrix_settings_lay", None)
    if lay is None:
        return
    key = getattr(page, "_unmix_matrix_tab", "matrix")
    title = getattr(page, "_unmix_matrix_settings_title", None)
    if title is not None:
        title.setText({
            "matrix": "<b>Matrix settings</b>",
            "curve": "<b>Curve settings</b>",
            "similarity": "<b>Similarity settings</b>",
        }.get(key, "<b>Matrix settings</b>"))
    _widgets_panels._clear_layout(ctx, lay)
    if key == "curve":
        _unmix_curve_settings_content(ctx, page, lay)
    else:
        _unmix_grid_settings_content(ctx, page, lay, key)
    lay.addStretch(1)


def _unmix_grid_settings_content(ctx, page, lay, section: str) -> None:
    """Cell geometry, colours and number display for a matrix-style image."""
    cfg = _unmix_matrix_cfg(ctx, page, section)
    editable = not page._unmix_locked
    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(6)

    grid.addWidget(QLabel("Cell width"), 0, 0)
    width_spin = _NoWheelSpinBox()
    width_spin.setRange(12, 400)
    width_spin.setValue(int(cfg["cell_width"]))
    width_spin.setEnabled(editable)
    grid.addWidget(width_spin, 0, 1)

    grid.addWidget(QLabel("Cell height"), 0, 2)
    height_spin = _NoWheelSpinBox()
    height_spin.setRange(12, 200)
    height_spin.setValue(int(cfg["cell_height"]))
    height_spin.setEnabled(editable)
    grid.addWidget(height_spin, 0, 3)

    grid.addWidget(QLabel("Colormap"), 1, 0)
    cmap_combo = _NoWheelComboBox()
    _widgets_colormap._populate_palette_combo(ctx,
        cmap_combo, cfg.get("colormap") or _default_palette()
    )
    cmap_combo.setEnabled(editable)
    grid.addWidget(cmap_combo, 1, 1, 1, 3)

    show_numbers = QCheckBox("Show numbers")
    show_numbers.setChecked(bool(cfg["show_numbers"]))
    show_numbers.setEnabled(editable)
    grid.addWidget(show_numbers, 2, 0, 1, 2)

    auto_color = QCheckBox("Auto number color")
    auto_color.setChecked(bool(cfg["auto_text_color"]))
    auto_color.setEnabled(editable)
    grid.addWidget(auto_color, 2, 2, 1, 2)

    grid.addWidget(QLabel("Number color"), 3, 0)
    color_btn = QPushButton()
    color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    color_btn.setFixedHeight(26)
    color_btn.setEnabled(editable and not auto_color.isChecked())
    grid.addWidget(color_btn, 3, 1)

    copy_btn = QPushButton(
        "Copy similarity to clipboard" if section == "similarity"
        else "Copy matrix to clipboard"
    )
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    grid.addWidget(copy_btn, 3, 2, 1, 2)
    lay.addLayout(grid)

    if section == "similarity":
        hint = QLabel(
            "Cosine similarity, fixed scale −1 … 1 (diverging colormap)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7785;")
        lay.addWidget(hint)

    def update_color_button():
        color = QColor(cfg["text_color"])
        text_color = "#ffffff" if color.lightness() < 128 else "#000000"
        color_btn.setText(color.name())
        color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {color.name()}; "
            f"color: {text_color}; border: 1px solid #999; }}"
        )

    def refresh():
        _unmix_refresh_matrix_views(ctx, page)
        _unmix_mark_dirty(ctx, page, data=False)

    def on_width(value):
        cfg["cell_width"] = int(value)
        refresh()

    def on_height(value):
        cfg["cell_height"] = int(value)
        refresh()

    def on_colormap():
        cfg["colormap"] = cmap_combo.currentData()
        refresh()

    def on_show_numbers(checked):
        cfg["show_numbers"] = bool(checked)
        refresh()

    def on_auto_color(checked):
        cfg["auto_text_color"] = bool(checked)
        color_btn.setEnabled(editable and not checked)
        refresh()

    def pick_color():
        color = QColorDialog.getColor(
            QColor(cfg["text_color"]), ctx.host, "Matrix number color"
        )
        if color.isValid():
            cfg["text_color"] = color.name()
            update_color_button()
            refresh()

    width_spin.valueChanged.connect(on_width)
    height_spin.valueChanged.connect(on_height)
    cmap_combo.currentIndexChanged.connect(lambda _=0: on_colormap())
    show_numbers.toggled.connect(on_show_numbers)
    auto_color.toggled.connect(on_auto_color)
    color_btn.clicked.connect(lambda _=False: pick_color())
    copy_btn.clicked.connect(
        lambda _=False: (
            _unmix_copy_similarity(ctx, page) if section == "similarity"
            else _unmix_copy_matrix(ctx, page)
        )
    )
    update_color_button()


def _unmix_curve_settings_content(ctx, page, lay) -> None:
    """Line width and one colour per signature for the curve sub-page."""
    cfg = _unmix_matrix_cfg(ctx, page, "curve")
    editable = not page._unmix_locked

    row = QHBoxLayout()
    row.addWidget(QLabel("Line width"))
    width_spin = _NoWheelSpinBox()
    width_spin.setRange(1, 6)
    width_spin.setValue(int(cfg.get("line_width", 2)))
    width_spin.setEnabled(editable)
    row.addWidget(width_spin)
    row.addStretch()
    lay.addLayout(row)

    lay.addWidget(QLabel("Signatures (tick to draw)"))
    rows = _unmix_active_rows(page)
    if not rows:
        hint = QLabel("Add a signature to give it a colour.")
        hint.setStyleSheet("color: #6b7785;")
        lay.addWidget(hint)
    colors = _unmix_curve_colors(ctx, page, rows)
    visible = cfg.setdefault("visible", {})
    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(4)
    for i, row_def in enumerate(rows):
        sid = str(row_def.get("id") or row_def.get("name") or "")
        name = QCheckBox(str(row_def.get("name") or ""))
        name.setToolTip(sid)
        name.setChecked(bool(visible.get(sid, True)))
        name.setEnabled(editable)

        def on_visible(checked, sid=sid, button=None):
            _unmix_matrix_cfg(ctx, page, "curve").setdefault("visible", {})[
                sid
            ] = bool(checked)
            _unmix_render_curve(ctx, page)
            _unmix_mark_dirty(ctx, page, data=False)

        name.toggled.connect(on_visible)
        grid.addWidget(name, i, 0)

        def color_style(color):
            qc = QColor(color)
            text = "#ffffff" if qc.lightness() < 128 else "#000000"
            return (
                f"QPushButton {{ background-color: {color}; color: {text}; "
                f"border: 1px solid #999; }}"
            )

        btn = QPushButton(colors.get(sid, ""))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(24)
        btn.setEnabled(editable)
        btn.setStyleSheet(color_style(colors.get(sid, "#ffffff")))

        def pick_color(_=False, sid=sid, button=btn):
            color = QColorDialog.getColor(
                QColor(button.text()), ctx.host, "Signature line colour"
            )
            if not color.isValid():
                return
            _unmix_matrix_cfg(ctx, page, "curve").setdefault("colors", {})[sid] = (
                color.name()
            )
            button.setText(color.name())
            button.setStyleSheet(color_style(color.name()))
            _unmix_render_curve(ctx, page)
            _unmix_mark_dirty(ctx, page, data=False)

        btn.clicked.connect(pick_color)
        grid.addWidget(btn, i, 1)
    lay.addLayout(grid)

    reset_btn = QPushButton("Reset colours to defaults")
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_btn.setEnabled(editable and bool(rows))

    def reset_colors(_=False):
        cfg["colors"] = {}
        _unmix_rebuild_matrix_settings(ctx, page)
        _unmix_render_curve(ctx, page)
        _unmix_mark_dirty(ctx, page, data=False)

    reset_btn.clicked.connect(reset_colors)
    lay.addWidget(reset_btn)

    def on_width(value):
        cfg["line_width"] = int(value)
        _unmix_render_curve(ctx, page)
        _unmix_mark_dirty(ctx, page, data=False)

    width_spin.valueChanged.connect(on_width)


def _unmix_matrix_cfg(ctx, page, section: str='matrix') -> dict:
    """Display settings of one matrix sub-page (``matrix``/``curve``/
    ``similarity``). Older projects stored the matrix keys at the top
    level; they are migrated into the ``matrix`` section once."""
    store = page._unmix_params.setdefault("matrix_settings", {})
    legacy_keys = (
        "cell_width", "cell_height", "colormap",
        "show_numbers", "auto_text_color", "text_color",
    )
    legacy = {k: store.pop(k) for k in legacy_keys if k in store}
    if legacy and not isinstance(store.get("matrix"), dict):
        store["matrix"] = dict(legacy)
    cfg = store.setdefault(section, {})
    defaults = {
        "matrix": {
            "cell_width": 35,
            "cell_height": 28,
            "colormap": "viridis",
            "show_numbers": True,
            "auto_text_color": True,
            "text_color": "#5b6b82",
        },
        "curve": {
            "line_width": 2,
            "colors": {},
            "visible": {},
        },
        "similarity": {
            "cell_width": 62,
            "cell_height": 28,
            "colormap": "coolwarm",
            "show_numbers": True,
            "auto_text_color": True,
            "text_color": "#5b6b82",
        },
    }.get(section, {})
    for key, value in defaults.items():
        cfg.setdefault(key, value)
    if section == "curve":
        for key in ("colors", "visible"):
            if not isinstance(cfg.get(key), dict):
                cfg[key] = {}
    return cfg


def _unmix_curve_colors(ctx, page, rows) -> dict:
    """One stable line colour per signature ({signature id: #rrggbb})."""
    import zlib

    cfg = _unmix_matrix_cfg(ctx, page, "curve")
    saved = cfg.setdefault("colors", {})
    palette = UNMIX_CURVE_COLORS
    used = set()
    out = {}
    for row in rows:
        sid = str(row.get("id") or row.get("name") or "")
        color = saved.get(sid)
        if not (isinstance(color, str) and color):
            # Deterministic default: reordering or renaming keeps it, and
            # unsaved sessions show the same colours.
            base = zlib.crc32(sid.encode("utf-8"))
            for step in range(len(palette)):
                candidate = palette[(base + step) % len(palette)]
                if candidate not in used:
                    color = candidate
                    break
            else:
                color = palette[base % len(palette)]
        out[sid] = color
        used.add(color)
    return out


def _unmix_similarity_values(ctx, page):
    """(names, matrix) of pairwise cosine similarity between signatures.

    Channels missing on either side are ignored (spectra are required to
    share their channels, so this only guards against incomplete rows).
    """
    import numpy as np

    _columns, rows = _unmix_matrix_table_values(ctx, page)
    names = [str(row.get("name", "")) for row, _values in rows]
    vectors = [
        np.asarray(values, dtype="float64") for _row, values in rows
    ]
    count = len(vectors)
    matrix = np.full((count, count), np.nan, dtype="float64")
    for i in range(count):
        for j in range(i, count):
            a, b = vectors[i], vectors[j]
            ok = np.isfinite(a) & np.isfinite(b)
            value = np.nan
            if ok.any():
                va, vb = a[ok], b[ok]
                denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
                if denom > 0:
                    value = float(np.dot(va, vb) / denom)
            matrix[i, j] = matrix[j, i] = value
    return names, matrix


def _unmix_copy_similarity(ctx, page) -> None:
    """Copy the similarity matrix as tab-separated text."""
    import numpy as np

    names, matrix = _unmix_similarity_values(ctx, page)
    if len(names) < 2:
        QMessageBox.information(
            ctx.host, "Copy similarity", "There is no similarity matrix to copy."
        )
        return
    lines = ["\t".join(["Signature"] + names)]
    for i, name in enumerate(names):
        cells = [name] + [
            f"{v:.4f}" if np.isfinite(v) else "" for v in matrix[i]
        ]
        lines.append("\t".join(cells))
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Similarity matrix copied to clipboard")


def _unmix_copy_matrix(ctx, page) -> None:
    """Copy the matrix as tab-separated text (pasteable into Excel)."""
    import numpy as np

    columns, rows = _unmix_matrix_table_values(ctx, page)
    if not rows or not columns:
        QMessageBox.information(
            ctx.host, "Copy matrix", "There is no matrix to copy."
        )
        return
    lines = ["\t".join(["Signature"] + list(columns))]
    for row, values in rows:
        cells = [str(row.get("name", ""))]
        cells += [
            # 17 significant digits round-trip every float64 value, so a
            # matrix pasted into another Unmix module reproduces the source
            # matrix exactly.
            format(float(v), ".17g") if np.isfinite(v) else ""
            for v in values
        ]
        lines.append("\t".join(cells))
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Matrix copied to clipboard")


def _unmix_signature_panel(ctx, page) -> QWidget:
    """Positive/negative/normalize/name rows that define the matrix."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Signatures</b>"))

    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("Signature source"))
    mode_combo = _NoWheelComboBox()
    mode_combo.addItem("From spectra", "spectra")
    mode_combo.addItem("Manual input", "manual")
    source = page._unmix_params.get("signature_source") or "spectra"
    index = mode_combo.findData(source)
    mode_combo.setCurrentIndex(max(0, index))
    mode_combo.setEnabled(not page._unmix_locked)
    mode_row.addWidget(mode_combo, 1)
    lay.addLayout(mode_row)

    stack = QStackedWidget()
    stack.addWidget(_unmix_spectra_signature_page(ctx, page))
    stack.addWidget(_unmix_manual_signature_page(ctx, page))
    stack.setCurrentIndex(1 if source == "manual" else 0)
    lay.addWidget(stack, 1)

    page._unmix_signature_mode_combo = mode_combo
    page._unmix_signature_stack = stack
    mode_combo.currentIndexChanged.connect(
        lambda _=0: _unmix_on_signature_source(ctx, page)
    )
    return panel


def _unmix_spectra_signature_page(ctx, page) -> QWidget:
    """Add signature + the four-column spectra editor."""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    add_btn = QPushButton("Add signature")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_btn.setEnabled(not page._unmix_locked)
    add_btn.clicked.connect(lambda _=False: _unmix_add_signature(ctx, page))
    add_row = QHBoxLayout()
    add_row.addWidget(add_btn)
    add_row.addStretch()
    lay.addLayout(add_row)

    table = QTableWidget()
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(
        ["Positive spectrum", "Negative spectrum", "Normalize", "Name"]
    )
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
    )
    table.verticalHeader().setDefaultSectionSize(30)
    table.verticalHeader().setSectionsMovable(True)
    table.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.ResizeMode.Stretch
    )
    table.horizontalHeader().setSectionResizeMode(
        1, QHeaderView.ResizeMode.Stretch
    )
    table.horizontalHeader().setSectionResizeMode(
        2, QHeaderView.ResizeMode.ResizeToContents
    )
    table.horizontalHeader().setSectionResizeMode(
        3, QHeaderView.ResizeMode.Stretch
    )
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.customContextMenuRequested.connect(
        lambda pos: _unmix_signature_menu(ctx, page, pos)
    )
    header = table.verticalHeader()
    header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header.customContextMenuRequested.connect(
        lambda pos: _unmix_signature_header_menu(ctx, page, pos)
    )
    header.sectionMoved.connect(
        lambda *_: page._unmix_sig_order_timer.start()
    )
    page._unmix_signature_table = table
    lay.addWidget(table, 1)
    return host


def _unmix_manual_signature_page(ctx, page) -> QWidget:
    """Paste a matrix copied from another Unmix module; cells stay editable."""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    top = QHBoxLayout()
    paste_btn = QPushButton("Paste matrix")
    paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    paste_btn.setEnabled(not page._unmix_locked)
    paste_btn.clicked.connect(
        lambda _=False: _unmix_paste_manual_matrix(ctx, page)
    )
    top.addWidget(paste_btn)
    top.addStretch()
    info = QLabel("")
    info.setStyleSheet("color: #6b7785; font-size: 8pt;")
    top.addWidget(info)
    lay.addLayout(top)

    hint = QLabel(
        "Copy a matrix with Copy matrix in another Unmix module, then paste "
        "it here. Every cell stays editable."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)

    table = QTableWidget()
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
    )
    table.verticalHeader().setDefaultSectionSize(28)
    table.verticalHeader().setSectionsMovable(True)
    table.verticalHeader().sectionMoved.connect(
        lambda *_: page._unmix_manual_order_timer.start()
    )
    table.itemChanged.connect(
        lambda item: _unmix_on_manual_cell(ctx, page, item)
    )
    page._unmix_manual_table = table
    page._unmix_manual_info = info
    page._unmix_manual_hint = hint
    page._unmix_paste_btn = paste_btn
    lay.addWidget(table, 1)
    return host


def _unmix_sample_panel(ctx, page) -> QWidget:
    """Which imported samples this matrix is applied to."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    head = QHBoxLayout()
    head.addWidget(QLabel("<b>Samples to be unmixed</b>"))
    head.addStretch()
    lay.addLayout(head)

    select_btn = QPushButton("Select files…")
    select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    select_btn.setEnabled(not page._unmix_locked)
    select_btn.clicked.connect(
        lambda _=False: _unmix_select_samples(ctx, page)
    )
    lay.addWidget(select_btn)

    samples_list = QListWidget()
    samples_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: none; }"
    )
    samples_list.setSelectionMode(
        QAbstractItemView.SelectionMode.NoSelection
    )
    page._unmix_samples_list = samples_list
    lay.addWidget(samples_list, 1)
    return panel


def _unmix_image_panel(ctx, page) -> QWidget:
    """Unmixing method, the Run unmix action and its log."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Unmixing</b>"))

    method_row = QHBoxLayout()
    method_row.addWidget(QLabel("Unmixing method"))
    method_combo = _NoWheelComboBox()
    method_combo.addItem("OLS (ordinary least squares)", "ols")
    method_index = method_combo.findData(
        page._unmix_params.get("method", "ols")
    )
    method_combo.setCurrentIndex(method_index if method_index >= 0 else 0)
    method_combo.setEnabled(not page._unmix_locked)
    method_combo.currentIndexChanged.connect(
        lambda _=0: _unmix_on_method_changed(ctx, page, method_combo)
    )
    method_row.addWidget(method_combo, 1)
    lay.addLayout(method_row)

    run_btn = QPushButton("Run unmix")
    run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    run_btn.setEnabled(not page._unmix_locked)
    run_btn.setToolTip(
        "Generate the unmixed channels of every selected sample and save "
        "them for downstream modules; image files also get image pyramids"
    )
    run_btn.clicked.connect(lambda _=False: _unmix_start_run(ctx, page))
    lay.addWidget(run_btn)
    page._unmix_run_btn = run_btn

    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    for line in page._unmix_params.get("image_log") or []:
        log.appendPlainText(str(line))
    lay.addWidget(log, 1)
    page._unmix_image_log = log
    return panel


def _unmix_rebuild_signature_table(ctx, page) -> None:
    """(Re)build the four-column signature editor from the data model."""
    table = page._unmix_signature_table
    spectra = page._unmix_spectra
    rows = page._unmix_matrix
    editable = not page._unmix_locked
    table.setRowCount(0)
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        _unmix_fill_signature_row(ctx, page, table, i, row, spectra, editable)
    table.setVerticalHeaderLabels([r.get("name", "") for r in rows])
    table.resizeRowsToContents()


def _unmix_fill_signature_row(ctx, page, table, i, row, spectra,
                              editable) -> None:
    """Build the widgets of one signature row (combo, combo, tick, name)."""
    pos_combo = _NoWheelComboBox()
    for spectrum in spectra:
        pos_combo.addItem(spectrum["name"], spectrum["id"])
    index = pos_combo.findData(row.get("positive"))
    pos_combo.setCurrentIndex(index if index >= 0 else 0)
    pos_combo.setEnabled(editable)

    neg_combo = _NoWheelComboBox()
    neg_combo.addItem("NA", None)
    for spectrum in spectra:
        neg_combo.addItem(spectrum["name"], spectrum["id"])
    index = neg_combo.findData(row.get("negative"))
    neg_combo.setCurrentIndex(index if index >= 0 else 0)
    neg_combo.setEnabled(editable)

    norm_box = QCheckBox()
    norm_box.setChecked(bool(row.get("normalize", True)))
    norm_box.setEnabled(editable)
    norm_host = QWidget()
    norm_lay = QHBoxLayout(norm_host)
    norm_lay.setContentsMargins(0, 0, 0, 0)
    norm_lay.addStretch()
    norm_lay.addWidget(norm_box)
    norm_lay.addStretch()

    name_edit = QLineEdit(row.get("name", ""))
    name_edit.setEnabled(editable)

    pos_combo.currentIndexChanged.connect(
        lambda _=0, r=row, c=pos_combo: _unmix_on_signature_field(ctx,
            page, r, "positive", c.currentData()
        )
    )
    neg_combo.currentIndexChanged.connect(
        lambda _=0, r=row, c=neg_combo: _unmix_on_signature_field(ctx,
            page, r, "negative", c.currentData()
        )
    )
    norm_box.toggled.connect(
        lambda checked, r=row: _unmix_on_signature_field(ctx,
            page, r, "normalize", bool(checked)
        )
    )
    name_edit.editingFinished.connect(
        lambda r=row, e=name_edit: _unmix_set_signature_name(ctx,
            page, r, e
        )
    )
    # Cell widgets swallow the table's context menu, so each one gets
    # its own "Remove signature" entry.
    for widget in (pos_combo, neg_combo, norm_box, norm_host, name_edit):
        widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        widget.customContextMenuRequested.connect(
            lambda pos, w=widget, r=row: _unmix_signature_widget_menu(ctx,
                page, r, w, pos
            )
        )

    table.setCellWidget(i, 0, pos_combo)
    table.setCellWidget(i, 1, neg_combo)
    table.setCellWidget(i, 2, norm_host)
    table.setCellWidget(i, 3, name_edit)


def _unmix_signature_reordered(ctx, page) -> None:
    """Apply the visual row order (from dragging the header) to the model."""
    table = page._unmix_signature_table
    header = table.verticalHeader()
    count = len(page._unmix_matrix)
    order = sorted(range(count), key=lambda i: header.visualIndex(i))
    if order == list(range(count)):
        return
    page._unmix_matrix[:] = [page._unmix_matrix[i] for i in order]
    _unmix_rebuild_signature_table(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_signature_menu(ctx, page, pos) -> None:
    table = page._unmix_signature_table
    index = table.rowAt(pos.y())
    if index < 0 or index >= len(page._unmix_matrix):
        return
    menu = QMenu(table)
    act = QAction("Remove signature", menu)
    act.setEnabled(not page._unmix_locked)
    act.triggered.connect(
        lambda _=False: _unmix_remove_signature(ctx, page, index)
    )
    menu.addAction(act)
    menu.exec(table.viewport().mapToGlobal(pos))


def _unmix_signature_header_menu(ctx, page, pos) -> None:
    """Right-click on a row header: remove that signature."""
    header = page._unmix_signature_table.verticalHeader()
    index = header.logicalIndexAt(pos)
    if index < 0 or index >= len(page._unmix_matrix):
        return
    menu = QMenu(header)
    act = QAction("Remove signature", menu)
    act.setEnabled(not page._unmix_locked)
    act.triggered.connect(
        lambda _=False: _unmix_remove_signature(ctx, page, index)
    )
    menu.addAction(act)
    menu.exec(header.mapToGlobal(pos))


def _unmix_signature_widget_menu(ctx, page, row, widget, pos) -> None:
    """Right-click inside a signature row's cell widgets."""
    index = next(
        (
            i
            for i, other in enumerate(page._unmix_matrix)
            if other is row
        ),
        -1,
    )
    if index < 0:
        return
    if isinstance(widget, QLineEdit):
        menu = widget.createStandardContextMenu()
        menu.addSeparator()
    else:
        menu = QMenu(widget)
    act = QAction("Remove signature", menu)
    act.setEnabled(not page._unmix_locked)
    act.triggered.connect(
        lambda _=False: _unmix_remove_signature(ctx, page, index)
    )
    menu.addAction(act)
    menu.exec(widget.mapToGlobal(pos))
    menu.deleteLater()


def _unmix_add_signature(ctx, page) -> None:
    if not page._unmix_spectra:
        QMessageBox.information(
            ctx.host,
            "Add signature",
            "Create a spectrum first on the Spectrum Extraction tab.",
        )
        return
    spectrum = page._unmix_spectra[0]
    row = {
        "id": uuid4().hex[:8],
        "positive": spectrum["id"],
        "negative": None,
        "normalize": True,
        "name": _unmix_unique_signature_name(ctx, page, spectrum["name"]),
    }
    page._unmix_matrix.append(row)
    table = page._unmix_signature_table
    index = len(page._unmix_matrix) - 1
    table.insertRow(index)
    _unmix_fill_signature_row(
        ctx, page, table, index, row, page._unmix_spectra,
        not page._unmix_locked,
    )
    table.setVerticalHeaderLabels(
        [r.get("name", "") for r in page._unmix_matrix]
    )
    table.resizeRowToContents(index)
    _unmix_refresh_matrix_views(ctx, page)
    # Signatures do not change the spectrum vectors, so the per-spectrum
    # cache stays valid.
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_remove_signature(ctx, page, index: int) -> None:
    if 0 <= index < len(page._unmix_matrix):
        del page._unmix_matrix[index]
        table = page._unmix_signature_table
        table.removeRow(index)
        table.setVerticalHeaderLabels(
            [r.get("name", "") for r in page._unmix_matrix]
        )
        _unmix_refresh_matrix_views(ctx, page)
        _unmix_mark_dirty(ctx, page, data=False)


def _unmix_empty_matrix_text(page, spectra_text: str,
                             manual_text: str) -> str:
    """Empty-state wording for the active signature source."""
    return manual_text if _unmix_signature_source(page) == "manual" else spectra_text


def _unmix_manual_data(page) -> dict:
    """The stored manual matrix (``columns`` + ``rows``)."""
    manual = page._unmix_params.get("manual_matrix")
    if not isinstance(manual, dict):
        manual = {}
        page._unmix_params["manual_matrix"] = manual
    manual.setdefault("columns", [])
    manual.setdefault("rows", [])
    return manual


def _unmix_signature_source(page) -> str:
    source = str(page._unmix_params.get("signature_source") or "spectra")
    return "manual" if source == "manual" else "spectra"


def _unmix_active_rows(page) -> list:
    """Signature rows of the active source (spectra or pasted manual)."""
    if _unmix_signature_source(page) == "manual":
        return list(_unmix_manual_data(page).get("rows") or [])
    return list(getattr(page, "_unmix_matrix", []) or [])


def _unmix_manual_cell_text(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    return f"{number:.10g}"


def _unmix_rebuild_signature_tables(ctx, page) -> None:
    """Rebuild both editors; only the active one is shown."""
    _unmix_rebuild_signature_table(ctx, page)
    _unmix_rebuild_manual_table(ctx, page)


def _unmix_rebuild_manual_table(ctx, page) -> None:
    """(Re)build the editable paste table from ``manual_matrix``."""
    table = getattr(page, "_unmix_manual_table", None)
    if table is None:
        return
    manual = _unmix_manual_data(page)
    columns = [str(c) for c in manual.get("columns") or []]
    rows = list(manual.get("rows") or [])
    editable = not page._unmix_locked
    flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    page._unmix_manual_editing = True
    page._unmix_manual_bad_cells = set()
    try:
        table.clear()
        table.setColumnCount(len(columns) + 1)
        table.setHorizontalHeaderLabels(["Signature"] + columns)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            texts = [str(row.get("name") or "")]
            texts += [
                _unmix_manual_cell_text(value)
                for value in (row.get("values") or [])
            ]
            for c, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setFlags(flags)
                item.setTextAlignment(
                    (Qt.AlignmentFlag.AlignLeft if c == 0
                     else Qt.AlignmentFlag.AlignRight)
                    | Qt.AlignmentFlag.AlignVCenter
                )
                table.setItem(r, c, item)
        header = table.horizontalHeader()
        metrics = QFontMetrics(table.font())
        space = max(1, metrics.horizontalAdvance(" "))
        header.setStretchLastSection(False)
        for c in range(len(columns) + 1):
            # Interactive: the user can drag any divider to widen a column.
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, space * 20)
        for c in range(1, len(columns) + 1):
            table.setColumnWidth(c, space * 5)
        table.setVerticalHeaderLabels([str(i + 1) for i in range(len(rows))])
        table.resizeRowsToContents()
    finally:
        page._unmix_manual_editing = False
    _unmix_update_manual_summary(ctx, page)


def _unmix_manual_channel_mismatch(ctx, page) -> tuple[list, list]:
    """(missing, extra) channels between the pasted matrix and the selection."""
    columns = [str(c) for c in _unmix_manual_data(page).get("columns") or []]
    current = _unmix_selected_parameters(ctx, page)
    column_set = set(columns)
    current_set = set(current)
    missing = [c for c in current if c not in column_set]
    extra = [c for c in columns if c not in current_set]
    return missing, extra


def _unmix_update_manual_summary(ctx, page) -> None:
    info = getattr(page, "_unmix_manual_info", None)
    if info is None:
        return
    manual = _unmix_manual_data(page)
    columns = list(manual.get("columns") or [])
    rows = list(manual.get("rows") or [])
    if not rows:
        info.setText("No matrix pasted")
        info.setStyleSheet("color: #6b7785; font-size: 8pt;")
        return
    problems = []
    missing, extra = _unmix_manual_channel_mismatch(ctx, page)
    if missing:
        problems.append("missing: " + ", ".join(missing))
    if extra:
        problems.append("not selected: " + ", ".join(extra))
    names = [str(r.get("name") or "") for r in rows]
    if any(not name.strip() for name in names):
        problems.append("empty signature name")
    if len(set(names)) != len(names):
        problems.append("duplicate signature name")
    if getattr(page, "_unmix_manual_bad_cells", None):
        problems.append("invalid number")
    if problems:
        info.setText(
            f"{len(rows)} × {len(columns)} · " + "; ".join(problems)
        )
        info.setStyleSheet("color: #b58900; font-size: 8pt;")
    else:
        info.setText(f"{len(rows)} signatures × {len(columns)} channels")
        info.setStyleSheet("color: #6b7785; font-size: 8pt;")


def _unmix_manual_problem(ctx, page) -> str:
    """A user-facing reason the manual matrix cannot be used, or ""."""
    if _unmix_signature_source(page) != "manual":
        return ""
    manual = _unmix_manual_data(page)
    columns = list(manual.get("columns") or [])
    rows = list(manual.get("rows") or [])
    if not rows or not columns:
        return "Paste a matrix copied with Copy matrix first."
    missing, extra = _unmix_manual_channel_mismatch(ctx, page)
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if extra:
            parts.append("not selected: " + ", ".join(extra))
        return (
            "The pasted matrix does not match the selected channels ("
            + "; ".join(parts) + ")."
        )
    names = [str(row.get("name") or "").strip() for row in rows]
    if any(not name for name in names):
        return "Every pasted signature needs a name."
    if len(set(names)) != len(names):
        return "Signature names must be unique."
    if getattr(page, "_unmix_manual_bad_cells", None):
        return "Fix the highlighted cells before unmixing."
    for number, row in enumerate(rows, start=2):
        values = list(row.get("values") or [])
        if len(values) != len(columns):
            return f"Row {number} does not match the pasted columns."
        if not all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in values
        ):
            return f"Row {number} has a missing or invalid value."
    return ""


def _unmix_on_signature_source(ctx, page) -> None:
    """Switch between spectra-built signatures and a pasted matrix."""
    combo = getattr(page, "_unmix_signature_mode_combo", None)
    if combo is None:
        return
    if page._unmix_locked:
        combo.blockSignals(True)
        combo.setCurrentIndex(max(
            0, combo.findData(
                page._unmix_params.get("signature_source") or "spectra"
            )
        ))
        combo.blockSignals(False)
        return
    source = combo.currentData() or "spectra"
    if source not in ("spectra", "manual"):
        source = "spectra"
    page._unmix_params["signature_source"] = source
    stack = getattr(page, "_unmix_signature_stack", None)
    if stack is not None:
        stack.setCurrentIndex(1 if source == "manual" else 0)
    if source == "manual":
        _unmix_rebuild_manual_table(ctx, page)
    else:
        _unmix_rebuild_signature_table(ctx, page)
    _unmix_rebuild_matrix_settings(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_parse_matrix_text(ctx, page, text) -> tuple[dict | None, str]:
    """Parse a TSV matrix copied with Copy matrix.

    Returns ``(manual_matrix, "")`` or ``(None, reason)``.  The pasted
    channel order is kept; only the channel *set* must match the spectrum
    selection.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None, "There is nothing on the clipboard."
    table = [line.split("\t") for line in lines]
    header = [cell.strip() for cell in table[0]]
    if not header or header[0].strip().lower() != "signature":
        return None, 'The first header cell must be "Signature".'
    columns = header[1:]
    if not columns:
        return None, "The header has no channels."
    repeated = sorted({c for c in columns if columns.count(c) > 1})
    if repeated:
        return None, (
            "The header repeats channel(s): " + ", ".join(repeated)
        )
    expected = _unmix_selected_parameters(ctx, page)
    if not expected:
        return None, (
            "Select the spectrum channels in Spectrum Extraction first."
        )
    expected_set = set(expected)
    column_set = set(columns)
    missing = [c for c in expected if c not in column_set]
    extra = [c for c in columns if c not in expected_set]
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if extra:
            parts.append("not selected: " + ", ".join(extra))
        return None, (
            "The matrix columns do not match the selected channels ("
            + "; ".join(parts) + ")."
        )
    if len(table) < 2:
        return None, "The matrix has no signature rows."
    names = set()
    rows = []
    for number, cells in enumerate(table[1:], start=2):
        if len(cells) != len(header):
            return None, (
                f"Row {number} has {len(cells)} column(s); "
                f"expected {len(header)}."
            )
        name = cells[0].strip()
        if not name:
            return None, f"Row {number} has no signature name."
        if name in names:
            return None, f'The signature name "{name}" appears twice.'
        names.add(name)
        values = []
        for index, cell in enumerate(cells[1:], start=1):
            text_value = cell.strip()
            if text_value == "":
                return None, (
                    f'Row {number}, channel "{header[index]}" is empty.'
                )
            try:
                value = float(text_value)
            except ValueError:
                return None, (
                    f'Row {number}, channel "{header[index]}" is not a '
                    f'number: "{text_value}".'
                )
            if not math.isfinite(value):
                return None, (
                    f'Row {number}, channel "{header[index]}" is not a '
                    "finite number."
                )
            values.append(value)
        rows.append({
            "id": f"manual-{uuid4().hex[:8]}",
            "name": name,
            "values": values,
        })
    return {"columns": list(columns), "rows": rows}, ""


def _unmix_paste_manual_matrix(ctx, page) -> None:
    """Replace the manual matrix with the clipboard's TSV matrix."""
    if page._unmix_locked:
        return
    data, error = _unmix_parse_matrix_text(
        ctx, page, QApplication.clipboard().text()
    )
    if error:
        QMessageBox.warning(ctx.host, "Paste matrix", error)
        return
    page._unmix_params["manual_matrix"] = data
    page._unmix_manual_bad_cells = set()
    _unmix_rebuild_manual_table(ctx, page)
    _unmix_rebuild_matrix_settings(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)
    ctx.statusBar().showMessage(
        f"Pasted {len(data['rows'])} signature(s) × "
        f"{len(data['columns'])} channel(s)"
    )


def _unmix_on_manual_cell(ctx, page, item) -> None:
    """Write one edited cell back into the manual matrix."""
    if getattr(page, "_unmix_manual_editing", False):
        return
    if page._unmix_locked or item is None:
        return
    manual = _unmix_manual_data(page)
    rows = list(manual.get("rows") or [])
    row_index = item.row()
    column = item.column()
    if not (0 <= row_index < len(rows)):
        return
    row = rows[row_index]
    text = str(item.text() or "").strip()
    bad = getattr(page, "_unmix_manual_bad_cells", None)
    if bad is None:
        bad = set()
        page._unmix_manual_bad_cells = bad
    if column == 0:
        row["name"] = text
        bad.discard((row_index, column))
        item.setData(Qt.ItemDataRole.BackgroundRole, None)
        # The curve settings list shows signature names.
        _unmix_rebuild_matrix_settings(ctx, page)
    else:
        value_index = column - 1
        try:
            value = float(text)
            if not math.isfinite(value):
                raise ValueError
        except ValueError:
            bad.add((row_index, column))
            item.setBackground(QBrush(QColor("#fde2e2")))
            item.setToolTip("Enter a number")
            _unmix_update_manual_summary(ctx, page)
            return
        values = row.setdefault("values", [])
        while len(values) <= value_index:
            values.append(float("nan"))
        values[value_index] = value
        bad.discard((row_index, column))
        item.setData(Qt.ItemDataRole.BackgroundRole, None)
        item.setToolTip("")
    _unmix_update_manual_summary(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_manual_reordered(ctx, page) -> None:
    """Apply the visual row order of the pasted matrix to the model."""
    if page._unmix_locked:
        return
    table = getattr(page, "_unmix_manual_table", None)
    if table is None:
        return
    manual = _unmix_manual_data(page)
    rows = list(manual.get("rows") or [])
    count = len(rows)
    order = sorted(
        range(count), key=lambda i: table.verticalHeader().visualIndex(i)
    )
    if order == list(range(count)):
        return
    manual["rows"][:] = [rows[i] for i in order]
    _unmix_rebuild_manual_table(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_unique_signature_name(ctx, page, base: str) -> str:
    """A name that avoids other signatures and every data parameter name."""
    taken = {r.get("name") for r in page._unmix_matrix}
    taken |= set(_unmix_spectrum_parameters(ctx, page))
    if base not in taken:
        return base
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def _unmix_on_signature_field(ctx, page, row, key: str, value) -> None:
    if row.get(key) == value:
        return
    row[key] = value
    _unmix_refresh_matrix_views(ctx, page)
    # The row changed, not the spectra — keep the spectrum-vector cache.
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_set_signature_name(ctx, page, row, edit) -> None:
    name = edit.text().strip()
    current = row.get("name", "")
    if name == current:
        return
    if not name:
        QMessageBox.information(
            ctx.host, "Signature name", "The name cannot be empty."
        )
        edit.setText(current)
        return
    if any(
        other is not row and other.get("name") == name
        for other in page._unmix_matrix
    ):
        QMessageBox.information(
            ctx.host,
            "Signature name",
            "Another signature already uses this name.",
        )
        edit.setText(current)
        return
    if name in set(_unmix_spectrum_parameters(ctx, page)):
        QMessageBox.information(
            ctx.host,
            "Signature name",
            "The name cannot match a parameter name of the sample data.",
        )
        edit.setText(current)
        return
    row["name"] = name
    try:
        index = page._unmix_matrix.index(row)
        page._unmix_signature_table.setVerticalHeaderItem(
            index, QTableWidgetItem(name)
        )
    except ValueError:
        pass
    _unmix_refresh_matrix_views(ctx, page)
    _unmix_mark_dirty(ctx, page, data=False)


def _unmix_select_samples(ctx, page) -> None:
    """Pick the samples to unmix with the shared multi-select dialog."""
    records = _data_files._available_files(ctx, page._unmix_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, p, page._unmix_task_id)
        for p in page._unmix_params.get("samples") or []
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Samples to be unmixed", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._unmix_params["samples"] = [
        by_name[name] for name in chosen if name in by_name
    ]
    _unmix_rebuild_sample_list(ctx, page)
    _unmix_mark_dirty(ctx, page)


def _unmix_rebuild_sample_list(ctx, page) -> None:
    """Show the selected samples (selection happens in the picker dialog)."""
    samples_list = page._unmix_samples_list
    selected = list(page._unmix_params.get("samples") or [])
    names = {
        rec.get("parquet"): rec.get("name", "")
        for rec in _data_files._available_files(ctx, page._unmix_task_id)
    }
    samples_list.blockSignals(True)
    samples_list.clear()
    for path in selected:
        if path not in names:
            continue
        item = QListWidgetItem(names[path])
        item.setData(Qt.ItemDataRole.UserRole, path)
        samples_list.addItem(item)
    samples_list.blockSignals(False)


def _unmix_on_method_changed(ctx, page, combo) -> None:
    page._unmix_params["method"] = combo.currentData()
    _unmix_mark_dirty(ctx, page)


def _unmix_matrix_table_values(ctx, page):
    """Return (columns, [(row, values)]) for the signature matrix."""
    import numpy as np

    if _unmix_signature_source(page) == "manual":
        manual = _unmix_manual_data(page)
        columns = [str(c) for c in manual.get("columns") or []]
        rows = []
        for row in manual.get("rows") or []:
            values = list(row.get("values") or [])
            if len(values) < len(columns):
                values += [float("nan")] * (len(columns) - len(values))
            rows.append((row, values[:len(columns)]))
        return columns, rows
    columns = _unmix_selected_parameters(ctx, page)
    by_id = {s["id"]: s for s in page._unmix_spectra}
    result = []
    for row in page._unmix_matrix:
        positive = by_id.get(row.get("positive"))
        negative = by_id.get(row.get("negative"))
        if positive is None:
            result.append((row, [float("nan")] * len(columns)))
            continue
        pos_values, _err = _unmix_spectrum_vector(ctx, page, positive)
        if pos_values is None:
            result.append((row, [float("nan")] * len(columns)))
            continue
        neg_values = {}
        if negative is not None:
            neg_values = _unmix_spectrum_vector(ctx, page, negative)[0] or {}
        values = []
        for column in columns:
            p = pos_values.get(column, float("nan"))
            n = neg_values.get(column, 0.0) if negative is not None else 0.0
            values.append(p - n if np.isfinite(p) else float("nan"))
        if row.get("normalize", True):
            finite = [v for v in values if np.isfinite(v)]
            peak = max(finite) if finite else None
            if peak is not None and peak > 0:
                values = [
                    (v / peak) if np.isfinite(v) else v for v in values
                ]
        result.append((row, values))
    return columns, result


def _unmix_matrix_paint_spec(page, row_names, columns, cfg) -> dict:
    """Font and geometry shared by the on-screen image and the exports."""
    label = getattr(page, "_unmix_matrix_label", None)
    base_font = label.font() if label is not None else QFont()
    cell_w = max(12, int(cfg["cell_width"]))
    cell_h = max(12, int(cfg["cell_height"]))
    font = QFont(base_font)
    font.setPixelSize(max(9, min(13, cell_h // 2 + 2)))
    metrics = QFontMetrics(font)
    left = max((metrics.horizontalAdvance(n) for n in row_names), default=0)
    bottom = max((metrics.horizontalAdvance(c) for c in columns), default=0)
    left += 12
    bottom += 12
    top = 8
    right = 8
    return {
        "cell_w": cell_w, "cell_h": cell_h,
        "font": font, "metrics": metrics,
        "left": left, "top": top, "right": right,
        "width": left + cell_w * len(columns) + right,
        "height": top + cell_h * len(row_names) + bottom,
    }


def _unmix_paint_matrix(painter, row_names, columns, values_rows, cfg,
                        lo, hi, decimals, spec) -> None:
    """Paint a labelled cell grid: row names left, channel names rotated below."""
    import numpy as np

    cell_w = spec["cell_w"]
    cell_h = spec["cell_h"]
    left = spec["left"]
    top = spec["top"]
    font = spec["font"]
    metrics = spec["metrics"]
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)

    lut = _figure_colormap(cfg["colormap"]).getLookupTable(0.0, 1.0, 256)
    show_numbers = bool(cfg["show_numbers"])
    auto_color = bool(cfg["auto_text_color"])
    fixed_text = QColor(cfg["text_color"])
    grid_pen = QPen(QColor(255, 255, 255, 140))
    grid_pen.setWidth(1)

    for i, values in enumerate(values_rows):
        y = top + i * cell_h
        for j, value in enumerate(values):
            x = left + j * cell_w
            if np.isfinite(value):
                frac = 0.5 if hi <= lo else (value - lo) / (hi - lo)
                frac = min(1.0, max(0.0, frac))
                r, g, b = (int(c) for c in lut[int(frac * 255)])
                painter.fillRect(x, y, cell_w, cell_h, QColor(r, g, b))
                luma = 0.299 * r + 0.587 * g + 0.114 * b
                text_color = (
                    QColor("#ffffff") if luma < 140 else QColor("#111111")
                ) if auto_color else fixed_text
            else:
                painter.fillRect(x, y, cell_w, cell_h, QColor("#f2f4f7"))
                text_color = QColor("#9aa4b2")
            painter.setPen(grid_pen)
            painter.drawRect(x, y, cell_w, cell_h)
            if show_numbers:
                painter.setPen(text_color)
                painter.drawText(
                    QRect(x, y, cell_w, cell_h),
                    Qt.AlignmentFlag.AlignCenter,
                    f"{value:.{decimals}f}" if np.isfinite(value) else "—",
                )
        painter.setPen(QColor("#333333"))
        painter.drawText(
            QRect(0, y, left - 10, cell_h),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            row_names[i],
        )

    # Column names rotated -90°, ending just below the grid.
    grid_bottom = top + cell_h * len(values_rows)
    shift = (metrics.ascent() - metrics.descent()) / 2.0
    for j, name in enumerate(columns):
        text_width = metrics.horizontalAdvance(name)
        cx = left + j * cell_w + cell_w / 2.0
        painter.save()
        painter.translate(cx + shift, grid_bottom + 6 + text_width)
        painter.rotate(-90)
        painter.setPen(QColor("#333333"))
        painter.drawText(0, 0, name)
        painter.restore()


def _unmix_paint_matrix_image(ctx, page, row_names, columns, values_rows, cfg, lo, hi, decimals):
    """Render the matrix grid into a QImage for the on-screen label."""
    spec = _unmix_matrix_paint_spec(page, row_names, columns, cfg)
    image = QImage(spec["width"], spec["height"], QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    try:
        _unmix_paint_matrix(
            painter, row_names, columns, values_rows, cfg, lo, hi, decimals,
            spec,
        )
    finally:
        painter.end()
    return image


def _unmix_render_matrix(ctx, page) -> None:
    """Paint the matrix as an image with rotated column names."""
    import numpy as np

    label = page._unmix_matrix_label
    columns, rows = _unmix_matrix_table_values(ctx, page)
    if not rows or not columns:
        page._unmix_matrix_image = None
        label.clear()
        label.setText(
            _unmix_empty_matrix_text(
                page,
                "Add a signature to build the matrix.",
                "Paste a matrix to build the matrix.",
            )
            if not rows
            else "No parameters selected."
        )
        return

    cfg = dict(_unmix_matrix_cfg(ctx, page, "matrix"))
    zoom = float(getattr(page, "_unmix_matrix_zoom", 1.0) or 1.0)
    cfg["cell_width"] = max(8, int(round(cfg["cell_width"] * zoom)))
    cfg["cell_height"] = max(8, int(round(cfg["cell_height"] * zoom)))
    finite = [v for _r, values in rows for v in values if np.isfinite(v)]
    lo = min(finite) if finite else 0.0
    hi = max(finite) if finite else 0.0
    image = _unmix_paint_matrix_image(ctx,
        page,
        [row.get("name", "") for row, _values in rows],
        list(columns),
        [values for _row, values in rows],
        cfg,
        lo,
        hi,
        int(page._unmix_spectrum_cfg.get("decimals", 2)),
    )

    page._unmix_matrix_image = image
    label.setText("")
    label.setPixmap(QPixmap.fromImage(image))
    label.resize(image.size())


def _unmix_render_curve(ctx, page) -> None:
    """One normalized curve per signature, with a wrapped legend above."""
    import numpy as np

    plot = getattr(page, "_unmix_curve_plot", None)
    if plot is None:
        return
    item = plot.getPlotItem()
    plot.clear()
    legend_lay = getattr(page, "_unmix_curve_legend_lay", None)
    if legend_lay is not None:
        _widgets_panels._clear_layout(ctx, legend_lay)

    columns, rows = _unmix_matrix_table_values(ctx, page)
    if not rows or not columns:
        item.setTitle(
            _unmix_empty_matrix_text(
                page,
                "Add a signature to build the curves.",
                "Paste a matrix to build the curves.",
            ),
            color="#888888", size="10pt",
        )
        return
    item.setTitle(None)

    cfg = _unmix_matrix_cfg(ctx, page, "curve")
    line_width = max(1, int(cfg.get("line_width", 2)))
    colors = _unmix_curve_colors(ctx, page, [row for row, _ in rows])
    visible = cfg.get("visible") or {}

    ticks = [(float(i), str(name)) for i, name in enumerate(columns)]
    bottom = item.getAxis("bottom")
    bottom.setTicks([ticks])
    # Rotated labels need as much height as the widest channel name, or
    # they are cut off and overlap the axis title.
    font = bottom.style.get("tickFont") or bottom.font()
    metrics = QFontMetrics(font)
    widest = max(
        (metrics.horizontalAdvance(str(name)) for name in columns), default=0
    )
    bottom.setHeight(min(int(widest) + 26, 260))
    xs = np.arange(len(columns), dtype="float64")
    entries = []
    for row, values in rows:
        y = np.asarray(values, dtype="float64")
        if not np.isfinite(y).any():
            continue
        sid = str(row.get("id") or row.get("name") or "")
        if not visible.get(sid, True):
            continue
        color = colors.get(sid, UNMIX_CURVE_COLORS[0])
        plot.plot(
            xs, y,
            pen=pg.mkPen(color, width=line_width),
            connect="finite",
        )
        entries.append((str(row.get("name") or sid), color))

    if legend_lay is not None:
        per_row = 6
        for index, (name, color) in enumerate(entries):
            chip = QLabel(
                f"<span style='color:{color}; font-size:13pt;'>■</span>"
                f" {name}"
            )
            chip.setTextFormat(Qt.TextFormat.RichText)
            legend_lay.addWidget(chip, index // per_row, index % per_row)


def _unmix_render_similarity(ctx, page) -> None:
    """Cosine similarity between the signatures, fixed scale −1 … 1."""
    import numpy as np

    label = getattr(page, "_unmix_similarity_label", None)
    if label is None:
        return
    names, matrix = _unmix_similarity_values(ctx, page)
    if len(names) < 2:
        page._unmix_similarity_image = None
        label.clear()
        label.setText(
            _unmix_empty_matrix_text(
                page,
                "Add at least two signatures to compare them.",
                "Paste at least two signatures to compare them.",
            )
            if names else _unmix_empty_matrix_text(
                page,
                "Add a signature to build the matrix.",
                "Paste a matrix to build the matrix.",
            )
        )
        return
    cfg = dict(_unmix_matrix_cfg(ctx, page, "similarity"))
    zoom = float(getattr(page, "_unmix_matrix_zoom", 1.0) or 1.0)
    cfg["cell_width"] = max(8, int(round(cfg["cell_width"] * zoom)))
    cfg["cell_height"] = max(8, int(round(cfg["cell_height"] * zoom)))
    image = _unmix_paint_matrix_image(ctx,
        page, list(names), list(names),
        [list(row) for row in matrix],
        cfg, -1.0, 1.0, 2,
    )
    page._unmix_similarity_image = image
    label.setText("")
    label.setPixmap(QPixmap.fromImage(image))
    label.resize(image.size())


def _unmix_heatmap_export_payload(ctx, page, key) -> dict | None:
    """Everything needed to repaint one matrix heatmap for export."""
    import numpy as np

    zoom = float(getattr(page, "_unmix_matrix_zoom", 1.0) or 1.0)

    def prepared(cfg):
        cfg = dict(cfg)
        cfg["cell_width"] = max(8, int(round(cfg["cell_width"] * zoom)))
        cfg["cell_height"] = max(8, int(round(cfg["cell_height"] * zoom)))
        return cfg

    if key == "similarity":
        names, matrix = _unmix_similarity_values(ctx, page)
        if len(names) < 2:
            return None
        return {
            "row_names": list(names),
            "columns": list(names),
            "values_rows": [list(row) for row in matrix],
            "cfg": prepared(_unmix_matrix_cfg(ctx, page, "similarity")),
            "lo": -1.0, "hi": 1.0, "decimals": 2,
        }
    columns, rows = _unmix_matrix_table_values(ctx, page)
    if not rows or not columns:
        return None
    finite = [v for _row, values in rows for v in values if np.isfinite(v)]
    return {
        "row_names": [row.get("name", "") for row, _values in rows],
        "columns": list(columns),
        "values_rows": [values for _row, values in rows],
        "cfg": prepared(_unmix_matrix_cfg(ctx, page, "matrix")),
        "lo": min(finite) if finite else 0.0,
        "hi": max(finite) if finite else 0.0,
        "decimals": int(page._unmix_spectrum_cfg.get("decimals", 2)),
    }


def _unmix_heatmap_export_name(key: str) -> str:
    return (
        "Unmix similarity matrix" if key == "similarity"
        else "Unmix signature matrix"
    )


def _unmix_save_heatmap_as(ctx, page, key: str) -> None:
    """Save one matrix heatmap as currently shown."""
    payload = _unmix_heatmap_export_payload(ctx, page, key)
    if payload is None:
        return
    spec = _unmix_matrix_paint_spec(
        page, payload["row_names"], payload["columns"], payload["cfg"]
    )

    def paint(painter):
        _unmix_paint_matrix(
            painter, payload["row_names"], payload["columns"],
            payload["values_rows"], payload["cfg"],
            payload["lo"], payload["hi"], payload["decimals"], spec,
        )

    try:
        path = _widgets_canvas_export._painter_export_save(
            paint, spec["width"], spec["height"], ctx.host,
            _unmix_heatmap_export_name(key), "Save Heatmap As",
        )
    except Exception as exc:  # noqa: BLE001 - report, never break the page
        runlog.info("unmix.export_failed", error=str(exc))
        QMessageBox.critical(
            ctx.host, "Save Heatmap As",
            f"Could not save the heatmap:\n{exc}",
        )
        return
    if path:
        runlog.info("unmix.export", path=str(path))
        ctx.statusBar().showMessage(f"Heatmap saved to {path}", 4000)


def _unmix_heatmap_menu(ctx, page, key: str, pos) -> None:
    """Right-click menu of the matrix / similarity heatmap: Save as… only."""
    label = (
        page._unmix_similarity_label if key == "similarity"
        else page._unmix_matrix_label
    )
    image = (
        page._unmix_similarity_image if key == "similarity"
        else page._unmix_matrix_image
    )
    if label is None or image is None:
        return
    menu = QMenu(ctx.host)
    save_as = menu.addAction("Save as…")
    save_as.setToolTip(
        "Save the heatmap as currently shown: a PDF with editable text and "
        "vector cells, or a PNG/JPEG image"
    )
    chosen = menu.exec(label.mapToGlobal(pos))
    if chosen is save_as:
        _unmix_save_heatmap_as(ctx, page, key)


def _unmix_plot_export_setup(ctx, page, plot, suggested_name: str) -> None:
    """Give a pyqtgraph plot a right-click Save as… menu."""
    item = plot.getPlotItem()
    item.setMenuEnabled(False)
    try:
        item.getViewBox().setMenuEnabled(False)
    except Exception:  # noqa: BLE001 - older pyqtgraph lacks the setter
        pass
    plot.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    plot.customContextMenuRequested.connect(
        lambda pos: _unmix_plot_menu(ctx, page, plot, pos, suggested_name)
    )


def _unmix_plot_menu(ctx, page, plot, pos, suggested_name: str) -> None:
    """Right-click menu of the spectrum / curve plot: Save as… only."""
    menu = QMenu(ctx.host)
    save_as = menu.addAction("Save as…")
    save_as.setToolTip(
        "Save the plot as currently shown: an editable PDF with vector axes "
        "and text, or a PNG/JPEG image"
    )
    chosen = menu.exec(plot.mapToGlobal(pos))
    if chosen is not save_as:
        return
    try:
        path = _widgets_plot_export._plot_export_save(
            plot, ctx.host, suggested_name
        )
    except Exception as exc:  # noqa: BLE001 - report, never break the page
        runlog.info("unmix.export_failed", error=str(exc))
        QMessageBox.critical(
            ctx.host, "Save Plot As",
            f"Could not save the plot:\n{exc}",
        )
        return
    if path:
        runlog.info("unmix.export", path=str(path))
        ctx.statusBar().showMessage(f"Plot saved to {path}", 4000)


def _unmix_zoom_matrix(ctx, page, factor, pos=None) -> None:
    """Wheel zoom for the matrix / similarity heatmaps (re-rendered)."""
    old = float(getattr(page, "_unmix_matrix_zoom", 1.0) or 1.0)
    new = max(0.35, min(4.0, old * float(factor)))
    if abs(new - old) < 1e-6:
        return
    page._unmix_matrix_zoom = new
    key = getattr(page, "_unmix_matrix_tab", "matrix")
    scroll = getattr(
        page,
        "_unmix_similarity_scroll" if key == "similarity"
        else "_unmix_matrix_scroll",
        None,
    )
    h_frac = v_frac = None
    if scroll is not None:
        hb = scroll.horizontalScrollBar()
        vb = scroll.verticalScrollBar()
        h_frac = hb.value() / max(1, hb.maximum())
        v_frac = vb.value() / max(1, vb.maximum())
    _unmix_render_matrix(ctx, page)
    _unmix_render_similarity(ctx, page)
    if scroll is not None:
        def restore() -> None:
            hb.setValue(int(round(h_frac * hb.maximum())))
            vb.setValue(int(round(v_frac * vb.maximum())))
        QTimer.singleShot(0, restore)


def _unmix_refresh_matrix_views(ctx, page) -> None:
    """Redraw the active matrix sub-page from the current signatures.

    The other tabs are redrawn when the user switches to them (see
    :func:`_unmix_on_matrix_tab`), so adding or editing a signature does
    not pay for three renders.
    """
    key = getattr(page, "_unmix_matrix_tab", "matrix")
    if key == "curve":
        _unmix_render_curve(ctx, page)
    elif key == "similarity":
        _unmix_render_similarity(ctx, page)
    else:
        _unmix_render_matrix(ctx, page)


def _unmix_refresh_outputs(ctx, page) -> None:
    """Recompute everything derived from the spectra."""
    _unmix_render_spectrum_plot(ctx, page)
    _unmix_refresh_matrix_views(ctx, page)


def _unmix_spectrum_vector(ctx, page, spectrum):
    """Return ({parameter: statistic}, error) for one spectrum, cached."""
    import numpy as np

    cache = getattr(page, "_unmix_spectrum_cache", None)
    if cache is None:
        cache = {}
        page._unmix_spectrum_cache = cache
    cached = cache.get(spectrum["id"])
    if cached is not None:
        return cached, None
    names, arrays, error = _unmix_spectrum_arrays(ctx, page, spectrum)
    if error:
        return None, error
    stat = page._unmix_spectrum_cfg.get("stat", "median")
    values = {}
    for name, arr in zip(names, arrays):
        if arr.size == 0:
            values[name] = float("nan")
        elif stat == "mean":
            values[name] = float(arr.mean())
        else:
            values[name] = float(np.median(arr))
    cache[spectrum["id"]] = values
    return values, None


def _unmix_mark_dirty(ctx, page, data: bool=True) -> None:
    """Mark the module modified.

    `data=False` is for changes that keep the spectrum vectors valid:
    matrix/curve colours, cell sizes, line width, visibility, and editing
    signature rows (which only combine spectra differently). Those paths
    must not force every channel of every sample file to be re-read.
    """
    page._unmix_dirty = True
    if data:
        page._unmix_spectrum_cache = {}
        # Invalidate a background spectrum refresh that is still running.
        page._unmix_spectrum_gen = getattr(page, "_unmix_spectrum_gen", 0) + 1
    _unmix_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _unmix_save_now(ctx, page) -> None:
    page._unmix_params["trees"] = [
        {
            "id": tree["id"],
            "params": tree["store"].get("params", {}),
            "spectrum_id": tree["spectrum_id"],
        }
        for tree in page._unmix_trees
    ]
    active = page._unmix_active_tree
    page._unmix_params["active_tree"] = active["id"] if active else None
    active_spectrum = page._unmix_active_spectrum
    page._unmix_params["active_spectrum"] = (
        active_spectrum["id"] if active_spectrum else None
    )
    page._unmix_task_def["params"] = page._unmix_params
    if ctx.project is not None:
        ctx.project.save()
    page._unmix_snapshot = copy.deepcopy(page._unmix_params)
    page._unmix_dirty = False
    _unmix_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Unmix saved")
    ctx._module_saved(page)


def _unmix_update_save_btn(ctx, page) -> None:
    page._unmix_save_btn.setEnabled(page._unmix_dirty and not page._unmix_locked)


def _unmix_close(ctx, page) -> None:
    """Close the page, prompting first if there are unsaved changes."""
    if page._unmix_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Unmix",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._unmix_task_def["params"] = page._unmix_snapshot
    _unmix_cleanup(ctx, page)
    ctx._close_module_page(page)


UNMIX_CURVE_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
    "#aec7e8", "#ff9896", "#98df8a", "#ffbb78", "#c5b0d5",
    "#c49c94", "#f7b6d2", "#9edae5", "#dbdb8d", "#c7c7c7",
]

