"""Gate tree editor: state, tree content, settings and rendering."""
from __future__ import annotations

import copy

from PySide6.QtCore import QRectF, QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGraphicsProxyWidget, QGraphicsScene, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTreeWidget, QVBoxLayout, QWidget,
)

from smage.core.settings import _default_palette
from smage.data.workers import _GatingStatsWorker
from smage.runlog import runlog
from smage.widgets.controls import (
    _NoWheelComboBox, _NoWheelSpinBox, _SizeField,
)
from smage.widgets.gate_editor.canvas import _GatingCanvasView
from smage.widgets.panels import _param_hint
from smage.widgets.plot import renderer as _plot_renderer
from smage.widgets.plot_axes import _gate_color, _gate_tool_icon


_GATING_PLOT_SIZE = (560, 480)

def _default_gating_preview(rec, cols) -> dict:
    """A fresh scatter preview config for the gating tree (first file + axes)."""
    # Pixel positions (`x`/`y`) are not channels: a scatter of them is just a
    # picture of the sample, so the first two real columns are used instead.
    usable = [c for c in cols if c not in ("x", "y")] or list(cols)
    x_default = usable[0] if usable else None
    y_default = usable[1] if len(usable) > 1 else x_default
    return {
        "type": "scatter",
        "file": rec.get("parquet") if rec else None,
        "file_name": rec.get("name") if rec else None,
        "x": x_default,
        "y": y_default,
        "last_x": x_default,
        "last_y": y_default,
        "color_mode": "density",
        "color_param": None,
        "palette": _default_palette(),
        "xmin": None,
        "xmax": None,
        "ymin": None,
        "ymax": None,
        "x_range_mode": "scale",
        "y_range_mode": "scale",
        "point_size": 4,
        "fixed_point_size": False,
        "solid_color": (0, 0, 200),
        "show_axes": True,
        "show_axis_names": True,
        "show_indices": True,
        "show_sample_name": True,
        "label_font_size": 20,
        "label_bold": False,
        "label_bg_opacity": 50,
        "show_gate_labels": True,
        "label_show_percent": True,
    }


def _gating_plot_size(preview) -> tuple:
    """The gating plot's fixed card size (width, height) in pixels."""
    size = (preview or {}).get("size") or _GATING_PLOT_SIZE
    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError):
        width, height = _GATING_PLOT_SIZE
    return (max(200, min(4000, width)), max(200, min(4000, height)))


