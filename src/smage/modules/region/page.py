"""Region module: detect, edit and convert image regions to channels."""
from __future__ import annotations

import copy
import math
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QKeySequence,
    QPainterPath,
    QPen,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsPathItem,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.gates import (
    _gate_geometry,
    _gate_source_ids,
    _point_in_polygon,
    _resolve_gates_for_file,
)
from smage.core.region_detection import (
    _mask_region,
    region_parts_mask,
    sam_mask_polygons,
    signal_expansion,
)
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.data import regions as _data_regions
from smage.widgets import image_level as _widgets_image_level
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from smage.widgets.gate_editor.canvas import _GatePolygonROI, _GateViewBox
from smage.widgets.plot import renderer as _widgets_plot_renderer
from smage.widgets.plot_axes import _make_fit_plot_item, _make_sci_axis
from smage.widgets.resources import _lock_pixmap


_REGION_COLORS = [
    (0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167),
    (230, 159, 0), (86, 180, 233), (240, 228, 66), (0, 0, 0),
]
_GATE_PEN = "#2b6cb0"
_GATE_SELECTED_PEN = "#e53935"
_REGION_LOG_LIMIT = 2000
# Above this many vertices, Edit boundary asks before building the handles.
_REGION_ANCHOR_WARN_LIMIT = 100
_REGION_DISPLAY_KEYS = (
    "show_axes",
    "show_axis_names",
    "show_indices",
    "show_sample_name",
    "axis_name_font_size",
    "index_font_size",
    "sample_name_font_size",
)


def _region_image_records(ctx, task_id):
    return [
        rec for rec in _data_files._available_files(ctx, task_id)
        if rec.get("parquet")
        and _data_outputs._seg_is_image_file(ctx, rec)
    ]


def _region_file_name(ctx, path, task_id) -> str:
    try:
        return _data_naming._comp_file_name(ctx, path, task_id)
    except Exception:  # noqa: BLE001
        return Path(str(path)).name


def _region_default_style(index: int) -> dict:
    color = _REGION_COLORS[index % len(_REGION_COLORS)]
    edge = tuple(max(0, int(c * 0.75)) for c in color)
    return {
        "fill": list(color),
        "fill_opacity": 0.25,
        "edge": list(edge),
        "edge_width": 2.0,
    }


def _region_style_for(region) -> dict:
    style = dict(region.get("style") or {})
    fill = style.get("fill") or [0, 114, 178]
    edge = style.get("edge") or fill
    return {
        "fill": list(fill),
        "edge": list(edge),
        "fill_opacity": float(style.get("fill_opacity", 0.25)),
        "edge_width": float(style.get("edge_width", 2.0)),
    }


def _region_next_name(store) -> str:
    highest = 0
    for region in store.get("regions") or []:
        name = str(region.get("name") or "")
        if name.startswith("Region "):
            try:
                highest = max(highest, int(name.split(" ", 1)[1]))
            except ValueError:
                pass
    return f"Region {highest + 1}"


def _region_unique_name(store, name: str, exclude_id=None) -> str:
    used = {
        str(region.get("name") or "")
        for region in store.get("regions") or []
        if str(region.get("id")) != str(exclude_id or "")
    }
    if name not in used:
        return name
    index = 2
    while f"{name} {index}" in used:
        index += 1
    return f"{name} {index}"


def _region_mark_dirty(ctx, page) -> None:
    page._region_dirty = True
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)
    page._region_save_btn.setEnabled(not page._region_locked)


def _region_update_save_btn(page) -> None:
    page._region_save_btn.setEnabled(
        page._region_dirty and not page._region_locked
    )


# ---------------------------------------------------------------------------
# Page construction
# ---------------------------------------------------------------------------

def _build_region_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    bar = QFrame()
    bar.setObjectName("moduleBar")
    tl = QHBoxLayout(bar)
    tl.setContentsMargins(12, 6, 12, 6)
    tl.setSpacing(4)
    tl.addWidget(QLabel(
        f"<b>{_data_files._module_display_title(ctx, task_def.get('id'), name)}</b>"
    ))
    if locked:
        icon = QLabel()
        icon.setPixmap(_lock_pixmap())
        icon.setFixedSize(14, 14)
        icon.setToolTip("Locked — a downstream generate module saved data")
        tl.addWidget(icon)
        draft = QLabel("Draft (locked)")
        draft.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft)
    tl.addStretch()
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setEnabled(not locked)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(lambda _=False: _region_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    notice = QLabel(
        "Regions can be converted to 0/1 channels for downstream modules. "
        "Click Convert, then tick Downstream visible. Show only changes the "
        "image overlay."
    )
    notice.setWordWrap(True)
    notice.setStyleSheet(
        "color: #6b7785; padding: 6px 12px 0 12px; font-size: 8.5pt;"
    )
    layout.addWidget(notice)

    page._region_task_def = task_def
    page._region_task_id = task_def.get("id")
    page._region_locked = locked
    page._region_dirty = False
    page._region_save_btn = save_btn
    params = task_def.setdefault("params", {})
    store = _data_regions._region_store_load(ctx, task_def)
    page._region_store = store
    page._region_params = params
    records = _region_image_records(ctx, task_def.get("id"))
    by_path = {rec.get("parquet"): rec for rec in records}
    files = params.get("files")
    if files is None:
        # A new module starts empty; the user chooses the image files.
        files = []
        params["files"] = files
    page._region_files = [
        str(path) for path in files if str(path) in by_path
    ]
    store["files"] = list(page._region_files)
    params["files"] = list(page._region_files)
    page._region_detect_files = [
        str(path) for path in (params.get("detect_files") or [])
        if str(path) in page._region_files
    ] or list(page._region_files)
    page._region_detect = copy.deepcopy(params.get("detect") or {})
    page._region_detect.setdefault("method", "signal")
    page._region_detect.setdefault("files", list(page._region_detect_files))
    page._region_detect.setdefault("channel", None)
    signal_params = page._region_detect.setdefault("signal", {})
    signal_params.setdefault("smooth", 1.0)
    signal_params.setdefault("expand", 2)
    signal_params.setdefault("fill_holes", True)
    signal_params.setdefault("fill_max_area", 0)
    signal_params.setdefault("min_area", 50)
    signal_params.setdefault("tolerance", 1.0)
    signal_params.setdefault("threshold", 0.0)
    signal_params.setdefault("percentile", 30.0)
    if "threshold_mode" not in signal_params:
        # Projects saved before percentile mode stored a plain Auto flag.
        signal_params["threshold_mode"] = (
            "otsu" if signal_params.get("auto_threshold", True)
            else "manual"
        )
    page._region_detect.setdefault("sam", {
        "points_per_side": 32,
        "points_per_batch": 64,
        "pred_iou": 0.88,
        "stability": 0.95,
        "crop_layers": 0,
        "min_area": 0,
        "tolerance": 1.0,
    })
    page._region_snapshot = copy.deepcopy(params)
    page._region_store_snapshot = copy.deepcopy(store)
    page._region_log_lines = list(params.get("log") or [])
    page._region_selected_gate_id = None
    page._region_selected_region_id = None
    page._region_selected_sample = None
    page._region_selected_sub_id = None
    # Every selected Sub-region: {"region": id, "sample": path, "sub": id}.
    page._region_selected_subs = []
    page._region_gate_tool = None
    page._region_region_tool = None
    page._region_region_target = None
    page._region_gate_rois = {}
    page._region_overlay_items = []
    page._region_edit_rois = []
    # Set only by the Region tree's "Edit boundary" action.  Selecting a
    # Sub-region otherwise just highlights it; building anchor handles for a
    # complex boundary is expensive and can freeze the window.
    page._region_edit_boundary = None
    page._region_channel_buttons = {}
    page._region_channel_checks = {}
    page._region_channel_state = None
    page._region_channel_token = 0
    # Records whose files are deleted only after the next successful save, so
    # closing a module without saving cannot destroy a still-referenced mask.
    page._region_channel_orphans = []
    page._region_channel_worker = _RegionChannelWorker()
    page._region_channel_timer = QTimer(page)
    page._region_channel_timer.setInterval(120)
    page._region_channel_timer.timeout.connect(
        lambda: _region_poll_channel(ctx, page)
    )
    page._region_overlay_file = None
    page._region_updating = False
    page._region_clipboard = None
    page._region_detect_token = 0
    page._region_detect_message = ""
    page._region_detect_worker = _RegionDetectWorker()
    page._region_detect_timer = QTimer(page)
    page._region_detect_timer.setInterval(120)
    page._region_detect_timer.timeout.connect(
        lambda: _region_poll_detect(ctx, page)
    )
    preview_files = page._region_files
    first = preview_files[0] if preview_files else None
    columns = _data_columns._columns_for(
        ctx, first, page._region_task_id
    ) if first else []
    channels = [c for c in columns if c not in ("x", "y")]
    display = params.get("preview_display") or {}
    page._region_preview = {
        "file": first,
        "file_name": _region_file_name(ctx, first, page._region_task_id)
        if first else None,
        "channel": params.get("preview_channel") or (
            channels[0] if channels else None
        ),
        "image_min": params.get("preview_min"),
        "image_max": params.get("preview_max"),
        "show_axes": bool(display.get("show_axes", True)),
        "show_axis_names": bool(display.get("show_axis_names", True)),
        "show_indices": bool(display.get("show_indices", True)),
        "show_sample_name": bool(display.get("show_sample_name", True)),
        "axis_name_font_size": int(
            display.get("axis_name_font_size", 11) or 11
        ),
        "index_font_size": int(display.get("index_font_size", 10) or 10),
        "sample_name_font_size": int(
            display.get("sample_name_font_size", 9) or 9
        ),
    }

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)

    left = _region_left_panel(ctx, page)
    middle = _region_middle_panel(ctx, page)
    right = _region_right_panel(ctx, page)

    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.addWidget(left)
    split.addWidget(middle)
    split.addWidget(right)
    split.setStretchFactor(0, 2)
    split.setStretchFactor(1, 2)
    split.setStretchFactor(2, 1)
    split.setSizes([400, 400, 200])
    page._region_main_ratio = (2, 2, 1)
    page._region_main_split = split
    cl.addWidget(split, 1)
    layout.addWidget(content, 1)

    save_btn.clicked.connect(lambda _=False: _region_save_now(ctx, page))
    _region_update_save_btn(page)
    _region_rebuild_files(ctx, page)
    _region_rebuild_gate_tree(ctx, page)
    _region_rebuild_region_tree(ctx, page)
    _region_rebuild_settings(ctx, page)
    _region_rebuild_detect_panel(ctx, page)
    _region_switch_file(ctx, page, page._region_preview.get("file"))
    QTimer.singleShot(0, lambda: _region_rebuild_detect_panel(ctx, page))
    return page


def _region_left_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    files_panel = _widgets_panels._build_panel_frame(ctx)
    fl = QVBoxLayout(files_panel)
    fl.setContentsMargins(10, 8, 10, 10)
    fl.setSpacing(4)
    fl.addWidget(QLabel("<b>Data files</b>"))
    select = QPushButton("Select files…")
    select.setCursor(Qt.CursorShape.PointingHandCursor)
    select.setEnabled(not page._region_locked)
    select.clicked.connect(lambda _=False: _region_select_files(ctx, page))
    fl.addWidget(select)
    from PySide6.QtWidgets import QListWidget, QAbstractItemView
    file_list = QListWidget()
    file_list.setMinimumHeight(40)
    file_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    file_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    file_list.itemClicked.connect(
        lambda item: _region_switch_file(
            ctx, page, item.data(Qt.ItemDataRole.UserRole)
        )
    )
    fl.addWidget(file_list, 1)
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    fl.addWidget(count)
    page._region_files_list = file_list
    page._region_files_count = count
    left_split = QSplitter(Qt.Orientation.Vertical)
    left_split.setChildrenCollapsible(False)
    left_split.addWidget(files_panel)
    page._region_left_split = left_split

    gates_panel = _widgets_panels._build_panel_frame(ctx)
    gl = QVBoxLayout(gates_panel)
    gl.setContentsMargins(10, 8, 10, 10)
    gl.setSpacing(4)
    gl.addWidget(QLabel("<b>Detection gates</b>"))
    hint = QLabel("The selected gate limits where regions are detected.")
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785; font-size: 8pt;")
    gl.addWidget(hint)
    gate_tree = QTreeWidget()
    gate_tree.setHeaderHidden(True)
    gate_tree.setStyleSheet(
        "QTreeWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    gate_tree.itemSelectionChanged.connect(
        lambda: _region_on_gate_selection(ctx, page)
    )
    gate_tree.itemDoubleClicked.connect(
        lambda item, col: _region_rename_gate(ctx, page, item)
    )
    gate_tree.itemChanged.connect(
        lambda item, col: _region_gate_item_changed(ctx, page, item, col)
    )
    gate_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    gate_tree.customContextMenuRequested.connect(
        lambda pos: _region_gate_menu(ctx, page, pos)
    )
    gl.addWidget(gate_tree, 1)
    page._region_gate_tree = gate_tree
    left_split.addWidget(gates_panel)

    regions_panel = _widgets_panels._build_panel_frame(ctx)
    rl = QVBoxLayout(regions_panel)
    rl.setContentsMargins(10, 8, 10, 10)
    rl.setSpacing(4)
    rl.addWidget(QLabel("<b>Regions</b>"))
    region_tree = QTreeWidget()
    region_tree.setColumnCount(5)
    region_tree.setHeaderLabels([
        "Region", "Show", "Convert as channel", "Status",
        "Downstream visible",
    ])
    # Shift and Ctrl select several Sub-regions for a combined highlight,
    # copy or delete.
    region_tree.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection
    )
    region_tree.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    region_tree.setColumnWidth(0, 190)
    region_tree.setColumnWidth(1, 44)
    region_tree.setColumnWidth(2, 118)
    region_tree.setColumnWidth(3, 92)
    region_tree.setColumnWidth(4, 112)
    header = region_tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for _column in (1, 2, 3, 4):
        header.setSectionResizeMode(
            _column, QHeaderView.ResizeMode.Fixed
        )
    region_tree.setStyleSheet(
        "QTreeWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    region_tree.itemSelectionChanged.connect(
        lambda: _region_on_region_selection(ctx, page)
    )
    region_tree.itemChanged.connect(
        lambda item, col: _region_on_region_item_changed(ctx, page, item, col)
    )
    region_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    region_tree.customContextMenuRequested.connect(
        lambda pos: _region_region_menu(ctx, page, pos)
    )
    rl.addWidget(region_tree, 1)
    add_region = QPushButton("Add New Region")
    add_region.setCursor(Qt.CursorShape.PointingHandCursor)
    add_region.setEnabled(not page._region_locked)
    add_region.clicked.connect(lambda _=False: _region_add_region(ctx, page))
    rl.addWidget(add_region)
    page._region_tree = region_tree
    page._region_add_btn = add_region
    left_split.addWidget(regions_panel)
    left_split.setStretchFactor(0, 1)
    left_split.setStretchFactor(1, 1)
    left_split.setStretchFactor(2, 3)
    left_split.setSizes([120, 120, 360])
    page._region_left_ratio = (1, 1, 3)
    lay.addWidget(left_split, 1)
    return panel


