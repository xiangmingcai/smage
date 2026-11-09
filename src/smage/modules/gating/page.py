"""Gating module: gate tree, canvas ROIs and per-file statistics."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.formatting import _category_value_label
from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_geometry,
    _gate_group_id,
    _gate_needed_columns,
    _gate_source_ids,
    _resolve_gates_for_file,
)
from smage.core.scale_math import (
    _forward_scale_value,
    _inverse_scale_value,
)
from smage.core.settings import (
    _log_exception,
    _plot_background,
    _setting,
)
from smage.runlog import runlog
from smage.widgets.controls import _NoWheelComboBox
from smage.widgets.gate_editor.canvas import (
    _GateLabel,
    _GatePolygonROI,
    _GateViewBox,
    _MIN_GATE_SPAN,
    _SkewedQuadrantROI,
)
from smage.widgets.plot_axes import (
    _detach_gate_item,
    _gate_color,
    _gate_hover_color,
    _gate_roi_pens,
    _make_fit_plot_item,
    _make_sci_axis,
)
from smage.widgets.resources import _lock_pixmap
from uuid import uuid4
import copy
import json
from pathlib import Path

import pyqtgraph as pg
from smage.data import columns as _data_columns
from smage.data import feature_outputs as _data_feature_outputs
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.data import sampling as _data_sampling
from smage.widgets.gate_editor import editor as _widgets_gate_editor_editor
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import renderer as _widgets_plot_renderer


_GATING_DISPLAY_KEYS = (
    "color_mode", "color_param", "palette", "solid_color",
    "point_size", "fixed_point_size",
    "image_channel", "image_min", "image_max",
    "bins", "hist_mode", "hist_color", "curve_color",
)


def _gating_display_snapshot(preview):
    """Capture the display state (plot colouring) currently shown in the preview."""
    return {
        key: copy.deepcopy(preview.get(key))
        for key in _GATING_DISPLAY_KEYS
        if preview.get(key) is not None
    }


def _gate_label_position(gate):
    """Return an (x, y) point inside `gate` for placing its name label."""
    t = gate.get("type")
    xr = (gate.get("xmin", 0.0), gate.get("xmax", 1.0))
    yr = (gate.get("ymin", 0.0), gate.get("ymax", 1.0))
    dx = (xr[1] - xr[0]) * 0.12
    dy = (yr[1] - yr[0]) * 0.12
    if t in ("rect", "ellipse"):
        x0, y0 = gate["pos"]
        w, h = gate["size"]
        return (x0 + w / 2.0, y0 + h / 2.0)
    if t == "polygon":
        pts = gate["points"]
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
    if t == "quadrant":
        vx, hy = gate.get("vx", 0.0), gate.get("hy", 0.0)
        q = gate.get("quadrant", 1)
        sx = 1 if q in (1, 4) else -1
        sy = 1 if q in (1, 2) else -1
        return (vx + sx * dx, hy + sy * dy)
    if t == "range":
        x1 = min(gate["x1"], gate["x2"])
        x2 = max(gate["x1"], gate["x2"])
        return ((x1 + x2) / 2.0, (yr[0] + yr[1]) / 2.0)
    if t == "split":
        vx = gate.get("vx", 0.0)
        sx = -1 if gate.get("side") == "left" else 1
        return (vx + sx * dx, (yr[0] + yr[1]) / 2.0)
    if t == "skewed_quadrant":
        cx, cy = gate["center"]
        arms = []
        for key in ("p_right", "p_up", "p_left", "p_down"):
            px, py = gate[key]
            arms.append((px - cx, py - cy))
        q = gate.get("quadrant", 1)
        if q == 1:
            a, b = arms[0], arms[1]
        elif q == 2:
            a, b = arms[1], arms[2]
        elif q == 3:
            a, b = arms[2], arms[3]
        else:
            a, b = arms[3], arms[0]
        la = (a[0] ** 2 + a[1] ** 2) ** 0.5 or 1.0
        lb = (b[0] ** 2 + b[1] ** 2) ** 0.5 or 1.0
        bx = a[0] / la + b[0] / lb
        by = a[1] / la + b[1] / lb
        lb2 = (bx ** 2 + by ** 2) ** 0.5 or 1.0
        d = max(dx, dy) * 1.5
        return (cx + bx / lb2 * d, cy + by / lb2 * d)
    return ((xr[0] + xr[1]) / 2.0, (yr[0] + yr[1]) / 2.0)


def _gating_scale_axes(ctx, page, gate) -> tuple:
    """(x_configs, y_configs) that map the gate's axes to plot units.

    Image plots use pixel coordinates directly and are never scaled.
    """
    if gate.get("plot_type", "scatter") == "image":
        return [], []
    parquet = page._gating_preview.get("file")
    task_id = page._gating_task_id
    xcfg = (
        _widgets_plot_renderer._figure_axis_scale_configs(
            ctx, task_id, parquet, gate.get("x")
        ) if gate.get("x") else []
    )
    ycfg = (
        _widgets_plot_renderer._figure_axis_scale_configs(
            ctx, task_id, parquet, gate.get("y")
        ) if gate.get("y") else []
    )
    return xcfg, ycfg


def _gating_preview_axes(ctx, page) -> tuple:
    """Display configs for the currently visible scatter axes."""
    preview = page._gating_preview
    return _gating_scale_axes(ctx, page, {
        "plot_type": preview.get("type", "scatter"),
        "x": preview.get("x"),
        "y": preview.get("y"),
    })


def _gating_convert_geometry(gate, geometry, xmap, ymap) -> dict:
    """Map a gate geometry dict through the per-axis coordinate maps."""
    t = gate.get("type")
    out = dict(geometry)
    if t in ("rect", "ellipse"):
        if "pos" in geometry and "size" in geometry:
            x, y = geometry["pos"]
            w, h = geometry["size"]
            out["pos"] = [xmap(x), ymap(y)]
            out["size"] = [xmap(x + w) - xmap(x), ymap(y + h) - ymap(y)]
    elif t == "polygon":
        if "points" in geometry:
            out["points"] = [
                [xmap(p[0]), ymap(p[1])] for p in geometry["points"]
            ]
    elif t in ("quadrant", "split"):
        if "vx" in geometry:
            out["vx"] = xmap(geometry["vx"])
        if "hy" in geometry:
            out["hy"] = ymap(geometry["hy"])
    elif t == "range":
        if "x1" in geometry:
            out["x1"] = xmap(geometry["x1"])
        if "x2" in geometry:
            out["x2"] = xmap(geometry["x2"])
    elif t == "skewed_quadrant":
        if "center" in geometry:
            out["center"] = [
                xmap(geometry["center"][0]), ymap(geometry["center"][1])
            ]
        for key in ("p_right", "p_up", "p_left", "p_down"):
            if key in geometry:
                out[key] = [xmap(geometry[key][0]), ymap(geometry[key][1])]
    return out


def _gating_geometry_to_display(ctx, page, gate, geometry) -> dict:
    """Stored (real) gate geometry → plot coordinates."""
    xcfg, ycfg = _gating_scale_axes(ctx, page, gate)
    if not xcfg and not ycfg:
        return geometry
    return _gating_convert_geometry(
        gate, geometry,
        (lambda v: _forward_scale_value(v, xcfg)) if xcfg else (lambda v: v),
        (lambda v: _forward_scale_value(v, ycfg)) if ycfg else (lambda v: v),
    )


def _gating_geometry_to_real(ctx, page, gate, geometry) -> dict:
    """Plot coordinates → stored (real) gate geometry."""
    xcfg, ycfg = _gating_scale_axes(ctx, page, gate)
    if not xcfg and not ycfg:
        return geometry
    return _gating_convert_geometry(
        gate, geometry,
        (lambda v: _inverse_scale_value(v, xcfg)) if xcfg else (lambda v: v),
        (lambda v: _inverse_scale_value(v, ycfg)) if ycfg else (lambda v: v),
    )


def _gating_preview_geometry_to_real(ctx, page, gate_type, geometry) -> dict:
    """Convert a preview-space geometry dict to real units."""
    xcfg, ycfg = _gating_preview_axes(ctx, page)
    if not xcfg and not ycfg:
        return geometry
    return _gating_convert_geometry(
        {"type": gate_type}, geometry,
        (lambda v: _inverse_scale_value(v, xcfg)) if xcfg else (lambda v: v),
        (lambda v: _inverse_scale_value(v, ycfg)) if ycfg else (lambda v: v),
    )


def _gating_store_path(ctx, task_def) -> Path | None:
    """Where a Gating task's gate tree lives (its own module folder)."""
    if ctx.project is None or not task_def.get("id"):
        return None
    folder = _data_files._module_dir(ctx, task_def.get("id"))
    return folder / "gates.json" if folder is not None else None


def _gating_store_load(ctx, task_def) -> dict:
    """Load ``{files, gates, per_file}`` for a Gating task (cached).

    Migrates a legacy project whose gates still live in ``params.gates``:
    the gates are written out to ``gates.json`` once and the inline copy is
    dropped.
    """
    import numpy as np  # noqa: F401  (kept for parity with other loaders)

    cache = getattr(ctx, "_gating_store_cache", None)
    if cache is None:
        cache = {}
        ctx._gating_store_cache = cache
    path = _gating_store_path(ctx, task_def)
    key = str(path) if path is not None else None
    if key is not None and key in cache:
        return cache[key]

    store = {"files": [], "gates": [], "per_file": {}}
    loaded = False
    if path is not None and path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            store["files"] = list(data.get("files") or [])
            store["gates"] = list(data.get("gates") or [])
            store["per_file"] = dict(data.get("per_file") or {})
            loaded = True
        except (OSError, ValueError, TypeError):
            store = {"files": [], "gates": [], "per_file": {}}

    # Legacy inline gates (older projects): migrate once.
    legacy = (task_def.get("params") or {}).get("gates")
    if not loaded and isinstance(legacy, list):
        store["gates"] = legacy
        store["files"] = [
            rec.get("parquet")
            for rec in _data_files._available_files(ctx, task_def.get("id"))
        ]
        _gating_store_save(ctx, task_def, store)
        params = task_def.setdefault("params", {})
        params.pop("gates", None)

    if key is not None:
        if len(cache) > 32:
            cache.clear()
        cache[key] = store
    return store


def _gating_store_save(ctx, task_def, store) -> None:
    """Write a Gating task's gate tree to its module folder."""
    path = _gating_store_path(ctx, task_def)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "files": list(store.get("files") or []),
        "gates": list(store.get("gates") or []),
        "per_file": dict(store.get("per_file") or {}),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    cache = getattr(ctx, "_gating_store_cache", None)
    if cache is not None:
        cache.pop(str(path), None)


def _build_gating_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Gating page: gate tree (1) | gate area (3) | settings (2)."""
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
    close_btn.clicked.connect(lambda _=False: _gating_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    # Per-page state (preview config first so the plot widget can bind A/F).
    _widgets_gate_editor_editor._gating_state_init(ctx, page, task_def, locked)
    page._gating_save_btn = save_btn
    save_btn.clicked.connect(lambda _=False: _gating_save_now(ctx, page))
    _gating_update_save_btn(ctx, page)

    layout.addWidget(_widgets_gate_editor_editor._gating_build_content(ctx, page, locked), 1)
    return page


def _gating_file_records(ctx, page) -> list:
    """The selected files as (parquet, display-name) records."""
    records = _data_files._available_files(ctx, page._gating_task_id)
    selected = set(page._gating_files or [])
    return [
        rec for rec in records if rec.get("parquet") in selected
    ]


def _gating_refresh_files(ctx, page) -> None:
    """Fill the left 'Data files' list and the right File combo's scope."""
    if not getattr(page, "_gating_uses_store", False):
        return
    # Drop selected files that are no longer visible (hidden/renamed).
    visible = {
        rec.get("parquet") for rec in _data_files._available_files(ctx, page._gating_task_id)
    }
    page._gating_files = [p for p in (page._gating_files or []) if p in visible]
    box = getattr(page, "_gating_files_box", None)
    if box is not None:
        box.blockSignals(True)
        box.clear()
        current = page._gating_preview.get("file")
        for path in page._gating_files:
            item = QListWidgetItem(
                _data_naming._comp_file_name(ctx, path, page._gating_task_id)
            )
            item.setData(Qt.ItemDataRole.UserRole, path)
            box.addItem(item)
            if path == current:
                box.setCurrentItem(item)
        box.blockSignals(False)
    count = getattr(page, "_gating_files_count", None)
    if count is not None:
        count.setText(f"{len(page._gating_files)} file(s) selected")
    # The preview file must be one of the selected files.
    preview = page._gating_preview
    if preview.get("file") not in page._gating_files and page._gating_files:
        rec = next(
            (r for r in _data_files._available_files(ctx, page._gating_task_id)
             if r.get("parquet") == page._gating_files[0]), None
        )
        if rec is not None:
            cols = _data_columns._columns_for(ctx,
                rec.get("parquet"), page._gating_task_id
            )
            preview["file"] = rec.get("parquet")
            preview["file_name"] = rec.get("name")
            if preview.get("x") not in cols:
                preview["x"] = None
            if preview.get("y") not in cols:
                preview["y"] = None