def _gating_state_init(ctx, page, task_def: dict, locked: bool) -> None:
    """Initialize the ``_gating_*`` state attributes on `page`.

    `page` is either a module page (a QWidget) or a `_GatingState` bag, so
    the same methods can drive several gate trees in the Unmix module.
    """
    records = ctx._available_files(task_def.get("id"))
    uses_store = task_def.get("type") == "gating"
    store = None
    if uses_store:
        # The Gating module plots only the files chosen in Data files. A new
        # module has no selection yet, so the preview starts empty instead of
        # showing the first available file.
        store = ctx._gating_store_load(task_def)
        selected = [
            f for f in (store.get("files") or [])
            if any(r.get("parquet") == f for r in records)
        ]
        rec = next(
            (r for r in records if r.get("parquet") == selected[0]), None
        ) if selected else None
    else:
        # Unmix's embedded trees have no file picker: plot the first file.
        selected = []
        rec = records[0] if records else None
    cols = ctx._columns_for(
        rec.get("parquet"), task_def.get("id")
    ) if rec else []
    params = task_def.setdefault("params", {})
    preview = params.get("preview")
    valid = isinstance(preview, dict) and (
        preview.get("file") is None
        or any(r.get("parquet") == preview.get("file") for r in records)
    )
    if not valid:
        preview = _default_gating_preview(rec, cols)
        params["preview"] = preview
    else:
        # Backfill last-used axis parameters for older projects.
        preview.setdefault("last_x", preview.get("x"))
        preview.setdefault("last_y", preview.get("y"))
        preview.setdefault("type", "scatter")
    # The gating plot sits on a bordered canvas card whose size is saved
    # with the module (Plot settings: W / H / resolution / square).
    preview.setdefault("size", list(_GATING_PLOT_SIZE))
    # Gating defaults to From scale; saved gate ranges only matter after the
    # user switches that axis to Manual.
    preview.setdefault("x_range_mode", "scale")
    preview.setdefault("y_range_mode", "scale")
    page._gating_preview = preview
    page._gating_task_def = task_def
    page._gating_task_id = task_def.get("id")
    # The Gating module keeps its gate tree (files, gates, per-file
    # overrides) in the module folder; Unmix's embedded trees stay inline.
    page._gating_uses_store = uses_store
    if uses_store:
        page._gating_files = list(selected)
        page._gating_gates = list(store.get("gates") or [])
        page._gating_per_file = dict(store.get("per_file") or {})
    else:
        page._gating_files = []
        page._gating_gates = params.setdefault("gates", [])
        page._gating_per_file = {}
    # Older polygon gates have only their corner vertices; on a non-linear
    # axis that data-space polygon does not match the drawn edges. Add the
    # sampled outline used by the mask.
    try:
        from smage.modules.gating import page as _gating_page_module

        _gating_page_module._gating_migrate_polygon_outlines(ctx, page)
    except Exception:  # noqa: BLE001 - migration must not block opening
        pass
    page._gating_rois = []
    page._gating_labels = []
    page._gating_crosshairs = {}
    page._gating_split_lines = {}
    page._gating_skewed_lines = {}
    page._gating_active_gate_id = None
    page._gating_context_gate_id = None
    page._gating_collapsed = set()
    page._gating_stats_worker = _GatingStatsWorker()
    page._gating_stat_generation = 0
    page._gating_last_stats = {}
    page._gating_stats_scope = (False, set())
    page._gating_stat_timer = QTimer(ctx.host)
    # Short poll: gate counts follow a drag with roughly this latency.
    page._gating_stat_timer.setInterval(40)
    page._gating_stat_timer.timeout.connect(
        lambda: ctx._gating_on_stats_timer(page)
    )
    page._gating_stat_timer.start()
    # The x/y that the user configured in Plot settings (independent of a
    # gate click, which temporarily switches to that gate's axes).
    page._gating_settings_x = page._gating_preview.get("x")
    page._gating_settings_y = page._gating_preview.get("y")
    page._gating_render_timer = None
    page._gating_locked = locked
    page._gating_parent_id = None
    page._gating_save_btn = None
    page._gating_dirty = False
    page._gating_snapshot = (
        copy.deepcopy({
            "files": page._gating_files,
            "gates": page._gating_gates,
            "per_file": page._gating_per_file,
        })
        if page._gating_uses_store
        else copy.deepcopy(params)
    )