def _region_middle_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    image_panel = _widgets_panels._build_panel_frame(ctx)
    top_lay = QVBoxLayout(image_panel)
    top_lay.setContentsMargins(0, 0, 0, 0)
    top_lay.setSpacing(0)

    tools = QWidget()
    tools.setStyleSheet(
        "background: #fbfcfe; border-bottom: 1px solid #e3e8ef;"
    )
    tl = QHBoxLayout(tools)
    tl.setContentsMargins(10, 8, 10, 8)
    tl.setSpacing(6)
    tl.addWidget(QLabel("<b>Tools</b>"))
    tl.addWidget(QLabel("<b>Gating tools</b>"))
    page._region_tool_buttons = {}
    from smage.widgets.plot_axes import _gate_tool_icon
    for text, key in (
        ("Rectangle", "rect"),
        ("Ellipse", "ellipse"),
        ("Polygon", "polygon"),
    ):
        btn = QPushButton()
        btn.setIcon(_gate_tool_icon(key))
        btn.setToolTip(text)
        btn.setFixedSize(30, 30)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(not page._region_locked)
        btn.clicked.connect(
            lambda _=False, k=key: _region_set_gate_tool(ctx, page, k)
        )
        tl.addWidget(btn)
        page._region_tool_buttons[key] = btn
    tl.addSpacing(12)
    tl.addWidget(QLabel("<b>Region tools</b>"))
    page._region_region_buttons = {}
    for text, key in (
        ("Region in", "region_in"),
        ("Region out", "region_out"),
    ):
        btn = QPushButton(text)
        btn.setToolTip(
            "Add a filled polygon to the selected Region."
            if key == "region_in" else
            "Remove the drawn polygon from the selected Sub-region."
        )
        btn.setFixedHeight(30)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(not page._region_locked)
        btn.clicked.connect(
            lambda _=False, k=key: _region_set_region_tool(ctx, page, k)
        )
        tl.addWidget(btn)
        page._region_region_buttons[key] = btn
    tl.addStretch()
    top_lay.addWidget(tools)
    escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), page)
    escape.activated.connect(lambda: _region_cancel_drawing(ctx, page))
    page._region_escape = escape

    viewbox = _GateViewBox(
        on_gate_created=lambda tool, roi: _region_on_gate_created(
            ctx, page, tool, roi
        ),
        on_empty_click=lambda: None,
    )
    viewbox.gate_color = _GATE_PEN
    plot_item = _make_fit_plot_item(viewbox=viewbox)
    plot_widget = pg.PlotWidget(plotItem=plot_item)
    plot_widget.setBackground("#ffffff")
    plot_widget.showGrid(x=True, y=True, alpha=0.2)
    plot_widget.getPlotItem().setAxisItems({
        "bottom": _make_sci_axis("bottom"),
        "left": _make_sci_axis("left"),
    })
    viewbox.setMouseEnabled(True, True)
    # The Region image has no context-menu actions yet; remove pyqtgraph's
    # built-in View All / Mouse Mode / Export menu from a right-click.
    viewbox.setMenuEnabled(False)
    plot_item.setMenuEnabled(False)
    plot_widget._gate_cache_data = True
    plot_item._on_fit = lambda: _region_fit_image(page)
    try:
        plot_item.autoBtn.clicked.disconnect()
    except (RuntimeError, TypeError):
        pass
    plot_item.autoBtn.clicked.connect(lambda _=None: _region_fit_image(page))
    plot_widget.setMinimumSize(240, 220)
    img_timer = QTimer(plot_widget)
    img_timer.setSingleShot(True)
    img_timer.setInterval(35)
    img_timer.timeout.connect(lambda: _region_render_image(ctx, page))
    viewbox.sigRangeChanged.connect(lambda *_: img_timer.start())
    page._region_img_timer = img_timer
    page._region_plot_widget = plot_widget
    page._region_viewbox = viewbox
    plot_widget.scene().sigMouseClicked.connect(
        lambda event: _region_on_image_click(ctx, page, event)
    )
    top_lay.addWidget(plot_widget, 1)

    detect_panel = _widgets_panels._build_panel_frame(ctx)
    dl = QVBoxLayout(detect_panel)
    dl.setContentsMargins(10, 8, 10, 8)
    dl.setSpacing(4)
    dl.addWidget(QLabel("<b>Detect Region</b>"))
    detect_body = QWidget()
    bl = QVBoxLayout(detect_body)
    bl.setContentsMargins(0, 0, 0, 0)
    bl.setSpacing(4)
    page._region_detect_host = QWidget()
    page._region_detect_layout = QVBoxLayout(page._region_detect_host)
    page._region_detect_layout.setContentsMargins(0, 0, 0, 0)
    page._region_detect_layout.setSpacing(4)
    bl.addWidget(page._region_detect_host)
    page._region_detect_status = QLabel("")
    page._region_detect_status.setWordWrap(True)
    page._region_detect_status.setStyleSheet("color: #6b7785;")
    bl.addWidget(page._region_detect_status)
    # The form stays scrollable inside its own panel, so the splitter can
    # still reach the 3:1 image/detection ratio.
    detect_scroll = QScrollArea()
    detect_scroll.setWidgetResizable(True)
    detect_scroll.setFrameShape(QFrame.Shape.NoFrame)
    detect_scroll.setStyleSheet("background: transparent;")
    detect_scroll.setWidget(detect_body)
    page._region_detect_scroll = detect_scroll
    dl.addWidget(detect_scroll, 1)
    middle_split = QSplitter(Qt.Orientation.Vertical)
    middle_split.setChildrenCollapsible(False)
    middle_split.addWidget(image_panel)
    middle_split.addWidget(detect_panel)
    middle_split.setStretchFactor(0, 3)
    middle_split.setStretchFactor(1, 1)
    middle_split.setSizes([600, 200])
    page._region_middle_split = middle_split
    lay.addWidget(middle_split, 1)
    return panel


def _region_right_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    settings_panel = _widgets_panels._build_panel_frame(ctx)
    sl0 = QVBoxLayout(settings_panel)
    sl0.setContentsMargins(10, 8, 10, 10)
    sl0.setSpacing(6)
    sl0.addWidget(QLabel("<b>Image settings</b>"))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet("background: transparent;")
    host = QWidget()
    host.setStyleSheet("background: transparent;")
    settings = QVBoxLayout(host)
    settings.setContentsMargins(0, 0, 0, 0)
    settings.setSpacing(6)
    scroll.setWidget(host)
    sl0.addWidget(scroll, 1)
    page._region_settings_layout = settings

    style_panel = _widgets_panels._build_panel_frame(ctx)
    sl = QVBoxLayout(style_panel)
    sl.setContentsMargins(10, 8, 10, 10)
    sl.setSpacing(4)
    sl.addWidget(QLabel("<b>Region setting</b>"))
    fill_row = QHBoxLayout()
    fill_row.addWidget(QLabel("Fill"))
    fill_btn = QPushButton()
    fill_btn.setFixedSize(28, 20)
    fill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    fill_btn.clicked.connect(lambda _=False: _region_pick_style(ctx, page, "fill"))
    fill_row.addWidget(fill_btn)
    fill_row.addWidget(QLabel("Opacity"))
    opacity = QSlider(Qt.Orientation.Horizontal)
    opacity.setRange(0, 100)
    opacity.setValue(25)
    opacity.valueChanged.connect(
        lambda value: _region_set_style(ctx, page, "fill_opacity", value / 100.0)
    )
    fill_row.addWidget(opacity, 1)
    sl.addLayout(fill_row)
    edge_row = QHBoxLayout()
    edge_row.addWidget(QLabel("Edge"))
    edge_btn = QPushButton()
    edge_btn.setFixedSize(28, 20)
    edge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    edge_btn.clicked.connect(lambda _=False: _region_pick_style(ctx, page, "edge"))
    edge_row.addWidget(edge_btn)
    edge_row.addWidget(QLabel("Width"))
    width = _NoWheelSpinBox()
    width.setRange(0, 12)
    width.setValue(2)
    width.valueChanged.connect(
        lambda value: _region_set_style(
            ctx, page, "edge_width", float(value)
        )
    )
    edge_row.addWidget(width)
    edge_row.addStretch()
    sl.addLayout(edge_row)
    reset = QPushButton("Reset to default")
    reset.setCursor(Qt.CursorShape.PointingHandCursor)
    reset.clicked.connect(lambda _=False: _region_reset_style(ctx, page))
    sl.addWidget(reset)
    page._region_fill_btn = fill_btn
    page._region_edge_btn = edge_btn
    page._region_opacity = opacity
    page._region_edge_width = width
    page._region_style_widgets = [
        fill_btn, edge_btn, opacity, width, reset,
    ]
    log_panel = _widgets_panels._build_panel_frame(ctx)
    ll = QVBoxLayout(log_panel)
    ll.setContentsMargins(10, 8, 10, 10)
    ll.setSpacing(4)
    ll.addWidget(QLabel("<b>Log</b>"))
    log_box = _widgets_panels._log_widget(
        "Detection progress appears here…"
    )
    for line in getattr(page, "_region_log_lines", []):
        log_box.appendPlainText(line)
    log_box.verticalScrollBar().setValue(
        log_box.verticalScrollBar().maximum()
    )
    ll.addWidget(log_box, 1)
    page._region_log = log_box

    right_split = QSplitter(Qt.Orientation.Vertical)
    right_split.setChildrenCollapsible(False)
    right_split.addWidget(settings_panel)
    right_split.addWidget(style_panel)
    right_split.addWidget(log_panel)
    right_split.setStretchFactor(0, 3)
    right_split.setStretchFactor(1, 1)
    right_split.setStretchFactor(2, 1)
    right_split.setSizes([450, 150, 150])
    page._region_right_split = right_split
    lay.addWidget(right_split, 1)
    return panel


# ---------------------------------------------------------------------------
# Files, preview image and settings
# ---------------------------------------------------------------------------

def _region_select_files(ctx, page) -> None:
    records = _region_image_records(ctx, page._region_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _region_file_name(ctx, path, page._region_task_id)
        for path in page._region_files
    ]
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Select image files", names, current, "Filter files…",
    )
    if chosen is None:
        return
    page._region_files = [
        str(by_name[name]) for name in chosen if by_name.get(name)
    ]
    page._region_store["files"] = list(page._region_files)
    page._region_params["files"] = list(page._region_files)
    page._region_detect_files = [
        path for path in page._region_detect_files
        if path in page._region_files
    ] or list(page._region_files)
    page._region_detect["files"] = list(page._region_detect_files)
    if page._region_preview.get("file") not in page._region_files:
        page._region_preview["file"] = (
            page._region_files[0] if page._region_files else None
        )
    _region_mark_dirty(ctx, page)
    _region_rebuild_files(ctx, page)
    _region_rebuild_detect_panel(ctx, page)
    _region_rebuild_region_tree(ctx, page)
    _region_switch_file(ctx, page, page._region_preview.get("file"))


def _region_rebuild_files(ctx, page) -> None:
    from PySide6.QtWidgets import QListWidgetItem

    file_list = page._region_files_list
    file_list.clear()
    for path in page._region_files:
        item = QListWidgetItem(
            _region_file_name(ctx, path, page._region_task_id)
        )
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        file_list.addItem(item)
    page._region_files_count.setText(
        f"{len(page._region_files)} image file(s)"
    )


def _region_switch_file(ctx, page, path) -> None:
    if path not in page._region_files:
        path = page._region_files[0] if page._region_files else None
    page._region_preview["file"] = path
    page._region_preview["file_name"] = (
        _region_file_name(ctx, path, page._region_task_id) if path else None
    )
    file_list = getattr(page, "_region_files_list", None)
    if file_list is not None:
        for index in range(file_list.count()):
            if file_list.item(index).data(Qt.ItemDataRole.UserRole) == path:
                file_list.setCurrentRow(index)
                break
    columns = _data_columns._columns_for(
        ctx, path, page._region_task_id
    ) if path else []
    channels = [c for c in columns if c not in ("x", "y")]
    if page._region_preview.get("channel") not in channels:
        page._region_preview["channel"] = channels[0] if channels else None
    _region_rebuild_settings(ctx, page)
    _region_render_image(ctx, page)
    _region_sync_gate_rois(ctx, page)
    _region_render_overlays(ctx, page)


def _region_render_image(ctx, page) -> None:
    plot_widget = page._region_plot_widget
    preview = page._region_preview
    path = preview.get("file")
    if not path:
        plot_widget.clear()
        plot_widget.setTitle(
            "Select an image file", color="#6b7785", size="10pt"
        )
        return
    channel = preview.get("channel")
    if not channel:
        plot_widget.clear()
        plot_widget.setTitle(
            "No image channel in this file", color="#c0392b", size="10pt"
        )
        return
    plot_dict = {
        "type": "image",
        "file": path,
        "file_name": preview.get("file_name"),
        "layers": [{
            "parameter": channel,
            "color": (255, 255, 255),
            "name": str(channel),
            "min": preview.get("image_min"),
            "max": preview.get("image_max"),
        }],
        "image_min": preview.get("image_min"),
        "image_max": preview.get("image_max"),
        "show_axes": preview.get("show_axes", True),
        "show_axis_names": preview.get("show_axis_names", True),
        "show_indices": preview.get("show_indices", True),
        "show_sample_name": preview.get("show_sample_name", True),
        "axis_name_font_size": preview.get("axis_name_font_size", 11),
        "index_font_size": preview.get("index_font_size", 10),
        "sample_name_font_size": preview.get("sample_name_font_size", 9),
    }
    _widgets_plot_renderer._figure_render_image(
        ctx, plot_dict, plot_widget, task_id=page._region_task_id
    )
    if page._region_overlay_file != path:
        page._region_overlay_file = path
        # Remove our own viewbox items explicitly.  PlotItem.clear() may
        # leave arbitrary QGraphicsItems behind, and dropping the Python
        # references here used to make every sample switch accumulate one
        # more copy of the previous file's Regions on the image.
        _region_clear_overlays(page)
        _region_sync_gate_rois(ctx, page)
        _region_render_overlays(ctx, page)


def _region_fit_image(page) -> None:
    plot_widget = page._region_plot_widget
    extent = getattr(plot_widget, "_img_extent", None)
    if extent:
        vb = plot_widget.getPlotItem().getViewBox()
        vb.disableAutoRange()
        vb.setRange(
            xRange=(extent[0], extent[1]),
            yRange=(extent[2], extent[3]),
            padding=0,
        )


def _region_rebuild_settings(ctx, page) -> None:
    layout = getattr(page, "_region_settings_layout", None)
    if layout is None:
        return
    _widgets_panels._clear_settings_layout(ctx, layout)
    preview = page._region_preview
    enabled = not page._region_locked
    layout.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    for path in page._region_files:
        file_combo.addItem(
            _region_file_name(ctx, path, page._region_task_id), path
        )
    if preview.get("file") is not None:
        index = file_combo.findData(preview["file"])
        if index >= 0:
            file_combo.setCurrentIndex(index)
    file_combo.setEnabled(enabled)
    layout.addWidget(_widgets_panels._file_nav_row(ctx, file_combo, enabled))

    columns = _data_columns._columns_for(
        ctx, preview.get("file"), page._region_task_id
    ) if preview.get("file") else []
    channels = [c for c in columns if c not in ("x", "y")]
    layout.addWidget(QLabel("Channel"))
    channel_combo = _NoWheelComboBox()
    for channel in channels:
        channel_combo.addItem(str(channel))
    if preview.get("channel") in channels:
        channel_combo.setCurrentIndex(channels.index(preview["channel"]))
    channel_combo.setEnabled(enabled)
    layout.addWidget(channel_combo)

    rec = _widgets_plot_renderer._figure_record(ctx, preview.get("file"))
    _widgets_image_level._image_level_controls(
        ctx, layout, preview.get("channel"),
        preview.get("image_min"), preview.get("image_max"), enabled,
        on_minmax=lambda lo, hi: _region_set_image_levels(ctx, page, lo, hi),
        task_id=page._region_task_id,
        parquet_path=rec.get("parquet") if rec else None,
        configs=_widgets_plot_renderer._figure_axis_scale_configs(
            ctx, page._region_task_id,
            rec.get("parquet") if rec else None,
            preview.get("channel"),
        ),
    )
    _widgets_plot_renderer._figure_display_toggles(
        ctx, layout, preview, enabled,
        on_change=lambda: _region_apply_display(ctx, page),
    )
    layout.addStretch()

    def on_file():
        _region_switch_file(ctx, page, file_combo.currentData())

    def on_channel():
        preview["channel"] = channel_combo.currentText() or None
        preview["image_min"] = None
        preview["image_max"] = None
        page._region_params["preview_channel"] = preview["channel"]
        page._region_params["preview_min"] = None
        page._region_params["preview_max"] = None
        _region_rebuild_settings(ctx, page)
        _region_render_image(ctx, page)

    file_combo.currentIndexChanged.connect(lambda _=0: on_file())
    channel_combo.currentIndexChanged.connect(lambda _=0: on_channel())
    page._region_file_combo = file_combo
    page._region_channel_combo = channel_combo


def _region_set_image_levels(ctx, page, low, high) -> None:
    page._region_preview["image_min"] = low
    page._region_preview["image_max"] = high
    page._region_params["preview_min"] = low
    page._region_params["preview_max"] = high
    _region_render_image(ctx, page)


def _region_apply_display(ctx, page) -> None:
    preview = page._region_preview
    page._region_params["preview_display"] = {
        key: preview.get(key) for key in _REGION_DISPLAY_KEYS
    }
    _region_render_image(ctx, page)
    _region_mark_dirty(ctx, page)


# ---------------------------------------------------------------------------
# Internal detection gate tree
# ---------------------------------------------------------------------------

def _region_gate_by_id(page, gate_id):
    for gate in page._region_store.get("gates") or []:
        if str(gate.get("id")) == str(gate_id):
            return gate
    return None


def _region_gate_children(page, gate_id):
    return [
        gate for gate in page._region_store.get("gates") or []
        if str(gate.get("parent")) == str(gate_id)
    ]


def _region_rebuild_gate_tree(ctx, page) -> None:
    tree = page._region_gate_tree
    tree.blockSignals(True)
    tree.clear()
    root = QTreeWidgetItem(["Root (whole image)"])
    root.setData(0, Qt.ItemDataRole.UserRole, None)
    tree.addTopLevelItem(root)
    by_parent = {}
    for gate in page._region_store.get("gates") or []:
        by_parent.setdefault(str(gate.get("parent") or ""), []).append(gate)
    for gate in by_parent.get("", []):
        _region_add_gate_item(page, tree, root, gate, by_parent)
    tree.expandAll()
    tree.blockSignals(False)
    selected = page._region_selected_gate_id
    if selected:
        item = _region_find_gate_item(tree, selected)
        if item is not None:
            tree.setCurrentItem(item)
    else:
        tree.setCurrentItem(root)