def _gating_select_files(ctx, page) -> None:
    """Pick the files this gate tree applies to."""
    records = _data_files._available_files(ctx, page._gating_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, p, page._gating_task_id)
        for p in (page._gating_files or [])
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._gating_files = [by_name[n] for n in chosen if n in by_name]
    # Per-file overrides for files no longer selected are dropped.
    drop = set(page._gating_files or [])
    for group, overrides in list(page._gating_per_file.items()):
        page._gating_per_file[group] = {
            f: g for f, g in overrides.items() if f in drop
        }
    _gating_refresh_files(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_make_plot(ctx, page):
    """Create the gating scatter plot (sci axes + A/F buttons + gate ViewBox)."""
    viewbox = _GateViewBox(
        on_gate_created=lambda tool, roi: _gating_add_gate(ctx, page, tool, roi),
        on_empty_click=lambda: _gating_deselect(ctx, page),
    )
    viewbox.gate_color = _gate_color(page._gating_preview)
    plot_item = _make_fit_plot_item(viewbox=viewbox)
    plot_widget = pg.PlotWidget(plotItem=plot_item)
    plot_widget.setBackground(_plot_background())
    plot_widget.showGrid(x=True, y=True, alpha=0.2)
    plot_widget.getPlotItem().setAxisItems({
        "bottom": _make_sci_axis("bottom"),
        "left": _make_sci_axis("left"),
    })
    viewbox.setMouseEnabled(True, True)
    try:
        plot_item.autoBtn.clicked.disconnect()
    except (RuntimeError, TypeError):
        pass
    plot_item.autoBtn.clicked.connect(
        lambda _=None: _widgets_plot_renderer._figure_apply_ranges(ctx, page._gating_preview, plot_widget)
    )
    plot_item._on_fit = lambda: _widgets_plot_renderer._figure_fit_all(ctx, page._gating_preview, plot_widget)
    # Gate editing moves ROI items on top of the data.  The data items are
    # cached as device-coordinate pixmaps so those moves blit the cached
    # scatter/image instead of re-running the per-point paint.
    plot_widget._gate_cache_data = True
    return plot_widget, viewbox


def _gating_set_tool(ctx, page, tool) -> None:
    page._gating_tool = tool
    page._gating_viewbox.set_gate_tool(tool)
    for key, btn in page._gating_tool_buttons.items():
        btn.setChecked(key == tool)


def _gating_update_tools(ctx, page) -> None:
    """Enable only the gate tools that make sense for the current plot type."""
    ptype = page._gating_preview.get("type", "scatter")
    allowed = {
        "scatter": ("rect", "ellipse", "polygon", "quadrant",
                    "skewed_quadrant", "split", "range"),
        "image": ("rect", "ellipse", "polygon"),
        "histogram": ("range", "split"),
    }.get(ptype, ())
    for key, btn in page._gating_tool_buttons.items():
        btn.setEnabled(key in allowed and not page._gating_locked)
    if page._gating_tool not in allowed:
        _gating_set_tool(ctx, page, None)


def _gating_default_axes(ctx, cols, preview) -> tuple:
    """Pick X/Y parameters for a data plot of `cols`.

    Image files store pixel positions in ``x``/``y``; a scatter of those
    is just a picture of the sample, so they are used only when the file
    has no other numeric columns. The last parameters the user chose win
    when they still exist in the file.
    """
    usable = [c for c in cols if c not in ("x", "y")] or list(cols)

    def pick(preferred, avoid):
        for value in preferred:
            if value in usable and value != avoid:
                return value
        for column in usable:
            if column != avoid:
                return column
        return usable[0] if usable else None

    x = pick((preview.get("last_x"), preview.get("x")), None)
    y = pick((preview.get("last_y"), preview.get("y")), x)
    return x, y


def _gating_set_plot_type(ctx, page, ptype) -> None:
    """Switch the gating view between scatter / image / histogram.

    The current gate hierarchy is kept: when the user has double-clicked
    into a gate, the new view still shows only the points inside that gate.
    Axes and view range remembered by the active gate are reused only when
    that gate was drawn as the same kind of plot; otherwise the new view
    starts from the last-used parameters and auto-fits.
    """
    preview = page._gating_preview
    previous = preview.get("type", "scatter")
    if previous == ptype:
        return
    rec = _widgets_plot_renderer._figure_record(ctx, preview.get("file"))
    cols = _data_columns._columns_for(ctx,
        rec.get("parquet"), page._gating_task_id
    ) if rec else []
    gate_id = page._gating_active_gate_id or page._gating_context_gate_id
    gate = next(
        (g for g in page._gating_gates if g["id"] == gate_id), None
    ) if gate_id else None
    # A gate remembers the plot type it was drawn in (boolean gates have
    # none); its axes and ranges only make sense for that same type.
    gate_type = gate.get("plot_type") if gate is not None else None
    gate_axes_ok = gate is not None and gate_type in (None, ptype)
    preview["type"] = ptype

    if ptype == "image":
        if previous != "image":
            # Remember the data axes so Scatter/Histogram can come back to
            # them instead of the pixel columns.
            preview["last_x"] = preview.get("x")
            preview["last_y"] = preview.get("y")
        preview["x"] = "x" if "x" in cols else None
        preview["y"] = "y" if "y" in cols else None
        channels = [c for c in cols if c not in ("x", "y")]
        if preview.get("image_channel") not in channels:
            preview["image_channel"] = channels[0] if channels else None
    elif ptype == "histogram":
        x = gate.get("x") if gate_axes_ok and gate.get("x") else None
        if x not in cols:
            x, _ = _gating_default_axes(ctx, cols, preview)
        preview["x"] = x if x in cols else (cols[0] if cols else None)
        preview["y"] = None
    else:
        if gate_axes_ok and gate.get("x") and gate.get("y"):
            preview["x"] = gate["x"]
            preview["y"] = gate["y"]
        else:
            preview["x"], preview["y"] = _gating_default_axes(ctx,
                cols, preview
            )

    if ptype == "image" or previous == "image":
        # Pixel-space ranges mean nothing in a data plot (and vice versa);
        # fall back to auto-range unless the gate carries this type's range.
        for key in ("xmin", "xmax", "ymin", "ymax"):
            preview[key] = gate.get(key) if gate_axes_ok else None

    if ptype != "image":
        # The settings panel now shows these parameters, so "All Events"
        # should return to them later.
        page._gating_settings_x = preview.get("x")
        page._gating_settings_y = preview.get("y")

    _gating_update_tools(ctx, page)
    _gating_reload_rois(ctx, page)
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)
    _gating_mark_dirty(ctx, page)
    runlog.action(
        "gating.plot_type", module=str(page._gating_task_id),
        old=str(previous), new=str(ptype),
        x=preview.get("x"), y=preview.get("y"),
        active=str(page._gating_active_gate_id or ""),
    )


def _gating_restore_gate_display(ctx, page, gate) -> None:
    """Restore the plot type, axes, range and colour state a gate was drawn in."""
    preview = page._gating_preview
    plot_type_changed = False
    ptype = gate.get("plot_type")
    if ptype in ("scatter", "image", "histogram") and preview.get("type") != ptype:
        preview["type"] = ptype
        plot_type_changed = True
    # Boolean gates (and/or) carry no axes of their own; leave the current
    # ones. Histogram gates store y=None on purpose, so a histogram gate
    # clears the y axis while gates that never define one are skipped.
    if gate.get("x") is not None:
        preview["x"] = gate["x"]
    if gate.get("y") is not None:
        preview["y"] = gate["y"]
    elif gate.get("plot_type") == "histogram":
        preview["y"] = None
    for key in ("xmin", "xmax", "ymin", "ymax"):
        if gate.get(key) is not None:
            preview[key] = gate[key]
    for key, value in (gate.get("display") or {}).items():
        preview[key] = copy.deepcopy(value)
    if plot_type_changed:
        _gating_update_tools(ctx, page)
        _gating_reload_rois(ctx, page)


def _gating_file_key(ctx, page):
    preview = getattr(page, "_gating_preview", None)
    return preview.get("file") if isinstance(preview, dict) else None


def _gating_sync_gate(ctx, gate: dict, roi):
    """Extract the geometry dict for a rect/ellipse/polygon ROI."""
    t = gate.get("type")
    if t in ("rect", "ellipse"):
        return {
            "pos": [roi.pos().x(), roi.pos().y()],
            "size": [roi.size().x(), roi.size().y()],
        }
    if t == "polygon":
        ox = roi.pos().x()
        oy = roi.pos().y()
        return {
            "points": [[p.x() + ox, p.y() + oy] for p in roi.getState()["points"]]
        }
    return {}


def _gating_apply_geometry(ctx, page, gate, geometry) -> None:
    """Store one gate's geometry: shared, or the current file's override."""
    group = _gate_group_id(gate)
    if getattr(page, "_gating_uses_store", False) and group in page._gating_per_file:
        file_key = _gating_file_key(ctx, page)
        if file_key:
            page._gating_per_file.setdefault(group, {})[file_key] = copy.deepcopy(geometry)
        return
    for key, value in geometry.items():
        gate[key] = copy.deepcopy(value)


def _gating_apply_group_geometry(ctx, page, group_id, geometry) -> None:
    """Store a grouped gate's shared geometry (quadrant/split/skewed)."""
    if getattr(page, "_gating_uses_store", False) and group_id in page._gating_per_file:
        file_key = _gating_file_key(ctx, page)
        if file_key:
            page._gating_per_file.setdefault(group_id, {})[file_key] = copy.deepcopy(geometry)
        return
    for g in page._gating_gates:
        if _gate_group_id(g) == group_id:
            for key, value in geometry.items():
                g[key] = copy.deepcopy(value)


def _gating_on_roi_drag(ctx, page, gate, roi) -> None:
    geometry = _gating_sync_gate(ctx, gate, roi)
    geometry = _gating_geometry_to_real(ctx, page, gate, geometry)
    _gating_apply_geometry(ctx, page, gate, geometry)
    runlog.action(
        "gating.gate_moved", module=str(page._gating_task_id),
        gate=str(gate.get("id") or ""), type=str(gate.get("type") or ""),
        pos=geometry.get("pos"), size=geometry.get("size"),
    )
    _gating_schedule_adjust(ctx, page, {gate["id"]})
    _gating_mark_dirty(ctx, page)


def _gating_move_label(page, gate, display_geometry) -> None:
    """Move one gate's label to follow an in-progress geometry change.

    The geometry is already in plot units (it comes straight from a ROI or
    a movable line), so no scale conversion is needed here.
    """
    if gate.get("type") in ("not", "and", "or"):
        return
    labels = getattr(page, "_gating_labels", None)
    gates = getattr(page, "_gating_gates", None)
    if not labels or gates is None or len(labels) != len(gates):
        return
    idx = next((i for i, g in enumerate(gates) if g is gate), -1)
    if idx < 0:
        return
    px, py = _gate_label_position({**gate, **display_geometry})
    ox, oy = gate.get("label_offset") or [0.0, 0.0]
    labels[idx].setPos(px + ox, py + oy)


def _gating_on_roi_move(ctx, page, gate, roi) -> None:
    """Move the label with the ROI while dragging; statistics wait for release."""
    if roi is None:
        return
    _gating_move_label(page, gate, _gating_sync_gate(ctx, gate, roi))


def _gating_quadrant_sync(ctx, page, crosshair_id, key, line) -> None:
    entry = page._gating_crosshairs.get(crosshair_id)
    geometry = {}
    if entry is not None and len(entry["lines"]) == 2:
        vline, hline = entry["lines"]
        geometry = {"vx": vline.value(), "hy": hline.value()}
        gate = next(
            (g for g in page._gating_gates
             if g.get("crosshair_id") == crosshair_id), None
        )
        if gate is not None:
            geometry = _gating_geometry_to_real(ctx, page, gate, geometry)
    affected = {
        g["id"] for g in page._gating_gates
        if g.get("crosshair_id") == crosshair_id
    }
    _gating_apply_group_geometry(ctx, page, crosshair_id, geometry)
    _gating_schedule_adjust(ctx, page, affected)
    _gating_mark_dirty(ctx, page)


def _gating_split_sync(ctx, page, split_id, line) -> None:
    affected = {
        g["id"] for g in page._gating_gates
        if g.get("split_id") == split_id
    }
    geometry = {"vx": line.value()}
    gate = next(
        (g for g in page._gating_gates if g.get("split_id") == split_id), None
    )
    if gate is not None:
        geometry = _gating_geometry_to_real(ctx, page, gate, geometry)
    _gating_apply_group_geometry(ctx, page, split_id, geometry)
    _gating_schedule_adjust(ctx, page, affected)
    _gating_mark_dirty(ctx, page)


def _gating_range_sync(ctx, page, gate, key, line) -> None:
    idx = next(
        (i for i, g in enumerate(page._gating_gates) if g["id"] == gate["id"]),
        -1,
    )
    geometry = None
    if 0 <= idx < len(page._gating_rois) and len(page._gating_rois[idx]) == 2:
        items = page._gating_rois[idx]
        geometry = {"x1": items[0].value(), "x2": items[1].value()}
    if geometry is None:
        # The stored geometry is already in real units; only the dragged
        # line value comes from the plot.
        geometry = _gate_geometry(gate)
        converted = _gating_geometry_to_real(
            ctx, page, gate, {key: line.value()}
        )
        geometry[key] = converted.get(key, line.value())
    else:
        geometry = _gating_geometry_to_real(ctx, page, gate, geometry)
    _gating_apply_geometry(ctx, page, gate, geometry)
    _gating_schedule_adjust(ctx, page, {gate["id"]})
    _gating_mark_dirty(ctx, page)


def _gating_skewed_sync(ctx, page, skewed_id) -> None:
    entry = page._gating_skewed_lines.get(skewed_id)
    if entry is None:
        return
    roi = entry["roi"]
    pts = [h.pos() for h in roi.getHandles()]
    if len(pts) < 5:
        return
    center = pts[0]
    keys = ("p_right", "p_up", "p_left", "p_down")
    geometry = {"center": [center.x(), center.y()]}
    for i, key in enumerate(keys):
        sp = pts[i + 1]
        geometry[key] = [sp.x(), sp.y()]
    affected = {
        g["id"] for g in page._gating_gates
        if g.get("skewed_id") == skewed_id
    }
    gate = next(
        (g for g in page._gating_gates if g.get("skewed_id") == skewed_id),
        None,
    )
    if gate is not None:
        geometry = _gating_geometry_to_real(ctx, page, gate, geometry)
    _gating_apply_group_geometry(ctx, page, skewed_id, geometry)
    _gating_schedule_adjust(ctx, page, affected)
    _gating_mark_dirty(ctx, page)


def _gating_group_move(page, group_key, display) -> None:
    """Move a grouped gate's labels while dragging (no statistics yet)."""
    affected = {
        g["id"] for g in page._gating_gates if g.get(group_key[0]) == group_key[1]
    }
    for g in page._gating_gates:
        if g.get("id") in affected:
            _gating_move_label(page, g, display)


def _gating_quadrant_move(ctx, page, crosshair_id, key, line) -> None:
    entry = page._gating_crosshairs.get(crosshair_id)
    if entry is None or len(entry["lines"]) != 2:
        return
    gate = next(
        (g for g in page._gating_gates
         if g.get("crosshair_id") == crosshair_id), None
    )
    if gate is None:
        return
    vline, hline = entry["lines"]
    display = {"vx": vline.value(), "hy": hline.value()}
    _gating_group_move(page, ("crosshair_id", crosshair_id), display)


def _gating_split_move(ctx, page, split_id, line) -> None:
    gate = next(
        (g for g in page._gating_gates if g.get("split_id") == split_id), None
    )
    if gate is None:
        return
    _gating_group_move(page, ("split_id", split_id), {"vx": line.value()})


def _gating_range_move(ctx, page, gate, key, line) -> None:
    idx = next(
        (i for i, g in enumerate(page._gating_gates) if g["id"] == gate["id"]),
        -1,
    )
    if not (0 <= idx < len(page._gating_rois) and len(page._gating_rois[idx]) == 2):
        return
    items = page._gating_rois[idx]
    _gating_move_label(page, gate, {"x1": items[0].value(), "x2": items[1].value()})