def _gating_build_content(ctx, page, locked: bool) -> QWidget:
    """Build the gate tree | scatter | plot settings pane for `page`."""
    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)

    # Left: data files (top, 1 part) + gate tree (bottom, 3 parts). The
    # files section only exists for the Gating module; Unmix's embedded
    # trees have no file scope.
    tree_panel = ctx._build_panel_frame()
    tree_lay = QVBoxLayout(tree_panel)
    tree_lay.setContentsMargins(10, 10, 10, 10)
    tree_lay.setSpacing(6)

    if page._gating_uses_store:
        tree_lay.addWidget(QLabel("<b>Data files</b>"))
        files_btn = QPushButton("Select files…")
        files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        files_btn.setEnabled(not locked)
        files_btn.clicked.connect(
            lambda _=False: ctx._gating_select_files(page)
        )
        tree_lay.addWidget(files_btn)
        files_box = QListWidget()
        files_box.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        files_box.setEnabled(not locked)
        files_box.itemClicked.connect(
            lambda item: ctx._gating_switch_file(
                page, item.data(Qt.ItemDataRole.UserRole)
            )
        )
        tree_lay.addWidget(files_box, 1)
        page._gating_files_box = files_box
        files_count = QLabel("")
        files_count.setStyleSheet("color: #6b7785;")
        tree_lay.addWidget(files_count)
        page._gating_files_count = files_count

    # Gates.
    tree_lay.addWidget(QLabel("<b>Gates</b>"))
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    tree.setStyleSheet(
        "QTreeWidget { background: #ffffff; border: none; }"
        "QTreeWidget::item:selected { background: #dbeafa; }"
    )
    tree_lay.addWidget(tree, 3 if page._gating_uses_store else 1)
    page._gating_tree = tree
    tree.itemCollapsed.connect(
        lambda item: ctx._gating_on_collapse(page, item)
    )
    tree.itemExpanded.connect(
        lambda item: ctx._gating_on_expand(page, item)
    )

    full_stats_btn = QPushButton("Update statistics with full data")
    full_stats_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    full_stats_btn.setToolTip(
        "Recalculate gate counts on every event. Normal editing uses the "
        "displayed sample, which is much faster."
    )
    full_stats_btn.clicked.connect(
        lambda _=False: ctx._gating_full_stats(page)
    )
    tree_lay.addWidget(full_stats_btn)
    page._gating_full_stats_btn = full_stats_btn
    tree_lay.addWidget(_param_hint(
        ctx,
        "Editing recalculates counts from the plotted sample to keep "
        "dragging fast. This button recomputes them on the complete data set.",
    ))

    # Middle: tool area (top) + scatter (bottom).
    mid_panel = ctx._build_panel_frame()
    mid_lay = QVBoxLayout(mid_panel)
    mid_lay.setContentsMargins(0, 0, 0, 0)
    mid_lay.setSpacing(0)
    tools = QWidget()
    tools.setStyleSheet("background: #fbfcfe; border-bottom: 1px solid #e3e8ef;")
    tools_lay = QHBoxLayout(tools)
    tools_lay.setContentsMargins(10, 8, 10, 8)
    tools_lay.setSpacing(6)
    tools_lay.addWidget(QLabel("<b>Tools</b>"))
    tool_buttons = {}
    for text, key in (
        ("Rectangle", "rect"),
        ("Ellipse", "ellipse"),
        ("Polygon", "polygon"),
        ("Quadrant", "quadrant"),
        ("Skewed Quadrant", "skewed_quadrant"),
        ("Split", "split"),
        ("Range", "range"),
    ):
        btn = QPushButton()
        btn.setIcon(_gate_tool_icon(key))
        btn.setIconSize(QSize(20, 20))
        btn.setFixedSize(30, 30)
        btn.setToolTip(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(not locked)

        def on_clicked(_=False, k=key):
            # Clicking the active tool again switches back to pan/zoom.
            ctx._gating_set_tool(page, None if page._gating_tool == k else k)

        btn.clicked.connect(on_clicked)
        tools_lay.addWidget(btn)
        tool_buttons[key] = btn
    # Keep the drawing tools grouped on the left; Col2Gate sits alone at
    # the far right.
    tools_lay.addStretch()
    col2gate_btn = QPushButton("Col2Gate")
    col2gate_btn.setToolTip(
        "Create one gate per distinct value of a column"
    )
    col2gate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    col2gate_btn.setEnabled(not locked)
    col2gate_btn.clicked.connect(
        lambda _=False: ctx._gating_open_col2gate(page)
    )
    tools_lay.addWidget(col2gate_btn)
    page._gating_col2gate_btn = col2gate_btn
    mid_lay.addWidget(tools)

    plot_widget, viewbox = ctx._gating_make_plot(page)
    # Overlay shown when the current file lacks the scatter's x/y parameter.
    plot_host = QWidget()
    host_grid = QGridLayout(plot_host)
    host_grid.setContentsMargins(0, 0, 0, 0)
    host_grid.addWidget(plot_widget, 0, 0)
    overlay = QWidget()
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    ov = QVBoxLayout(overlay)
    ov.setContentsMargins(0, 0, 0, 0)
    ov.addStretch()
    ov_row = QHBoxLayout()
    ov_row.addStretch()
    overlay_box = QLabel("")
    overlay_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    overlay_box.setWordWrap(True)
    overlay_box.setStyleSheet(
        "background-color: rgba(200, 200, 200, 210); color: #444; "
        "border: 1px solid #999; border-radius: 6px; padding: 14px 18px;"
    )
    overlay_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
    ov_row.addWidget(overlay_box)
    ov_row.addStretch()
    ov.addLayout(ov_row)
    ov.addStretch()
    overlay.hide()
    host_grid.addWidget(overlay, 0, 0)

    # The plot is a bordered card at a fixed size on a light canvas, like
    # the Figure module (size is set in Plot settings).
    plot_card = QFrame()
    plot_card.setObjectName("gatingPlotCard")
    plot_card.setStyleSheet(
        "#gatingPlotCard { background: #ffffff;"
        " border: 1px solid #c9d5e2; border-radius: 6px; }"
    )
    card_lay = QVBoxLayout(plot_card)
    card_lay.setContentsMargins(6, 6, 6, 6)
    card_lay.setSpacing(0)
    card_lay.addWidget(plot_host, 1)
    card_w, card_h = _gating_plot_size(page._gating_preview)
    plot_card.setFixedSize(card_w, card_h)

    # Canvas (like the Figure module): the card lives in a graphics scene,
    # the wheel zooms the canvas around the empty area.
    plot_scene = QGraphicsScene()
    plot_proxy = QGraphicsProxyWidget()
    plot_proxy.setWidget(plot_card)
    plot_scene.addItem(plot_proxy)
    plot_proxy.setPos(0, 0)
    plot_scene.setSceneRect(
        QRectF(0, 0, card_w, card_h).adjusted(-24, -24, 24, 24)
    )
    canvas = _GatingCanvasView(plot_scene, plot_widget, plot_card)

    mid_lay.addWidget(canvas, 5)
    page._gating_plot_widget = plot_widget
    page._gating_viewbox = viewbox
    page._gating_plot_card = plot_card
    page._gating_plot_canvas = canvas
    page._gating_plot_scene = plot_scene
    page._gating_plot_proxy = plot_proxy
    page._gating_overlay = overlay
    page._gating_overlay_box = overlay_box
    page._gating_tool = None
    page._gating_tool_buttons = tool_buttons
    ctx._gating_update_tools(page)

    # Image plots re-sample (LOD) when the user zooms/pans their data.
    # `page` may be a _GatingState (Unmix), which is not a QObject, so the
    # timer is parented to the plot widget instead.
    img_refresh = QTimer(plot_widget)
    img_refresh.setSingleShot(True)
    img_refresh.setInterval(30)
    img_refresh.timeout.connect(lambda: ctx._gating_image_refresh(page))
    viewbox.sigRangeChanged.connect(lambda *_: img_refresh.start())
    page._gating_img_refresh_timer = img_refresh

    # Right: scatter settings.
    settings_panel = ctx._build_panel_frame()
    settings_lay = QVBoxLayout(settings_panel)
    settings_lay.setContentsMargins(10, 10, 10, 10)
    settings_lay.setSpacing(6)
    settings_lay.addWidget(QLabel("<b>Plot settings</b>"))
    settings_scroll = QScrollArea()
    settings_scroll.setWidgetResizable(True)
    settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
    # Scoped selectors keep the transparent rule from reaching QToolTip,
    # whose palette would otherwise turn black.
    settings_scroll.setObjectName("gatingSettingsScroll")
    settings_scroll.setStyleSheet(
        "QScrollArea#gatingSettingsScroll { background: transparent; }"
    )
    settings_host = QWidget()
    settings_host.setObjectName("gatingSettingsHost")
    settings_host.setStyleSheet(
        "QWidget#gatingSettingsHost { background: transparent; }"
    )
    settings_layout = QVBoxLayout(settings_host)
    settings_layout.setContentsMargins(0, 0, 0, 0)
    settings_layout.setSpacing(6)
    settings_scroll.setWidget(settings_host)
    settings_lay.addWidget(settings_scroll, 1)
    page._gating_settings_layout = settings_layout

    # Tree | plot canvas | plot settings, resizable by dragging the edges.
    gating_split = QSplitter(Qt.Orientation.Horizontal)
    gating_split.setChildrenCollapsible(False)
    gating_split.addWidget(tree_panel)
    gating_split.addWidget(mid_panel)
    gating_split.addWidget(settings_panel)
    gating_split.setStretchFactor(0, 1)
    gating_split.setStretchFactor(1, 2)
    gating_split.setStretchFactor(2, 1)
    gating_split.setSizes([300, 700, 320])
    cl.addWidget(gating_split, 1)
    page._gating_splitter = gating_split

    tree.itemClicked.connect(lambda item, col=0: ctx._gating_on_tree_selection(page, item, col))
    tree.itemDoubleClicked.connect(lambda item, col=0: ctx._gating_on_tree_double_clicked(page, item, col))
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    tree.customContextMenuRequested.connect(lambda pos: ctx._gating_tree_context_menu(page, pos))
    ctx._gating_refresh_files(page)
    ctx._gating_load_rois(page)
    ctx._gating_rebuild_tree(page)
    ctx._gating_render(page)
    ctx._gating_rebuild_settings(page)
    # Gates are drawn with the saved colour; make sure every item picked
    # it up (lines created before the colour was known, handles, …).
    ctx._gating_apply_gate_color(page)
    return content


def _gating_render(ctx, page) -> None:
    """Render the gating plot for the current view mode (scatter/image/histogram)."""
    rec = ctx._figure_record(page._gating_preview.get("file"))
    preview = page._gating_preview
    plot_widget = page._gating_plot_widget
    viewbox = page._gating_viewbox
    plot_type = preview.get("type", "scatter")
    runlog.action("gating.render", type=str(plot_type),
                  items=len(plot_widget.getPlotItem().items))

    # A new Gating module has no Data files selected yet: keep the plot empty
    # instead of drawing the first available file.
    if getattr(page, "_gating_uses_store", False) and not page._gating_files:
        plot_widget.clear()
        page._gating_gates_hidden = True
        page._gating_render_key = None
        page._gating_overlay_box.setText("Select data files to start.")
        page._gating_overlay.show()
        ctx._gating_update_roi_visibility(page)
        return

    # Undo image-view locks (aspect / auto-range) when leaving image mode.
    if plot_type != "image":
        viewbox.setAspectLocked(False)

    cols = ctx._columns_for(
        rec.get("parquet"), page._gating_task_id
    ) if rec else []

    # Show a gray overlay when this plot type cannot display the file.
    overlay_text = None
    if rec is None:
        overlay_text = "No data file"
    elif plot_type == "image":
        ent = ctx._ensure_image_index(rec.get("parquet"))
        if ent is None or ent.get("manifest") is None:
            overlay_text = (
                "This file is not an image.\n"
                "Image view needs an imported image (TIFF)."
            )
    else:
        axes = [preview.get("x")] if plot_type == "histogram" else [
            preview.get("x"), preview.get("y")
        ]
        missing = [str(n) for n in axes if n and n not in cols]
        if missing:
            overlay_text = "Parameter not in this file:\n" + ", ".join(missing)
    if overlay_text:
        page._gating_overlay_box.setText(overlay_text)
        page._gating_overlay.show()
    else:
        page._gating_overlay.hide()
    page._gating_gates_hidden = bool(overlay_text)
    gate_overlay = getattr(page, "_gating_gate_overlay", None)
    if gate_overlay is not None:
        gate_overlay.update()

    mask = None
    active = page._gating_active_gate_id
    context = page._gating_context_gate_id
    if active:
        if rec:
            mask = ctx._gating_compute_mask(page, active, rec["parquet"])
    elif context:
        if rec:
            mask = ctx._gating_context_mask(page, context, rec["parquet"])
    # Preserve the current view range for the subset view (don't auto-fit),
    # but only while the same axes are still shown: after a plot-type or
    # parameter change the old range belongs to different data.
    render_key = (
        plot_type, preview.get("file"), preview.get("x"), preview.get("y"),
        active, context,
    )
    preserved = None
    same_view = getattr(page, "_gating_render_key", None) == render_key
    # Image views keep their zoom across re-renders (the extent is fixed by
    # the image); scatter views only restore the range for a subset view.
    if same_view and (plot_type == "image" or active):
        preserved = viewbox.viewRange()
    page._gating_render_key = render_key

    if plot_type == "image":
        plot_dict = dict(preview)
        channel = preview.get("image_channel")
        available = [c for c in cols if c not in ("x", "y")]
        if not channel or channel not in available:
            channel = available[0] if available else None
        if channel:
            preview["image_channel"] = channel
            plot_dict["image_channel"] = channel
            plot_dict["layers"] = [{
                "parameter": channel,
                "color": (255, 255, 255),
                "name": str(channel),
                "min": preview.get("image_min"),
                "max": preview.get("image_max"),
            }]
        ctx._figure_render_image(
            plot_dict, plot_widget, task_id=page._gating_task_id,
            row_mask=mask,
        )
    elif plot_type == "histogram":
        ctx._figure_render_histogram(
            preview, plot_widget, mask=mask, task_id=page._gating_task_id
        )
    else:
        ctx._figure_render_scatter(
            preview, plot_widget, mask=mask, task_id=page._gating_task_id
        )
    if preserved is not None:
        xr, yr = preserved
        viewbox.setXRange(xr[0], xr[1], padding=0)
        viewbox.setYRange(yr[0], yr[1], padding=0)
    ctx._gating_update_roi_visibility(page)


def _gating_rebuild_settings(ctx, page) -> None:
    layout = page._gating_settings_layout
    ctx._clear_settings_layout(layout)
    preview = page._gating_preview
    enabled = not page._gating_locked
    plot_widget = page._gating_plot_widget

    records = ctx._available_files(page._gating_task_id)
    if getattr(page, "_gating_uses_store", False):
        selected = set(page._gating_files or [])
        records = [r for r in records if r.get("parquet") in selected]
    rec = ctx._figure_record(preview.get("file"))
    columns = ctx._columns_for(
        rec.get("parquet"), page._gating_task_id
    ) if rec else []

    layout.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    for r in records:
        file_combo.addItem(r.get("name", ""), r.get("parquet", ""))
    if preview.get("file") is not None:
        idx = file_combo.findData(preview["file"])
        if idx >= 0:
            file_combo.setCurrentIndex(idx)
    file_combo.setEnabled(enabled)
    layout.addWidget(ctx._file_nav_row(file_combo, enabled))

    layout.addWidget(QLabel("Plot type"))
    type_combo = _NoWheelComboBox()
    type_combo.addItem("Scatter", "scatter")
    type_combo.addItem("Image", "image")
    type_combo.addItem("Histogram", "histogram")
    idx = type_combo.findData(preview.get("type", "scatter"))
    type_combo.setCurrentIndex(max(0, idx))
    type_combo.setEnabled(enabled)
    layout.addWidget(type_combo)

    plot_type = preview.get("type", "scatter")
    x_combo = y_combo = channel_combo = None
    if plot_type == "image":
        layout.addWidget(QLabel("Channel"))
        channels = [c for c in columns if c not in ("x", "y")]
        channel_combo = ctx._gating_param_combo(
            channels, preview.get("image_channel"), enabled
        )
        layout.addWidget(channel_combo)
        ctx._image_level_controls(
            layout, preview.get("image_channel"),
            preview.get("image_min"), preview.get("image_max"),
            enabled,
            on_minmax=lambda lo, hi: ctx._gating_set_image_levels(
                page, lo, hi
            ),
            task_id=page._gating_task_id,
            parquet_path=rec.get("parquet") if rec else None,
        )
    else:
        layout.addWidget(QLabel("X parameter"))
        x_combo = ctx._gating_param_combo(columns, preview.get("x"), enabled)
        layout.addWidget(x_combo)
        if plot_type == "scatter":
            layout.addWidget(QLabel("Y parameter"))
            y_combo = ctx._gating_param_combo(columns, preview.get("y"), enabled)
            layout.addWidget(y_combo)

    if plot_type == "scatter":
        ctx._figure_scatter_color_settings(
            layout, preview, plot_widget, columns, enabled,
            on_change=lambda: ctx._gating_on_preview_change(page),
            on_rebuild=lambda: (
                ctx._gating_rebuild_settings(page),
                ctx._gating_on_preview_change(page),
            ),
            task_id=page._gating_task_id,
        )

    if plot_type != "image":
        layout.addWidget(QLabel("X range"))
        layout.addWidget(ctx._figure_range_row(
            preview, "x", plot_widget, enabled,
            on_commit=lambda: ctx._gating_mark_dirty(page),
            configs=ctx._figure_axis_scale_configs(
                page._gating_task_id, preview.get("file"),
                preview.get("x"),
            ),
            task_id=page._gating_task_id,
        ))
    if plot_type == "scatter":
        layout.addWidget(QLabel("Y range"))
        layout.addWidget(ctx._figure_range_row(
            preview, "y", plot_widget, enabled,
            on_commit=lambda: ctx._gating_mark_dirty(page),
            configs=ctx._figure_axis_scale_configs(
                page._gating_task_id, preview.get("file"),
                preview.get("y"),
            ),
            task_id=page._gating_task_id,
        ))

    # ----- plot size on the canvas (same controls as the Figure module) --
    layout.addWidget(QLabel("Size (px)"))
    size_row = QWidget()
    sh = QHBoxLayout(size_row)
    sh.setContentsMargins(0, 0, 0, 0)
    sh.setSpacing(4)
    size = _gating_plot_size(preview)

    def apply_size(width, height):
        width = max(200, min(4000, int(width)))
        height = max(200, min(4000, int(height)))
        preview["size"] = [width, height]
        card = getattr(page, "_gating_plot_card", None)
        if card is not None:
            card.setFixedSize(width, height)
        scene = getattr(page, "_gating_plot_scene", None)
        if scene is not None:
            scene.setSceneRect(
                QRectF(0, 0, width, height).adjusted(-24, -24, 24, 24)
            )
        ctx._gating_mark_dirty(page)

    def on_width():
        width = width_field.value()
        if width is None:
            return
        height = height_field.value() or width
        resolution_field.set_value(max(20, min(400, round(width / 10.0))))
        apply_size(width, height)

    def on_height():
        width = width_field.value()
        height = height_field.value()
        if width is None or height is None:
            return
        apply_size(width, height)

    def on_resolution():
        res = resolution_field.value()
        if res is None:
            return
        width = int(res) * 10
        old_w = width_field.value() or 0
        old_h = height_field.value() or 0
        height = (
            int(round(old_h * width / old_w)) if old_w
            else int(round(width * 0.86))
        )
        width_field.set_value(width)
        height_field.set_value(height)
        apply_size(width, height)

    def on_square():
        height = height_field.value() or width_field.value()
        if height is None:
            return
        width_field.set_value(height)
        resolution_field.set_value(max(20, min(400, round(height / 10.0))))
        apply_size(height, height)

    width_field = _SizeField(size[0], lambda _v=None: on_width())
    height_field = _SizeField(size[1], lambda _v=None: on_height())
    width_field.set_enabled(enabled)
    height_field.set_enabled(enabled)
    sh.addWidget(QLabel("W"))
    sh.addWidget(width_field)
    sh.addWidget(QLabel("H"))
    sh.addWidget(height_field)
    sh.addStretch()
    layout.addWidget(size_row)

    layout.addWidget(QLabel("Resolution"))
    res_row = QWidget()
    rh = QHBoxLayout(res_row)
    rh.setContentsMargins(0, 0, 0, 0)
    rh.setSpacing(4)
    resolution_field = _SizeField(
        max(20, min(400, int(round(size[0] / 10.0)))),
        lambda _v=None: on_resolution(), minimum=20, maximum=400,
    )
    resolution_field.set_enabled(enabled)
    rh.addWidget(resolution_field)
    square_btn = QPushButton("Square")
    square_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    square_btn.setEnabled(enabled)
    square_btn.setToolTip("Make width equal to height")
    square_btn.clicked.connect(lambda _=False: on_square())
    rh.addWidget(square_btn)
    rh.addStretch()
    layout.addWidget(res_row)

    ctx._figure_display_toggles(
        layout, preview, enabled,
        on_change=lambda: (
            ctx._figure_apply_display_options(preview, plot_widget),
            ctx._gating_mark_dirty(page),
        ),
    )

    layout.addWidget(QLabel("Gate color"))
    gate_color_btn = QPushButton()
    gate_color_btn.setFixedSize(60, 22)
    gate_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    gate_color_btn.setEnabled(enabled)
    gate_color_btn.setToolTip("Colour of the gate outlines")
    # Scoped: a bare background rule also styles QToolTip and replaces its
    # white palette with the gate colour.
    gate_color_btn.setObjectName("gateColorButton")
    gate_color_btn.setStyleSheet(
        f"QPushButton#gateColorButton {{ "
        f"background-color: {_gate_color(preview)}; "
        "border: 1px solid #6b7785; border-radius: 3px; }"
    )
    gate_color_btn.clicked.connect(
        lambda _=False: ctx._gating_pick_gate_color(page, gate_color_btn)
    )
    layout.addWidget(gate_color_btn)

    show_labels_cb = QCheckBox("Show gate labels")
    show_labels_cb.setChecked(bool(preview.get("show_gate_labels", True)))
    show_labels_cb.setEnabled(enabled)
    layout.addWidget(show_labels_cb)

    show_percent_cb = QCheckBox("Show percentage in gate labels")
    show_percent_cb.setChecked(bool(preview.get("label_show_percent", True)))
    show_percent_cb.setEnabled(enabled)
    layout.addWidget(show_percent_cb)

    layout.addWidget(QLabel("Gate label font size"))
    font_spin = _NoWheelSpinBox()
    font_spin.setRange(6, 48)
    font_spin.setValue(int(preview.get("label_font_size", 20)))
    font_spin.setEnabled(enabled)
    layout.addWidget(font_spin)

    bold_cb = QCheckBox("Bold gate label")
    bold_cb.setChecked(bool(preview.get("label_bold", False)))
    bold_cb.setEnabled(enabled)
    layout.addWidget(bold_cb)

    layout.addWidget(QLabel("Gate label background opacity (%)"))
    opacity_spin = _NoWheelSpinBox()
    opacity_spin.setRange(0, 100)
    opacity_spin.setValue(int(preview.get("label_bg_opacity", 50)))
    opacity_spin.setEnabled(enabled)
    layout.addWidget(opacity_spin)

    def on_label_style_change():
        preview["show_gate_labels"] = show_labels_cb.isChecked()
        preview["label_show_percent"] = show_percent_cb.isChecked()
        preview["label_font_size"] = font_spin.value()
        preview["label_bold"] = bold_cb.isChecked()
        preview["label_bg_opacity"] = opacity_spin.value()
        ctx._gating_update_labels(page)
        ctx._gating_apply_label_style(page)
        ctx._gating_mark_dirty(page)

    show_labels_cb.toggled.connect(lambda _=0: on_label_style_change())
    show_percent_cb.toggled.connect(lambda _=0: on_label_style_change())
    font_spin.valueChanged.connect(lambda _=0: on_label_style_change())
    bold_cb.toggled.connect(lambda _=0: on_label_style_change())
    opacity_spin.valueChanged.connect(lambda _=0: on_label_style_change())

    layout.addStretch()

    def on_file_changed():
        ctx._gating_switch_file(
            page, file_combo.currentData(), file_combo.currentText()
        )
    file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())

    def on_param(attr):
        def handler(value):
            value = value or None
            preview[attr] = value
            # Remember this as the last-used parameter.
            preview["last_" + attr] = value
            if attr == "x":
                page._gating_settings_x = value
            elif attr == "y":
                page._gating_settings_y = value
            # Store the old channel's range and load the new channel's own
            # record before re-rendering.
            _plot_renderer._figure_sync_range_record(preview, attr)
            # Keep the active subset / context; only the display axes change.
            ctx._gating_render(page)
            # Reload the per-channel range record in the settings panel.
            ctx._gating_rebuild_settings(page)
            ctx._gating_mark_dirty(page)
        return handler
    if x_combo is not None:
        x_combo.currentTextChanged.connect(on_param("x"))
    if y_combo is not None:
        y_combo.currentTextChanged.connect(on_param("y"))
    if channel_combo is not None:
        channel_combo.currentTextChanged.connect(
            lambda text, _c=channel_combo: on_channel(text)
        )

    def on_channel(text):
        preview["image_channel"] = text or None
        ctx._gating_render(page)
        # Rebuild settings so the channel histogram follows the new channel.
        ctx._gating_rebuild_settings(page)
        ctx._gating_mark_dirty(page)

    def on_plot_type_changed():
        ctx._gating_set_plot_type(page, type_combo.currentData() or "scatter")

    type_combo.currentIndexChanged.connect(lambda _=0: on_plot_type_changed())