def _region_add_gate_item(page, tree, parent, gate, by_parent) -> None:
    item = QTreeWidgetItem([str(gate.get("name") or "Gate")])
    item.setData(0, Qt.ItemDataRole.UserRole, str(gate.get("id")))
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
    if gate.get("type") in ("not", "and", "or"):
        item.setToolTip(0, "Boolean gate: selects the combined points.")
    per_file = page._region_store.get("per_file") or {}
    if str(gate.get("id")) in per_file:
        item.setToolTip(
            0, "Geometry is adjusted separately for each file."
        )
        font = item.font(0)
        font.setItalic(True)
        item.setFont(0, font)
    parent.addChild(item)
    for child in by_parent.get(str(gate.get("id")), []):
        _region_add_gate_item(page, tree, item, child, by_parent)


def _region_find_gate_item(tree, gate_id):
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if str(item.data(0, Qt.ItemDataRole.UserRole)) == str(gate_id):
            return item
        stack.extend(item.child(i) for i in range(item.childCount()))
    return None


def _region_on_gate_selection(ctx, page) -> None:
    tree = page._region_gate_tree
    item = tree.currentItem()
    gate_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
    page._region_selected_gate_id = gate_id
    _region_style_gate_rois(page)


def _region_rename_gate(ctx, page, item) -> None:
    if item is None:
        return
    if not item.data(0, Qt.ItemDataRole.UserRole):
        return
    page._region_gate_tree.editItem(item, 0)


def _region_gate_item_changed(ctx, page, item, column) -> None:
    if column != 0 or page._region_updating:
        return
    gate_id = item.data(0, Qt.ItemDataRole.UserRole)
    if not gate_id:
        return
    gate = _region_gate_by_id(page, gate_id)
    if gate is None:
        return
    text = item.text(0).strip()
    if not text:
        page._region_updating = True
        item.setText(0, str(gate.get("name") or "Gate"))
        page._region_updating = False
        return
    gate["name"] = text
    _region_mark_dirty(ctx, page)


def _region_gate_menu(ctx, page, pos) -> None:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu
    item = page._region_gate_tree.itemAt(pos)
    if item is None:
        return
    gate_id = item.data(0, Qt.ItemDataRole.UserRole)
    gate = _region_gate_by_id(page, gate_id) if gate_id else None
    menu = QMenu(ctx.host)
    rename = menu.addAction("Rename")
    delete = menu.addAction("Delete gate")
    delete.setEnabled(bool(gate_id))
    if gate is not None:
        bool_menu = menu.addMenu("Boolean gating")
        not_act = bool_menu.addAction("NOT")
        and_act = bool_menu.addAction("AND")
        or_act = bool_menu.addAction("OR")
        menu.addSeparator()
        per_file = page._region_store.get("per_file") or {}
        perfile_act = QAction("Per-file gate", menu)
        perfile_act.setCheckable(True)
        perfile_act.setChecked(str(gate_id) in per_file)
        perfile_act.setEnabled(gate.get("type") not in ("not", "and", "or"))
        perfile_act.setToolTip(
            "Adjust this gate's position and size separately for each file."
        )
        menu.addAction(perfile_act)
        unify_act = QAction("Apply one file to all…", menu)
        unify_act.setEnabled(gate.get("type") not in ("not", "and", "or"))
        unify_act.setToolTip(
            "Copy this gate's shape from one selected file to every "
            "selected file."
        )
        menu.addAction(unify_act)
    chosen = menu.exec(page._region_gate_tree.viewport().mapToGlobal(pos))
    if chosen is rename:
        _region_gate_tree_edit(page, item)
    elif chosen is delete and gate_id:
        _region_delete_gate(ctx, page, gate_id)
    elif gate is not None and chosen is not_act:
        _region_add_not_gate(ctx, page, gate_id)
    elif gate is not None and chosen is and_act:
        _region_add_and_or_gate(ctx, page, gate_id, "and")
    elif gate is not None and chosen is or_act:
        _region_add_and_or_gate(ctx, page, gate_id, "or")
    elif gate is not None and chosen is perfile_act:
        _region_toggle_per_file(ctx, page, gate_id)
    elif gate is not None and chosen is unify_act:
        _region_unify_gate(ctx, page, gate_id)


def _region_gate_tree_edit(page, item) -> None:
    tree = page._region_gate_tree
    tree.setCurrentItem(item)
    tree.editItem(item, 0)


def _region_boolean_dependents(page, gate_id) -> list:
    """Bool gates that use `gate_id` as a source."""
    target = str(gate_id)
    return [
        gate for gate in page._region_store.get("gates") or []
        if target in {str(source) for source in _gate_source_ids(gate)}
    ]


def _region_delete_gate(ctx, page, gate_id) -> None:
    children = _region_gate_children(page, gate_id)
    if children:
        QMessageBox.information(
            ctx.host, "Region",
            "This gate has child gates. Delete the children first.",
        )
        return
    dependents = _region_boolean_dependents(page, gate_id)
    if dependents:
        names = ", ".join(
            str(gate.get("name") or gate.get("id"))
            for gate in dependents[:3]
        )
        QMessageBox.information(
            ctx.host, "Region",
            f"This gate is used by the boolean gate {names}. "
            "Delete that gate first.",
        )
        return
    _region_remove_gate_roi(page, gate_id)
    page._region_store["gates"] = [
        gate for gate in page._region_store.get("gates") or []
        if str(gate.get("id")) != str(gate_id)
    ]
    (page._region_store.get("per_file") or {}).pop(str(gate_id), None)
    page._region_selected_gate_id = None
    _region_rebuild_gate_tree(ctx, page)
    _region_style_gate_rois(page)
    _region_mark_dirty(ctx, page)


def _region_select_gate(ctx, page, gate_id) -> None:
    page._region_selected_gate_id = str(gate_id) if gate_id else None
    item = (
        _region_find_gate_item(page._region_gate_tree, gate_id)
        if gate_id else None
    )
    if item is not None:
        page._region_gate_tree.setCurrentItem(item)


def _region_add_not_gate(ctx, page, gate_id) -> None:
    """Create a 'NOT <gate>' sibling that selects points outside the gate."""
    gate = _region_gate_by_id(page, gate_id)
    if gate is None:
        return
    new_id = uuid4().hex
    page._region_store.setdefault("gates", []).append({
        "id": new_id,
        "name": f"NOT {gate.get('name') or gate_id}",
        "type": "not",
        "source": str(gate.get("id")),
        "parent": gate.get("parent"),
    })
    _region_rebuild_gate_tree(ctx, page)
    _region_select_gate(ctx, page, new_id)
    _region_sync_gate_rois(ctx, page)
    _region_mark_dirty(ctx, page)


def _region_boolean_candidates(page, gate_id) -> list:
    """Sibling gates that can be combined with `gate_id`."""
    gate = _region_gate_by_id(page, gate_id)
    if gate is None:
        return []
    parent = gate.get("parent")
    return [
        other for other in page._region_store.get("gates") or []
        if str(other.get("id")) != str(gate_id)
        and other.get("parent") == parent
    ]


def _region_add_and_or_gate(ctx, page, gate_id, op, source_ids=None) -> None:
    """Create '<A> AND/OR <B> …' from `gate_id` and sibling gates."""
    gate = _region_gate_by_id(page, gate_id)
    if gate is None:
        return
    sources = [gate]
    if source_ids is None:
        siblings = _region_boolean_candidates(page, gate_id)
        if not siblings:
            QMessageBox.information(
                ctx.host, f"{op.upper()} gate",
                "This gate has no sibling gates at the same level.",
            )
            return
        dialog = QDialog(ctx.host)
        dialog.setWindowTitle(f"{op.upper()} gate")
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.addWidget(QLabel(
            f"Select the sibling gates to {op.upper()} with "
            f"'{gate.get('name')}':"
        ))
        checkboxes = []
        for sibling in siblings:
            box = QCheckBox(str(sibling.get("name") or sibling.get("id")))
            checkboxes.append((sibling, box))
            dlg_layout.addWidget(box)
        buttons = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        buttons.addStretch()
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        dlg_layout.addLayout(buttons)
        chosen = []

        def accept() -> None:
            chosen.extend(
                sibling for sibling, box in checkboxes if box.isChecked()
            )
            dialog.accept()

        ok_btn.clicked.connect(accept)
        cancel_btn.clicked.connect(dialog.reject)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or not chosen
        ):
            return
        sources.extend(chosen)
    else:
        by_id = {
            str(other.get("id")): other
            for other in page._region_store.get("gates") or []
        }
        for source_id in source_ids:
            other = by_id.get(str(source_id))
            if other is not None and other is not gate:
                sources.append(other)
        if len(sources) < 2:
            return
    sep = " AND " if op == "and" else " OR "
    new_id = uuid4().hex
    page._region_store.setdefault("gates", []).append({
        "id": new_id,
        "name": sep.join(
            str(item.get("name") or item.get("id")) for item in sources
        ),
        "type": op,
        "sources": [str(item.get("id")) for item in sources],
        "parent": gate.get("parent"),
    })
    _region_rebuild_gate_tree(ctx, page)
    _region_select_gate(ctx, page, new_id)
    _region_sync_gate_rois(ctx, page)
    _region_mark_dirty(ctx, page)


def _region_toggle_per_file(ctx, page, gate_id) -> None:
    """Enable/disable per-file geometry for one gate (its type never changes)."""
    gate = _region_gate_by_id(page, gate_id)
    if gate is None or gate.get("type") in ("not", "and", "or"):
        return
    per_file = page._region_store.setdefault("per_file", {})
    key = str(gate.get("id"))
    if key in per_file:
        per_file.pop(key, None)
    else:
        per_file[key] = {}
    _region_rebuild_gate_tree(ctx, page)
    _region_sync_gate_rois(ctx, page)
    _region_mark_dirty(ctx, page)


def _region_unify_gate(ctx, page, gate_id) -> None:
    """Copy one file's shape for this gate to every selected file."""
    gate = _region_gate_by_id(page, gate_id)
    if gate is None or gate.get("type") in ("not", "and", "or"):
        return
    files = page._region_files or []
    if not files:
        QMessageBox.information(
            ctx.host, "Apply one file to all", "Select data files first."
        )
        return
    names = [
        _region_file_name(ctx, path, page._region_task_id)
        for path in files
    ]
    chosen_name, ok = QInputDialog.getItem(
        ctx.host, "Apply one file to all",
        "Copy this gate's shape from:", names, 0, False,
    )
    if not ok:
        return
    file_key = str(files[names.index(chosen_name)])
    resolved = _resolve_gates_for_file(
        [gate], page._region_store.get("per_file") or {}, file_key
    )[0]
    geometry = _gate_geometry(resolved)
    if not geometry:
        return
    gate.update(copy.deepcopy(geometry))
    (page._region_store.get("per_file") or {}).pop(str(gate.get("id")), None)
    _region_rebuild_gate_tree(ctx, page)
    _region_sync_gate_rois(ctx, page)
    _region_mark_dirty(ctx, page)


def _region_clear_region_tool(page) -> None:
    page._region_region_tool = None
    page._region_region_target = None
    for button in getattr(page, "_region_region_buttons", {}).values():
        button.setChecked(False)


def _region_set_gate_tool(ctx, page, key) -> None:
    if getattr(page, "_region_edit_boundary", None):
        page._region_edit_boundary = None
        _region_clear_edit_rois(page)
    _region_clear_region_tool(page)
    active = page._region_gate_tool == key
    page._region_gate_tool = None if active else key
    page._region_viewbox.set_gate_tool(page._region_gate_tool)
    for name, button in page._region_tool_buttons.items():
        button.setChecked(name == page._region_gate_tool)
    if page._region_gate_tool:
        _region_set_status(page, "Draw the detection range on the image.")


def _region_cancel_drawing(ctx, page) -> None:
    page._region_gate_tool = None
    _region_clear_region_tool(page)
    page._region_viewbox.set_gate_tool(None)
    for button in page._region_tool_buttons.values():
        button.setChecked(False)
    if getattr(page, "_region_edit_boundary", None):
        # Esc (or switching tools) also leaves boundary editing.
        page._region_edit_boundary = None
        _region_clear_edit_rois(page)
    _region_set_status(page, "")


def _region_region_tool_target(ctx, page, key):
    """Resolve the Region-tree target for region in/out before drawing."""
    region = _region_selected_region(page)
    if key == "region_out":
        if region is None or not page._region_selected_sub_id:
            QMessageBox.information(
                ctx.host, "Region out",
                "Select a Sub-region in the Region tree first.",
            )
            return None
        sample_path = page._region_selected_sample
        if (
            not sample_path
            or sample_path != page._region_preview.get("file")
        ):
            QMessageBox.information(
                ctx.host, "Region out",
                "Switch the image to this Sub-region's Sample first.",
            )
            return None
        sub = _region_sub_by_id(
            region, sample_path, page._region_selected_sub_id
        )
        if sub is None:
            QMessageBox.information(
                ctx.host, "Region out",
                "Select a Sub-region in the Region tree first.",
            )
            return None
        return {
            "region": str(region.get("id")),
            "sample": str(sample_path),
            "sub": str(sub.get("id")),
            "key": key,
        }

    if region is None:
        QMessageBox.information(
            ctx.host, "Region in",
            "Select a Region in the Region tree first.",
        )
        return None
    sample_path = (
        page._region_selected_sample
        or page._region_preview.get("file")
    )
    if not sample_path:
        QMessageBox.information(
            ctx.host, "Region in", "Select an image file first."
        )
        return None
    sample_path = str(sample_path)
    if (
        page._region_selected_sample
        and page._region_selected_sample != page._region_preview.get("file")
    ):
        QMessageBox.information(
            ctx.host, "Region in",
            "Switch the image to this Sample first, then draw the Region.",
        )
        return None
    if (
        page._region_selected_sub_id
        and page._region_selected_sample == sample_path
    ):
        sub = _region_sub_by_id(
            region, sample_path, page._region_selected_sub_id
        )
        if sub is not None:
            return {
                "region": str(region.get("id")),
                "sample": sample_path,
                "sub": str(sub.get("id")),
                "key": key,
            }
    samples = region.setdefault("samples", {})
    if sample_path not in samples:
        samples[sample_path] = {"visible": True, "sub_regions": []}
        _region_rebuild_region_tree(ctx, page)
    return {
        "region": str(region.get("id")),
        "sample": sample_path,
        "sub": None,
        "key": key,
    }


def _region_set_region_tool(ctx, page, key) -> None:
    if page._region_locked:
        return
    if page._region_region_tool == key:
        _region_cancel_drawing(ctx, page)
        return
    target = _region_region_tool_target(ctx, page, key)
    if target is None:
        return
    if getattr(page, "_region_edit_boundary", None):
        page._region_edit_boundary = None
        _region_clear_edit_rois(page)
    _region_clear_region_tool(page)
    page._region_gate_tool = None
    page._region_region_tool = key
    page._region_region_target = target
    page._region_viewbox.set_gate_tool("polygon")
    for button in page._region_tool_buttons.values():
        button.setChecked(False)
    for name, button in page._region_region_buttons.items():
        button.setChecked(name == key)
    _region_set_status(
        page,
        "Draw a filled polygon to add to the Region."
        if key == "region_in" else
        "Draw a polygon to remove it from the selected Sub-region.",
    )


def _region_retessellate(page, inside, outside, polygon=None, add=True):
    """Rebuild one sub-region's in/out rings from a local pixel mask."""
    import numpy as np

    points = [
        point
        for ring in list(inside or []) + list(outside or [])
        for point in ring
    ]
    if polygon:
        points.extend(polygon)
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x0 = int(math.floor(min(xs))) - 1
    x1 = int(math.ceil(max(xs))) + 1
    y0 = int(math.floor(min(ys))) - 1
    y1 = int(math.ceil(max(ys))) + 1
    gx = np.arange(x0, x1 + 1, dtype="float64")
    gy = np.arange(y0, y1 + 1, dtype="float64")
    xx, yy = np.meshgrid(gx, gy)
    mask = region_parts_mask(
        [{"in": list(inside or []), "out": list(outside or [])}],
        xx.ravel(), yy.ravel(),
    ).reshape(xx.shape)
    if polygon:
        poly_mask = _point_in_polygon(
            xx.ravel(), yy.ravel(), polygon
        ).reshape(xx.shape)
        mask = mask | poly_mask if add else mask & ~poly_mask
    if not mask.any():
        return None
    tolerance = float(
        (page._region_detect.get("signal") or {}).get("tolerance") or 1.0
    )
    return _mask_region(mask, float(x0), float(y0), tolerance)