def _gating_skewed_move(ctx, page, skewed_id) -> None:
    entry = page._gating_skewed_lines.get(skewed_id)
    if entry is None:
        return
    pts = [h.pos() for h in entry["roi"].getHandles()]
    if len(pts) < 5:
        return
    gate = next(
        (g for g in page._gating_gates if g.get("skewed_id") == skewed_id), None
    )
    if gate is None:
        return
    display = {"center": [pts[0].x(), pts[0].y()]}
    for i, key in enumerate(("p_right", "p_up", "p_left", "p_down")):
        display[key] = [pts[i + 1].x(), pts[i + 1].y()]
    _gating_group_move(page, ("skewed_id", skewed_id), display)


def _gating_default_name(ctx, page, tool: str) -> str:
    prefixes = {"rect": "Rect", "ellipse": "Oval", "polygon": "Poly", "range": "Range"}
    count = sum(1 for g in page._gating_gates if g.get("type") == tool) + 1
    return f"{prefixes.get(tool, 'Gate')} {count}"


def _gating_col2gate_parquets(ctx, page) -> list:
    """The data files this gate tree applies to (Col2Gate's scope)."""
    if page._gating_files:
        return list(page._gating_files)
    # Embedded gate trees (Unmix) have no file selection: every file the
    # task can see is used.
    return [
        rec.get("parquet")
        for rec in _data_files._available_files(ctx, page._gating_task_id)
    ]


def _gating_col2gate_columns(ctx, page) -> list:
    """Union of every column across the gating module's data files."""
    columns = []
    seen = set()
    for parquet in _gating_col2gate_parquets(ctx, page):
        for name in _data_columns._columns_for(ctx, parquet, page._gating_task_id):
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns


def _gating_col2gate_counts(ctx, page, column) -> list:
    """Return ``[(value, count), ...]`` for `column` across the selected files."""
    from collections import Counter

    import pandas as pd

    counter = Counter()
    for parquet in _gating_col2gate_parquets(ctx, page):
        values = _data_columns._column_data(ctx,
            page._gating_task_id, parquet, [column], numeric=False
        ).get(column)
        if values is None or len(values) == 0:
            continue
        for value, count in pd.Series(values).dropna().value_counts().items():
            native = value.item() if hasattr(value, "item") else value
            counter[native] += int(count)

    def sort_key(item):
        value = item[0]
        if isinstance(value, (int, float)):
            return (0, value)
        return (1, str(value))

    return sorted(counter.items(), key=sort_key)


def _gating_open_col2gate(ctx, page) -> None:
    """Open the Col2Gate dialog: turn one column's categories into gates."""
    columns = _gating_col2gate_columns(ctx, page)
    if not columns:
        QMessageBox.information(
            ctx.host, "Col2Gate",
            "No columns are available in the selected data files.",
        )
        return

    dialog = QDialog(ctx.host)
    dialog.setWindowTitle("Col2Gate")
    dialog.resize(720, 480)
    root = QHBoxLayout(dialog)
    root.setContentsMargins(12, 12, 12, 12)
    root.setSpacing(12)

    left = QVBoxLayout()
    left.setSpacing(8)
    left.addWidget(QLabel("<b>Column</b>"))
    col_combo = _NoWheelComboBox()
    for name in columns:
        col_combo.addItem(str(name))
    left.addWidget(col_combo)
    check_btn = QPushButton("Check")
    check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    left.addWidget(check_btn)
    left.addStretch()

    right = QVBoxLayout()
    right.setSpacing(8)
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Value", "Count"])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.ResizeMode.Stretch
    )
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    right.addWidget(table, 1)

    prefix_label = QLabel("Prefix")
    right.addWidget(prefix_label)
    prefix_edit = QLineEdit("cate_")
    right.addWidget(prefix_edit)
    set_btn = QPushButton("Set as gates")
    set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    right.addWidget(set_btn)

    for widget in (table, prefix_label, prefix_edit, set_btn):
        widget.setVisible(False)

    root.addLayout(left, 0)
    root.addLayout(right, 1)

    state = {"rows": []}
    cate_max = int(_setting("gating", "col2gate_cate_max", 100))

    def on_check():
        column = col_combo.currentText()
        if not column:
            return
        counts = _gating_col2gate_counts(ctx, page, column)
        if len(counts) > cate_max:
            for widget in (table, prefix_label, prefix_edit, set_btn):
                widget.setVisible(False)
            QMessageBox.warning(
                dialog, "Col2Gate",
                f"Possible value count exceeds the allowed maximum "
                f"({cate_max}).\nCheck the column or choose another column.",
            )
            return
        state["rows"] = counts
        table.setRowCount(len(counts))
        for row, (value, count) in enumerate(counts):
            table.setItem(
                row, 0, QTableWidgetItem(_category_value_label(value))
            )
            table.setItem(row, 1, QTableWidgetItem(f"{count:,}"))
        for widget in (table, prefix_label, prefix_edit, set_btn):
            widget.setVisible(True)

    def on_set():
        counts = state.get("rows")
        if not counts:
            return
        column = col_combo.currentText()
        prefix = prefix_edit.text().strip()
        reply = QMessageBox.question(
            dialog, "Col2Gate",
            f"Add {len(counts)} gate(s) under the current gate?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        _gating_col2gate_generate(ctx, page, column, prefix, counts)
        dialog.accept()

    check_btn.clicked.connect(on_check)
    set_btn.clicked.connect(on_set)
    dialog.exec()


def _gating_col2gate_generate(ctx, page, column, prefix, counts) -> None:
    """Create one ``<prefix><value>`` child gate per category value."""
    preview = page._gating_preview
    xr, yr = page._gating_viewbox.viewRange()
    added = []
    for value, _count in counts:
        label = _category_value_label(value)
        gate = {
            "id": uuid4().hex[:8],
            "name": f"{prefix}{label}",
            "type": "categorical",
            "x": column,
            "y": None,
            "value": value,
            "parent": page._gating_parent_id,
            "plot_type": preview.get("type", "scatter"),
            "display": _gating_display_snapshot(preview),
            "xmin": xr[0], "xmax": xr[1],
            "ymin": yr[0], "ymax": yr[1],
        }
        page._gating_gates.append(gate)
        page._gating_rois.append([])
        added.append(gate)

    page._gating_context_gate_id = added[-1]["id"] if added else None
    _gating_reload_rois(ctx, page)
    _gating_update_roi_visibility(ctx, page)
    _gating_rebuild_tree(ctx, page)
    _gating_mark_dirty(ctx, page)
    runlog.action(
        "gating.col2gate", module=str(page._gating_task_id),
        column=str(column), gates=len(added),
        parent=str(page._gating_parent_id or ""),
    )


def _gating_discard_items(page, *items) -> None:
    """Remove transient gate items from the plot (the overlay draws them)."""
    viewbox = getattr(page, "_gating_viewbox", None)
    if viewbox is None:
        return
    for item in items:
        try:
            if item is not None and item.scene() is not None:
                _detach_gate_item(item)
                viewbox.removeItem(item)
        except (RuntimeError, AttributeError):
            pass


def _gating_add_gate(ctx, page, tool, arg) -> None:
    """Create a gate and record the action in the session log."""
    before = len(page._gating_gates)
    try:
        _gating_add_gate_impl(ctx, page, tool, arg)
    finally:
        added = page._gating_gates[before:]
        if added:
            _gating_log_gates(ctx, page, tool, added)


def _gating_log_gates(ctx, page, tool, gates) -> None:
    preview = page._gating_preview
    first = gates[0]
    fields = {
        "module": str(page._gating_task_id), "tool": str(tool),
        "count": len(gates), "plot": str(preview.get("type")),
        "x": preview.get("x"), "y": preview.get("y"),
        "parent": str(first.get("parent") or ""),
    }
    if first.get("pos") is not None:
        fields["pos"] = first.get("pos")
        fields["size"] = first.get("size")
    elif first.get("points") is not None:
        fields["points"] = len(first.get("points") or [])
    elif first.get("vx") is not None:
        fields["vx"] = first.get("vx")
        fields["hy"] = first.get("hy")
    runlog.action("gating.gate_created", **fields)


def _gating_add_gate_impl(ctx, page, tool, arg) -> None:
    preview = page._gating_preview
    gate_color = _gate_color(preview)
    xr, yr = page._gating_viewbox.viewRange()
    if tool == "quadrant":
        x0, y0 = arg
        crosshair_id = uuid4().hex[:8]
        pen = pg.mkPen(_gate_color(preview), width=2)
        hover = pg.mkPen(_gate_hover_color(_gate_color(preview)), width=3)
        vline = pg.InfiniteLine(pos=x0, angle=90, movable=True, pen=pen, hoverPen=hover)
        hline = pg.InfiniteLine(pos=y0, angle=0, movable=True, pen=pen, hoverPen=hover)
        page._gating_viewbox.addItem(vline, ignoreBounds=True)
        page._gating_viewbox.addItem(hline, ignoreBounds=True)
        page._gating_crosshairs[crosshair_id] = {
            "lines": [vline, hline],
            "x": preview.get("x"),
            "y": preview.get("y"),
        }
        real = _gating_preview_geometry_to_real(
            ctx, page, "quadrant", {"vx": x0, "hy": y0}
        )
        for qnum in (1, 2, 3, 4):
            gate = {
                "id": uuid4().hex[:8],
                "name": f"Q{qnum}",
                "type": "quadrant",
                "quadrant": qnum,
                "vx": real["vx"],
                "hy": real["hy"],
                "crosshair_id": crosshair_id,
                "x": preview.get("x"),
                "y": preview.get("y"),
                "parent": page._gating_parent_id,
                "plot_type": preview.get("type", "scatter"),
                "display": _gating_display_snapshot(preview),
                "xmin": xr[0], "xmax": xr[1],
                "ymin": yr[0], "ymax": yr[1],
            }
            page._gating_gates.append(gate)
            page._gating_rois.append([])
        vline.sigPositionChangeFinished.connect(
            lambda l=vline, cid=crosshair_id: _gating_quadrant_sync(ctx, page, cid, "vx", l)
        )
        hline.sigPositionChangeFinished.connect(
            lambda l=hline, cid=crosshair_id: _gating_quadrant_sync(ctx, page, cid, "hy", l)
        )
        vline.sigPositionChanged.connect(
            lambda l=vline, cid=crosshair_id: _gating_quadrant_move(ctx, page, cid, "vx", l)
        )
        hline.sigPositionChanged.connect(
            lambda l=hline, cid=crosshair_id: _gating_quadrant_move(ctx, page, cid, "hy", l)
        )
        page._gating_context_gate_id = page._gating_gates[-1]["id"]
        _gating_update_roi_visibility(ctx, page)
        _gating_rebuild_tree(ctx, page)
        _gating_set_tool(ctx, page, None)
        _gating_mark_dirty(ctx, page)
        return

    if tool == "range":
        x1, x2 = arg
        real = _gating_preview_geometry_to_real(
            ctx, page, "range", {"x1": x1, "x2": x2}
        )
        gate = {
            "id": uuid4().hex[:8],
            "name": _gating_default_name(ctx, page, "range"),
            "type": "range",
            "x": preview.get("x"),
            "y": preview.get("y"),
            "x1": real["x1"],
            "x2": real["x2"],
            "parent": page._gating_parent_id,
            "plot_type": preview.get("type", "scatter"),
            "display": _gating_display_snapshot(preview),
            "xmin": xr[0], "xmax": xr[1],
            "ymin": yr[0], "ymax": yr[1],
        }
        pen = pg.mkPen(_gate_color(preview), width=2)
        hover = pg.mkPen(_gate_hover_color(_gate_color(preview)), width=3)
        vline1 = pg.InfiniteLine(pos=x1, angle=90, movable=True, pen=pen, hoverPen=hover)
        vline2 = pg.InfiniteLine(pos=x2, angle=90, movable=True, pen=pen, hoverPen=hover)
        page._gating_viewbox.addItem(vline1, ignoreBounds=True)
        page._gating_viewbox.addItem(vline2, ignoreBounds=True)
        _gating_setup_line(ctx, page, vline1, gate["id"])
        _gating_setup_line(ctx, page, vline2, gate["id"])
        vline1.sigPositionChangeFinished.connect(
            lambda l=vline1, g=gate: _gating_range_sync(ctx, page, g, "x1", l)
        )
        vline2.sigPositionChangeFinished.connect(
            lambda l=vline2, g=gate: _gating_range_sync(ctx, page, g, "x2", l)
        )
        vline1.sigPositionChanged.connect(
            lambda l=vline1, g=gate: _gating_range_move(ctx, page, g, "x1", l)
        )
        vline2.sigPositionChanged.connect(
            lambda l=vline2, g=gate: _gating_range_move(ctx, page, g, "x2", l)
        )
        page._gating_gates.append(gate)
        page._gating_rois.append([vline1, vline2])
        _gating_set_items_handles_visible(ctx, [vline1, vline2], False)
        page._gating_context_gate_id = page._gating_gates[-1]["id"]
        _gating_update_roi_visibility(ctx, page)
        _gating_rebuild_tree(ctx, page)
        _gating_set_tool(ctx, page, None)
        _gating_mark_dirty(ctx, page)
        return

    if tool == "split":
        x0, _ = arg
        split_id = uuid4().hex[:8]
        pen = pg.mkPen(_gate_color(preview), width=2)
        hover = pg.mkPen(_gate_hover_color(_gate_color(preview)), width=3)
        vline = pg.InfiniteLine(pos=x0, angle=90, movable=True, pen=pen, hoverPen=hover)
        page._gating_viewbox.addItem(vline, ignoreBounds=True)
        page._gating_split_lines[split_id] = {
            "lines": [vline],
            "x": preview.get("x"),
            "y": preview.get("y"),
        }
        real = _gating_preview_geometry_to_real(
            ctx, page, "split", {"vx": x0}
        )
        for side, name in (("left", "Left"), ("right", "Right")):
            gate = {
                "id": uuid4().hex[:8],
                "name": name,
                "type": "split",
                "side": side,
                "vx": real["vx"],
                "split_id": split_id,
                "x": preview.get("x"),
                "y": preview.get("y"),
                "parent": page._gating_parent_id,
                "plot_type": preview.get("type", "scatter"),
                "display": _gating_display_snapshot(preview),
                "xmin": xr[0], "xmax": xr[1],
                "ymin": yr[0], "ymax": yr[1],
            }
            page._gating_gates.append(gate)
            page._gating_rois.append([])
        vline.sigPositionChangeFinished.connect(
            lambda l=vline, cid=split_id: _gating_split_sync(ctx, page, cid, l)
        )
        vline.sigPositionChanged.connect(
            lambda l=vline, cid=split_id: _gating_split_move(ctx, page, cid, l)
        )
        page._gating_context_gate_id = page._gating_gates[-1]["id"]
        _gating_update_roi_visibility(ctx, page)
        _gating_rebuild_tree(ctx, page)
        _gating_set_tool(ctx, page, None)
        _gating_mark_dirty(ctx, page)
        return

    if tool == "skewed_quadrant":
        center, p_right, p_up, p_left, p_down = arg
        cx, cy = center
        skewed_id = uuid4().hex[:8]
        roi = _SkewedQuadrantROI(
            center, p_right, p_up, p_left, p_down, **_gate_roi_pens(gate_color),
        )
        _gating_style_handles(ctx, roi)
        roi.setAcceptHoverEvents(True)
        roi.setCursor(Qt.CursorShape.CrossCursor)
        roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        page._gating_viewbox.addItem(roi, ignoreBounds=True)
        page._gating_skewed_lines[skewed_id] = {
            "roi": roi,
            "x": preview.get("x"),
            "y": preview.get("y"),
        }
        real = _gating_preview_geometry_to_real(
            ctx, page, "skewed_quadrant", {
                "center": [cx, cy],
                "p_right": list(p_right),
                "p_up": list(p_up),
                "p_left": list(p_left),
                "p_down": list(p_down),
            },
        )
        for qnum in (1, 2, 3, 4):
            gate = {
                "id": uuid4().hex[:8],
                "name": f"Q{qnum}",
                "type": "skewed_quadrant",
                "quadrant": qnum,
                "center": real["center"],
                "p_right": real["p_right"],
                "p_up": real["p_up"],
                "p_left": real["p_left"],
                "p_down": real["p_down"],
                "skewed_id": skewed_id,
                "x": preview.get("x"),
                "y": preview.get("y"),
                "parent": page._gating_parent_id,
                "plot_type": preview.get("type", "scatter"),
                "display": _gating_display_snapshot(preview),
                "xmin": xr[0], "xmax": xr[1],
                "ymin": yr[0], "ymax": yr[1],
            }
            page._gating_gates.append(gate)
            page._gating_rois.append([])
        roi.sigRegionChangeFinished.connect(
            lambda: _gating_skewed_sync(ctx, page, skewed_id)
        )
        roi.sigRegionChanged.connect(
            lambda r=roi, s=skewed_id: _gating_skewed_move(ctx, page, s)
        )
        roi.sigClicked.connect(
            lambda *a: _gating_select_gate_by_id(ctx,
                page, page._gating_gates[-1]["id"]
            )
        )
        _gating_set_items_handles_visible(ctx, [roi], False)
        page._gating_context_gate_id = page._gating_gates[-1]["id"]
        _gating_update_roi_visibility(ctx, page)
        _gating_rebuild_tree(ctx, page)
        _gating_set_tool(ctx, page, None)
        _gating_mark_dirty(ctx, page)
        return

    gate = {
        "id": uuid4().hex[:8],
        "name": _gating_default_name(ctx, page, tool),
        "type": tool,
        "x": preview.get("x"),
        "y": preview.get("y"),
        "parent": page._gating_parent_id,
        "plot_type": preview.get("type", "scatter"),
        "display": _gating_display_snapshot(preview),
        "xmin": xr[0], "xmax": xr[1],
        "ymin": yr[0], "ymax": yr[1],
    }
    runlog.action("gating.create", module=str(page._gating_task_id),
                  tool=str(tool), id=str(gate["id"]))
    geometry = _gating_sync_gate(ctx, gate, arg)
    geometry = _gating_geometry_to_real(ctx, page, gate, geometry)
    for key, value in geometry.items():
        gate[key] = copy.deepcopy(value)
    runlog.action("gating.create.geometry", id=str(gate["id"]))
    _gating_style_handles(ctx, arg)
    runlog.action("gating.create.handles", id=str(gate["id"]))
    arg.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
    arg.setAcceptHoverEvents(True)
    arg.setCursor(Qt.CursorShape.CrossCursor)
    arg.sigRegionChangeFinished.connect(
        lambda r=arg, g=gate: _gating_on_roi_drag(ctx, page, g, r)
    )
    arg.sigRegionChanged.connect(
        lambda r=arg, g=gate: _gating_on_roi_move(ctx, page, g, r)
    )
    arg.sigClicked.connect(
        lambda roi, ev, g=gate: _gating_gate_clicked(ctx, page, g["id"], ev)
    )
    page._gating_gates.append(gate)
    page._gating_rois.append([arg])
    _gating_set_items_handles_visible(ctx, [arg], False)
    page._gating_context_gate_id = gate["id"]
    runlog.action("gating.create.visibility", id=str(gate["id"]))
    _gating_update_roi_visibility(ctx, page)
    runlog.action("gating.create.tree", id=str(gate["id"]))
    _gating_rebuild_tree(ctx, page)
    _gating_set_tool(ctx, page, None)
    _gating_mark_dirty(ctx, page)
    runlog.action("gating.create.done", id=str(gate["id"]))


def _gating_style_handles(ctx, roi) -> None:
    """Make a gate ROI's handles round and a bit larger."""
    roi.handleSize = 10
    for h in roi.handles:
        item = h["item"]
        item.radius = 10
        item.sides = 24
        item.buildPath()


def _gating_set_items_handles_visible(ctx, items, visible: bool) -> None:
    """Show a gate's handles/nodes only when selected."""
    from shiboken6 import isValid

    for item in items:
        if not isValid(item):
            continue
        if isinstance(item, pg.InfiniteLine):
            # Lines have no separate handles; keep them movable/hoverable.
            continue
        try:
            for h in item.handles:
                handle = h["item"]
                if isValid(handle):
                    handle.setVisible(visible)
        except (RuntimeError, AttributeError):
            continue


def _gating_show_handles(ctx, page, idx) -> None:
    """Show handles for the gate at `idx`, hiding every other gate's."""
    for i, gate_items in enumerate(page._gating_rois):
        _gating_set_items_handles_visible(ctx, gate_items, i == idx)
    selected_skewed = None
    if 0 <= idx < len(page._gating_gates):
        selected_skewed = page._gating_gates[idx].get("skewed_id")
    for sid, entry in page._gating_skewed_lines.items():
        _gating_set_items_handles_visible(ctx,
            [entry["roi"]], sid == selected_skewed
        )


def _gating_setup_line(ctx, page, line, gate_id) -> None:
    """Give an InfiniteLine a crosshair cursor and click-to-select."""
    line.setCursor(Qt.CursorShape.CrossCursor)
    line.sigClicked.connect(
        lambda *a, gid=gate_id: _gating_select_gate_by_id(ctx, page, gid)
    )


def _gating_select_gate_by_id(ctx, page, gate_id) -> None:
    """Select a gate by clicking it in the plot (full selection + tree sync)."""
    idx = -1
    for i, g in enumerate(page._gating_gates):
        if g["id"] == gate_id:
            idx = i
            break
    if idx < 0:
        return
    tree = page._gating_tree
    if page._gating_context_gate_id == gate_id:
        # Already selected: sync the tree highlight only. A re-render and a
        # settings rebuild here made every fine adjustment feel slow.
        tree.blockSignals(True)
        item = _gating_tree_item_for_index(ctx, page, idx)
        if item is not None:
            item.setSelected(True)
            tree.setCurrentItem(item)
        tree.blockSignals(False)
        return
    _gating_apply_selection(ctx, page, idx)
    tree.blockSignals(True)
    tree.clearSelection()
    item = _gating_tree_item_for_index(ctx, page, idx)
    if item is not None:
        item.setSelected(True)
        tree.setCurrentItem(item)
    tree.blockSignals(False)
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)