def _gating_param_combo(ctx, columns, current, enabled):
    """A parameter combo that also shows `current` when it isn't in `columns`."""
    combo = ctx._figure_param_combo(columns, current, enabled)
    if current is not None and combo.findText(str(current)) < 0:
        combo.addItem(str(current))
        combo.setCurrentIndex(combo.count() - 1)
    return combo


def _gating_cleanup(ctx, page) -> None:
    """Remove gate items from the ViewBox before the page is torn down."""
    for items in page._gating_rois:
        for item in items:
            try:
                page._gating_viewbox.removeItem(item)
            except Exception:
                pass
    for entry in page._gating_crosshairs.values():
        for line in entry["lines"]:
            try:
                page._gating_viewbox.removeItem(line)
            except Exception:
                pass
    for entry in page._gating_split_lines.values():
        for line in entry["lines"]:
            try:
                page._gating_viewbox.removeItem(line)
            except Exception:
                pass
    for entry in page._gating_skewed_lines.values():
        try:
            page._gating_viewbox.removeItem(entry["roi"])
        except Exception:
            pass
    page._gating_rois = []
    page._gating_crosshairs = {}
    page._gating_split_lines = {}
    page._gating_skewed_lines = {}
    for label in page._gating_labels:
        try:
            page._gating_viewbox.removeItem(label)
        except Exception:
            pass
    page._gating_labels = []
    # Stop the background worker and drop its column/mask caches.
    worker = getattr(page, "_gating_stats_worker", None)
    if worker is not None:
        worker.stop()
        page._gating_stats_worker = None
    timer = getattr(page, "_gating_stat_timer", None)
    if timer is not None:
        timer.stop()
        page._gating_stat_timer = None
    adjust = getattr(page, "_gating_adjust_timer", None)
    if adjust is not None:
        adjust.stop()
        page._gating_adjust_timer = None
    page._gating_adjust_affected = "missing"
    page._gating_last_stats = {}