def _region_on_region_shape(ctx, page, key, roi) -> None:
    target = page._region_region_target
    points = _region_roi_geometry("polygon", roi).get("points") or []
    try:
        page._region_viewbox.removeItem(roi)
    except Exception:  # noqa: BLE001
        pass
    _region_cancel_drawing(ctx, page)
    if target is None or len(points) < 3:
        return
    region = _region_region_by_id(page, target["region"])
    if region is None:
        return
    sample = (region.get("samples") or {}).get(target["sample"])
    if sample is None:
        return

    sub = None
    if target.get("sub"):
        sub = _region_sub_by_id(region, target["sample"], target["sub"])
    if key == "region_out":
        if sub is None:
            return
        part = _region_retessellate(
            page, sub.get("in") or [], sub.get("out") or [],
            points, add=False,
        )
        if part is None:
            QMessageBox.information(
                ctx.host, "Region out",
                "This Region out removed the whole Sub-region; it will be "
                "deleted.",
            )
            sample["sub_regions"] = [
                item for item in sample.get("sub_regions") or []
                if item is not sub
            ]
            page._region_selected_sub_id = None
        else:
            sub["kind"] = "region"
            sub["in"] = part["in"]
            sub["out"] = part["out"]
            page._region_selected_region_id = region["id"]
            page._region_selected_sample = target["sample"]
            page._region_selected_sub_id = sub["id"]
    else:
        if sub is None:
            part = _region_retessellate(page, [], [], points, add=True)
            if part is None:
                return
            sub = {
                "id": uuid4().hex,
                "name": _region_next_sub_name(sample),
                "visible": True,
                "kind": "region",
                "in": part["in"],
                "out": part["out"],
            }
            sample.setdefault("sub_regions", []).append(sub)
        else:
            part = _region_retessellate(
                page, sub.get("in") or [], sub.get("out") or [],
                points, add=True,
            )
            if part is None:
                return
            sub["kind"] = "region"
            sub["in"] = part["in"]
            sub["out"] = part["out"]
        page._region_selected_region_id = region["id"]
        page._region_selected_sample = target["sample"]
        page._region_selected_sub_id = sub["id"]

    _region_rebuild_region_tree(ctx, page)
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


def _region_on_gate_created(ctx, page, tool, roi) -> None:
    if page._region_region_tool is not None:
        _region_on_region_shape(ctx, page, page._region_region_tool, roi)
        return
    if page._region_locked:
        try:
            page._region_viewbox.removeItem(roi)
        except Exception:  # noqa: BLE001
            pass
        return
    parent = page._region_selected_gate_id or None
    gate_id = uuid4().hex
    geometry = _region_roi_geometry(tool, roi)
    if not geometry:
        return
    gate = {
        "id": gate_id,
        "name": _region_next_gate_name(page),
        "type": tool,
        "parent": parent,
    }
    gate.update(geometry)
    page._region_store.setdefault("gates", []).append(gate)
    page._region_viewbox.removeItem(roi)
    _region_rebuild_gate_tree(ctx, page)
    page._region_selected_gate_id = gate_id
    item = _region_find_gate_item(page._region_gate_tree, gate_id)
    if item is not None:
        page._region_gate_tree.setCurrentItem(item)
    _region_sync_gate_rois(ctx, page)
    _region_style_gate_rois(page)
    _region_cancel_drawing(ctx, page)
    _region_mark_dirty(ctx, page)


def _region_next_gate_name(page) -> str:
    names = {
        str(gate.get("name") or "")
        for gate in page._region_store.get("gates") or []
    }
    index = 1
    while f"Gate {index}" in names:
        index += 1
    return f"Gate {index}"


def _region_roi_geometry(tool, roi) -> dict:
    if tool in ("rect", "ellipse"):
        return {
            "pos": [roi.pos().x(), roi.pos().y()],
            "size": [roi.size().x(), roi.size().y()],
        }
    if tool == "polygon":
        ox = roi.pos().x()
        oy = roi.pos().y()
        points = [
            [point.x() + ox, point.y() + oy]
            for point in roi.getState()["points"]
        ]
        if len(points) >= 3:
            return {"points": points}
    return {}


def _region_sync_gate_rois(ctx, page) -> None:
    for roi in list(page._region_gate_rois.values()):
        try:
            page._region_viewbox.removeItem(roi)
        except Exception:  # noqa: BLE001
            pass
    page._region_gate_rois = {}
    if page._region_locked:
        return
    selected = page._region_selected_gate_id
    gates = page._region_store.get("gates") or []
    resolved = _resolve_gates_for_file(
        gates, page._region_store.get("per_file") or {},
        page._region_preview.get("file"),
    )
    for gate, shown in zip(gates, resolved):
        gate_id = str(gate.get("id"))
        pen_color = (
            _GATE_SELECTED_PEN if gate_id == str(selected)
            else _GATE_PEN
        )
        pen = pg.mkPen(
            pen_color, width=2.5 if gate_id == str(selected) else 1.5
        )
        gate_type = gate.get("type")
        try:
            if gate_type == "rect":
                roi = pg.RectROI(
                    shown.get("pos", [0, 0]), shown.get("size", [1, 1]),
                    pen=pen, handlePen=pen,
                )
            elif gate_type == "ellipse":
                roi = pg.EllipseROI(
                    shown.get("pos", [0, 0]), shown.get("size", [1, 1]),
                    pen=pen, handlePen=pen,
                )
            elif gate_type == "polygon":
                points = shown.get("points") or []
                if len(points) < 3:
                    continue
                roi = _GatePolygonROI(
                    [tuple(point) for point in points],
                    closed=True, pen=pen, handlePen=pen,
                )
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        roi.setZValue(30)
        page._region_viewbox.addItem(roi, ignoreBounds=True)
        roi.sigRegionChanged.connect(
            lambda r=roi, g=gate: _region_on_gate_roi_changed(ctx, page, g, r)
        )
        page._region_gate_rois[gate_id] = roi


def _region_on_gate_roi_changed(ctx, page, gate, roi) -> None:
    if page._region_updating:
        return
    geometry = _region_roi_geometry(gate.get("type"), roi)
    if not geometry:
        return
    gate_id = str(gate.get("id"))
    per_file = page._region_store.setdefault("per_file", {})
    file_key = page._region_preview.get("file")
    if gate_id in per_file and file_key:
        per_file[gate_id][str(file_key)] = copy.deepcopy(geometry)
    else:
        gate.update(geometry)
    _region_mark_dirty(ctx, page)


def _region_remove_gate_roi(page, gate_id) -> None:
    roi = page._region_gate_rois.pop(str(gate_id), None)
    if roi is not None:
        try:
            page._region_viewbox.removeItem(roi)
        except Exception:  # noqa: BLE001
            pass


def _region_style_gate_rois(page) -> None:
    selected = page._region_selected_gate_id
    for gate_id, roi in page._region_gate_rois.items():
        color = _GATE_SELECTED_PEN if str(gate_id) == str(selected) else _GATE_PEN
        pen = pg.mkPen(color, width=2.5 if str(gate_id) == str(selected) else 1.5)
        try:
            roi.setPen(pen)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Region tree
# ---------------------------------------------------------------------------

def _region_item_key(item):
    return item.data(0, Qt.ItemDataRole.UserRole) if item else None