def _gating_deselect(ctx, page) -> None:
    """Deselect all gates (click on empty plot space), without changing the view."""
    _gating_show_handles(ctx, page, -1)
    tree = page._gating_tree
    tree.blockSignals(True)
    tree.clearSelection()
    tree.blockSignals(False)


def _gating_open_gate_subset(ctx, page, gate_id) -> None:
    """Open a gate's subset view (same as double-clicking it in the tree)."""
    idx = -1
    for i, g in enumerate(page._gating_gates):
        if g["id"] == gate_id:
            idx = i
            break
    preview = page._gating_preview
    if idx < 0:
        page._gating_active_gate_id = None
        page._gating_parent_id = None
        page._gating_context_gate_id = None
    else:
        gate = page._gating_gates[idx]
        page._gating_active_gate_id = gate["id"]
        page._gating_context_gate_id = None
        page._gating_parent_id = gate["id"]
        _gating_restore_gate_display(ctx, page, gate)
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)
    tree = page._gating_tree
    tree.blockSignals(True)
    tree.clearSelection()
    item = _gating_tree_item_for_index(ctx, page, idx)
    if item is not None:
        item.setSelected(True)
        tree.setCurrentItem(item)
    tree.blockSignals(False)


def _gating_gate_clicked(ctx, page, gate_id, ev) -> None:
    """Handle a click on a gate ROI: double-click opens the subset view."""
    if ev is not None and ev.double():
        _gating_open_gate_subset(ctx, page, gate_id)
    else:
        _gating_select_gate_by_id(ctx, page, gate_id)


def _gating_affected_ids(ctx, page, gate_ids) -> set:
    """Every gate whose count/percent changes when `gate_ids` change.

    That covers the gates themselves, their tree descendants (a child's
    percentage divides by its parent's count) and any boolean gate that
    references one of them as a source — plus that boolean gate's own
    descendants, transitively (``NOT (A OR B)`` must follow a moved A).
    """
    gates = page._gating_gates
    by_parent = {}
    for g in gates:
        by_parent.setdefault(g.get("parent"), []).append(g)
    affected = set(gate_ids)

    def add_descendants(gid) -> None:
        stack = [gid]
        while stack:
            pid = stack.pop()
            for child in by_parent.get(pid, []):
                cid = child.get("id")
                if cid and cid not in affected:
                    affected.add(cid)
                    stack.append(cid)

    for gid in tuple(affected):
        add_descendants(gid)
    sources = {
        g["id"]: set(_gate_source_ids(g))
        for g in gates
        if g.get("type") in ("not", "and", "or") and g.get("id")
    }
    changed = True
    while changed:
        changed = False
        for gid, refs in sources.items():
            if gid not in affected and refs & affected:
                affected.add(gid)
                add_descendants(gid)
                changed = True
    return affected


def _gating_visible_ids(ctx, page):
    """Return the set of gate ids to show for the current view mode."""
    active = page._gating_active_gate_id
    if active:
        # Subset view: show only this gate's direct children (the gates
        # you can draw inside it), not their deeper descendants.
        return {g["id"] for g in page._gating_gates if g.get("parent") == active}
    context = page._gating_context_gate_id
    if context:
        ctx = next((g for g in page._gating_gates if g["id"] == context), None)
        if ctx is not None:
            p = ctx.get("parent")
            return {g["id"] for g in page._gating_gates if g.get("parent") == p}
        return set()
    # "All Events" view: no gate is bound to it, so show nothing.
    return set()


def _gating_update_roi_visibility(ctx, page) -> None:
    """Show gates bound to the current (x, y) axes and gating-tree context."""
    from shiboken6 import isValid

    overlay = getattr(page, "_gating_gate_overlay", None)
    if overlay is not None:
        overlay.update()

    def set_visible(item, visible):
        if isValid(item):
            item.setVisible(visible)

    x = page._gating_preview.get("x")
    y = page._gating_preview.get("y")
    visible_ids = _gating_visible_ids(ctx, page)
    visible_crosshairs = None
    visible_splits = None
    visible_skewed = None
    if visible_ids is not None:
        visible_crosshairs = set()
        visible_splits = set()
        visible_skewed = set()
        for g in page._gating_gates:
            if g.get("id") not in visible_ids:
                continue
            if g.get("crosshair_id"):
                visible_crosshairs.add(g.get("crosshair_id"))
            if g.get("split_id"):
                visible_splits.add(g.get("split_id"))
            if g.get("skewed_id"):
                visible_skewed.add(g.get("skewed_id"))
    for gate, items in zip(page._gating_gates, page._gating_rois):
        xy_ok = gate.get("x") == x and gate.get("y") == y
        hier_ok = visible_ids is None or gate.get("id") in visible_ids
        visible = xy_ok and hier_ok
        for item in items:
            set_visible(item, visible)
    for cid, entry in page._gating_crosshairs.items():
        xy_ok = entry.get("x") == x and entry.get("y") == y
        hier_ok = visible_crosshairs is None or cid in visible_crosshairs
        visible = xy_ok and hier_ok
        for line in entry["lines"]:
            set_visible(line, visible)
    for sid, entry in page._gating_split_lines.items():
        xy_ok = entry.get("x") == x and entry.get("y") == y
        hier_ok = visible_splits is None or sid in visible_splits
        visible = xy_ok and hier_ok
        for line in entry["lines"]:
            set_visible(line, visible)
    for sid, entry in page._gating_skewed_lines.items():
        xy_ok = entry.get("x") == x and entry.get("y") == y
        hier_ok = visible_skewed is None or sid in visible_skewed
        visible = xy_ok and hier_ok
        set_visible(entry["roi"], visible)
    _gating_update_labels(ctx, page)


def _gating_set_label_style(ctx, page, label) -> None:
    """Apply the configured font size / weight / background to one label."""
    preview = page._gating_preview
    size = int(preview.get("label_font_size", 11))
    bold = bool(preview.get("label_bold", False))
    opacity = int(preview.get("label_bg_opacity", 50))
    alpha = int(round(opacity * 255 / 100.0))
    font = label.textItem.font()
    font.setPixelSize(size)
    font.setBold(bold)
    label.textItem.setFont(font)
    label.fill = pg.mkBrush(255, 255, 255, alpha)


def _gating_apply_label_style(ctx, page) -> None:
    for label in page._gating_labels:
        _gating_set_label_style(ctx, page, label)


def _gating_new_label(ctx, page) -> _GateLabel:
    label = _GateLabel(
        on_drag_finished=lambda l: _gating_on_label_dragged(ctx, page, l),
        text="", color="#000000", anchor=(0.5, 0.5),
    )
    _gating_set_label_style(ctx, page, label)
    label.setAcceptHoverEvents(True)
    label.setCursor(Qt.CursorShape.CrossCursor)
    label.setZValue(10)
    page._gating_viewbox.addItem(label, ignoreBounds=True)
    label.setVisible(False)
    return label