def _region_rebuild_region_tree(ctx, page) -> None:
    tree = page._region_tree
    tree.blockSignals(True)
    tree.clear()
    page._region_channel_buttons = {}
    page._region_channel_checks = {}
    for region in page._region_store.get("regions") or []:
        item = QTreeWidgetItem([
            str(region.get("name") or "Region"), "", "", "", "",
        ])
        item.setData(
            0, Qt.ItemDataRole.UserRole,
            ("region", str(region.get("id")), None, None),
        )
        item.setFlags(
            item.flags() | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(
            1,
            Qt.CheckState.Checked if region.get("visible", True)
            else Qt.CheckState.Unchecked,
        )
        tree.addTopLevelItem(item)
        _region_build_channel_cells(ctx, page, item, region)
        for sample_path, sample in (region.get("samples") or {}).items():
            sample_item = QTreeWidgetItem([
                _region_file_name(ctx, sample_path, page._region_task_id),
                "", "", "", "",
            ])
            sample_item.setData(
                0, Qt.ItemDataRole.UserRole,
                ("sample", str(region.get("id")), str(sample_path), None),
            )
            sample_item.setFlags(
                sample_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            sample_item.setCheckState(
                1,
                Qt.CheckState.Checked
                if sample.get("visible", True)
                else Qt.CheckState.Unchecked,
            )
            item.addChild(sample_item)
            for sub in sample.get("sub_regions") or []:
                sub_item = QTreeWidgetItem([
                    str(sub.get("name") or "Sub-region"), "", "", "", "",
                ])
                sub_item.setData(
                    0, Qt.ItemDataRole.UserRole,
                    (
                        "sub", str(region.get("id")), str(sample_path),
                        str(sub.get("id")),
                    ),
                )
                sub_item.setFlags(
                    sub_item.flags() | Qt.ItemFlag.ItemIsEditable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                sub_item.setCheckState(
                    1,
                    Qt.CheckState.Checked
                    if sub.get("visible", True)
                    else Qt.CheckState.Unchecked,
                )
                sample_item.addChild(sub_item)
    tree.expandAll()
    tree.blockSignals(False)
    _region_select_tree_item(ctx, page)


def _region_select_tree_item(ctx, page) -> None:
    tree = page._region_tree
    target = page._region_selected_sub_id
    for top in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(top)
        key = _region_item_key(item)
        if key and key[1] == str(page._region_selected_region_id or ""):
            if target is None and key[0] == "region":
                tree.setCurrentItem(item)
                return
            for child_i in range(item.childCount()):
                child = item.child(child_i)
                ckey = _region_item_key(child)
                if (
                    target is None and ckey and ckey[2] == page._region_selected_sample
                    and ckey[0] == "sample"
                ):
                    tree.setCurrentItem(child)
                    return
                for sub_i in range(child.childCount()):
                    sub = child.child(sub_i)
                    skey = _region_item_key(sub)
                    if skey and skey[3] == target:
                        tree.setCurrentItem(sub)
                        return


def _region_channels_locked(ctx, page) -> bool:
    """Conversion/visibility is off for a locked module or its draft copy."""
    return bool(
        page._region_locked
        or str(page._region_task_id)
        in getattr(ctx, "_draft_task_ids", ())
    )


def _region_channel_status(ctx, page, region) -> dict:
    """Derive the Convert/Status/checkbox state of one Region."""
    region_id = str(region.get("id"))
    samples = [str(path) for path in (region.get("samples") or {})]
    total = len(samples)
    entry = (
        ((page._region_params.get("channels") or {}).get(region_id))
        or {}
    )
    records = entry.get("files") or {}
    valid = 0
    stale = 0
    details = []
    for path in samples:
        rec = records.get(path)
        channel_file = _data_regions._region_channel_file(ctx, rec)
        name = _region_file_name(ctx, path, page._region_task_id)
        if channel_file is None:
            details.append(f"{name}: not converted")
            continue
        if rec.get("signature") != _data_regions._region_channel_signature(
            channel_file
        ):
            details.append(f"{name}: channel file changed")
            continue
        valid += 1
        if rec.get("region_signature") != (
            _data_regions._region_geometry_signature(region, path)
        ):
            stale += 1
            details.append(f"{name}: boundary changed")
        else:
            details.append(
                f"{name}: {int(rec.get('pixels') or 0):,} px inside"
            )
    state = getattr(page, "_region_channel_state", None)
    converting = bool(
        state and str(state.get("region_id")) == region_id
    )
    error = str(entry.get("last_error") or "")
    if converting:
        text = (
            f"Converting {int(state.get('done') or 0)}/"
            f"{int(state.get('total') or total)}"
        )
        color = "#2b6cb0"
    elif total == 0:
        text, color = "No Samples", "#6b7785"
    elif valid == 0 and error:
        text, color = "Failed", "#c0392b"
    elif valid == 0:
        text, color = "Not converted", "#6b7785"
    elif valid < total:
        text, color = f"Partial ({valid}/{total})", "#b58900"
    elif stale:
        text, color = "Needs update", "#b58900"
    else:
        text, color = "Converted", "#2f855a"
    if error:
        details.append(error)
    locked = _region_channels_locked(ctx, page)
    can_convert = bool(not locked and total > 0 and not converting)
    checkbox_enabled = bool(
        not locked and total > 0 and valid == total and not converting
    )
    return {
        "text": text,
        "color": color,
        "tooltip": "\n".join(details) or "No Samples",
        "button_text": "Reconvert" if valid else "Convert",
        "button_enabled": can_convert,
        "checkbox_enabled": checkbox_enabled,
        "visible": bool(entry.get("downstream_visible")),
        "valid": valid,
        "total": total,
        "stale": stale,
    }


def _region_build_channel_cells(ctx, page, item, region) -> None:
    """Add the Convert button, Status and Downstream visible controls."""
    status = _region_channel_status(ctx, page, region)
    region_id = str(region.get("id"))
    button = QPushButton(status["button_text"])
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setEnabled(status["button_enabled"])
    button.setToolTip(
        "Write this Region as a 0/1 channel for every file that has a Sample."
    )
    button.clicked.connect(
        lambda _=False, rid=region_id: _region_convert_channel(
            ctx, page, rid
        )
    )
    page._region_tree.setItemWidget(item, 2, button)
    page._region_channel_buttons[region_id] = button

    item.setText(3, status["text"])
    item.setToolTip(3, status["tooltip"])
    item.setForeground(3, QBrush(QColor(status["color"])))

    check = QCheckBox()
    check.setChecked(status["visible"])
    check.setEnabled(status["checkbox_enabled"])
    check.setToolTip(
        "Expose this converted Region to downstream modules as a channel."
    )
    check.setStyleSheet("margin-left: 14px;")
    check.toggled.connect(
        lambda checked, rid=region_id: _region_set_downstream_visible(
            ctx, page, rid, checked
        )
    )
    page._region_tree.setItemWidget(item, 4, check)
    page._region_channel_checks[region_id] = check


def _region_set_downstream_visible(ctx, page, region_id, checked) -> None:
    if _region_channels_locked(ctx, page):
        return
    region = _region_region_by_id(page, region_id)
    if region is None:
        return
    status = _region_channel_status(ctx, page, region)
    if checked and not status["checkbox_enabled"]:
        check = page._region_channel_checks.get(str(region_id))
        if check is not None:
            check.blockSignals(True)
            check.setChecked(not checked)
            check.blockSignals(False)
        return
    entry = _data_regions._region_channel_entry(
        page._region_params, region_id, create=True
    )
    if entry is None:
        return
    entry["downstream_visible"] = bool(checked)
    _region_save_now(ctx, page)
    _region_log(
        ctx, page,
        f"Region '{region.get('name')}' downstream visible = "
        f"{'yes' if checked else 'no'}.",
    )


def _region_delete_channel_record(ctx, rec) -> None:
    """Delete one converted channel parquet and its index pyramid."""
    if not isinstance(rec, dict) or ctx.project is None:
        return
    rel = rec.get("path")
    if not rel:
        return
    path = ctx.project.path / rel
    try:
        path.unlink()
    except OSError:
        pass
    shutil.rmtree(Path(str(path) + ".imgidx"), ignore_errors=True)


def _region_delete_channel_entry(ctx, entry) -> None:
    if not isinstance(entry, dict):
        return
    for rec in (entry.get("files") or {}).values():
        _region_delete_channel_record(ctx, rec)


def _region_walk_tasks(tasks):
    for task in tasks:
        yield task
        yield from _region_walk_tasks(task.get("children") or [])


def _region_channel_names_in_use(ctx) -> set:
    """Every channel name visible in any module's data files.

    Also includes the names of converted Region channels, so two Regions in
    different modules cannot publish the same downstream channel name.
    """
    names = set()
    if ctx.project is None:
        return names
    tasks = list(_region_walk_tasks(ctx.project.workflow.get("tasks") or []))
    for task in tasks:
        task_id = task.get("id")
        try:
            records = ctx._available_files(task_id)
        except Exception:  # noqa: BLE001
            records = []
        for rec in records:
            path = rec.get("parquet")
            if not path:
                continue
            try:
                names.update(
                    str(column)
                    for column in ctx._columns_for(path, task_id)
                )
            except Exception:  # noqa: BLE001
                continue
    for task in tasks:
        if task.get("type") != "region":
            continue
        channels = (task.get("params") or {}).get("channels") or {}
        if not channels:
            continue
        store = _data_regions._region_store_load(ctx, task)
        by_id = {
            str(region.get("id")): region
            for region in store.get("regions") or []
        }
        for region_id, entry in channels.items():
            if not isinstance(entry, dict):
                continue
            if not entry.get("downstream_visible"):
                continue
            region = by_id.get(str(region_id))
            if region is not None:
                names.add(str(region.get("name") or region_id))
    return names


def _region_channel_name_conflict(ctx, page, name) -> bool:
    name = str(name or "").strip()
    if not name:
        return False
    return name in _region_channel_names_in_use(ctx)


def _region_free_region_name(ctx, page, base, exclude_id=None) -> str:
    """A Region name that collides with no Region or channel name."""
    base = str(base or "Region").strip() or "Region"
    used = _region_channel_names_in_use(ctx)
    region_names = {
        str(region.get("name") or "")
        for region in page._region_store.get("regions") or []
        if str(region.get("id")) != str(exclude_id or "")
    }
    candidate = base
    index = 1
    while candidate in used or candidate in region_names:
        index += 1
        candidate = f"{base} {index}"
    return candidate


def _region_convert_channel(ctx, page, region_id) -> None:
    """Convert one Region to 0/1 channels on all files it has a Sample on."""
    if _region_channels_locked(ctx, page):
        return
    if getattr(page, "_region_channel_state", None):
        QMessageBox.information(
            ctx.host, "Convert Region",
            "A Region conversion is already running.",
        )
        return
    region = _region_region_by_id(page, region_id)
    if region is None:
        return
    samples = [str(path) for path in (region.get("samples") or {})]
    name = str(region.get("name") or region_id)
    if not samples:
        QMessageBox.information(
            ctx.host, "Convert Region",
            "This Region has no Samples to convert.",
        )
        return
    entry = _data_regions._region_channel_entry(
        page._region_params, region_id
    )
    already_visible = bool(entry and entry.get("downstream_visible"))
    if not already_visible and _region_channel_name_conflict(
        ctx, page, name
    ):
        QMessageBox.warning(
            ctx.host, "Convert Region",
            f"'{name}' is already a channel name in one of the data files. "
            "Rename the Region first.",
        )
        _region_log(
            ctx, page,
            f"✗ Region '{name}' was not converted: its name is already a "
            "channel in a data file.",
        )
        return
    # Persist the tree first so the geometry signatures match what is drawn.
    _region_save_now(ctx, page)
    jobs = []
    for path in samples:
        parts = _data_regions._region_parts(region, path)
        jobs.append({
            "path": path,
            "parts": list(parts or []),
            "region_signature": (
                _data_regions._region_geometry_signature(region, path)
            ),
        })
    page._region_channel_token += 1
    token = page._region_channel_token
    page._region_channel_state = {
        "token": token,
        "region_id": str(region_id),
        "name": name,
        "total": len(jobs),
        "done": 0,
        "records": {},
        "errors": {},
    }
    _region_log(
        ctx, page,
        f"===== Convert Region '{name}' as channel — {len(jobs)} file(s) =====",
    )
    page._region_channel_worker.request(
        token, ctx, page._region_task_id, str(region_id), jobs
    )
    page._region_channel_timer.start()
    # Do not rebuild the tree from inside the Convert button's click signal:
    # the rebuild deletes that button.  Refresh on the next event-loop turn.
    QTimer.singleShot(
        0, lambda: _region_rebuild_region_tree(ctx, page)
    )


def _region_poll_channel(ctx, page) -> None:
    state = getattr(page, "_region_channel_state", None)
    if not state:
        page._region_channel_timer.stop()
        return
    finished = False
    for message in page._region_channel_worker.poll():
        if not message:
            continue
        kind = message[0]
        token = message[1] if len(message) > 1 else None
        if token != state["token"]:
            continue
        if kind == "file":
            _, _token, path, record, error, seconds = message[:6]
            name = _region_file_name(
                ctx, path, page._region_task_id
            )
            state["done"] = int(state.get("done") or 0) + 1
            if error:
                state["errors"][str(path)] = str(error)
                _region_log(ctx, page, f"✗ {name}: {error}")
            else:
                state["records"][str(path)] = record
                pixels = int((record or {}).get("pixels") or 0)
                _region_log(
                    ctx, page,
                    f"{name}: {pixels:,} px inside — "
                    f"{float(seconds):.2f} s",
                )
            _region_rebuild_region_tree(ctx, page)
        elif kind == "done":
            finished = True
    if finished:
        page._region_channel_timer.stop()
        _region_finish_channel(ctx, page)


def _region_finish_channel(ctx, page) -> None:
    state = page._region_channel_state
    page._region_channel_state = None
    if not state:
        return
    region_id = str(state.get("region_id"))
    entry = _data_regions._region_channel_entry(
        page._region_params, region_id, create=True
    )
    if entry is not None:
        old_files = dict(entry.get("files") or {})
        files = dict(old_files)
        files.update(state.get("records") or {})
        region = _region_region_by_id(page, region_id)
        samples = (
            {str(path) for path in (region.get("samples") or {})}
            if region is not None else set()
        )
        entry["files"] = {
            path: record for path, record in files.items()
            if path in samples
        }
        for path, old_record in old_files.items():
            new_record = entry["files"].get(path)
            if (
                isinstance(new_record, dict)
                and new_record.get("path") == old_record.get("path")
            ):
                continue
            if isinstance(old_record, dict):
                page._region_channel_orphans.append(old_record)
        errors = state.get("errors") or {}
        entry["last_error"] = (
            "; ".join(
                f"{Path(str(path)).name}: {message}"
                for path, message in list(errors.items())[:3]
            ) if errors else ""
        )
    errors = state.get("errors") or {}
    if errors:
        _region_log(
            ctx, page,
            f"✗ Region '{state.get('name')}' conversion finished with "
            f"{len(errors)} error(s).",
        )
    else:
        _region_log(
            ctx, page,
            f"✓ Region '{state.get('name')}' converted "
            f"({len(state.get('records') or {})} file(s)).",
        )
    _region_save_now(ctx, page)
    _region_rebuild_region_tree(ctx, page)


def _region_on_region_selection(ctx, page) -> None:
    tree = page._region_tree
    item = tree.currentItem()
    key = _region_item_key(item)
    if not key:
        return
    if page._region_region_tool is not None:
        _region_cancel_drawing(ctx, page)
    kind, region_id, sample_path, sub_id = key
    page._region_selected_region_id = region_id
    page._region_selected_sample = sample_path
    page._region_selected_sub_id = sub_id
    selected_subs = {}
    for selected in tree.selectedItems():
        skey = _region_item_key(selected)
        if not skey or skey[0] != "sub":
            continue
        entry = (str(skey[1]), str(skey[2]), str(skey[3]))
        selected_subs[entry] = {
            "region": entry[0], "sample": entry[1], "sub": entry[2],
        }
    if kind == "sub":
        entry = (str(region_id), str(sample_path), str(sub_id))
        selected_subs.setdefault(entry, {
            "region": entry[0], "sample": entry[1], "sub": entry[2],
        })
    page._region_selected_subs = list(selected_subs.values())
    if sample_path:
        _region_switch_file(ctx, page, sample_path)
    _region_style_panel_from_selection(ctx, page)
    _region_render_overlays(ctx, page)


def _region_on_region_item_changed(ctx, page, item, column) -> None:
    if page._region_updating:
        return
    key = _region_item_key(item)
    if not key:
        return
    kind, region_id, sample_path, sub_id = key
    region = _region_region_by_id(page, region_id)
    if region is None:
        return
    if column == 1:
        checked = item.checkState(1) == Qt.CheckState.Checked
        if kind == "region":
            region["visible"] = checked
        elif kind == "sample":
            sample = (region.get("samples") or {}).get(sample_path)
            if sample is not None:
                sample["visible"] = checked
        elif kind == "sub":
            sub = _region_sub_by_id(region, sample_path, sub_id)
            if sub is not None:
                sub["visible"] = checked
        if kind in ("sample", "sub") and sample_path:
            # Clicking the Show checkbox must also show that file. Otherwise
            # the image stays on the previous sample and keeps drawing its
            # Regions while the user is looking at a different tree row.
            page._region_selected_region_id = region_id
            page._region_selected_sample = sample_path
            page._region_selected_sub_id = (
                sub_id if kind == "sub" else None
            )
            page._region_selected_subs = (
                [{
                    "region": str(region_id),
                    "sample": str(sample_path),
                    "sub": str(sub_id),
                }] if kind == "sub" else []
            )
            if page._region_preview.get("file") != sample_path:
                _region_switch_file(ctx, page, sample_path)
            _region_style_panel_from_selection(ctx, page)
        _region_mark_dirty(ctx, page)
        _region_render_overlays(ctx, page)
        return
    if column == 0:
        name = item.text(0).strip()
        if kind == "region":
            old_name = str(region.get("name") or "Region")
            name = name or "Region"
            other_names = {
                str(other.get("name") or "")
                for other in page._region_store.get("regions") or []
                if str(other.get("id")) != str(region_id)
            }
            if name != old_name and (
                name in other_names
                or _region_channel_name_conflict(ctx, page, name)
            ):
                QMessageBox.warning(
                    ctx.host, "Rename Region",
                    f"'{name}' is already used as a Region or channel name. "
                    "Choose another name.",
                )
                page._region_tree.blockSignals(True)
                item.setText(0, old_name)
                page._region_tree.blockSignals(False)
                return
            region["name"] = name
            item.setText(0, name)
        elif kind == "sub":
            sub = _region_sub_by_id(region, sample_path, sub_id)
            if sub is not None:
                sub["name"] = name or "Sub-region"
                item.setText(0, sub["name"])
        _region_mark_dirty(ctx, page)


def _region_region_by_id(page, region_id):
    for region in page._region_store.get("regions") or []:
        if str(region.get("id")) == str(region_id):
            return region
    return None


def _region_sub_by_id(region, sample_path, sub_id):
    sample = (region.get("samples") or {}).get(sample_path) or {}
    for sub in sample.get("sub_regions") or []:
        if str(sub.get("id")) == str(sub_id):
            return sub
    return None


def _region_selected_region(page):
    return _region_region_by_id(page, page._region_selected_region_id)


def _region_prune_edit_boundary(page, path=None) -> None:
    """Drop the edit state when the selection or preview file moved away."""
    edit = getattr(page, "_region_edit_boundary", None)
    if not edit:
        return
    current_file = (
        path if path is not None else page._region_preview.get("file")
    )
    if (
        str(edit.get("region")) != str(page._region_selected_region_id or "")
        or str(edit.get("sample")) != str(page._region_selected_sample or "")
        or str(edit.get("sub")) != str(page._region_selected_sub_id or "")
        or str(edit.get("sample")) != str(current_file or "")
        or len(getattr(page, "_region_selected_subs", None) or []) > 1
    ):
        page._region_edit_boundary = None


def _region_editing_sub(page, path):
    """The Sub-region whose anchors are shown, if Edit boundary was used."""
    edit = getattr(page, "_region_edit_boundary", None)
    if not edit or str(edit.get("sample")) != str(path or ""):
        return None
    region = _region_selected_region(page)
    if region is None or not region.get("visible", True):
        return None
    sample = (region.get("samples") or {}).get(path)
    if sample is None or not sample.get("visible", True):
        return None
    sub = _region_sub_by_id(region, path, edit.get("sub"))
    if sub is None or not sub.get("visible", True):
        return None
    return sub


def _region_start_edit_boundary(ctx, page, key) -> None:
    """Show anchor handles for one Sub-region, after an Edit boundary menu."""
    kind, region_id, sample_path, sub_id = key
    if kind != "sub":
        return
    region = _region_region_by_id(page, region_id)
    sub = _region_sub_by_id(region, sample_path, sub_id)
    if sub is None:
        return
    if page._region_gate_tool or page._region_region_tool:
        _region_cancel_drawing(ctx, page)
    # Select and show the Sub-region first, so the handles appear on the
    # right file.  Selecting it alone only highlights the boundary.
    page._region_selected_region_id = str(region_id)
    page._region_selected_sample = str(sample_path)
    page._region_selected_sub_id = str(sub_id)
    page._region_edit_boundary = None
    if page._region_preview.get("file") != sample_path:
        _region_switch_file(ctx, page, sample_path)
    _region_rebuild_region_tree(ctx, page)
    _region_style_panel_from_selection(ctx, page)

    anchors = sum(
        len(ring)
        for kind_name in ("in", "out")
        for ring in (sub.get(kind_name) or [])
    )
    if anchors > _REGION_ANCHOR_WARN_LIMIT:
        answer = QMessageBox.warning(
            ctx.host,
            "Edit boundary",
            f"This Sub-region has {anchors} anchor points. Showing them can "
            "freeze or crash Smage. Show the anchors anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            _region_render_overlays(ctx, page)
            return
    page._region_edit_boundary = {
        "region": str(region_id),
        "sample": str(sample_path),
        "sub": str(sub_id),
    }
    _region_set_status(
        page,
        "Drag the anchors to edit this Sub-region; press Esc to finish.",
    )
    _region_render_overlays(ctx, page)


def _region_start_rename(page, item) -> None:
    """Start inline editing of a Region or Sub-region name."""
    if item is None:
        return
    tree = page._region_tree
    tree.clearSelection()
    tree.setCurrentItem(item)
    tree.editItem(item, 0)


def _region_region_menu(ctx, page, pos) -> None:
    from PySide6.QtWidgets import QMenu
    tree = page._region_tree
    item = tree.itemAt(pos)
    key = _region_item_key(item)
    selected_keys = [
        selected for selected in (
            _region_item_key(sel) for sel in tree.selectedItems()
        ) if selected
    ]
    if key is None:
        multi_keys = []
    elif key in selected_keys:
        multi_keys = selected_keys
    else:
        multi_keys = [key]
    sub_keys = [selected for selected in multi_keys if selected[0] == "sub"]
    multi_subs = (
        key is not None and key[0] == "sub" and len(sub_keys) > 1
    )
    menu = QMenu(ctx.host)
    rename = menu.addAction("Rename")
    menu.addSeparator()
    add_sample = menu.addAction("Add Sample…")
    add_sub = menu.addAction("Add Sub-region")
    edit_boundary = menu.addAction("Edit boundary…")
    menu.addSeparator()
    copy = menu.addAction("Copy Branch")
    paste = menu.addAction("Paste Branch")
    menu.addSeparator()
    delete = menu.addAction("Delete")
    kind = key[0] if key else None
    rename.setEnabled(
        kind in ("region", "sub") and not page._region_locked
    )
    add_sample.setEnabled(kind == "region" and not page._region_locked)
    add_sub.setEnabled(kind == "sample" and not page._region_locked)
    edit_boundary.setEnabled(kind == "sub" and not page._region_locked)
    copy.setEnabled(kind in ("region", "sample", "sub"))
    clip_kind = (page._region_clipboard or {}).get("kind")
    paste.setEnabled(
        page._region_clipboard is not None and not page._region_locked
        and (
            clip_kind == "region" and kind in (None, "region")
            or clip_kind in ("sub", "subs") and kind in ("sample", "sub")
            or clip_kind == "sample" and kind in ("region", "sample", "sub")
        )
    )
    delete.setEnabled(kind is not None and not page._region_locked)
    chosen = menu.exec(tree.viewport().mapToGlobal(pos))
    if chosen is rename and kind in ("region", "sub"):
        _region_start_rename(page, item)
    elif chosen is add_sample and kind == "region":
        _region_add_sample(ctx, page, key[1])
    elif chosen is add_sub and kind == "sample":
        _region_arm_subregion(ctx, page, key[1], key[2])
    elif chosen is edit_boundary and kind == "sub":
        _region_start_edit_boundary(ctx, page, key)
    elif chosen is copy:
        if multi_subs:
            _region_copy_subs(page, sub_keys)
        else:
            _region_copy(ctx, page, key)
    elif chosen is paste:
        _region_paste(ctx, page, key)
    elif chosen is delete:
        if multi_subs:
            _region_delete_subs(ctx, page, sub_keys)
        else:
            _region_delete(ctx, page, key)


def _region_add_region(ctx, page) -> None:
    region = {
        "id": uuid4().hex,
        "name": _region_free_region_name(
            ctx, page, _region_next_name(page._region_store)
        ),
        "visible": True,
        "style": _region_default_style(
            len(page._region_store.get("regions") or [])
        ),
        "samples": {},
    }
    page._region_store.setdefault("regions", []).append(region)
    page._region_selected_region_id = region["id"]
    page._region_selected_sample = None
    page._region_selected_sub_id = None
    _region_rebuild_region_tree(ctx, page)
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


def _region_add_sample(ctx, page, region_id) -> None:
    region = _region_region_by_id(page, region_id)
    if region is None:
        return
    existing = set((region.get("samples") or {}).keys())
    names = [
        _region_file_name(ctx, path, page._region_task_id)
        for path in page._region_files if path not in existing
    ]
    by_name = {
        _region_file_name(ctx, path, page._region_task_id): path
        for path in page._region_files if path not in existing
    }
    if not names:
        QMessageBox.information(
            ctx.host, "Region", "Every selected image file is already in this Region."
        )
        return
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Add Sample", names, [], "Filter files…",
    )
    if not chosen:
        return
    samples = region.setdefault("samples", {})
    for name in chosen:
        path = by_name.get(name)
        if not path:
            continue
        samples[path] = {"visible": True, "sub_regions": []}
    _region_rebuild_region_tree(ctx, page)
    _region_mark_dirty(ctx, page)


def _region_arm_subregion(ctx, page, region_id, sample_path) -> None:
    page._region_selected_region_id = str(region_id)
    page._region_selected_sample = str(sample_path)
    page._region_selected_sub_id = None
    page._region_selected_subs = []
    _region_set_region_tool(ctx, page, "region_in")


def _region_next_sub_name(sample) -> str:
    names = {str(sub.get("name") or "") for sub in sample.get("sub_regions") or []}
    index = 1
    while f"Sub-region {index}" in names:
        index += 1
    return f"Sub-region {index}"


def _region_copy(ctx, page, key) -> None:
    kind = key[0]
    region = _region_region_by_id(page, key[1])
    if region is None:
        return
    if kind == "region":
        page._region_clipboard = {
            "kind": "region",
            "region": copy.deepcopy(region),
        }
    elif kind == "sample":
        sample = (region.get("samples") or {}).get(key[2])
        if sample is not None:
            page._region_clipboard = {
                "kind": "sample",
                "sample": str(key[2]),
                "data": copy.deepcopy(sample),
            }
    elif kind == "sub":
        sub = _region_sub_by_id(region, key[2], key[3])
        if sub is not None:
            page._region_clipboard = {
                "kind": "sub", "sub": copy.deepcopy(sub),
            }


def _region_copy_subs(page, keys) -> None:
    """Copy several selected Sub-regions as one clipboard branch."""
    subs = []
    for key in keys:
        region = _region_region_by_id(page, key[1])
        if region is None:
            continue
        sub = _region_sub_by_id(region, key[2], key[3])
        if sub is not None:
            subs.append(copy.deepcopy(sub))
    if subs:
        page._region_clipboard = {"kind": "subs", "subs": subs}


def _region_paste(ctx, page, key) -> None:
    clip = page._region_clipboard
    if not clip:
        return
    if clip.get("kind") == "region":
        source = clip.get("region") or {}
        region = copy.deepcopy(source)
        region["id"] = uuid4().hex
        region["name"] = _region_free_region_name(
            ctx, page, f"{source.get('name', 'Region')} copy"
        )
        for sample in (region.get("samples") or {}).values():
            for sub in sample.get("sub_regions") or []:
                sub["id"] = uuid4().hex
        page._region_store.setdefault("regions", []).append(region)
        page._region_selected_region_id = region["id"]
        page._region_selected_sample = None
        page._region_selected_sub_id = None
        _region_rebuild_region_tree(ctx, page)
        _region_mark_dirty(ctx, page)
        _region_render_overlays(ctx, page)
        return
    if clip.get("kind") == "sample":
        if not key or key[0] not in ("region", "sample", "sub"):
            return
        region = _region_region_by_id(page, key[1])
        if region is None:
            return
        sample_path = str(clip.get("sample") or "")
        if not sample_path:
            return
        samples = region.get("samples")
        if samples is None:
            samples = {}
            region["samples"] = samples
        if sample_path in samples:
            name = _region_file_name(
                ctx, sample_path, page._region_task_id
            )
            QMessageBox.information(
                ctx.host, "Paste Branch",
                f"Region '{region.get('name') or 'Region'}' already contains "
                f"Sample '{name}'.",
            )
            return
        if sample_path not in page._region_files:
            QMessageBox.information(
                ctx.host, "Paste Branch",
                "The file of this Sample is not one of this Region module's "
                "image files.",
            )
            return
        sample = copy.deepcopy(clip.get("data") or {})
        sub_regions = list(sample.get("sub_regions") or [])
        for sub in sub_regions:
            sub["id"] = uuid4().hex
        sample["sub_regions"] = sub_regions
        samples[sample_path] = sample
        page._region_selected_region_id = str(key[1])
        page._region_selected_sample = sample_path
        page._region_selected_sub_id = None
        _region_rebuild_region_tree(ctx, page)
        _region_mark_dirty(ctx, page)
        _region_render_overlays(ctx, page)
        return
    if clip.get("kind") in ("sub", "subs"):
        if not key or key[0] not in ("sample", "sub"):
            return
        region = _region_region_by_id(page, key[1])
        if region is None:
            return
        sample = (region.get("samples") or {}).get(key[2])
        if sample is None:
            return
        sources = (
            [clip.get("sub") or {}]
            if clip.get("kind") == "sub"
            else list(clip.get("subs") or [])
        )
        pasted = []
        for source in sources:
            sub = copy.deepcopy(source or {})
            sub["id"] = uuid4().hex
            sub["name"] = _region_next_sub_name(sample)
            sample.setdefault("sub_regions", []).append(sub)
            pasted.append(sub)
        if not pasted:
            return
        page._region_selected_region_id = key[1]
        page._region_selected_sample = key[2]
        page._region_selected_sub_id = pasted[-1]["id"]
        _region_rebuild_region_tree(ctx, page)
        _region_mark_dirty(ctx, page)
        _region_render_overlays(ctx, page)


def _region_delete(ctx, page, key) -> None:
    kind, region_id, sample_path, sub_id = key
    region = _region_region_by_id(page, region_id)
    if region is None:
        return
    if kind == "region":
        channels = page._region_params.get("channels") or {}
        channel_entry = channels.pop(str(region_id), None)
        if isinstance(channel_entry, dict):
            page._region_channel_orphans.extend(
                (channel_entry.get("files") or {}).values()
            )
        page._region_store["regions"] = [
            item for item in page._region_store.get("regions") or []
            if str(item.get("id")) != str(region_id)
        ]
        page._region_selected_region_id = None
        page._region_selected_sample = None
        page._region_selected_sub_id = None
    elif kind == "sample":
        (region.get("samples") or {}).pop(sample_path, None)
        entry = (page._region_params.get("channels") or {}).get(
            str(region_id)
        )
        if isinstance(entry, dict):
            record = (entry.get("files") or {}).pop(
                str(sample_path), None
            )
            if isinstance(record, dict):
                page._region_channel_orphans.append(record)
        page._region_selected_sample = None
        page._region_selected_sub_id = None
    elif kind == "sub":
        sample = (region.get("samples") or {}).get(sample_path) or {}
        sample["sub_regions"] = [
            item for item in sample.get("sub_regions") or []
            if str(item.get("id")) != str(sub_id)
        ]
        page._region_selected_sub_id = None
    page._region_selected_subs = []
    _region_rebuild_region_tree(ctx, page)
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


def _region_delete_subs(ctx, page, keys) -> None:
    """Delete several selected Sub-regions in one step."""
    groups = {}
    for key in keys:
        groups.setdefault((str(key[1]), str(key[2])), set()).add(
            str(key[3])
        )
    for (region_id, sample_path), ids in groups.items():
        region = _region_region_by_id(page, region_id)
        if region is None:
            continue
        sample = (region.get("samples") or {}).get(sample_path)
        if sample is None:
            continue
        sample["sub_regions"] = [
            sub for sub in sample.get("sub_regions") or []
            if str(sub.get("id")) not in ids
        ]
    page._region_selected_sub_id = None
    page._region_selected_subs = []
    _region_rebuild_region_tree(ctx, page)
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


# ---------------------------------------------------------------------------
# Region drawing
# ---------------------------------------------------------------------------

def _region_clear_edit_rois(page) -> None:
    for entry in list(getattr(page, "_region_edit_rois", []) or []):
        try:
            page._region_viewbox.removeItem(entry["roi"])
        except Exception:  # noqa: BLE001
            pass
    page._region_edit_rois = []


def _region_clear_overlays(page) -> None:
    for item in page._region_overlay_items:
        try:
            page._region_viewbox.removeItem(item)
        except Exception:  # noqa: BLE001
            pass
    page._region_overlay_items = []
    _region_clear_edit_rois(page)


def _region_on_image_click(ctx, page, event) -> None:
    """Select the Sub-region under a left click in the image."""
    if event.button() != Qt.MouseButton.LeftButton:
        return
    if page._region_gate_tool or page._region_region_tool:
        return
    for roi in getattr(page, "_region_gate_rois", {}).values():
        try:
            if roi.sceneBoundingRect().contains(event.scenePos()):
                return
        except RuntimeError:
            continue
    viewbox = page._region_viewbox
    if not viewbox.sceneBoundingRect().contains(event.scenePos()):
        return
    point = viewbox.mapSceneToView(event.scenePos())
    path = page._region_preview.get("file")
    if not path:
        return
    import numpy as np

    x = np.asarray([float(point.x())], dtype="float64")
    y = np.asarray([float(point.y())], dtype="float64")
    for region in page._region_store.get("regions") or []:
        if not region.get("visible", True):
            continue
        sample = (region.get("samples") or {}).get(path)
        if sample is None or not sample.get("visible", True):
            continue
        for sub in sample.get("sub_regions") or []:
            if not sub.get("visible", True):
                continue
            part = [{
                "in": sub.get("in") or [],
                "out": sub.get("out") or [],
            }]
            if not part[0]["in"]:
                continue
            if not bool(region_parts_mask(part, x, y)[0]):
                continue
            page._region_selected_region_id = str(region.get("id"))
            page._region_selected_sample = str(path)
            page._region_selected_sub_id = str(sub.get("id"))
            page._region_selected_subs = [{
                "region": str(region.get("id")),
                "sample": str(path),
                "sub": str(sub.get("id")),
            }]
            _region_rebuild_region_tree(ctx, page)
            _region_style_panel_from_selection(ctx, page)
            item = page._region_tree.currentItem()
            if item is not None:
                page._region_tree.scrollToItem(item)
            _region_render_overlays(ctx, page)
            return


def _region_render_overlays(ctx, page) -> None:
    if not hasattr(page, "_region_viewbox"):
        return
    _region_clear_overlays(page)
    path = page._region_preview.get("file")
    _region_prune_edit_boundary(page, path)
    if not path:
        return
    regions = page._region_store.get("regions") or []
    total = max(1, len(regions))
    for index, region in enumerate(regions):
        if not region.get("visible", True):
            continue
        sample = (region.get("samples") or {}).get(path)
        if sample is None or not sample.get("visible", True):
            continue
        style = _region_style_for(region)
        selected_subs = {
            str(entry.get("sub"))
            for entry in (getattr(page, "_region_selected_subs", None) or [])
            if str(entry.get("sample")) == str(path)
            and str(entry.get("region")) == str(region.get("id"))
        }
        if (
            page._region_selected_sub_id
            and page._region_selected_sample == path
            and str(region.get("id"))
            == str(page._region_selected_region_id or "")
        ):
            selected_subs.add(str(page._region_selected_sub_id))
        for sub in sample.get("sub_regions") or []:
            if not sub.get("visible", True):
                continue
            rings = list(sub.get("in") or []) + list(sub.get("out") or [])
            if not any(len(ring) >= 3 for ring in rings):
                continue
            is_selected = str(sub.get("id")) in selected_subs
            # One path per sub-region: even-odd makes every nested ring a
            # hole/island correctly, including a new in inside an old out.
            path_item = QPainterPath()
            path_item.setFillRule(Qt.FillRule.OddEvenFill)
            for ring in rings:
                if len(ring) < 3:
                    continue
                path_item.addPolygon(QPolygonF([
                    QPointF(float(x), float(y)) for x, y in ring
                ]))
                path_item.closeSubpath()
            item = QGraphicsPathItem(path_item)
            fill = style["fill"]
            edge = style["edge"]
            opacity = max(0.0, min(1.0, style["fill_opacity"]))
            item.setBrush(
                QBrush(QColor(int(fill[0]), int(fill[1]), int(fill[2]),
                              int(round(opacity * 255))))
            )
            # Use the chosen width exactly; selecting a Region must not
            # silently clamp widths 0–2 to 3 px.
            width = max(0.0, float(style["edge_width"]))
            if is_selected:
                # Selection only highlights the boundary; anchors are built
                # by Edit boundary, so a complex Sub-region stays cheap.
                item.setPen(QPen(
                    QColor("#ffd166"), max(2.5, width + 1.5)
                ))
            elif width > 0:
                item.setPen(QPen(
                    QColor(int(edge[0]), int(edge[1]), int(edge[2])),
                    width,
                ))
            else:
                item.setPen(QPen(Qt.PenStyle.NoPen))
            item.setZValue(
                10 + (total - index) * 0.01 + (0.5 if is_selected else 0.0)
            )
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            page._region_viewbox.addItem(item, ignoreBounds=True)
            page._region_overlay_items.append(item)

    sub = _region_editing_sub(page, path)
    if sub is not None:
        region = _region_selected_region(page)
        if region is not None:
            for kind in ("in", "out"):
                for ring_index, ring in enumerate(sub.get(kind) or []):
                    if len(ring) < 3:
                        continue
                    try:
                        pen = pg.mkPen(
                            "#ff9f43" if kind == "out" else "#ffffff",
                            width=2,
                            style=(
                                Qt.PenStyle.DashLine if kind == "out"
                                else Qt.PenStyle.SolidLine
                            ),
                        )
                        roi = _GatePolygonROI(
                            [tuple(point) for point in ring],
                            closed=True, pen=pen,
                            handlePen=pg.mkPen(
                                "#ff9f43" if kind == "out" else "#ffffff",
                                width=1,
                            ),
                        )
                        roi.setZValue(40)
                        page._region_viewbox.addItem(
                            roi, ignoreBounds=True
                        )
                        signal = getattr(
                            roi, "sigRegionChangeFinished",
                            roi.sigRegionChanged,
                        )
                        signal.connect(
                            lambda r=roi, reg=region, s=sub, k=kind,
                            i=ring_index: _region_on_subregion_ring_changed(
                                ctx, page, reg, s, k, i, r
                            )
                        )
                        page._region_edit_rois.append({
                            "roi": roi, "kind": kind, "index": ring_index,
                        })
                    except Exception:  # noqa: BLE001
                        continue


def _region_on_subregion_ring_changed(
        ctx, page, region, sub, kind, index, roi) -> None:
    if page._region_updating:
        return
    points = _region_roi_geometry("polygon", roi).get("points") or []
    if len(points) < 3:
        return
    rings = list(sub.get(kind) or [])
    if not (0 <= index < len(rings)):
        return
    rings[index] = points
    sub[kind] = rings
    part = _region_retessellate(
        page, sub.get("in") or [], sub.get("out") or []
    )
    if part is None:
        QMessageBox.information(
            ctx.host, "Region",
            "This edit removed the whole Sub-region; it will be deleted.",
        )
        sample = (region.get("samples") or {}).get(
            page._region_selected_sample
        )
        if sample is not None:
            sample["sub_regions"] = [
                item for item in sample.get("sub_regions") or []
                if item is not sub
            ]
        page._region_selected_sub_id = None
    else:
        sub["kind"] = "region"
        sub["in"] = part["in"]
        sub["out"] = part["out"]
    _region_rebuild_region_tree(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)

def _region_style_panel_from_selection(ctx, page) -> None:
    region = _region_selected_region(page)
    widgets = page._region_style_widgets
    enabled = region is not None and not page._region_locked
    for widget in widgets:
        widget.setEnabled(enabled)
    if region is None:
        return
    style = _region_style_for(region)
    page._region_updating = True
    try:
        page._region_opacity.setValue(int(round(style["fill_opacity"] * 100)))
        page._region_edge_width.setValue(int(round(style["edge_width"])))
        fill = style["fill"]
        edge = style["edge"]
        page._region_fill_btn.setStyleSheet(
            f"background: rgb({fill[0]},{fill[1]},{fill[2]});"
            "border: 1px solid #888;"
        )
        page._region_edge_btn.setStyleSheet(
            f"background: rgb({edge[0]},{edge[1]},{edge[2]});"
            "border: 1px solid #888;"
        )
    finally:
        page._region_updating = False


def _region_set_style(ctx, page, key, value) -> None:
    if page._region_updating:
        return
    region = _region_selected_region(page)
    if region is None or page._region_locked:
        return
    style = _region_style_for(region)
    style[key] = value
    region["style"] = style
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


def _region_pick_style(ctx, page, key) -> None:
    region = _region_selected_region(page)
    if region is None or page._region_locked:
        return
    style = _region_style_for(region)
    color = QColor(*style[key])
    chosen = QColorDialog.getColor(color, ctx.host, "Region color")
    if not chosen.isValid():
        return
    style[key] = [chosen.red(), chosen.green(), chosen.blue()]
    if key == "fill":
        edge = chosen.darker(130)
        style["edge"] = [edge.red(), edge.green(), edge.blue()]
    region["style"] = style
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


def _region_reset_style(ctx, page) -> None:
    region = _region_selected_region(page)
    if region is None or page._region_locked:
        return
    index = next(
        (
            i for i, item in enumerate(page._region_store.get("regions") or [])
            if item is region
        ),
        0,
    )
    region["style"] = _region_default_style(index)
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)


# ---------------------------------------------------------------------------
# Detection panel and background detection
# ---------------------------------------------------------------------------

def _region_detect_columns(ctx, page) -> list:
    files = list(page._region_detect_files)
    if not files:
        return []
    common = None
    for path in files:
        columns = set(
            _data_columns._columns_for(ctx, path, page._region_task_id)
        )
        common = columns if common is None else common & columns
    return sorted((common or set()) - {"x", "y"})


def _region_rebuild_detect_panel(ctx, page) -> None:
    layout = getattr(page, "_region_detect_layout", None)
    if layout is None:
        return
    scroll = getattr(page, "_region_detect_scroll", None)
    keep_scroll = (
        scroll.verticalScrollBar().value() if scroll is not None else None
    )
    _widgets_panels._clear_layout(ctx, layout)
    detect = page._region_detect
    enabled = not page._region_locked and not _region_detect_running(page)
    _region_set_status(
        page, getattr(page, "_region_detect_message", "")
    )

    row = QHBoxLayout()
    row.setSpacing(6)
    files_btn = QPushButton(
        f"All {len(page._region_files)} files"
        if len(page._region_detect_files) == len(page._region_files)
        else f"{len(page._region_detect_files)} / "
             f"{len(page._region_files)} files"
    )
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(enabled)
    files_btn.clicked.connect(
        lambda _=False: _region_choose_detect_files(ctx, page)
    )
    row.addWidget(files_btn)
    method = _NoWheelComboBox()
    method.addItem("Signal Expansion", "signal")
    method.addItem("SAM 3 (external)", "sam")
    method.setCurrentIndex(
        max(0, method.findData(detect.get("method", "signal")))
    )
    method.setEnabled(enabled)
    row.addWidget(method, 1)
    layout.addLayout(row)
    layout.addWidget(_widgets_panels._param_hint(
        ctx,
        "Signal Expansion thresholds this channel; SAM 3 runs the external "
        "model configured in Extension Manager.",
    ))

    channel_row = QHBoxLayout()
    channel_row.setSpacing(6)
    channel_row.addWidget(QLabel("Channel"))
    columns = _region_detect_columns(ctx, page)
    channel = _NoWheelComboBox()
    for column in columns:
        channel.addItem(str(column))
    current = detect.get("channel")
    if current not in columns:
        current = columns[0] if columns else None
        detect["channel"] = current
    if current in columns:
        channel.setCurrentIndex(columns.index(current))
    channel.setEnabled(enabled and bool(columns))
    channel_row.addWidget(channel, 1)
    layout.addLayout(channel_row)
    layout.addWidget(_widgets_panels._param_hint(
        ctx, "Image channel whose intensity is used to find Regions."
    ))

    is_sam = detect.get("method") == "sam"
    signal_host = QWidget()
    sam_host = QWidget()
    if is_sam:
        _region_build_sam_params(ctx, page, sam_host, enabled)
        layout.addWidget(sam_host)
    else:
        _region_build_signal_params(ctx, page, signal_host, enabled)
        layout.addWidget(signal_host)

    buttons = QHBoxLayout()
    buttons.setSpacing(6)
    detect_btn = QPushButton("Detect Region")
    detect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    detect_btn.setEnabled(enabled and bool(columns))
    detect_btn.clicked.connect(lambda _=False: _region_start_detect(ctx, page))
    buttons.addWidget(detect_btn)
    if is_sam:
        manage = QPushButton("Manage SAM…")
        manage.setCursor(Qt.CursorShape.PointingHandCursor)
        manage.clicked.connect(
            lambda _=False: _region_manage_sam(ctx, page)
        )
        buttons.addWidget(manage)
    buttons.addStretch()
    layout.addLayout(buttons)

    def on_method(_index=0):
        detect["method"] = method.currentData() or "signal"
        _region_mark_dirty(ctx, page)
        _region_rebuild_detect_panel(ctx, page)

    def on_channel(_index=0):
        detect["channel"] = channel.currentText() or None
        _region_mark_dirty(ctx, page)

    method.currentIndexChanged.connect(on_method)
    channel.currentIndexChanged.connect(on_channel)
    page._region_detect_files_btn = files_btn
    page._region_method_combo = method
    page._region_channel_combo = channel
    page._region_detect_btn = detect_btn
    if scroll is not None and keep_scroll is not None:
        bar = scroll.verticalScrollBar()

        def restore_scroll(_bar=bar, _pos=keep_scroll):
            try:
                _bar.setValue(min(_pos, _bar.maximum()))
            except RuntimeError:
                pass

        restore_scroll()
        QTimer.singleShot(0, restore_scroll)


def _region_build_signal_params(ctx, page, host, enabled) -> None:
    detect = page._region_detect.setdefault("signal", {})
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(2)
    grid.setColumnStretch(1, 1)

    mode = _NoWheelComboBox()
    mode.addItem("Auto (Otsu)", "otsu")
    mode.addItem("Percentile", "percentile")
    mode.addItem("Manual", "manual")
    current_mode = detect.get("threshold_mode")
    if current_mode is None:
        current_mode = (
            "manual" if not detect.get("auto_threshold", True) else "otsu"
        )
    mode.setCurrentIndex(max(0, mode.findData(current_mode)))
    mode.setEnabled(enabled)
    threshold = _NoWheelDoubleSpinBox()
    threshold.setDecimals(3)
    threshold.setRange(-1e12, 1e12)
    threshold.setValue(float(detect.get("threshold") or 0.0))
    threshold.setEnabled(enabled and current_mode == "manual")
    percentile = _NoWheelDoubleSpinBox()
    percentile.setDecimals(1)
    percentile.setRange(0.0, 100.0)
    percentile.setValue(float(detect.get("percentile", 30.0)))
    percentile.setSuffix(" %")
    percentile.setEnabled(enabled and current_mode == "percentile")
    smooth = _NoWheelDoubleSpinBox()
    smooth.setRange(0.0, 20.0)
    smooth.setValue(float(detect.get("smooth") or 0.0))
    smooth.setEnabled(enabled)
    expand = _NoWheelSpinBox()
    expand.setRange(0, 100)
    expand.setValue(int(detect.get("expand") or 0))
    expand.setEnabled(enabled)
    fill = QCheckBox("Fill holes")
    fill.setChecked(bool(detect.get("fill_holes", True)))
    fill.setEnabled(enabled)
    fill_area = _NoWheelSpinBox()
    fill_area.setRange(0, 10_000_000)
    fill_area.setValue(int(detect.get("fill_max_area") or 0))
    fill_area.setSuffix(" px")
    fill_area.setEnabled(enabled and fill.isChecked())
    min_area = _NoWheelSpinBox()
    min_area.setRange(0, 10_000_000)
    min_area.setValue(int(detect.get("min_area") or 0))
    min_area.setEnabled(enabled)
    tolerance = _NoWheelDoubleSpinBox()
    tolerance.setRange(0.0, 100.0)
    tolerance.setDecimals(2)
    tolerance.setValue(float(detect.get("tolerance") or 0.0))
    tolerance.setEnabled(enabled)

    hint = lambda text: _widgets_panels._param_hint(ctx, text)
    row = 0

    def add_param(label, widget, text):
        nonlocal row
        if label:
            grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(widget, row, 1 if label else 0, 1, 1 if label else 2)
        row += 1
        grid.addWidget(hint(text), row, 0, 1, 2)
        row += 1

    add_param("Threshold mode", mode,
              "Auto uses Otsu on each file separately; Percentile uses each "
              "file's own percentile; Manual uses one fixed value for every "
              "file.")
    add_param("Threshold", threshold,
              "Only used in Manual mode. Pixels above this value become "
              "signal.")
    add_param("Percentile", percentile,
              "Only used in Percentile mode. 30% makes the 30th percentile "
              "of each file the cut-off, so about 70% of that file's pixels "
              "are above it.")
    add_param("Smooth", smooth,
              "Gaussian blur radius in pixels before thresholding. Larger "
              "values join noisy signal; 0 disables smoothing.")
    add_param("Expand", expand,
              "Grow every detected object by this many pixels to connect "
              "nearby pieces. 0 keeps the thresholded shape.")
    add_param(None, fill,
              "Fill enclosed gaps inside a detected object.")
    add_param("Max hole area", fill_area,
              "0 fills every enclosed hole. A positive value only fills "
              "holes up to this many pixels, leaving larger lumens open.")
    add_param("Min area", min_area,
              "Objects smaller than this many pixels are discarded as "
              "debris.")
    add_param("Tolerance", tolerance,
              "Polygon simplification in pixels. Larger values give fewer "
              "vertices and smaller sub-regions.")

    def store(*_args):
        selected_mode = mode.currentData() or "otsu"
        detect["threshold_mode"] = selected_mode
        detect["threshold"] = float(threshold.value())
        detect["percentile"] = float(percentile.value())
        detect["smooth"] = float(smooth.value())
        detect["expand"] = int(expand.value())
        detect["fill_holes"] = bool(fill.isChecked())
        detect["fill_max_area"] = int(fill_area.value())
        detect["min_area"] = int(min_area.value())
        detect["tolerance"] = float(tolerance.value())
        threshold.setEnabled(enabled and selected_mode == "manual")
        percentile.setEnabled(enabled and selected_mode == "percentile")
        fill_area.setEnabled(enabled and fill.isChecked())
        _region_mark_dirty(ctx, page)

    for widget in (
        mode, threshold, percentile, smooth, expand, fill, fill_area,
        min_area, tolerance,
    ):
        signal = getattr(widget, "valueChanged", None)
        if signal is not None:
            signal.connect(store)
        signal = getattr(widget, "currentIndexChanged", None)
        if signal is not None:
            signal.connect(store)
        toggle = getattr(widget, "toggled", None)
        if toggle is not None:
            toggle.connect(store)


def _region_build_sam_params(ctx, page, host, enabled) -> None:
    detect = page._region_detect.setdefault("sam", {})
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(4)
    specs = [
        ("Points / side", "points_per_side", 1, 200, 1,
         "Grid of prompt points per image side. Higher values find smaller "
         "objects but run slower."),
        ("Points / batch", "points_per_batch", 1, 256, 1,
         "Prompt points processed at once. Lower this if GPU memory is "
         "tight."),
        ("IoU threshold", "pred_iou", 0.0, 1.0, 0.01,
         "Minimum predicted mask quality. Higher values keep fewer, cleaner "
         "masks."),
        ("Stability", "stability", 0.0, 1.0, 0.01,
         "Minimum mask stability. Higher values keep fewer, more confident "
         "masks."),
        ("Crop layers", "crop_layers", 0, 10, 1,
         "Extra image crops SAM analyses. More layers find small objects at "
         "extra cost."),
        ("Min area", "min_area", 0, 10_000_000, 1,
         "Masks smaller than this many pixels are discarded."),
        ("Tolerance", "tolerance", 0.0, 100.0, 0.01,
         "Polygon simplification in pixels. Larger values give fewer "
         "vertices and smaller sub-regions."),
    ]
    widgets = {}
    row = 0
    for label, key, low, high, step, text in specs:
        grid.addWidget(QLabel(label), row, 0)
        if isinstance(step, int):
            widget = _NoWheelSpinBox()
            widget.setRange(int(low), int(high))
            widget.setValue(int(detect.get(key) or low))
        else:
            widget = _NoWheelDoubleSpinBox()
            widget.setDecimals(3)
            widget.setRange(float(low), float(high))
            widget.setSingleStep(float(step))
            widget.setValue(float(detect.get(key) or low))
        widget.setEnabled(enabled)
        grid.addWidget(widget, row, 1)
        widgets[key] = widget
        row += 1
        grid.addWidget(
            _widgets_panels._param_hint(ctx, text), row, 0, 1, 2
        )
        row += 1

    ready = ctx._ext_ready("sam3")
    hint = QLabel(
        "SAM 3 is configured. It runs in its external environment."
        if ready else
        "SAM 3 is not configured. Use Manage SAM… to install its "
        "environment and model."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet(
        "color: #8a94a6; font-size: 8pt;"
        if ready else "color: #b58900; font-size: 8pt;"
    )
    grid.addWidget(hint, row, 0, 1, 2)

    def store(*_args):
        for key, widget in widgets.items():
            value = widget.value()
            detect[key] = float(value) if isinstance(widget, QDoubleSpinBox) else int(value)
        _region_mark_dirty(ctx, page)

    for widget in widgets.values():
        widget.valueChanged.connect(store)


def _region_choose_detect_files(ctx, page) -> None:
    names = [
        _region_file_name(ctx, path, page._region_task_id)
        for path in page._region_files
    ]
    by_name = {
        _region_file_name(ctx, path, page._region_task_id): path
        for path in page._region_files
    }
    current = [
        _region_file_name(ctx, path, page._region_task_id)
        for path in page._region_detect_files
    ]
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Files for detection", names, current, "Filter files…",
    )
    if chosen is None:
        return
    page._region_detect_files = [
        by_name[name] for name in chosen if by_name.get(name)
    ]
    page._region_detect["files"] = list(page._region_detect_files)
    _region_mark_dirty(ctx, page)
    _region_rebuild_detect_panel(ctx, page)


def _region_manage_sam(ctx, page) -> None:
    try:
        ctx._open_extension_manager(select="sam3")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(ctx.host, "SAM 3", str(exc))
    _region_rebuild_detect_panel(ctx, page)


def _region_detect_running(page) -> bool:
    return bool(getattr(page, "_region_detect_state", None))


def _region_detect_status_text(page) -> str:
    return (
        "Detection runs inside the selected gate and each run creates a new "
        "Region."
    )


def _region_set_status(page, text: str) -> None:
    page._region_detect_message = str(text or "")
    label = getattr(page, "_region_detect_status", None)
    if label is not None:
        label.setText(
            page._region_detect_message or _region_detect_status_text(page)
        )


def _region_store_log(page) -> None:
    """Keep the detection log in params (and the unsaved snapshot)."""
    lines = list(getattr(page, "_region_log_lines", []))
    params = getattr(page, "_region_params", None)
    if params is not None:
        params["log"] = lines
    snapshot = getattr(page, "_region_snapshot", None)
    if isinstance(snapshot, dict):
        snapshot["log"] = lines


def _region_log(ctx, page, text: str, timestamp: str | None = None) -> None:
    """Append a timestamped, user-readable line to the Region log."""
    ts = timestamp or datetime.now().strftime("%H:%M:%S.%f")[:-3]
    lines = getattr(page, "_region_log_lines", None)
    if lines is None:
        lines = []
        page._region_log_lines = lines
    for line in str(text or "").splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _REGION_LOG_LIMIT:
        del lines[:-_REGION_LOG_LIMIT]
    _region_store_log(page)
    box = getattr(page, "_region_log", None)
    if box is not None:
        for line in str(text or "").splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")
        bar = box.verticalScrollBar()
        bar.setValue(bar.maximum())


def _region_start_detect(ctx, page) -> None:
    if page._region_locked or _region_detect_running(page):
        return
    detect = page._region_detect
    files = list(page._region_detect_files)
    channel = detect.get("channel")
    if not files:
        QMessageBox.information(
            ctx.host, "Detect Region", "Select at least one file for detection."
        )
        return
    if not channel:
        QMessageBox.information(
            ctx.host, "Detect Region", "Select an image channel first."
        )
        return
    gates = copy.deepcopy(page._region_store.get("gates") or [])
    per_file = copy.deepcopy(page._region_store.get("per_file") or {})
    method = detect.get("method", "signal")
    if method == "sam":
        if not ctx._ext_ready("sam3"):
            QMessageBox.information(
                ctx.host, "Detect Region",
                "SAM 3 is not configured. Use Manage SAM… to set it up.",
            )
            return
        python = ctx._ext_python("sam3")
        runner = ctx._ext_runner_path("sam3")
        from smage.extensions_registry import _extension_def
        entry = _extension_def("sam3") or {}
        model = str(entry.get("model") or "facebook/sam3")
        job = {
            "ctx": ctx,
            "task_id": page._region_task_id,
            "method": "sam",
            "files": files,
            "channel": channel,
            "gates": gates,
            "per_file": per_file,
            "gate_id": page._region_selected_gate_id,
            "sam": copy.deepcopy(detect.get("sam") or {}),
            "python": python,
            "runner": str(runner),
            "model": model,
        }
    else:
        job = {
            "ctx": ctx,
            "task_id": page._region_task_id,
            "method": "signal",
            "files": files,
            "channel": channel,
            "gates": gates,
            "per_file": per_file,
            "gate_id": page._region_selected_gate_id,
            "signal": copy.deepcopy(detect.get("signal") or {}),
        }
    page._region_detect_token += 1
    token = page._region_detect_token
    page._region_detect_state = {
        "token": token,
        "files": files,
        "results": {},
        "errors": {},
        "total": len(files),
        "done": 0,
    }
    _region_set_status(page, "Detection started; see the log below.")
    if method == "sam":
        method_label = "SAM 3"
    else:
        params = detect.get("signal") or {}
        mode = params.get("threshold_mode")
        if mode is None:
            mode = (
                "manual" if not params.get("auto_threshold", True)
                else "otsu"
            )
        if mode == "manual":
            cut = f"manual threshold {float(params.get('threshold') or 0.0):g}"
        elif mode == "percentile":
            cut = (
                f"P{float(params.get('percentile') or 0.0):g} "
                "threshold per file"
            )
        else:
            cut = "Otsu threshold per file"
        method_label = f"Signal Expansion, {cut}"
    _region_log(
        ctx, page,
        f"===== Region detection — {method_label}, {len(files)} file(s), "
        f"channel '{channel}' =====",
    )
    page._region_detect_worker.request(token, job)
    page._region_detect_timer.start()
    _region_rebuild_detect_panel(ctx, page)


def _region_poll_detect(ctx, page) -> None:
    state = getattr(page, "_region_detect_state", None)
    if not state:
        page._region_detect_timer.stop()
        return
    messages = page._region_detect_worker.poll()
    finished = False
    for message in messages:
        if not message:
            continue
        kind = message[0]
        token = message[1] if len(message) > 1 else None
        if token != state["token"]:
            continue
        if kind == "progress":
            stamp = message[5] if len(message) > 5 else None
            _, _token, index, total, _text = message[:5]
            files = state.get("files") or []
            path = files[index] if 0 <= index < len(files) else None
            name = (
                _region_file_name(ctx, path, page._region_task_id)
                if path else "file"
            )
            _region_log(
                ctx, page, f"→ Detecting {name} ({index + 1}/{total})…",
                timestamp=stamp,
            )
        elif kind == "step":
            _, _token, path, stamp, text = message
            name = _region_file_name(
                ctx, path, page._region_task_id
            )
            _region_log(
                ctx, page, f"{name}: {text}", timestamp=stamp
            )
        elif kind == "file":
            _, _token, path, parts, error = message[:5]
            used_threshold = (
                message[5] if len(message) > 5 else None
            )
            state["results"][path] = parts or []
            if error:
                state["errors"][path] = error
            state["done"] += 1
            name = _region_file_name(
                ctx, path, page._region_task_id
            )
            if error:
                _region_log(ctx, page, f"✗ {name}: {error}")
            else:
                detail = f"{len(parts or [])} sub-region(s)"
                if used_threshold is not None:
                    detail += (
                        f", threshold {float(used_threshold):.6g}"
                    )
                _region_log(
                    ctx, page, f"✓ {name}: {detail}",
                )
        elif kind == "done":
            finished = True
    if finished:
        page._region_detect_timer.stop()
        _region_finish_detect(ctx, page)


def _region_finish_detect(ctx, page) -> None:
    state = getattr(page, "_region_detect_state", None)
    page._region_detect_state = None
    if not state:
        _region_rebuild_detect_panel(ctx, page)
        return
    results = state.get("results") or {}
    errors = state.get("errors") or {}
    if not results or (
        errors and not any(bool(value) for value in results.values())
    ):
        detail = "; ".join(
            f"{_region_file_name(ctx, path, page._region_task_id)}: {message}"
            for path, message in list(errors.items())[:3]
        )
        _region_log(
            ctx, page,
            "Detection failed." + (f" {detail}" if detail else ""),
        )
        _region_set_status(
            page,
            "Detection failed." + (f"\n{detail}" if detail else ""),
        )
        _region_rebuild_detect_panel(ctx, page)
        return
    store = page._region_store
    index = len(store.get("regions") or [])
    region = {
        "id": uuid4().hex,
        "name": _region_free_region_name(
            ctx, page, _region_next_name(store)
        ),
        "visible": True,
        "style": _region_default_style(index),
        "samples": {},
    }
    total_polygons = 0
    for path in state.get("files") or []:
        parts = results.get(path) or []
        sub_regions = []
        for number, part in enumerate(parts, start=1):
            inside = part.get("in") or []
            if not inside:
                continue
            sub_regions.append({
                "id": uuid4().hex,
                "name": f"Sub-region {number}",
                "visible": True,
                "kind": "region",
                "in": inside,
                "out": list(part.get("out") or []),
            })
        total_polygons += len(sub_regions)
        region["samples"][str(path)] = {
            "visible": True,
            "sub_regions": sub_regions,
        }
    store.setdefault("regions", []).append(region)
    page._region_selected_region_id = region["id"]
    page._region_selected_sample = None
    page._region_selected_sub_id = None
    summary = (
        f"Created {region['name']} with {total_polygons} sub-region(s)."
        + (
            f" {len(errors)} file(s) could not be processed."
            if errors else ""
        )
    )
    _region_set_status(page, summary)
    _region_log(ctx, page, summary)
    _region_rebuild_region_tree(ctx, page)
    _region_style_panel_from_selection(ctx, page)
    _region_mark_dirty(ctx, page)
    _region_render_overlays(ctx, page)
    _region_rebuild_detect_panel(ctx, page)


class _RegionChannelWorker:
    """Background conversion of Regions to 0/1 mask channels."""

    def __init__(self) -> None:
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, token, ctx, task_id, region_id, jobs) -> None:
        self._jobs.put((
            token, ctx, task_id, region_id, list(jobs),
        ))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                token, ctx, task_id, region_id, jobs = self._jobs.get(
                    timeout=0.2
                )
            except queue.Empty:
                continue
            try:
                for job in jobs:
                    if self._stop.is_set():
                        return
                    started = time.perf_counter()
                    path = str(job.get("path"))
                    try:
                        from smage.core.region_detection import (
                            _region_parts_raster,
                        )
                        from smage.data import image_index as _image_index

                        ent = _image_index._ensure_image_index(ctx, path)
                        if ent is None or ent.get("manifest") is None:
                            raise RuntimeError(
                                "source image has no image index"
                            )
                        manifest = ent["manifest"]
                        xmin = int(round(float(manifest["xmin"])))
                        ymin = int(round(float(manifest["ymin"])))
                        width = int(round(
                            float(manifest["xmax"]) - float(manifest["xmin"])
                        )) + 1
                        height = int(round(
                            float(manifest["ymax"]) - float(manifest["ymin"])
                        )) + 1
                        mask = _region_parts_raster(
                            job.get("parts") or [],
                            width, height, xmin, ymin,
                        )
                        rel, pixels, signature = (
                            _data_regions._region_write_channel(
                                ctx, task_id, path, mask, region_id,
                            )
                        )
                        record = {
                            "path": rel,
                            "signature": signature,
                            "region_signature": job.get(
                                "region_signature"
                            ),
                            "pixels": int(pixels),
                        }
                        self._results.put((
                            "file", token, path, record, None,
                            time.perf_counter() - started,
                        ))
                    except Exception as exc:  # noqa: BLE001
                        self._results.put((
                            "file", token, path, None, str(exc),
                            time.perf_counter() - started,
                        ))
                self._results.put(("done", token))
            except Exception:  # noqa: BLE001
                self._results.put(("done", token))


class _RegionDetectWorker:
    """Background Signal Expansion / SAM detection for the Region module."""

    def __init__(self) -> None:
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, token, job) -> None:
        self._jobs.put((token, job))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                token, job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            files = list(job.get("files") or [])
            total = len(files)
            try:
                for index, path in enumerate(files):
                    if self._stop.is_set():
                        return
                    name = Path(str(path)).name
                    self._results.put((
                        "progress", token, index, total,
                        f"Detecting {name} ({index + 1}/{total})…",
                        datetime.now().strftime("%H:%M:%S.%f")[:-3],
                    ))

                    def report(step, seconds, detail="", _path=path):
                        stamp = datetime.now().strftime(
                            "%H:%M:%S.%f"
                        )[:-3]
                        text = str(step)
                        if detail:
                            text += f" ({detail})"
                        if seconds is not None:
                            text += f" — {float(seconds):.3f} s"
                        self._results.put((
                            "step", token, str(_path), stamp, text,
                        ))

                    started_file = time.perf_counter()
                    used_threshold = None
                    try:
                        parts, error, used_threshold = self._detect_file(
                            job, path, report
                        )
                    except Exception as exc:  # noqa: BLE001
                        parts, error = [], str(exc)
                    report(
                        "total", time.perf_counter() - started_file
                    )
                    self._results.put((
                        "file", token, str(path), parts, error,
                        used_threshold,
                    ))
                self._results.put(("done", token))
            except Exception as exc:  # noqa: BLE001
                self._results.put((
                    "progress", token, 0, max(1, total),
                    f"Detection failed: {exc}",
                ))
                self._results.put(("done", token))

    @staticmethod
    def _detect_file(job, path, report):
        from smage.data import regions as _regions

        ctx = job["ctx"]
        task_id = job["task_id"]
        channel = job["channel"]
        started = time.perf_counter()
        image, x0, y0 = _regions._region_image_grid(
            ctx, task_id, path, channel
        )
        if image is None:
            report(
                "read channel",
                time.perf_counter() - started,
                "channel or image index unavailable",
            )
            return [], f"channel '{channel}' or the image index is unavailable"
        report(
            "read channel", time.perf_counter() - started,
            f"{image.shape[1]}x{image.shape[0]} px",
        )
        started = time.perf_counter()
        gate = _regions._region_image_gate_mask(
            ctx, task_id, path, job.get("gates") or [],
            job.get("gate_id"), job.get("per_file") or {},
        )
        report(
            "detection gate", time.perf_counter() - started,
            "none" if gate is None else "clipped",
        )
        if job.get("method") == "sam":
            parts, error = _RegionDetectWorker._detect_sam(
                job, path, image, gate, x0, y0, report
            )
            return parts, error, None
        params = job.get("signal") or {}
        threshold_mode = params.get("threshold_mode")
        if threshold_mode is None:
            threshold_mode = (
                "manual" if not params.get("auto_threshold", True) else "otsu"
            )
        threshold = None
        percentile = None
        if threshold_mode == "manual":
            threshold = float(params.get("threshold") or 0.0)
        elif threshold_mode == "percentile":
            percentile = float(params.get("percentile") or 0.0)
        parts, used_threshold, _mask = signal_expansion(
            image, gate,
            threshold=threshold,
            percentile=percentile,
            smooth=float(params.get("smooth") or 0.0),
            expand=int(params.get("expand") or 0),
            fill_holes=bool(params.get("fill_holes", True)),
            fill_max_area=int(params.get("fill_max_area") or 0),
            min_area=int(params.get("min_area") or 0),
            tolerance=float(params.get("tolerance") or 0.0),
            x_origin=float(x0 or 0.0),
            y_origin=float(y0 or 0.0),
            progress=report,
        )
        return parts, None, float(used_threshold)

    @staticmethod
    def _detect_sam(job, path, image, gate, x0, y0, report):
        import numpy as np

        config = job.get("sam") or {}
        started = time.perf_counter()
        finite = np.isfinite(image)
        if not finite.any():
            return [], "no finite pixels in this channel"
        values = image[finite]
        low, high = np.percentile(values, [1.0, 99.0])
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            low = float(values.min())
            high = float(values.max())
        if high <= low:
            high = low + 1.0
        scaled = np.clip((image - low) / (high - low), 0.0, 1.0)
        scaled[~finite] = 0.0
        gray = (scaled * 255.0).astype("uint8")
        report(
            "sam prepare", time.perf_counter() - started,
            f"range {low:.4g}..{high:.4g}",
        )
        try:
            from PIL import Image
        except Exception:  # noqa: BLE001
            return [], "Pillow is missing in the Smage environment"
        with tempfile.TemporaryDirectory(prefix="smage-sam-") as folder:
            png = str(Path(folder) / "image.png")
            out = str(Path(folder) / "masks.npz")
            started = time.perf_counter()
            Image.fromarray(gray, mode="L").convert("RGB").save(png)
            report(
                "sam write image", time.perf_counter() - started
            )
            command = [
                str(job["python"]), str(job["runner"]), "run",
                "--input", png, "--output", out,
                "--model", str(job.get("model") or "facebook/sam3"),
                "--device", str(config.get("device") or "auto"),
                "--points-per-side", str(int(config.get("points_per_side") or 32)),
                "--points-per-batch", str(int(config.get("points_per_batch") or 64)),
                "--pred-iou", str(float(config.get("pred_iou") or 0.88)),
                "--stability", str(float(config.get("stability") or 0.95)),
                "--crop-layers", str(int(config.get("crop_layers") or 0)),
                "--min-area", str(int(config.get("min_area") or 0)),
            ]
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                return [], f"SAM could not start: {exc}"
            report(
                "sam inference", time.perf_counter() - started,
                "external process",
            )
            if completed.returncode != 0 or not Path(out).is_file():
                detail = (completed.stderr or completed.stdout or "").strip()
                return [], "SAM failed" + (f": {detail[-500:]}" if detail else "")
            started = time.perf_counter()
            try:
                # Closing the archive matters on Windows: the temporary
                # folder cannot be removed while masks.npz is still open.
                with np.load(out) as data:
                    count = int(data["count"])
                    height, width = (int(v) for v in data["shape"])
                    packed = np.array(data["masks"])
            except Exception as exc:  # noqa: BLE001
                return [], f"Could not read SAM output: {exc}"
            report(
                "sam read masks", time.perf_counter() - started,
                f"{count} mask(s)",
            )
            started = time.perf_counter()
            parts = []
            for index in range(count):
                if count == 0:
                    continue
                row = np.unpackbits(packed[index], axis=-1)
                mask = row[:width].astype(bool)
                if mask.size != height * width:
                    continue
                mask = mask.reshape(height, width)
                parts.extend(
                    sam_mask_polygons(
                        mask, gate,
                        min_area=int(config.get("min_area") or 0),
                        tolerance=float(config.get("tolerance") or 0.0),
                        x_origin=float(x0 or 0.0),
                        y_origin=float(y0 or 0.0),
                    )
                )
            report(
                "sam regions", time.perf_counter() - started,
                f"{len(parts)} region part(s)",
            )
            return parts, None


# ---------------------------------------------------------------------------
# Save / close / refresh / cleanup
# ---------------------------------------------------------------------------

def _region_save_now(ctx, page) -> None:
    if page._region_locked:
        return
    store = page._region_store
    store["files"] = list(page._region_files)
    _data_regions._region_store_save(ctx, page._region_task_def, store)
    params = page._region_task_def.setdefault("params", {})
    params["files"] = list(page._region_files)
    params["detect_files"] = list(page._region_detect_files)
    params["detect"] = copy.deepcopy(page._region_detect)
    params["detect"]["files"] = list(page._region_detect_files)
    params["preview_channel"] = page._region_preview.get("channel")
    params["preview_min"] = page._region_preview.get("image_min")
    params["preview_max"] = page._region_preview.get("image_max")
    params["preview_display"] = {
        key: page._region_preview.get(key) for key in _REGION_DISPLAY_KEYS
    }
    if ctx.project is not None:
        ctx.project.save()
    # The project now references only the new records; old masks can go.
    for record in page._region_channel_orphans:
        _region_delete_channel_record(ctx, record)
    page._region_channel_orphans = []
    page._region_snapshot = copy.deepcopy(params)
    page._region_store_snapshot = copy.deepcopy(store)
    page._region_dirty = False
    _region_update_save_btn(page)
    ctx.statusBar().showMessage("Region saved")
    ctx._module_saved(page)