def _gating_on_label_dragged(ctx, page, label) -> None:
    """Store the user's label offset (relative to the gate's default spot)."""
    labels = page._gating_labels
    if label not in labels:
        return
    i = labels.index(label)
    if i >= len(page._gating_gates):
        return
    gate = page._gating_gates[i]
    dx, dy = _gate_label_position(
        _gating_geometry_to_display(ctx, page, gate, gate)
    )
    pos = label.pos()
    gate["label_offset"] = [pos.x() - dx, pos.y() - dy]
    _gating_mark_dirty(ctx, page)


def _gating_update_labels(ctx, page, only=None) -> None:
    """Reconcile and refresh the per-gate name labels on the scatter.

    ``only`` restricts the refresh to a set of gate ids (the gates touched
    by a drag); computing every label's display geometry added up during
    rapid adjustments.
    """
    labels = page._gating_labels
    gates = page._gating_gates
    if len(labels) != len(gates):
        runlog.action("gating.labels_reconcile",
                      labels=len(labels), gates=len(gates))
    while len(labels) < len(gates):
        labels.append(_gating_new_label(ctx, page))
    while len(labels) > len(gates):
        item = labels.pop()
        try:
            page._gating_viewbox.removeItem(item)
        except Exception:
            pass
    x = page._gating_preview.get("x")
    y = page._gating_preview.get("y")
    plot_type = page._gating_preview.get("type", "scatter")
    show_labels = page._gating_preview.get("show_gate_labels", True)
    visible_ids = _gating_visible_ids(ctx, page)
    show_percent = page._gating_preview.get("label_show_percent", True)
    resolved = _resolve_gates_for_file(
        gates, page._gating_per_file, _gating_file_key(ctx, page)
    )
    for i, gate in enumerate(gates):
        label = labels[i]
        if only is not None and gate.get("id") not in only:
            continue
        if gate.get("type") in ("not", "and", "or"):
            # Boolean gates have no scatter label.
            label.setVisible(False)
            continue
        name = gate.get("name", f"Gate {i + 1}")
        st = page._gating_last_stats.get(gate["id"])
        if show_percent and st is not None:
            _, pct = st
            label.setText(f"{name}\n{pct:.2f}%")
        else:
            label.setText(name)
        px, py = _gate_label_position(
            _gating_geometry_to_display(ctx, page, gate, resolved[i])
        )
        ox, oy = gate.get("label_offset") or [0.0, 0.0]
        label.setPos(px + ox, py + oy)
        xy_ok = gate.get("x") == x and gate.get("y") == y
        hier_ok = gate.get("id") in visible_ids
        type_ok = gate.get("plot_type", "scatter") == plot_type
        label.setVisible(show_labels and xy_ok and hier_ok and type_ok)