def _region_close(ctx, page) -> None:
    if page._region_dirty:
        reply = QMessageBox.question(
            ctx.host, "Close Region",
            "There are unsaved changes.\nClose without saving? Unsaved "
            "changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._region_task_def["params"] = page._region_snapshot
        page._region_store = page._region_store_snapshot
    _region_cleanup(ctx, page)
    ctx._close_module_page(page)


def _region_cleanup(ctx, page) -> None:
    for name in ("_region_detect_timer", "_region_channel_timer"):
        timer = getattr(page, name, None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
    timer = getattr(page, "_region_img_timer", None)
    if timer is not None:
        try:
            timer.stop()
        except RuntimeError:
            pass
    for name in ("_region_detect_worker", "_region_channel_worker"):
        worker = getattr(page, name, None)
        if worker is not None:
            worker.stop()


def _region_refresh(ctx, page) -> None:
    records = _region_image_records(ctx, page._region_task_id)
    available = {rec.get("parquet") for rec in records}
    changed = False
    new_files = [
        path for path in page._region_files if path in available
    ]
    if new_files != page._region_files:
        page._region_files = new_files
        changed = True
    page._region_store["files"] = list(page._region_files)
    for region in page._region_store.get("regions") or []:
        samples = region.get("samples") or {}
        keep = {path: value for path, value in samples.items() if path in available}
        if keep != samples:
            region["samples"] = keep
            changed = True
    page._region_detect_files = [
        path for path in page._region_detect_files
        if path in page._region_files
    ] or list(page._region_files)
    page._region_detect["files"] = list(page._region_detect_files)
    if page._region_preview.get("file") not in page._region_files:
        page._region_preview["file"] = (
            page._region_files[0] if page._region_files else None
        )
        changed = True
    if changed:
        page._region_params["files"] = list(page._region_files)
        _region_mark_dirty(ctx, page)
    _region_rebuild_files(ctx, page)
    _region_rebuild_gate_tree(ctx, page)
    _region_rebuild_region_tree(ctx, page)
    _region_rebuild_settings(ctx, page)
    _region_rebuild_detect_panel(ctx, page)
    _region_switch_file(ctx, page, page._region_preview.get("file"))