def _gating_load_rois(ctx, page) -> None:
    viewbox = page._gating_viewbox
    page._gating_rois = []
    page._gating_crosshairs = {}
    page._gating_split_lines = {}
    page._gating_skewed_lines = {}
    file_key = _gating_file_key(ctx, page)
    resolved = _resolve_gates_for_file(
        page._gating_gates, page._gating_per_file, file_key
    )
    gate_color = _gate_color(page._gating_preview)
    plot_type = page._gating_preview.get("type", "scatter")
    for gate, rgate in zip(page._gating_gates, resolved):
        items = []
        if gate.get("plot_type", "scatter") != plot_type:
            page._gating_rois.append(items)
            continue
        # Gates are stored in real data units; the ROIs live in plot units.
        rgate = _gating_geometry_to_display(ctx, page, gate, rgate)
        t = gate.get("type")
        try:
            if t == "rect":
                roi = pg.RectROI(
                    (rgate["pos"][0], rgate["pos"][1]),
                    (
                        max(float(rgate["size"][0]), _MIN_GATE_SPAN),
                        max(float(rgate["size"][1]), _MIN_GATE_SPAN),
                    ),
                    **_gate_roi_pens(gate_color),
                )
                _gating_style_handles(ctx, roi)
                roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                roi.setAcceptHoverEvents(True)
                roi.setCursor(Qt.CursorShape.CrossCursor)
                roi.sigRegionChangeFinished.connect(
                    lambda r=roi, g=gate: _gating_on_roi_drag(ctx, page, g, r)
                )
                roi.sigRegionChanged.connect(
                    lambda r=roi, g=gate: _gating_on_roi_move(ctx, page, g, r)
                )
                roi.sigClicked.connect(
                    lambda roi, ev, g=gate: _gating_gate_clicked(ctx, page, g["id"], ev)
                )
                items = [roi]
            elif t == "ellipse":
                roi = pg.EllipseROI(
                    (rgate["pos"][0], rgate["pos"][1]),
                    (
                        max(float(rgate["size"][0]), _MIN_GATE_SPAN),
                        max(float(rgate["size"][1]), _MIN_GATE_SPAN),
                    ),
                    **_gate_roi_pens(gate_color),
                )
                _gating_style_handles(ctx, roi)
                roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                roi.setAcceptHoverEvents(True)
                roi.setCursor(Qt.CursorShape.CrossCursor)
                roi.sigRegionChangeFinished.connect(
                    lambda r=roi, g=gate: _gating_on_roi_drag(ctx, page, g, r)
                )
                roi.sigRegionChanged.connect(
                    lambda r=roi, g=gate: _gating_on_roi_move(ctx, page, g, r)
                )
                roi.sigClicked.connect(
                    lambda roi, ev, g=gate: _gating_gate_clicked(ctx, page, g["id"], ev)
                )
                items = [roi]
            elif t == "polygon":
                roi = _GatePolygonROI(
                    [tuple(p) for p in rgate["points"]],
                    closed=True, **_gate_roi_pens(gate_color),
                )
                _gating_style_handles(ctx, roi)
                roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                roi.setAcceptHoverEvents(True)
                roi.setCursor(Qt.CursorShape.CrossCursor)
                roi.sigRegionChangeFinished.connect(
                    lambda r=roi, g=gate: _gating_on_roi_drag(ctx, page, g, r)
                )
                roi.sigRegionChanged.connect(
                    lambda r=roi, g=gate: _gating_on_roi_move(ctx, page, g, r)
                )
                roi.sigClicked.connect(
                    lambda roi, ev, g=gate: _gating_gate_clicked(ctx, page, g["id"], ev)
                )
                items = [roi]
            elif t == "quadrant":
                cid = gate.get("crosshair_id")
                if cid and cid not in page._gating_crosshairs:
                    pen = pg.mkPen(gate_color, width=2)
                    hover = pg.mkPen(_gate_hover_color(gate_color), width=3)
                    vline = pg.InfiniteLine(pos=rgate.get("vx", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                    hline = pg.InfiniteLine(pos=rgate.get("hy", 0), angle=0, movable=True, pen=pen, hoverPen=hover)
                    viewbox.addItem(vline, ignoreBounds=True)
                    viewbox.addItem(hline, ignoreBounds=True)
                    page._gating_crosshairs[cid] = {
                        "lines": [vline, hline],
                        "x": gate.get("x"),
                        "y": gate.get("y"),
                    }
                    vline.sigPositionChangeFinished.connect(
                        lambda l=vline, c=cid: _gating_quadrant_sync(ctx, page, c, "vx", l)
                    )
                    hline.sigPositionChangeFinished.connect(
                        lambda l=hline, c=cid: _gating_quadrant_sync(ctx, page, c, "hy", l)
                    )
                    vline.sigPositionChanged.connect(
                        lambda l=vline, c=cid: _gating_quadrant_move(ctx, page, c, "vx", l)
                    )
                    hline.sigPositionChanged.connect(
                        lambda l=hline, c=cid: _gating_quadrant_move(ctx, page, c, "hy", l)
                    )
                items = []
            elif t == "range":
                pen = pg.mkPen(gate_color, width=2)
                hover = pg.mkPen(_gate_hover_color(gate_color), width=3)
                vline1 = pg.InfiniteLine(pos=rgate.get("x1", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                vline2 = pg.InfiniteLine(pos=rgate.get("x2", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                viewbox.addItem(vline1, ignoreBounds=True)
                viewbox.addItem(vline2, ignoreBounds=True)
                _gating_setup_line(ctx, page, vline1, gate["id"])
                _gating_setup_line(ctx, page, vline2, gate["id"])
                vline1.sigPositionChangeFinished.connect(
                    lambda l=vline1, g=gate: _gating_range_sync(ctx, page, g, "x1", l)
                )
                vline2.sigPositionChangeFinished.connect(
                    lambda l=vline2, g=gate: _gating_range_sync(ctx, page, g, "x2", l)
                )
                vline1.sigPositionChanged.connect(
                    lambda l=vline1, g=gate: _gating_range_move(ctx, page, g, "x1", l)
                )
                vline2.sigPositionChanged.connect(
                    lambda l=vline2, g=gate: _gating_range_move(ctx, page, g, "x2", l)
                )
                items = [vline1, vline2]
            elif t == "split":
                sid = gate.get("split_id")
                if sid and sid not in page._gating_split_lines:
                    pen = pg.mkPen(gate_color, width=2)
                    hover = pg.mkPen(_gate_hover_color(gate_color), width=3)
                    vline = pg.InfiniteLine(pos=rgate.get("vx", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                    viewbox.addItem(vline, ignoreBounds=True)
                    page._gating_split_lines[sid] = {
                        "lines": [vline],
                        "x": gate.get("x"),
                        "y": gate.get("y"),
                    }
                    vline.sigPositionChangeFinished.connect(
                        lambda l=vline, c=sid: _gating_split_sync(ctx, page, c, l)
                    )
                    vline.sigPositionChanged.connect(
                        lambda l=vline, c=sid: _gating_split_move(ctx, page, c, l)
                    )
                items = []
            elif t == "skewed_quadrant":
                sid = gate.get("skewed_id")
                if sid and sid not in page._gating_skewed_lines:
                    cx, cy = rgate.get("center", (0, 0))
                    roi = _SkewedQuadrantROI(
                        (cx, cy),
                        rgate.get("p_right", (cx, cy)),
                        rgate.get("p_up", (cx, cy)),
                        rgate.get("p_left", (cx, cy)),
                        rgate.get("p_down", (cx, cy)),
                        **_gate_roi_pens(gate_color),
                    )
                    _gating_style_handles(ctx, roi)
                    roi.setAcceptHoverEvents(True)
                    roi.setCursor(Qt.CursorShape.CrossCursor)
                    roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                    viewbox.addItem(roi, ignoreBounds=True)
                    page._gating_skewed_lines[sid] = {
                        "roi": roi,
                        "x": gate.get("x"),
                        "y": gate.get("y"),
                    }
                    roi.sigRegionChangeFinished.connect(
                        lambda c=sid: _gating_skewed_sync(ctx, page, c)
                    )
                    roi.sigRegionChanged.connect(
                        lambda r=roi, c=sid: _gating_skewed_move(ctx, page, c)
                    )
                    roi.sigClicked.connect(
                        lambda *a: _gating_select_gate_by_id(ctx, page, gate["id"])
                    )
                items = []
        except (KeyError, IndexError, TypeError):
            items = []
        except Exception as exc:  # noqa: BLE001
            # One broken gate must not abort the whole reload: that would
            # leave `_gating_rois` out of sync with `_gating_gates`.
            _log_exception(exc)
            items = []
        for item in items:
            # Gate outlines are annotations: they must never change
            # the axes (auto-range fits the data only).
            viewbox.addItem(item, ignoreBounds=True)
        _gating_set_items_handles_visible(ctx, items, False)
        page._gating_rois.append(items)


def _gating_remove_rois(ctx, page) -> None:
    """Remove every gate ROI/line from the ViewBox (labels are kept).

    Items are validated first: a rebuild may leave a wrapper whose C++
    object is already gone, and removing it would crash the scene.
    """
    from shiboken6 import isValid

    def drop(item) -> None:
        if item is None:
            return
        try:
            if isValid(item):
                _detach_gate_item(item)
                page._gating_viewbox.removeItem(item)
        except (RuntimeError, AttributeError):
            pass

    for items in page._gating_rois:
        for item in items:
            drop(item)
    for entry in page._gating_crosshairs.values():
        for line in entry["lines"]:
            drop(line)
    for entry in page._gating_split_lines.values():
        for line in entry["lines"]:
            drop(line)
    for entry in page._gating_skewed_lines.values():
        drop(entry["roi"])
    page._gating_rois = []
    page._gating_crosshairs = {}
    page._gating_split_lines = {}
    page._gating_skewed_lines = {}


def _gating_reload_rois(ctx, page) -> None:
    """Recreate gate items for the current file's per-file geometry."""
    _gating_remove_rois(ctx, page)
    _gating_load_rois(ctx, page)


def _gating_delete_selected(ctx, page) -> None:
    items = page._gating_tree.selectedItems()
    if not items:
        return
    idx = items[0].data(0, Qt.ItemDataRole.UserRole)
    _gating_delete_gate(ctx, page, idx, cascade=True)


def _gating_delete_gate(ctx, page, idx, cascade: bool) -> None:
    """Delete a gate. If `cascade`, also delete all its descendants."""
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    gate = page._gating_gates[idx]
    gate_id = gate["id"]
    parent_id = gate.get("parent")

    to_delete = {gate_id}
    # Quadrant / skewed-quadrant / split gates are bound to their shared
    # crosshair/rays/line, so deleting one deletes the whole group.
    for key in ("crosshair_id", "skewed_id", "split_id"):
        shared = gate.get(key)
        if shared:
            for g in page._gating_gates:
                if g.get(key) == shared:
                    to_delete.add(g["id"])
            break
    if cascade:
        changed = True
        while changed:
            changed = False
            for g in page._gating_gates:
                if g.get("parent") in to_delete and g["id"] not in to_delete:
                    to_delete.add(g["id"])
                    changed = True
    # A gate used as a spectrum's source (Unmix) must be re-set first.
    protected = getattr(page, "_gating_protected_gate_id", None)
    if protected and protected in to_delete:
        QMessageBox.information(
            ctx.host,
            "Delete gate",
            "This gate is the source of a spectrum.\n"
            'Use "Re-set as spectrum..." to move the source to another '
            "gate before deleting it.",
        )
        return
    # Ask before anything is removed: a branch can take many gates with it.
    name = gate.get("name") or "Unnamed gate"
    count = len(to_delete)
    victims = [
        str(g.get("name") or "Unnamed gate")
        for g in page._gating_gates if g["id"] in to_delete
    ]
    preview = ", ".join(victims[:6])
    if count > 6:
        preview += f" … (+{count - 6} more)"
    if cascade:
        question = f"Delete gate '{name}'"
        if count > 1:
            question += f" and its {count - 1} descendant gate(s)"
        question += f"?\n\n{preview}\n\nThis cannot be undone."
    else:
        if count > 1:
            question = (
                f"Delete gate '{name}' and the {count - 1} other gate(s) "
                f"that share its boundaries?\n\n{preview}"
            )
        else:
            question = f"Delete gate '{name}'?"
        children = sum(
            1 for g in page._gating_gates
            if g.get("parent") in to_delete and g["id"] not in to_delete
        )
        if children:
            question += (
                f"\n\n{children} child gate(s) will be kept and moved up "
                "one level."
            )
        question += "\n\nThis cannot be undone."
    reply = QMessageBox.question(
        ctx.host, "Delete gate", question,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return
    if not cascade:
        # Keep children; re-parent them to this gate's parent.
        for g in page._gating_gates:
            if g.get("parent") in to_delete:
                g["parent"] = parent_id

    deleted_names = [
        g.get("name") for g in page._gating_gates if g["id"] in to_delete
    ]
    indices = [i for i, g in enumerate(page._gating_gates) if g["id"] in to_delete]
    for i in reversed(indices):
        for item in page._gating_rois[i]:
            _detach_gate_item(item)
            page._gating_viewbox.removeItem(item)
        if i < len(page._gating_labels):
            label = page._gating_labels.pop(i)
            try:
                page._gating_viewbox.removeItem(label)
            except Exception:
                pass
        del page._gating_gates[i]
        del page._gating_rois[i]
    runlog.action(
        "gating.gate_deleted", module=str(page._gating_task_id),
        gates=len(to_delete), cascade=bool(cascade),
        names=deleted_names,
    )
    _gating_prune_crosshairs(ctx, page)
    # Keep the same hierarchy level in view when possible.
    if page._gating_context_gate_id in to_delete:
        page._gating_context_gate_id = None
        for g in page._gating_gates:
            if g.get("parent") == parent_id:
                page._gating_context_gate_id = g["id"]
                break
    if page._gating_active_gate_id in to_delete:
        page._gating_active_gate_id = None
    if page._gating_parent_id in to_delete:
        page._gating_parent_id = None
    # Drop per-file overrides for gates that no longer exist.
    used_groups = {_gate_group_id(g) for g in page._gating_gates}
    for group in list(page._gating_per_file.keys()):
        if group not in used_groups:
            page._gating_per_file.pop(group, None)
    _gating_rebuild_tree(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_prune_crosshairs(ctx, page) -> None:
    """Remove shared boundary lines whose gates are all gone."""
    used = {g.get("crosshair_id") for g in page._gating_gates if g.get("crosshair_id")}
    for cid in list(page._gating_crosshairs.keys()):
        if cid not in used:
            for line in page._gating_crosshairs[cid]["lines"]:
                page._gating_viewbox.removeItem(line)
            del page._gating_crosshairs[cid]
    used_splits = {g.get("split_id") for g in page._gating_gates if g.get("split_id")}
    for sid in list(page._gating_split_lines.keys()):
        if sid not in used_splits:
            for line in page._gating_split_lines[sid]["lines"]:
                page._gating_viewbox.removeItem(line)
            del page._gating_split_lines[sid]
    used_skewed = {g.get("skewed_id") for g in page._gating_gates if g.get("skewed_id")}
    for sid in list(page._gating_skewed_lines.keys()):
        if sid not in used_skewed:
            page._gating_viewbox.removeItem(page._gating_skewed_lines[sid]["roi"])
            del page._gating_skewed_lines[sid]


def _gating_gate_chain(ctx, page, gate_id) -> list:
    """Return the gates from root to `gate_id` (inclusive), in order."""
    by_id = {g["id"]: g for g in page._gating_gates}
    chain = []
    cur = by_id.get(gate_id)
    while cur is not None:
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    chain.reverse()
    return chain


def _gating_compute_mask(ctx, page, gate_id, parquet_path):
    """Return the AND mask of a gate and all its ancestors (on transformed data)."""
    gates = _resolve_gates_for_file(
        page._gating_gates, page._gating_per_file, str(parquet_path)
    )
    by_id = {g["id"]: g for g in gates}
    needed = set()
    for g in gates:
        needed |= _gate_needed_columns(g, by_id)
    values = _data_columns._column_data(ctx,
        page._gating_task_id, parquet_path, list(needed)
    )
    try:
        return _compute_gate_filter_mask(
            gates, gate_id, parquet_path, {},
            existing=set(values), extra=values,
        )
    except Exception:
        return None


def _gating_context_mask(ctx, page, gate_id, parquet_path):
    """Return the parent's population mask (the points the parent includes)."""
    gate = next((g for g in page._gating_gates if g["id"] == gate_id), None)
    if gate is None or not gate.get("parent"):
        return None  # top-level gate -> all events
    return _gating_compute_mask(ctx, page, gate.get("parent"), parquet_path)


def _gating_on_tree_double_clicked(ctx, page, item, column) -> None:
    idx = item.data(0, Qt.ItemDataRole.UserRole)
    preview = page._gating_preview
    _gating_cancel_pending_render(ctx, page)
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        page._gating_active_gate_id = None
        page._gating_parent_id = None
        page._gating_context_gate_id = None
    else:
        gate = page._gating_gates[idx]
        page._gating_active_gate_id = gate["id"]
        page._gating_context_gate_id = None
        # New gates drawn inside this subset become children of this gate.
        page._gating_parent_id = gate["id"]
        _gating_restore_gate_display(ctx, page, gate)
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)


def _gating_tree_context_menu(ctx, page, pos) -> None:
    tree = page._gating_tree
    item = tree.itemAt(pos)
    if item is None:
        return
    idx = item.data(0, Qt.ItemDataRole.UserRole)
    if idx is None or idx >= len(page._gating_gates):
        return
    is_root = idx < 0
    menu = QMenu(tree)

    # Copy / paste (paste inserts as a child of the clicked gate; the root
    # node pastes as a top-level gate).
    if not is_root:
        copy_gate = QAction("Copy gate", menu)
        copy_gate.triggered.connect(
            lambda _=False: _gating_copy_gate(ctx, page, idx)
        )
        menu.addAction(copy_gate)
        copy_branch = QAction("Copy branch", menu)
        copy_branch.triggered.connect(
            lambda _=False: _gating_copy_branch(ctx, page, idx)
        )
        menu.addAction(copy_branch)
    paste_gate = QAction("Paste gate", menu)
    paste_gate.setEnabled(bool(getattr(ctx, "_gating_clipboard_gate", None)))
    paste_gate.triggered.connect(
        lambda _=False: _gating_paste_gate(ctx, page, idx)
    )
    menu.addAction(paste_gate)
    paste_branch = QAction("Paste branch", menu)
    paste_branch.setEnabled(bool(getattr(ctx, "_gating_clipboard_branch", None)))
    paste_branch.triggered.connect(
        lambda _=False: _gating_paste_branch(ctx, page, idx)
    )
    menu.addAction(paste_branch)

    if is_root:
        menu.exec(tree.viewport().mapToGlobal(pos))
        return

    ren_act = QAction("Rename", menu)
    ren_act.triggered.connect(lambda: _gating_rename_gate(ctx, page, idx))
    menu.addAction(ren_act)

    # Per-file editing (only for the Gating module, and only geometry).
    if getattr(page, "_gating_uses_store", False):
        gate = page._gating_gates[idx]
        if gate.get("type") not in ("not", "and", "or"):
            group = _gate_group_id(gate)
            per_file_on = group in page._gating_per_file
            menu.addSeparator()
            perfile_act = QAction("Per-file gate", menu)
            perfile_act.setCheckable(True)
            perfile_act.setChecked(per_file_on)
            perfile_act.setToolTip(
                "Adjust this gate's position and size separately for each "
                "file. Its type stays the same."
            )
            perfile_act.triggered.connect(
                lambda _=False: _gating_toggle_per_file(ctx, page, idx)
            )
            menu.addAction(perfile_act)
            unify_act = QAction("Apply one file to all…", menu)
            unify_act.setToolTip(
                "Copy this gate's shape from one selected file to every "
                "selected file."
            )
            unify_act.triggered.connect(
                lambda _=False: _gating_unify_gate(ctx, page, idx)
            )
            menu.addAction(unify_act)

    # Unmix extends this menu with "Set as spectrum" (and re-set).
    extra_menu = getattr(page, "_gating_spectrum_menu", None)
    if extra_menu is not None:
        extra_menu(menu, page, idx)

    # Boolean gating.
    bool_menu = QMenu("Boolean gating", menu)
    not_act = QAction("NOT", bool_menu)
    not_act.triggered.connect(lambda _=False: _gating_add_not_gate(ctx, page, idx))
    bool_menu.addAction(not_act)
    and_act = QAction("AND", bool_menu)
    and_act.triggered.connect(
        lambda _=False: _gating_add_boolean_gate(ctx, page, idx, "and")
    )
    bool_menu.addAction(and_act)
    or_act = QAction("OR", bool_menu)
    or_act.triggered.connect(
        lambda _=False: _gating_add_boolean_gate(ctx, page, idx, "or")
    )
    bool_menu.addAction(or_act)
    menu.addMenu(bool_menu)

    # A gate that is a spectrum's source (Unmix) cannot be deleted.
    protected = getattr(page, "_gating_protected_gate_id", None)
    is_protected = bool(protected) and page._gating_gates[idx]["id"] == protected

    del_one = QAction("Delete this gate", menu)
    del_one.setEnabled(not is_protected)
    del_one.triggered.connect(lambda: _gating_delete_gate(ctx, page, idx, cascade=False))
    menu.addAction(del_one)
    del_all = QAction("Delete gate and all children", menu)
    del_all.setEnabled(not is_protected)
    del_all.triggered.connect(lambda: _gating_delete_gate(ctx, page, idx, cascade=True))
    menu.addAction(del_all)
    menu.exec(tree.viewport().mapToGlobal(pos))


def _gating_toggle_per_file(ctx, page, idx) -> None:
    """Enable/disable per-file editing for one gate (its type never changes)."""
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    gate = page._gating_gates[idx]
    group = _gate_group_id(gate)
    if group in page._gating_per_file:
        page._gating_per_file.pop(group, None)
    else:
        page._gating_per_file[group] = {}
    _gating_reload_rois(ctx, page)
    _gating_update_labels(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_unify_gate(ctx, page, idx) -> None:
    """Copy one selected file's shape for this gate to every selected file."""
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    gate = page._gating_gates[idx]
    group = _gate_group_id(gate)
    files = page._gating_files or []
    if not files:
        QMessageBox.information(
            ctx.host, "Apply one file to all",
            "Select data files first."
        )
        return
    names = [_data_naming._comp_file_name(ctx, p, page._gating_task_id) for p in files]
    chosen_name, ok = QInputDialog.getItem(
        ctx.host, "Apply one file to all",
        "Copy this gate's shape from:", names, 0, False,
    )
    if not ok:
        return
    file_key = files[names.index(chosen_name)]
    resolved = _resolve_gates_for_file(
        [gate], page._gating_per_file, file_key
    )[0]
    geometry = _gate_geometry(resolved)
    for g in page._gating_gates:
        if _gate_group_id(g) == group:
            for key, value in geometry.items():
                g[key] = copy.deepcopy(value)
    page._gating_per_file.pop(group, None)
    _gating_reload_rois(ctx, page)
    _gating_update_labels(ctx, page)
    _gating_mark_dirty(ctx, page)
    _gating_rebuild_tree(ctx, page)


def _gating_copy_nodes(ctx, page, ids) -> list:
    """Deep-copy the gates whose id is in `ids` (kept in tree order)."""
    return [
        copy.deepcopy(g) for g in page._gating_gates if g.get("id") in ids
    ]


def _gating_collect_single(ctx, page, gate) -> set:
    """Ids for 'copy gate': the gate, its group members and boolean sources."""
    ids = set()

    def add(gid):
        if not gid or gid in ids:
            return
        ids.add(gid)
        g = next((gg for gg in page._gating_gates if gg.get("id") == gid), None)
        if g is None:
            return
        for key in ("crosshair_id", "split_id", "skewed_id"):
            group = g.get(key)
            if group:
                for gg in page._gating_gates:
                    if gg.get(key) == group:
                        add(gg.get("id"))
        for sid in _gate_source_ids(g):
            add(sid)

    add(gate.get("id"))
    return ids


def _gating_collect_branch(ctx, page, gate) -> set:
    """Ids for 'copy branch': the gate and all its descendants."""
    ids = set()

    def add_subtree(gid):
        if not gid or gid in ids:
            return
        ids.add(gid)
        for g in page._gating_gates:
            if g.get("parent") == gid:
                add_subtree(g.get("id"))

    add_subtree(gate.get("id"))
    return ids


def _gating_copy_gate(ctx, page, idx) -> None:
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    ids = _gating_collect_single(ctx, page, page._gating_gates[idx])
    ctx._gating_clipboard_gate = _gating_copy_nodes(ctx, page, ids)
    ctx.statusBar().showMessage("Gate copied")


def _gating_copy_branch(ctx, page, idx) -> None:
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    ids = _gating_collect_branch(ctx, page, page._gating_gates[idx])
    ctx._gating_clipboard_branch = _gating_copy_nodes(ctx, page, ids)
    ctx.statusBar().showMessage("Branch copied")


def _gating_paste_nodes(ctx, page, nodes, paste_parent_id) -> int:
    """Append copied `nodes` as children of `paste_parent_id`; return count."""
    if not nodes:
        return 0
    nodes = copy.deepcopy(nodes)
    id_map = {g["id"]: uuid4().hex[:8] for g in nodes}
    group_map = {}
    for g in nodes:
        for key in ("crosshair_id", "split_id", "skewed_id"):
            gid = g.get(key)
            if gid and gid not in group_map:
                group_map[gid] = uuid4().hex[:8]
    for g in nodes:
        g["id"] = id_map[g["id"]]
        parent = g.get("parent")
        g["parent"] = id_map[parent] if parent in id_map else paste_parent_id
        source = g.get("source")
        if source in id_map:
            g["source"] = id_map[source]
        sources = g.get("sources")
        if sources:
            g["sources"] = [id_map[s] for s in sources if s in id_map]
        for key in ("crosshair_id", "split_id", "skewed_id"):
            gid = g.get(key)
            if gid in group_map:
                g[key] = group_map[gid]
    page._gating_gates.extend(nodes)
    return len(nodes)


def _gating_paste_gate(ctx, page, idx) -> None:
    nodes = getattr(ctx, "_gating_clipboard_gate", None)
    parent_id = (
        page._gating_gates[idx]["id"]
        if isinstance(idx, int) and 0 <= idx < len(page._gating_gates)
        else None
    )
    count = _gating_paste_nodes(ctx, page, nodes, parent_id)
    if count:
        _gating_reload_rois(ctx, page)
        _gating_update_roi_visibility(ctx, page)
        _gating_rebuild_tree(ctx, page)
        _gating_mark_dirty(ctx, page)


def _gating_paste_branch(ctx, page, idx) -> None:
    nodes = getattr(ctx, "_gating_clipboard_branch", None)
    parent_id = (
        page._gating_gates[idx]["id"]
        if isinstance(idx, int) and 0 <= idx < len(page._gating_gates)
        else None
    )
    count = _gating_paste_nodes(ctx, page, nodes, parent_id)
    if count:
        _gating_reload_rois(ctx, page)
        _gating_update_roi_visibility(ctx, page)
        _gating_rebuild_tree(ctx, page)
        _gating_mark_dirty(ctx, page)


def _gating_apply_selection(ctx, page, idx) -> None:
    """Apply a selection: `idx` is a gate index, or -1 for 'All Events'."""
    preview = page._gating_preview
    page._gating_active_gate_id = None
    if isinstance(idx, int) and 0 <= idx < len(page._gating_gates):
        gate = page._gating_gates[idx]
        page._gating_context_gate_id = gate["id"]
        page._gating_parent_id = gate.get("parent")
        _gating_restore_gate_display(ctx, page, gate)
    else:
        page._gating_context_gate_id = None
        page._gating_parent_id = None
        preview["x"] = page._gating_settings_x
        preview["y"] = page._gating_settings_y
        preview["xmin"] = None
        preview["xmax"] = None
        preview["ymin"] = None
        preview["ymax"] = None
    _gating_show_handles(ctx, page, idx if isinstance(idx, int) and idx >= 0 else -1)
    # Update ROI/label visibility right away, so a single click shows the
    # correct gates immediately (the data render is deferred to catch
    # double-clicks).
    _gating_update_roi_visibility(ctx, page)


def _gating_tree_item_for_index(ctx, page, idx):
    tree = page._gating_tree
    found = []

    def walk(item):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) == idx:
                found.append(child)
                return
            walk(child)

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return found[0] if found else None


def _gating_on_tree_selection(ctx, page, item, column) -> None:
    """Single-click: open the gate's drawing context (axes + range + parent points)."""
    idx = item.data(0, Qt.ItemDataRole.UserRole) if item else -1
    idx = idx if isinstance(idx, int) and idx >= 0 else -1
    current = page._gating_context_gate_id
    target = (
        page._gating_gates[idx]["id"]
        if 0 <= idx < len(page._gating_gates) else None
    )
    if target == current:
        # Selecting the gate that is already active must not rebuild the
        # plot/settings; keeping this cheap makes rapid gate edits usable.
        return
    _gating_apply_selection(ctx, page, idx)
    # Render immediately (no double-click deferral, which felt sluggish).
    _gating_render_and_settings(ctx, page)


def _gating_cancel_pending_render(ctx, page) -> None:
    timer = getattr(page, "_gating_render_timer", None)
    if timer is not None:
        timer.stop()
        page._gating_render_timer = None


def _gating_render_and_settings(ctx, page) -> None:
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)


def _gating_image_refresh(ctx, page) -> None:
    """Re-sample the image LOD after the user zooms/pans the image view."""
    if page._gating_preview.get("type") != "image":
        return
    _widgets_gate_editor_editor._gating_render(ctx, page)


def _gating_rename_gate(ctx, page, idx) -> None:
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    gate = page._gating_gates[idx]
    text, ok = QInputDialog.getText(
        ctx.host, "Rename gate", "Name:", QLineEdit.EchoMode.Normal,
        gate.get("name", ""),
    )
    if ok and text.strip():
        gate["name"] = text.strip()
        _gating_rebuild_tree(ctx, page)
        _gating_update_labels(ctx, page)
        _gating_mark_dirty(ctx, page)


def _gating_add_not_gate(ctx, page, idx) -> None:
    """Create a 'NOT <gate>' gate, a sibling of `gate` that selects points
    outside it. It has no shape or label on the scatter."""
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    gate = page._gating_gates[idx]
    not_gate = {
        "id": uuid4().hex[:8],
        "name": f"NOT {gate.get('name', f'Gate {idx + 1}')}",
        "type": "not",
        "source": gate["id"],
        "x": gate.get("x"),
        "y": gate.get("y"),
        "parent": gate.get("parent"),
        "xmin": gate.get("xmin"), "xmax": gate.get("xmax"),
        "ymin": gate.get("ymin"), "ymax": gate.get("ymax"),
    }
    page._gating_gates.append(not_gate)
    page._gating_rois.append([])
    _gating_rebuild_tree(ctx, page)
    _gating_update_labels(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_add_boolean_gate(ctx, page, idx, op) -> None:
    """Create a '<A> op <B> op …' gate combining `gate` with selected
    siblings (`op` is "and" or "or"). It has no shape or label on the scatter."""
    sep = " AND " if op == "and" else " OR "
    if idx is None or idx < 0 or idx >= len(page._gating_gates):
        return
    gate = page._gating_gates[idx]
    parent_id = gate.get("parent")
    siblings = [
        g for g in page._gating_gates
        if g.get("parent") == parent_id and g["id"] != gate["id"]
    ]
    if not siblings:
        QMessageBox.information(
            ctx.host, f"{op.upper()} gate",
            "This gate has no sibling gates at the same level.",
        )
        return

    dialog = QDialog(ctx.host)
    dialog.setWindowTitle(f"{op.upper()} gate")
    dlg_layout = QVBoxLayout(dialog)
    dlg_layout.addWidget(QLabel(f"Select sibling gates to {op.upper()} together:"))
    checkboxes = []
    for g in siblings:
        cb = QCheckBox(g.get("name", g["id"]))
        cb.setChecked(False)
        checkboxes.append((g, cb))
        dlg_layout.addWidget(cb)
    btn_row = QHBoxLayout()
    ok_btn = QPushButton("OK")
    cancel_btn = QPushButton("Cancel")
    btn_row.addStretch()
    btn_row.addWidget(ok_btn)
    btn_row.addWidget(cancel_btn)
    dlg_layout.addLayout(btn_row)

    selected = []

    def on_ok():
        for g, cb in checkboxes:
            if cb.isChecked():
                selected.append(g)
        dialog.accept()

    ok_btn.clicked.connect(on_ok)
    cancel_btn.clicked.connect(dialog.reject)
    if dialog.exec() != QDialog.DialogCode.Accepted or not selected:
        return

    sources = [gate] + selected
    bool_gate = {
        "id": uuid4().hex[:8],
        "name": sep.join(g.get("name", g["id"]) for g in sources),
        "type": op,
        "sources": [g["id"] for g in sources],
        "parent": parent_id,
    }
    page._gating_gates.append(bool_gate)
    page._gating_rois.append([])
    _gating_rebuild_tree(ctx, page)
    _gating_update_labels(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_stage_signature(stages) -> str:
    """Digest of the data pipeline (so gate edits reuse its cached output)."""
    import hashlib
    import json

    import numpy as np

    def array_token(value):
        try:
            arr = np.ascontiguousarray(np.asarray(value, dtype="float64"))
            return hashlib.sha1(arr.tobytes()).hexdigest()[:10]
        except Exception:  # noqa: BLE001
            return str(value)[:64]

    payload = []
    for kind, stage in stages:
        if kind == "compensation":
            payload.append([
                kind, stage.get("id"), sorted(stage.get("files") or []),
                list(stage.get("names") or []),
                array_token(stage.get("matrix")),
            ])
        elif kind == "unmix":
            payload.append([
                kind, stage.get("task_id"),
                sorted(stage.get("samples") or []),
                list(stage.get("names") or []),
                array_token(stage.get("matrix")),
            ])
        elif kind == "illumination":
            payload.append([
                kind, stage.get("task_id"),
                [
                    [sub.get("channel"), sorted(sub.get("files") or []),
                     sub.get("T"), array_token(sub.get("mask"))]
                    for sub in stage.get("submodules") or []
                ],
            ])
        elif kind == "batch_align":
            payload.append([
                kind, stage.get("id"), stage.get("model_id"),
                sorted((stage.get("files") or {}).items()),
                array_token(stage.get("codes")),
            ])
        elif kind == "rename":
            payload.append([
                kind, sorted((stage.get("map") or {}).items()),
                sorted(stage.get("files") or []),
            ])
        else:
            payload.append([kind, repr(stage)[:500]])
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _gating_request_stats(ctx, page, affected=None, full=False) -> None:
    """Submit a background statistics recomputation (incremental if affected).

    By default the counts come from the displayed sample (the same rows the
    plot draws), which keeps gate editing fast.  ``full=True`` computes on
    every event and is only used by the explicit "full data" button.
    """
    worker = getattr(page, "_gating_stats_worker", None)
    if worker is None:
        return
    parquet_path = page._gating_preview.get("file")
    if not parquet_path or not page._gating_gates:
        page._gating_last_stats = {}
        return
    # Resolve the current file's per-file geometry, so the percentages
    # reflect the gate shapes this file actually uses.
    gates = _resolve_gates_for_file(
        page._gating_gates, page._gating_per_file, str(parquet_path)
    )
    stages = _data_columns._map_stages(
        ctx, page._gating_task_id, parquet_path=parquet_path
    )
    # A Feature output is not in the import column index and already holds
    # the transforms applied above its Feature module; only the stages
    # below it still apply (renames remain a name change). Without this a
    # gating module on aggregated data had no columns to evaluate, so no
    # gate ever received a count/percent.
    feature = _data_feature_outputs._feature_output(ctx, parquet_path)
    if feature is not None:
        post = _data_columns._map_stages(
            ctx, page._gating_task_id, after=feature.get("feature_task"),
            parquet_path=parquet_path,
        )
        renames = [
            (kind, stage) for kind, stage in stages
            if kind == "rename" and (kind, stage) not in post
        ]
        stages = renames + post
        base_columns = set(feature.get("columns") or [])
    else:
        base_columns = set(
            ctx._column_index.get(_data_files._rel_data_key(ctx, parquet_path), [])
        )
    if not base_columns:
        try:
            import pyarrow.parquet as pq
            base_columns = set(
                pq.ParquetFile(parquet_path).schema.names
            )
        except Exception:  # noqa: BLE001
            base_columns = set()
    page._gating_stat_generation += 1
    # Track which gates the pending results cover.  Requests coalesce in the
    # worker, so the scope accumulates until the matching result is applied
    # (a full request covers everything).
    if affected is None:
        page._gating_stats_scope = (True, set())
    else:
        full_scope, ids = getattr(page, "_gating_stats_scope", (False, set()))
        if not full_scope:
            page._gating_stats_scope = (False, set(ids) | set(affected))
    # Mask columns exposed by an upstream Segmentation module are not in
    # the data file; resolve them here and hand them to the worker.
    virtual = {}
    for name, mask_path in _data_outputs._seg_applied_columns(ctx,
        page._gating_task_id, parquet_path
    ).items():
        arr = _data_outputs._seg_mask_values(ctx, parquet_path, mask_path)
        if arr is not None:
            virtual[name] = arr
    subsample = _data_sampling._subsample_row_index(
        ctx, page._gating_task_id, parquet_path
    )
    total_rows = ctx._data_row_count(parquet_path)
    row_index = None
    scale = 1.0
    mode = "full"
    if subsample is not None:
        # An upstream Subsample defines the population; the sampled/full
        # distinction only applies above it.
        row_index = subsample
    elif not full:
        tier = ctx._pool_tier()
        pool = ctx._pool_rows_cache.get((str(parquet_path), int(tier)))
        if pool is not None and len(pool):
            total = total_rows or len(pool)
            if len(pool) >= total:
                # The sample already covers every row.
                row_index = None
            else:
                row_index = pool
                scale = float(total) / float(len(pool))
                mode = "sampled"
    page._gating_stats_total = total_rows or (
        len(row_index) if row_index is not None else 0
    )
    page._gating_stats_rows = (
        len(row_index) if row_index is not None else page._gating_stats_total
    )
    page._gating_stats_scale = scale
    page._gating_stats_mode = mode
    runlog.action(
        "gating.stats_request",
        mode=mode,
        rows=page._gating_stats_rows,
        total=page._gating_stats_total,
        affected=("all" if affected is None else len(affected)),
    )
    _gating_update_stats_button(ctx, page)
    worker.request(
        page._gating_stat_generation,
        parquet_path, gates, stages, base_columns, affected, virtual,
        row_index,
        _gating_stage_signature(stages),
    )


def _gating_update_stats_button(ctx, page) -> None:
    """Keep the full-data button's tooltip in step with the current scope."""
    button = getattr(page, "_gating_full_stats_btn", None)
    if button is None:
        return
    rows = getattr(page, "_gating_stats_rows", 0)
    total = getattr(page, "_gating_stats_total", 0)
    if total and rows >= total:
        button.setToolTip(
            f"All {total:,} rows are already used, so the gate counts are "
            "exact; clicking only recomputes them."
        )
    elif rows and total:
        button.setToolTip(
            f"Recalculate gate counts on all {total:,} rows. Gate editing "
            f"uses a {rows:,}-row sample instead."
        )
    else:
        button.setToolTip(
            "Recalculate gate counts on every event. Normal editing uses the "
            "displayed sample, which is much faster."
        )


def _gating_full_stats(ctx, page) -> None:
    """Button action: recompute gate counts on the complete data set."""
    page._gating_stats_announce = True
    _gating_request_stats(ctx, page, affected=None, full=True)
    rows = getattr(page, "_gating_stats_rows", 0)
    try:
        if rows:
            ctx.statusBar().showMessage(
                f"Recomputing gate statistics on {rows:,} rows…", 5000
            )
        else:
            ctx.statusBar().showMessage(
                "Recomputing gate statistics on full data…", 5000
            )
    except Exception:  # noqa: BLE001 - status bar is cosmetic
        pass


def _gating_apply_stats(ctx, page, stats) -> None:
    """Update the gate tree labels with a fresh stats dict."""
    scale = getattr(page, "_gating_stats_scale", 1.0)
    if getattr(page, "_gating_stats_mode", "full") == "sampled" and scale > 0:
        # Sampled counts are scaled to the whole file so the numbers stay
        # comparable; percentages are already ratios.
        stats = {
            gate_id: (int(round(count * scale)), pct)
            for gate_id, (count, pct) in stats.items()
        }
    page._gating_last_stats = stats
    full_scope, scope_ids = getattr(page, "_gating_stats_scope", (True, set()))
    page._gating_stats_scope = (False, set())
    only = None if full_scope else set(scope_ids)
    tree = page._gating_tree
    tree.blockSignals(True)

    def update(item):
        for i in range(item.childCount()):
            child = item.child(i)
            idx = child.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(idx, int) and 0 <= idx < len(page._gating_gates):
                gate = page._gating_gates[idx]
                if only is None or gate.get("id") in only:
                    child.setText(
                        0, _gating_gate_display_name(ctx, gate, idx, stats)
                    )
            update(child)

    for i in range(tree.topLevelItemCount()):
        update(tree.topLevelItem(i))
    tree.blockSignals(False)
    tree.resizeColumnToContents(0)
    _gating_update_labels(ctx, page, only=only)
    if only is None:
        # A complete refresh (module open or the full-data button): log the
        # resulting counts so a "nothing changed" report can be checked.
        runlog.action(
            "gating.stats_counts",
            counts={
                g.get("name", g["id"]): stats.get(g["id"], (None, None))[0]
                for g in page._gating_gates
            },
        )
    if getattr(page, "_gating_stats_announce", False):
        page._gating_stats_announce = False
        rows = getattr(page, "_gating_stats_rows", 0)
        total = getattr(page, "_gating_stats_total", 0)
        try:
            if total and rows >= total:
                note = f"Gate statistics updated on all {rows:,} rows."
            elif rows and total:
                note = f"Gate statistics updated on {rows:,} of {total:,} rows."
            else:
                note = "Gate statistics updated."
            ctx.statusBar().showMessage(note, 5000)
        except Exception:  # noqa: BLE001 - status bar is cosmetic
            pass


def _gating_on_stats_timer(ctx, page) -> None:
    """Poll the worker and apply the latest non-stale result."""
    if not hasattr(page, "_gating_tree"):
        return
    worker = getattr(page, "_gating_stats_worker", None)
    if worker is None:
        return
    for result in worker.poll():
        if not result:
            continue
        generation, stats = result
        if generation == page._gating_stat_generation:
            runlog.action(
                "gating.stats_apply",
                gates=len(page._gating_gates),
                mode=getattr(page, "_gating_stats_mode", "full"),
                rows=getattr(page, "_gating_stats_rows", 0),
            )
            _gating_apply_stats(ctx, page, stats)
            runlog.action("gating.stats_applied")


def _gating_gate_display_name(ctx, gate, idx, stats) -> str:
    name = gate.get("name", f"Gate {idx + 1}")
    st = stats.get(gate["id"])
    if st is None:
        return name
    count, pct = st
    return f"{name} ({pct:.2f}%, {count})"


def _gating_refresh_tree_stats(ctx, page, affected=None) -> None:
    """Refresh gate count/percent labels (background, incremental if affected)."""
    _gating_request_stats(ctx, page, affected=affected)


def _gating_flush_adjust(ctx, page) -> None:
    """Apply the pending post-adjust refresh (statistics + labels)."""
    pending = getattr(page, "_gating_adjust_affected", "missing")
    if pending == "missing":
        return
    page._gating_adjust_affected = "missing"
    try:
        _gating_refresh_tree_stats(ctx, page, affected=pending)
        _gating_update_labels(ctx, page, only=pending)
    except RuntimeError:
        # The page was closed before the refresh ran.
        pass


def _gating_schedule_adjust(ctx, page, affected=None) -> None:
    """Refresh statistics/labels after a gate adjustment finishes.

    This is called when a drag or line move ends (``sigRegionChangeFinished``
    / ``sigPositionChangeFinished``), never while the gate is still moving:
    recomputing the counts for every intermediate shape made dragging stutter
    and the intermediate numbers were never used.  A very short single-shot
    delay coalesces the finish/changed signal pair emitted on release.
    The affected set expands to descendants and to boolean gates that
    reference a moved gate (see ``_gating_affected_ids``).
    """
    timer = getattr(page, "_gating_adjust_timer", None)
    if timer is None:
        timer = QTimer(ctx.host)
        timer.setSingleShot(True)
        timer.setInterval(50)
        timer.timeout.connect(lambda: _gating_flush_adjust(ctx, page))
        page._gating_adjust_timer = timer
    if affected is not None:
        # Descendants and the boolean gates that reference a moved gate all
        # change; see _gating_affected_ids.
        affected = _gating_affected_ids(ctx, page, affected)
    previous = getattr(page, "_gating_adjust_affected", "missing")
    if previous == "missing":
        previous = set()
    if affected is None or previous is None:
        combined = None
    else:
        combined = set(previous) | set(affected)
    page._gating_adjust_affected = combined
    timer.start()


def _gating_on_collapse(ctx, page, item) -> None:
    gid = item.data(0, Qt.ItemDataRole.UserRole + 1)
    if gid:
        page._gating_collapsed.add(gid)


def _gating_on_expand(ctx, page, item) -> None:
    gid = item.data(0, Qt.ItemDataRole.UserRole + 1)
    if gid:
        page._gating_collapsed.discard(gid)


def _gating_rebuild_tree(ctx, page) -> None:
    """Rebuild the hierarchical gate tree."""
    tree = page._gating_tree
    tree.blockSignals(True)
    tree.clear()
    root = QTreeWidgetItem(["All Events"])
    root.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
    root.setData(0, Qt.ItemDataRole.UserRole, -1)
    root.setData(0, Qt.ItemDataRole.UserRole + 1, None)
    tree.addTopLevelItem(root)

    by_parent = {}
    for idx, gate in enumerate(page._gating_gates):
        by_parent.setdefault(gate.get("parent"), []).append((idx, gate))

    # Show last-known stats immediately; a background recompute follows.
    stats = page._gating_last_stats

    def add_children(parent_item, parent_id):
        for idx, gate in by_parent.get(parent_id, []):
            item = QTreeWidgetItem(
                [_gating_gate_display_name(ctx, gate, idx, stats)]
            )
            item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            )
            item.setData(0, Qt.ItemDataRole.UserRole, idx)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, gate["id"])
            parent_item.addChild(item)
            add_children(item, gate["id"])

    add_children(root, None)

    def expand_item(item):
        gid = item.data(0, Qt.ItemDataRole.UserRole + 1)
        item.setExpanded(gid not in page._gating_collapsed)
        for i in range(item.childCount()):
            expand_item(item.child(i))

    expand_item(root)
    tree.blockSignals(False)
    tree.resizeColumnToContents(0)
    for gate_items in page._gating_rois:
        _gating_set_items_handles_visible(ctx, gate_items, False)
    for entry in page._gating_skewed_lines.values():
        _gating_set_items_handles_visible(ctx, [entry["roi"]], False)
    overlay = getattr(page, "_gating_gate_overlay", None)
    if overlay is not None:
        overlay.update()
    _gating_refresh_tree_stats(ctx, page)


def _gating_set_image_levels(ctx, page, lo, hi) -> None:
    """Store the image channel's display Min/Max and re-render (visual only)."""
    preview = page._gating_preview
    preview["image_min"] = lo
    preview["image_max"] = hi
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_on_preview_change(ctx, page) -> None:
    """Re-render and mark dirty after a scatter setting change."""
    _widgets_gate_editor_editor._gating_render(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_pick_gate_color(ctx, page, button=None) -> None:
    """Choose the colour used to draw every gate outline."""
    preview = page._gating_preview
    chosen = QColorDialog.getColor(
        QColor(_gate_color(preview)), ctx.host, "Gate color"
    )
    if not chosen.isValid():
        return
    preview["gate_color"] = chosen.name()
    if button is not None:
        button.setStyleSheet(
            f"background-color: {chosen.name()}; "
            "border: 1px solid #6b7785; border-radius: 3px;"
        )
    _gating_apply_gate_color(ctx, page)
    _gating_mark_dirty(ctx, page)


def _gating_apply_gate_color(ctx, page) -> None:
    """Re-pen every gate item with the current gate colour."""
    overlay = getattr(page, "_gating_gate_overlay", None)
    if overlay is not None:
        overlay.update()
    color = _gate_color(page._gating_preview)
    hover = _gate_hover_color(color)
    viewbox = getattr(page, "_gating_viewbox", None)
    if viewbox is not None:
        viewbox.gate_color = color
    pen = pg.mkPen(color, width=2)
    hover_pen = pg.mkPen(hover, width=3)
    handle_pen = pg.mkPen(color, width=1)
    handle_hover_pen = pg.mkPen(hover, width=2)

    def repen(item) -> None:
        from shiboken6 import isValid

        if not isValid(item):
            return
        if isinstance(item, pg.InfiniteLine):
            item.setPen(pen)
            item.setHoverPen(hover_pen)
            return
        try:
            item.setPen(pen)
            item.setHoverPen(hover_pen)
        except (AttributeError, TypeError):
            pass
        # pyqtgraph handles are Handle objects (not QGraphicsItems): they
        # keep their pens as attributes, so set those and repaint.
        for handle in getattr(item, "handles", []):
            widget = handle.get("item") if isinstance(handle, dict) else handle
            if widget is None:
                continue
            try:
                widget.pen = handle_pen
                widget.hoverPen = handle_hover_pen
                widget.currentPen = handle_pen
                widget.update()
            except (AttributeError, TypeError):
                pass

    for items in page._gating_rois:
        for item in items:
            repen(item)
    for entry in page._gating_crosshairs.values():
        for line in entry.get("lines", []):
            repen(line)
    for entry in getattr(page, "_gating_split_lines", {}).values():
        for line in entry.get("lines", []):
            repen(line)
    for entry in getattr(page, "_gating_skewed_lines", {}).values():
        roi = entry.get("roi")
        if roi is not None:
            repen(roi)


def _gating_switch_file(ctx, page, path, name=None) -> None:
    """Show `path` in the gating plot, keeping the gate hierarchy.

    Called from the left data-file list and from the plot settings File
    combo; the chosen file is part of the saved preview state.
    """
    if not path:
        return
    preview = page._gating_preview
    rec = _widgets_plot_renderer._figure_record(ctx, path)
    if name is None and rec is not None:
        name = rec.get("name")
    if preview.get("file") == path and name in (None, preview.get("file_name")):
        return
    old_file = str(preview.get("file") or "")
    preview["file"] = path
    preview["file_name"] = name
    # Keep the current gate hierarchy (subset / editing context) when
    # switching files; only the axes may need to follow the new file.
    new_cols = _data_columns._columns_for(ctx, path, page._gating_task_id)
    gate_id = page._gating_active_gate_id or page._gating_context_gate_id
    gate = next(
        (g for g in page._gating_gates if g["id"] == gate_id), None
    ) if gate_id else None
    plot_type = preview.get("type", "scatter")
    gate_type = gate.get("plot_type") if gate is not None else None
    gate_axes_ok = gate is not None and gate_type in (None, plot_type)
    old_axes = (preview.get("x"), preview.get("y"))
    if plot_type == "image":
        preview["x"] = "x" if "x" in new_cols else None
        preview["y"] = "y" if "y" in new_cols else None
        channels = [c for c in new_cols if c not in ("x", "y")]
        if preview.get("image_channel") not in channels:
            preview["image_channel"] = channels[0] if channels else None
    elif plot_type == "histogram":
        x = gate["x"] if gate_axes_ok and gate.get("x") else None
        if x not in new_cols:
            x, _ = _gating_default_axes(ctx, new_cols, preview)
        preview["x"] = x if x in new_cols else (
            new_cols[0] if new_cols else None
        )
        preview["y"] = None
    else:
        if gate_axes_ok and gate.get("x") and gate.get("y"):
            preview["x"] = gate["x"]
            preview["y"] = gate["y"]
        else:
            preview["x"], preview["y"] = _gating_default_axes(ctx,
                new_cols, preview
            )
    # Keep the visible range when switching files (not for image view,
    # which uses the image's own extent), but only when the axes did not
    # change with the file.
    keep_view = (
        plot_type != "image"
        and (preview.get("x"), preview.get("y")) == old_axes
    )
    vb = page._gating_viewbox
    xr, yr = vb.viewRange()
    _gating_reload_rois(ctx, page)
    _widgets_gate_editor_editor._gating_render(ctx, page)
    if keep_view:
        vb.setXRange(xr[0], xr[1], padding=0)
        vb.setYRange(yr[0], yr[1], padding=0)
    # Clear the previous file's statistics and recompute for this one.
    page._gating_last_stats = {}
    _gating_rebuild_tree(ctx, page)
    _gating_mark_dirty(ctx, page)
    _widgets_gate_editor_editor._gating_rebuild_settings(ctx, page)
    _gating_refresh_files(ctx, page)
    runlog.action(
        "gating.file", module=str(page._gating_task_id),
        old=old_file, new=str(path), type=str(plot_type),
    )


def _gating_mark_dirty(ctx, page) -> None:
    page._gating_dirty = True
    _gating_update_save_btn(ctx, page)
    # `page` may be a _GatingState embedded in another module (Unmix).
    ctx._module_set_state(getattr(page, "_module_page", page), MODULE_STATE_MODIFIED)


def _gating_save_now(ctx, page) -> None:
    params = page._gating_task_def.setdefault("params", {})
    params["preview"] = page._gating_preview
    if page._gating_uses_store:
        _gating_store_save(ctx, page._gating_task_def, {
            "files": page._gating_files,
            "gates": page._gating_gates,
            "per_file": page._gating_per_file,
        })
    else:
        params["gates"] = page._gating_gates
    if ctx.project is not None:
        ctx.project.save()
    page._gating_snapshot = (
        copy.deepcopy({
            "files": page._gating_files,
            "gates": page._gating_gates,
            "per_file": page._gating_per_file,
        })
        if page._gating_uses_store
        else copy.deepcopy(page._gating_task_def.get("params", {}))
    )
    runlog.info(
        "gating.saved", module=str(page._gating_task_id),
        gates=len(page._gating_gates), files=len(page._gating_files or []),
    )
    page._gating_dirty = False
    _gating_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Gating saved")
    ctx._module_saved(page)


def _gating_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_gating_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._gating_dirty and not page._gating_locked)
    hook = getattr(page, "_gating_on_dirty", None)
    if hook is not None:
        hook()


def _gating_close(ctx, page) -> None:
    if page._gating_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Gating",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if page._gating_uses_store:
            page._gating_files = list(
                (page._gating_snapshot or {}).get("files") or []
            )
            page._gating_gates = list(
                (page._gating_snapshot or {}).get("gates") or []
            )
            page._gating_per_file = dict(
                (page._gating_snapshot or {}).get("per_file") or {}
            )
        else:
            page._gating_task_def["params"] = page._gating_snapshot
    _widgets_gate_editor_editor._gating_cleanup(ctx, page)
    ctx._close_module_page(page)
