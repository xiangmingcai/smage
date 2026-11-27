"""Figure renderers: scatter, image, histogram, ranges and their settings."""
from __future__ import annotations

import math

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QFrame,
    QGraphicsItem, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smage.core.formatting import _format_level, _round_level
from smage.core.image_ops import _pool_mask
from smage.core.pipeline import (
    _evaluate_stages, _pipeline_touches, _rename_applies, _stages_after,
)
from smage.core.sampling import _stable_sample_selection
from smage.core.scale_math import (
    _apply_scale_transforms, _forward_scale_value, _inverse_scale_value,
)
from smage.core.settings import (
    TOOLTIP_QSS, _default_palette, _plot_background, _setting,
)
from smage.runlog import runlog
from smage.widgets.colormap import _figure_colormap
from smage.widgets.controls import (
    _LogStepSpinBox, _NoWheelComboBox, _NoWheelSpinBox,
)
from smage.widgets.image_raster import _image_cells, _image_raster_texture
from smage.widgets.plot_axes import (
    _figure_image_layers, _is_scatter_plot, _make_fit_plot_item,
    _make_original_axis, _make_sci_axis,
)


FIGURE_COLOR_MODES = [
    ("Density", "density"),
    ("Parameter", "parameter"),
    ("Solid", "solid"),
]

# Point sizes are screen pixels.  The upper bound keeps a saved value from
# an older project (where Fixed meant data units) from filling the plot.
POINT_SIZE_MIN = 0.5
POINT_SIZE_MAX = 64.0
POINT_SIZE_DEFAULT = 4.0


def _point_size_px(value, default: float = POINT_SIZE_DEFAULT) -> float:
    """A point diameter in screen pixels, safe for every saved project."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(size):
        return float(default)
    if size > POINT_SIZE_MAX:
        # Pre-pixel-mode projects stored a data-unit size (often thousands),
        # which would now be a plot-filling disc.
        return float(default)
    return max(POINT_SIZE_MIN, size)
def _figure_status_title(plot_widget, text, **kwargs) -> None:
    """Set a transient/error title and force display options to reapply later.

    The display-options cache includes the sample-name title.  Without this
    invalidation, a successful render could skip re-applying the title and
    leave "Loading data…" on a plot whose points are already drawn.
    """
    plot_widget.setTitle(text, **kwargs)
    plot_widget._display_signature = None


IMAGE_LAYER_COLORS = [
    (0, 0, 255),      # blue
    (0, 255, 0),      # green
    (255, 0, 0),      # red
    (255, 0, 255),    # magenta
    (0, 255, 255),    # cyan
    (255, 255, 0),    # yellow
    (255, 128, 0),    # orange
    (128, 0, 255),    # violet
]

# Empty margin around a data plot: the configured Min/Max sit at 5 % and 95 %
# of the visible axis, so points and bars do not touch the frame. Image plots
# keep their exact extent (aspect-locked pixels).
FIGURE_AXIS_MARGIN = 0.05

class _FigureLegendWidget(QFrame):
    """A compact colour legend shown beside an image plot."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("imageLegend")
        self.setStyleSheet(
            "#imageLegend { background: #ffffff; border: 1px solid #c9d5e2; "
            "border-radius: 4px; }"
        )
        self.setMinimumWidth(80)
        self.setMaximumWidth(180)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(6, 6, 6, 6)
        self._lay.setSpacing(4)

    def set_layers(self, layers) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for layer in layers:
            color = tuple(layer.get("color", (255, 255, 255)))
            name = str(layer.get("name") or layer.get("parameter") or "")
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(4)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background-color: rgb({color[0]},{color[1]},{color[2]}); "
                "border: 1px solid #888;"
            )
            label = QLabel(name)
            label.setStyleSheet("color: #333;")
            rh.addWidget(swatch)
            rh.addWidget(label, 1)
            self._lay.addWidget(row)
        self._lay.addStretch(1)


def _figure_record(ctx, file_path):
    """Return the file record (imported or Feature output) for a path."""
    return ctx._file_record(file_path)


def _figure_axis_scale_configs(ctx, task_id, parquet_path, column, column_transforms=None) -> list:
    """Display-only Scale transforms for `column` at this module.

    The data itself is never scaled; these configs are applied when drawing
    (and when converting axis labels/ranges back to original values).

    Memoized per context: gate labels ask for the same axis configs on
    every mouse move while a gate is dragged, and the lookup walks the task
    chain.  ``_bump_stages_revision`` (module saved / task deleted) clears it.
    """
    if not column:
        return []
    revision = getattr(ctx, "_stages_revision", 0)
    cache = getattr(ctx, "_axis_scale_cache", None)
    if cache is None:
        cache = {}
        ctx._axis_scale_cache = cache
    elif getattr(ctx, "_axis_scale_cache_revision", None) != revision:
        cache.clear()
    ctx._axis_scale_cache_revision = revision
    cache_key = (
        task_id,
        None if parquet_path is None else str(parquet_path),
        column,
    )
    cached = None if column_transforms else cache.get(cache_key)
    if cached is not None:
        return list(cached)
    stages = (
        ctx._map_stages(
            task_id, include_scale=True, parquet_path=parquet_path
        ) if task_id else []
    )
    feat = ctx._feature_output(parquet_path)
    if feat is not None and task_id:
        post = ctx._map_stages(
            task_id, after=feat.get("feature_task"), include_scale=True,
            parquet_path=parquet_path,
        )
        stages = [(k, st) for k, st in stages if k == "rename"] + post
    configs = []
    for kind, stage in stages:
        if kind != "scale":
            continue
        files = stage.get("files")
        if (
            files is not None and parquet_path is not None
            and parquet_path not in files
        ):
            # This Scale module does not apply to this file.
            continue
        config = stage["columns"].get(column)
        if config:
            configs.append(config)
    extra = (column_transforms or {}).get(column) or []
    configs.extend(extra)
    # Only log10 / arcsinh move the values; a linear config leaves the
    # axis untouched (pyqtgraph's own ticks are the right ones there).
    result = [
        config for config in configs
        if (config or {}).get("method") in ("log10", "arcsinh")
    ]
    if not column_transforms:
        cache[cache_key] = result
    return list(result)


def _figure_render(ctx, plot_dict: dict, plot_widget, column_transforms=None, task_id=None, extra_stages=None, image_levels=None, base_stages=None, delta_stages=None, base_cache=None, base_sig=None, base_coords=()) -> None:
    """(Re)draw a plot widget from its plot_dict.

    `base_stages`/`delta_stages` split the pipeline for the image renderer:
    the base is cached per view and only the delta (the module's own
    correction) is recomputed on a redraw. See `_image_raster_texture`.
    """
    if plot_dict.get("type") == "image":
        ctx._figure_render_image(
            plot_dict, plot_widget, column_transforms, task_id, extra_stages,
            image_levels=image_levels,
            base_stages=base_stages, delta_stages=delta_stages,
            base_cache=base_cache, base_sig=base_sig,
            base_coords=base_coords,
        )
        return
    mask, error = ctx._figure_filter_mask(plot_dict, task_id)
    if error:
        plot_widget.clear()
        _figure_status_title(plot_widget, error, color="#c0392b", size="10pt")
        return
    if plot_dict.get("type") == "histogram":
        ctx._figure_render_histogram(
            plot_dict, plot_widget, column_transforms, mask, task_id,
            extra_stages,
        )
    else:
        ctx._figure_render_scatter(
            plot_dict, plot_widget, column_transforms, mask, task_id,
            extra_stages,
        )


def _figure_range_row(ctx, plot_dict: dict, axis: str, plot_widget, enabled: bool,
                      on_commit=None, configs=None, task_id=None,
                      column_transforms=None) -> QWidget:
    """Build the range row: mode (Manual / From scale / Auto) + Min/Max.

    Scatter, overlay and histogram axes offer the three modes. Image plots
    and the Scale module's own preview keep the old Auto checkbox (there is
    no channel/Scale source to read there). The spin boxes show original
    units even though the plot draws transformed values.
    """
    ptype = plot_dict.get("type")
    min_key = f"{axis}min"
    max_key = f"{axis}max"
    use_modes = ptype != "image" and bool(task_id)
    data_min = getattr(plot_widget, f"_data_{axis}min", None)
    data_max = getattr(plot_widget, f"_data_{axis}max", None)

    def display(value):
        return _figure_display_range_value(value, configs)

    if not use_modes:
        # Legacy two-state behaviour: Auto checkbox + Min/Max.
        if configs:
            data_min = display(data_min)
            data_max = display(data_max)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        auto = QCheckBox("Auto")
        auto.setChecked(
            plot_dict.get(min_key) is None
            and plot_dict.get(max_key) is None
        )
        auto.setEnabled(enabled)

        min_val = display(plot_dict.get(min_key))
        max_val = display(plot_dict.get(max_key))
        if min_val is None:
            min_val = data_min
        if max_val is None:
            max_val = data_max

        min_spin = _LogStepSpinBox(min_val, lambda v: commit(), decimals=1)
        max_spin = _LogStepSpinBox(max_val, lambda v: commit(), decimals=1)

        def commit():
            if auto.isChecked():
                plot_dict[min_key] = None
                plot_dict[max_key] = None
                min_spin.set_value(data_min)
                max_spin.set_value(data_max)
            else:
                plot_dict[min_key] = _forward_scale_value(
                    min_spin.value(), configs
                )
                plot_dict[max_key] = _forward_scale_value(
                    max_spin.value(), configs
                )
            min_spin.set_enabled(enabled and not auto.isChecked())
            max_spin.set_enabled(enabled and not auto.isChecked())
            ctx._figure_apply_ranges(
                plot_dict, plot_widget, task_id, column_transforms
            )
            if on_commit is not None:
                on_commit()

        auto.toggled.connect(lambda _: commit())
        min_spin.set_enabled(enabled and not auto.isChecked())
        max_spin.set_enabled(enabled and not auto.isChecked())
        h.addWidget(auto)
        h.addWidget(QLabel("Min"))
        h.addWidget(min_spin)
        h.addWidget(QLabel("Max"))
        h.addWidget(max_spin)
        h.addStretch()
        return row

    # Three-mode row.
    count_axis = ptype == "histogram" and axis == "y"
    if not count_axis:
        # Load this channel's own range; the previous channel was saved by
        # its last edit or by this call.
        _figure_sync_range_record(plot_dict, axis)
    scale_ok = False
    if not count_axis:
        scale_ok = _figure_scale_axis_bounds(
            ctx, task_id, plot_dict.get("file"), plot_dict.get(axis),
            column_transforms,
        ) is not None
    fallback_key = f"{axis}_range_auto_fallback"
    mode = _figure_axis_mode(plot_dict, axis, ptype)
    # The mode is the user's choice and survives a file switch: From scale
    # without a fully configured Scale simply uses the data extent until a
    # Scale is available again.  Never rewrite it to Auto here.
    scale_missing = not count_axis and mode == "scale" and not scale_ok
    plot_dict.pop(fallback_key, None)
    if count_axis:
        mode = "manual" if mode == "manual" else "auto"
    plot_dict[f"{axis}_range_mode"] = mode
    _figure_save_range_record(plot_dict, axis, mode, False)

    state = {"updating": False}
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    combo = _NoWheelComboBox()
    combo.addItem("Manual", "manual")
    if not count_axis:
        # Keep the option visible even when the current file has no Scale:
        # the choice must not silently change when another file is opened.
        combo.addItem("From scale", "scale")
    combo.addItem("Auto", "auto")
    combo.setCurrentIndex(max(0, combo.findData(mode)))
    combo.setEnabled(enabled)
    combo.setMinimumWidth(104)
    combo.setToolTip(
        "Manual: use the Min/Max below.\n"
        "From scale: the nearest upstream Scale with both Min and Max;\n"
        "without one, the data extent is used.\n"
        "Auto: the extent of the drawn data.\n"
        "Every mode leaves a 5 % border around the range."
    )
    # Tooltips inside the settings scroll area can inherit the platform's
    # dark tooltip palette; pin this one to the app's light tooltip look.
    combo.setStyleSheet(TOOLTIP_QSS)

    def effective():
        lo, hi = _figure_axis_range(
            ctx, task_id, plot_dict.get("file"), plot_dict, axis,
            plot_widget, column_transforms,
        )
        return display(lo), display(hi)

    lo, hi = effective()
    min_spin = _LogStepSpinBox(lo, lambda v: on_spin_changed(), decimals=1)
    max_spin = _LogStepSpinBox(hi, lambda v: on_spin_changed(), decimals=1)

    def set_spins(lo, hi):
        state["updating"] = True
        min_spin.set_value(lo)
        max_spin.set_value(hi)
        state["updating"] = False

    def update_enabled():
        editable = enabled and mode == "manual"
        min_spin.set_enabled(editable)
        max_spin.set_enabled(editable)

    def on_spin_changed():
        if state["updating"] or mode != "manual":
            return
        plot_dict[min_key] = _forward_scale_value(
            min_spin.value(), configs
        )
        plot_dict[max_key] = _forward_scale_value(
            max_spin.value(), configs
        )
        _figure_save_range_record(plot_dict, axis, "manual", False)
        ctx._figure_apply_ranges(
            plot_dict, plot_widget, task_id, column_transforms
        )
        if on_commit is not None:
            on_commit()

    def on_mode_changed(*_args):
        nonlocal mode
        if state["updating"]:
            return
        new_mode = combo.currentData() or "auto"
        if new_mode == mode:
            return
        if new_mode == "manual":
            # Pre-fill from the range currently on screen.
            lo, hi = effective()
            if lo is not None and hi is not None:
                plot_dict[min_key] = _forward_scale_value(lo, configs)
                plot_dict[max_key] = _forward_scale_value(hi, configs)
        else:
            plot_dict[min_key] = None
            plot_dict[max_key] = None
        mode = new_mode
        plot_dict[f"{axis}_range_mode"] = mode
        plot_dict.pop(fallback_key, None)
        _figure_save_range_record(plot_dict, axis, mode, False)
        set_spins(*effective())
        update_enabled()
        ctx._figure_apply_ranges(
            plot_dict, plot_widget, task_id, column_transforms
        )
        if on_commit is not None:
            on_commit()

    combo.currentIndexChanged.connect(on_mode_changed)
    update_enabled()

    h.addWidget(combo)
    h.addWidget(QLabel("Min"))
    h.addWidget(min_spin)
    h.addWidget(QLabel("Max"))
    h.addWidget(max_spin)
    h.addStretch()
    v.addLayout(h)
    if scale_missing:
        hint = QLabel(
            "No upstream Scale sets both Min and Max for this channel; "
            "using the data extent until one does."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7785;")
        v.addWidget(hint)
    return box


class _CursorLeaveFilter(QObject):
    """Hide the cursor readout when the pointer leaves an image plot."""

    def __init__(self, plot_widget) -> None:
        super().__init__(plot_widget)
        self._plot_widget = plot_widget

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Leave:
            _figure_hide_cursor_readout(self._plot_widget)
        return False


def _figure_hide_cursor_readout(plot_widget) -> None:
    item = plot_widget.getPlotItem()
    setter = getattr(item, "set_readout", None)
    if setter is not None:
        setter("")


def _figure_release_cursor_row(plot_widget) -> None:
    """Give back the extra row under the X axis (non-image plots)."""
    item = plot_widget.getPlotItem()
    release = getattr(item, "release_readout_row", None)
    if release is not None:
        release()


def _figure_install_cursor_readout(plot_widget) -> None:
    """Wire scene mouse-move events to the image cursor readout once."""
    item = plot_widget.getPlotItem()
    reserve = getattr(item, "reserve_readout_row", None)
    if reserve is not None:
        reserve()
    if getattr(plot_widget, "_cursor_readout_ready", False):
        return
    plot_widget._cursor_readout_ready = True
    last = {"time": 0.0}

    def on_move(pos) -> None:
        import time

        now = time.monotonic()
        if now - last["time"] < 0.03:
            return
        last["time"] = now
        _figure_update_cursor_readout(plot_widget, pos)

    plot_widget.scene().sigMouseMoved.connect(on_move)
    leave_filter = _CursorLeaveFilter(plot_widget)
    plot_widget.installEventFilter(leave_filter)
    viewport = plot_widget.viewport()
    if viewport is not None:
        viewport.installEventFilter(leave_filter)
    plot_widget._cursor_leave_filter = leave_filter


def _figure_format_cursor_value(value) -> str:
    """One decimal place, plain (never scientific) for the cursor readout."""
    import math

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(numeric):
        return "—"
    return f"{numeric:.1f}"


def _figure_update_cursor_readout(plot_widget, scene_pos) -> None:
    """Show x/y and every visible layer's value under the cursor."""
    item = plot_widget.getPlotItem()
    setter = getattr(item, "set_readout", None)
    if setter is None:
        return
    probe = getattr(plot_widget, "_image_probe", None)
    if not probe or getattr(plot_widget, "_cursor_readout_suppressed", False):
        setter("")
        return
    viewbox = item.getViewBox()
    if not viewbox.sceneBoundingRect().contains(scene_pos):
        setter("")
        return
    point = viewbox.mapSceneToView(scene_pos)
    x, y = float(point.x()), float(point.y())
    parts = [f"x: {int(round(x))}", f"y: {int(round(y))}"]
    for layer in probe.get("layers") or []:
        array = layer.get("array")
        rect = layer.get("rect")
        if array is None or rect is None:
            continue
        if rect.width() <= 0 or rect.height() <= 0:
            continue
        u = (x - rect.x()) / rect.width()
        v = (y - rect.y()) / rect.height()
        if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
            continue
        rows, cols = array.shape[:2]
        i = min(cols - 1, max(0, int(u * cols)))
        j = min(rows - 1, max(0, int(v * rows)))
        value = array[j, i]
        text = _figure_format_cursor_value(value)
        name = layer.get("name") or layer.get("channel") or ""
        parts.append(f"{name}: {text}".strip(": "))
    setter("  ".join(parts))


def _image_filter_fraction_for_rect(mask_ent, manifest, rect, shape):
    """Filter-mask coverage for a raster that has no pooled `frac` yet.

    Most image channels come from the normal pyramid path, which already
    returns a pooled mask fraction.  Unmixed, segmentation, Gate2Col,
    Feature Up, dimension-reduction, cluster and data-clean rasters come from
    their own saved pyramids, so they need the filter mask here — otherwise
    switching to such a channel silently draws the unfiltered image.
    """
    import numpy as np

    if mask_ent is None or not shape:
        return None
    height, width = int(shape[0]), int(shape[1])
    if height <= 0 or width <= 0:
        return None
    xmin = float(manifest["xmin"])
    ymin = float(manifest["ymin"])
    levels = mask_ent.get("levels") or {}
    if levels:
        cell = float(rect.width()) / float(width)
        block = int(round(cell))
        if block >= 1 and abs(cell - block) < 1e-6 and block in levels:
            level = levels[block]
            i0 = max(0, int(round((float(rect.left()) - xmin) / block)))
            j0 = max(0, int(round((float(rect.top()) - ymin) / block)))
            crop = level[j0:j0 + height, i0:i0 + width]
            if crop.shape == (height, width):
                return crop
    mask2d = mask_ent.get("mask_2d")
    if mask2d is None:
        return None
    xs = float(rect.left()) + (
        np.arange(width, dtype="float64") + 0.5
    ) * float(rect.width()) / width
    ys = float(rect.top()) + (
        np.arange(height, dtype="float64") + 0.5
    ) * float(rect.height()) / height
    qi = np.clip(
        np.floor(xs - xmin).astype(np.int64), 0, mask2d.shape[1] - 1
    )
    qj = np.clip(
        np.floor(ys - ymin).astype(np.int64), 0, mask2d.shape[0] - 1
    )
    return mask2d[np.ix_(qj, qi)].astype("float64")


def _figure_render_image(ctx, plot_dict: dict, plot_widget, column_transforms=None, task_id=None, extra_stages=None, row_mask=None, image_levels=None, base_stages=None, delta_stages=None, base_cache=None, base_sig=None, base_coords=()) -> None:
    """Render an Image plot as a raster (LOD pyramid), ImageJ-style.

    `row_mask` is an optional boolean/uint8 array aligned to the image's
    pixel rows; pixels it marks False are hidden (used by Gating's subset
    view to show only the points inside the active gate).

    `image_levels` is an optional (lo, hi) display range shared by several
    plots of the same channel (the Illumination previews). It is used for
    layers without their own Min/Max, in display (post-Scale) units.

    `base_stages`/`delta_stages` split the pipeline: the channel after
    `base_stages` is cached per view, and `delta_stages` (the module's own
    correction) is applied on top on each redraw without re-reading the
    file. Both may be None for the normal path.
    """
    import numpy as np
    ctx._figure_bump_render_token(plot_widget)
    plot_widget._image_probe = None
    _figure_hide_cursor_readout(plot_widget)
    ctx._figure_update_image_legend(plot_widget, [])
    # Apply the axis labels before any early return: switching from a
    # scatter plot to an image must show x/y even if this file cannot be
    # rastered yet. The later call is a no-op thanks to the signature check.
    ctx._figure_apply_display_options(plot_dict, plot_widget)
    rec = ctx._figure_record(plot_dict.get("file"))
    if not rec:
        plot_widget.clear()
        plot_widget._img_fitted = False
        _figure_status_title(plot_widget, "No data file", color="#c0392b", size="10pt")
        return
    parquet_path = rec.get("parquet")
    # Display-only scale transforms (never modify the data itself).
    display_cache = {}

    def display_configs(column):
        if column not in display_cache:
            display_cache[column] = _figure_axis_scale_configs(
                ctx, task_id, parquet_path, column, column_transforms
            )
        return display_cache[column]

    ent = ctx._ensure_image_index(parquet_path)
    if ent is None or ent.get("manifest") is None:
        plot_widget.clear()
        plot_widget._img_fitted = False
        _figure_status_title(plot_widget,
            "No image index — re-import if this is an image",
            color="#c0392b", size="10pt",
        )
        return
    manifest = ent["manifest"]
    vb = plot_widget.getPlotItem().getViewBox()
    # Rebuild the image item when the plot shows a different file (its
    # extent may differ) or after an error cleared the plot.
    if (
        not getattr(plot_widget, "_img_fitted", False)
        or getattr(plot_widget, "_img_path", None) != parquet_path
    ):
        plot_widget.clear()
        img = pg.ImageItem(axisOrder="row-major")
        plot_widget.addItem(img)
        if getattr(plot_widget, "_gate_cache_data", False):
            img.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        plot_widget._img_item = img
        plot_widget._img_path = parquet_path
        plot_widget._img_extent = (
            manifest["xmin"], manifest["xmax"],
            manifest["ymin"], manifest["ymax"],
        )
        vb.setAspectLocked(True)
        vb.disableAutoRange()
        vb.setRange(
            xRange=(manifest["xmin"], manifest["xmax"]),
            yRange=(manifest["ymin"], manifest["ymax"]),
            padding=0,
        )
        plot_widget._img_fitted = True
    img = plot_widget._img_item
    xr = vb.viewRange()[0]
    yr = vb.viewRange()[1]
    x0, x1 = min(xr), max(xr)
    y0, y1 = min(yr), max(yr)
    if x1 <= x0 or y1 <= y0:
        return
    wpx = max(1, plot_widget.width())
    hpx = max(1, plot_widget.height())
    spacing = max(1e-12, min((x1 - x0) / wpx, (y1 - y0) / hpx))

    # Resolve the subset/filter mask (computed in a background worker, cached).
    mask_ent = None
    if row_mask is not None:
        # Gating subset view: hide pixels the active gate excludes.
        height = int(round(manifest["ymax"] - manifest["ymin"] + 1))
        width = int(round(manifest["xmax"] - manifest["xmin"] + 1))
        m = np.asarray(row_mask, dtype=np.uint8)
        if m.size == height * width:
            mask_2d = m.reshape(int(height), int(width))
            blocks = [info["block"] for info in manifest.get("levels", [])]
            mask_ent = {"mask_2d": mask_2d, "levels": _pool_mask(mask_2d, blocks)}
    f = plot_dict.get("filter")
    if mask_ent is None and f:
        sig = (f.get("task_id"), f.get("gate_id"))
        key = (parquet_path, sig)
        cache = getattr(ctx, "_image_filter_cache", {})
        if key not in cache:
            err = ctx._figure_image_filter_error(f, parquet_path)
            if err:
                _figure_status_title(plot_widget, err, color="#c0392b", size="10pt")
                return
            worker = ctx._image_filter_worker()
            pending = getattr(ctx, "_image_filter_pending", {})
            if key not in pending:
                pending[key] = True
                gating_task = ctx._find_task_by_id(f.get("task_id"))
                gates = ctx._gating_gates_for(gating_task, parquet_path)
                stages = ctx._map_stages(
                    f.get("task_id"), parquet_path=parquet_path
                )
                base_columns = set(
                    ctx._column_index.get(
                        ctx._rel_data_key(parquet_path), []
                    )
                )
                height = int(round(manifest["ymax"] - manifest["ymin"] + 1))
                width = int(round(manifest["xmax"] - manifest["xmin"] + 1))
                blocks = [info["block"] for info in manifest.get("levels", [])]
                worker.request(
                    key, parquet_path, gates, f.get("gate_id"),
                    stages, base_columns, height, width, blocks,
                    origin=(manifest["xmin"], manifest["ymin"]),
                )
            _figure_status_title(plot_widget, "Applying filter…", color="#b58900", size="10pt")
            return
        mask_ent = cache[key]
        if isinstance(mask_ent, str):
            _figure_status_title(plot_widget, mask_ent, color="#c0392b", size="10pt")
            return
        if mask_ent is None:
            _figure_status_title(plot_widget,
                "Filter could not be applied", color="#c0392b", size="10pt"
            )
            return

    # Sampled pixels from an upstream Subsample module: an overlay that
    # hides blocks that are not mostly sampled. It rides the same
    # block-level fraction channel as the gate filter and uses the same
    # "more than half" rule, so a block is opaque only when most of its
    # pixels survived sampling.
    cover = None
    if task_id:
        cover = ctx._subsample_cover_crop(
            task_id, parquet_path, manifest, (x0, x1, y0, y1), spacing
        )

    stages = (
        ctx._map_stages(task_id, parquet_path=parquet_path) if task_id else []
    )
    if extra_stages:
        stages = stages + list(extra_stages)
    color_cols = manifest.get("color_cols", [])

    layers = _figure_image_layers(plot_dict)
    if not layers:
        # Nothing selected: default to the first real channel (never x/y).
        default_channel = next(
            (c for c in color_cols if c not in ("x", "y")),
            color_cols[0] if color_cols else None,
        )
        if default_channel is None:
            plot_widget.clear()
            plot_widget._img_fitted = False
            _figure_status_title(plot_widget,
                "No channels available", color="#c0392b", size="10pt",
            )
            ctx._figure_update_image_legend(plot_widget, [])
            return
        layers = [{
            "parameter": default_channel,
            "color": (255, 255, 255),
            "name": str(default_channel),
        }]

    rasters = []
    rendered_layers = []
    probe_layers = []
    ref_shape = None
    ref_rect = None
    has_mask = False
    mask_accum = None
    unavailable = []
    # Mask columns exposed by an upstream Segmentation module are not part
    # of the image index; they are rasterised straight from the mask file.
    seg_cols = ctx._seg_applied_columns(task_id, parquet_path) if task_id else {}
    # Converted Region channels come from their own 0/1 mask parquet.
    region_cols = (
        ctx._region_applied_columns(task_id, parquet_path) if task_id else {}
    )
    # Gate2Col 0/1 columns likewise come from their own image parquet.
    g2c_cols = ctx._gate2col_applied_columns(task_id, parquet_path) if task_id else {}
    # Feature Up mapped columns likewise come from their own image parquet.
    fup_cols = ctx._feature_up_applied_columns(task_id, parquet_path) if task_id else {}
    # Dimension Reduction columns likewise come from their own image parquet.
    dimred_cols = ctx._dimred_applied_columns(task_id, parquet_path) if task_id else {}
    # Cluster label columns likewise come from their own image parquet.
    cluster_cols = ctx._cluster_applied_columns(task_id, parquet_path) if task_id else {}
    # Data Clean 0/1 columns likewise come from their own image parquet.
    dc_cols = ctx._dc_applied_columns(task_id, parquet_path) if task_id else {}
    # Precomputed unmixed channels from "Unmix for image" (usable ones and
    # names whose saved pyramid is stale/missing).
    if task_id:
        unmix_cols, unmix_stale = ctx._unmix_image_outputs(task_id, parquet_path)
    else:
        unmix_cols, unmix_stale = {}, set()
    # Channels whose stored pyramid cannot be read: reported as such
    # instead of "parameter not in this file".
    missing_pyramid = []
    for layer in layers:
        if layer.get("visible") is False:
            continue
        channel = layer.get("parameter")
        if not channel:
            continue
        if channel in seg_cols:
            chan, rect = ctx._seg_mask_raster(
                seg_cols[channel], (x0, x1, y0, y1), spacing
            )
            if chan is None:
                unavailable.append(channel)
                continue
            lo = 0.0
            hi = float(chan.max()) if chan.size else 1.0
            if hi <= lo:
                hi = lo + 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in region_cols:
            rec = region_cols[channel]
            chan, rect = ctx._region_channel_raster(
                rec, (x0, x1, y0, y1), spacing
            )
            if chan is None:
                unavailable.append(channel)
                continue
            lo = 0.0
            hi = 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in g2c_cols:
            rec = g2c_cols[channel]
            chan, rect = ctx._g2c_raster(rec, (x0, x1, y0, y1), spacing)
            if chan is None:
                if not ctx._stored_pyramid_ok(rec):
                    missing_pyramid.append(channel)
                else:
                    unavailable.append(channel)
                continue
            lo = 0.0
            hi = float(chan.max()) if chan.size else 1.0
            if hi <= lo:
                hi = lo + 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in fup_cols:
            rec = fup_cols[channel]
            chan, rect = ctx._feature_up_raster(rec, (x0, x1, y0, y1), spacing)
            if chan is None:
                if not ctx._stored_pyramid_ok(rec):
                    missing_pyramid.append(channel)
                else:
                    unavailable.append(channel)
                continue
            lo = float(np.nanmin(chan)) if chan.size else 0.0
            hi = float(np.nanmax(chan)) if chan.size else 1.0
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = 0.0, 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in dimred_cols:
            rec = dimred_cols[channel]
            chan, rect = ctx._dimred_raster(rec, (x0, x1, y0, y1), spacing)
            if chan is None:
                if not ctx._stored_pyramid_ok(rec):
                    missing_pyramid.append(channel)
                else:
                    unavailable.append(channel)
                continue
            lo = float(np.nanmin(chan)) if chan.size else 0.0
            hi = float(np.nanmax(chan)) if chan.size else 1.0
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = 0.0, 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in cluster_cols:
            rec = cluster_cols[channel]
            chan, rect = ctx._cluster_raster(rec, (x0, x1, y0, y1), spacing)
            if chan is None:
                if not ctx._stored_pyramid_ok(rec):
                    missing_pyramid.append(channel)
                else:
                    unavailable.append(channel)
                continue
            lo = float(np.nanmin(chan)) if chan.size else 0.0
            hi = float(np.nanmax(chan)) if chan.size else 1.0
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = 0.0, 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in dc_cols:
            rec = dc_cols[channel]
            chan, rect = ctx._dc_raster(rec, (x0, x1, y0, y1), spacing)
            if chan is None:
                if not ctx._stored_pyramid_ok(rec):
                    missing_pyramid.append(channel)
                else:
                    unavailable.append(channel)
                continue
            lo = 0.0
            hi = 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in unmix_cols:
            rec = unmix_cols[channel]
            chan, rect = ctx._unmix_raster(rec, (x0, x1, y0, y1), spacing)
            if chan is None:
                if not ctx._stored_pyramid_ok(rec):
                    missing_pyramid.append(channel)
                else:
                    unavailable.append(channel)
                continue
            # The saved pyramid holds post-unmix values; apply the map stages
            # below the Unmix task (Scale in particular) so the raster and
            # the Min/Max controls share one unit system.  Position-dependent
            # stages (Illumination) measure the pattern from the image origin
            # and average it over each raster cell, so the correction keeps
            # its absolute position at every zoom level.
            post = _stages_after(stages, "unmix", rec.get("task_id"))
            if post:
                values = {channel: chan}
                evaluated = _evaluate_stages(
                    post, values, [channel],
                    parquet_path=parquet_path,
                    origin=(
                        float(manifest["xmin"]), float(manifest["ymin"])
                    ),
                    cells=_image_cells(rect, chan.shape),
                )
                chan = evaluated.get(channel, chan)
            lo = float(np.nanmin(chan)) if chan.size else 0.0
            hi = float(np.nanmax(chan)) if chan.size else 1.0
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = 0.0, 1.0
            frac = None
            raster_stages = None
            cname = None
        elif channel in unmix_stale:
            # The saved pyramid no longer matches the current matrix.
            missing_pyramid.append(channel)
            continue
        else:
            # A column renamed by an upstream Rename module is reachable
            # under its new name only.
            renamed_away = any(
                kind == "rename"
                and _rename_applies(stage, parquet_path)
                and channel in stage["map"]
                and stage["map"][channel] != channel
                for kind, stage in stages
            )
            if channel in color_cols:
                if renamed_away:
                    unavailable.append(channel)
                    continue
            else:
                # Channels produced by an upstream map stage are renderable
                # too: Unmix output names and columns renamed by a Rename
                # module (the raster pipeline maps the name back to the
                # stored column).
                produced = any(
                    (
                        kind == "unmix"
                        and not stage.get("generated")
                        and channel in stage["names"]
                    )
                    or (
                        kind == "calculation"
                        and any(
                            row.get("output") == channel
                            for row in stage.get("rows") or []
                        )
                    )
                    for kind, stage in stages
                )
                renamed = any(
                    kind == "rename"
                    and channel in stage["map"].values()
                    and _rename_applies(stage, parquet_path)
                    for kind, stage in stages
                )
                if not (produced or renamed):
                    unavailable.append(channel)
                    continue
            raster_stages = stages if _pipeline_touches(stages, channel) else None
            if base_stages is not None:
                # The base (everything above this module) is cached per
                # view; only the module's own correction is recomputed, so
                # changing a correction parameter does not read the file.
                chan, rect, frac, _cached = _image_raster_texture(
                    ctx, parquet_path, manifest, (x0, x1, y0, y1), spacing,
                    channel, mask_ent=mask_ent, stages=base_stages,
                    force_raw=row_mask is not None,
                    stages_after=delta_stages, base_cache=base_cache,
                    base_sig=base_sig, base_coords=base_coords,
                )
                cname = None
            else:
                chan, rect, frac = ctx._image_raster(
                    parquet_path, manifest, (x0, x1, y0, y1), spacing, channel,
                    mask_ent, raster_stages,
                    # A gate subset must be pixel-exact: pooled block means
                    # mix masked and unmasked pixels and look like the wrong
                    # region.
                    force_raw=row_mask is not None,
                )
                cname = channel if raster_stages is None else None
            if chan is None:
                continue
            ranges = manifest.get("color_ranges", {}) or {}
            lo, hi = ranges.get(cname, (None, None))
            if lo is None or hi is None or not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo = float(np.nanmin(chan))
                hi = float(np.nanmax(chan))
        if frac is None and mask_ent is not None:
            # Channels rastered from another saved pyramid (Unmix,
            # Segmentation, Gate2Col, Feature Up, DimRed, Cluster, Data
            # Clean) do not return a mask fraction; sample the filter here so
            # a channel switch cannot turn a filtered image into a full one.
            frac = _image_filter_fraction_for_rect(
                mask_ent, manifest, rect, getattr(chan, "shape", None)
            )
        # Scale is a display transform: apply it to the raster (and to the
        # automatic range) without touching the stored data.
        configs = display_configs(channel)
        if configs:
            if cname is not None:
                lo = _forward_scale_value(lo, configs)
                hi = _forward_scale_value(hi, configs)
            chan = _apply_scale_transforms(chan, configs)
            if cname is None:
                lo = float(np.nanmin(chan)) if chan.size else 0.0
                hi = float(np.nanmax(chan)) if chan.size else 1.0
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = 0.0, 1.0
        # A caller-supplied shared range (the Illumination module shows the
        # same Before/After image on one scale) replaces the per-view auto
        # range; an explicit layer min/max still wins below.
        if (
            image_levels is not None
            and layer.get("min") is None
            and layer.get("max") is None
        ):
            try:
                level_lo, level_hi = float(image_levels[0]), float(image_levels[1])
            except (TypeError, ValueError, IndexError):
                level_lo = level_hi = None
            if (
                level_lo is not None and level_hi is not None
                and np.isfinite(level_lo) and np.isfinite(level_hi)
                and level_hi > level_lo
            ):
                lo, hi = level_lo, level_hi
        # Per-channel brightness/contrast: a user-set min/max overrides the
        # automatic range for this layer. Either bound may be set alone.
        user_min = layer.get("min")
        user_max = layer.get("max")
        try:
            if user_min is not None:
                lo = float(user_min)
            if user_max is not None:
                hi = float(user_max)
        except (TypeError, ValueError):
            pass
        if hi > lo:
            norm = (chan.astype("float64") - lo) / (hi - lo)
        else:
            norm = np.zeros(chan.shape, dtype="float64")
        # Pixels with no value (a Feature Up / Gate2Col mask outside its
        # objects, for instance) must contribute nothing. Leaving NaN in
        # would poison the additive composite below and black out every
        # layer drawn before it — which is exactly what happened at deep
        # zoom, where the raster is read from raw pixels and the
        # background is NaN instead of the pyramid's 0.
        norm = np.nan_to_num(norm, nan=0.0, posinf=1.0, neginf=0.0)
        norm = np.clip(norm, 0.0, 1.0)
        if frac is not None:
            cov = (frac >= 0.5).astype("float64")
            norm = norm * cov
            has_mask = True
            if mask_accum is None:
                mask_accum = cov
            elif cov.shape == mask_accum.shape:
                mask_accum = np.maximum(mask_accum, cov)
        color = tuple(layer.get("color", (255, 255, 255)))
        rasters.append((norm, color, rect))
        rendered_layers.append({
            "parameter": channel,
            "color": color,
            "name": str(layer.get("name") or channel),
        })
        # Keep the values used to paint this layer so the cursor readout can
        # index them without re-reading the image.
        probe_layers.append({
            "channel": str(channel),
            "name": str(layer.get("name") or channel),
            "array": chan,
            "rect": rect,
        })
        if ref_shape is None:
            ref_shape = norm.shape
            ref_rect = rect

    if not rasters or ref_shape is None:
        img.setVisible(False)
        plot_widget._image_probe = None
        _figure_hide_cursor_readout(plot_widget)
        ctx._figure_update_image_legend(plot_widget, [])
        if missing_pyramid:
            _figure_status_title(plot_widget,
                "Image pyramid not found for: "
                + ", ".join(missing_pyramid)
                + " — please check",
                color="#c0392b", size="10pt",
            )
        elif unavailable:
            _figure_status_title(plot_widget,
                "Parameter not available in this file: " + ", ".join(unavailable),
                color="#c0392b", size="10pt",
            )
        return

    height, width = ref_shape
    canvas = np.zeros((height, width, 3), dtype="float64")
    for norm, color, _rect in rasters:
        if norm.shape != ref_shape:
            continue
        canvas += norm[..., None] * np.asarray(color, dtype="float64")[None, None, :]
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., 0:3] = np.clip(canvas, 0.0, 255.0).astype(np.uint8)
    alpha = None
    if has_mask and mask_accum is not None and mask_accum.shape == ref_shape:
        alpha = mask_accum.astype("float64")
    if cover is not None and cover.shape == ref_shape:
        # A block is opaque only when more than half of its pixels were
        # sampled (each raw-path cell is a single pixel, so it is 0 or 1).
        visible = (cover > 0.5).astype("float64")
        alpha = visible if alpha is None else alpha * visible
    if alpha is not None:
        rgba[..., 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    else:
        rgba[..., 3] = 255
    img.setImage(rgba, autoLevels=False)
    # Keep the composited array alive for as long as the image item is shown
    # (ImageItem paints a QImage that can reference this buffer).
    plot_widget._img_data = rgba
    img.setRect(ref_rect)
    img.setVisible(True)
    item = plot_widget.getPlotItem()
    if getattr(item, "set_readout", None) is not None:
        # Only plots that can show the readout keep the raster values.
        plot_widget._image_probe = {
            "layers": [
                layer for layer in probe_layers
                if layer["array"].shape == ref_shape
            ]
        }
        _figure_install_cursor_readout(plot_widget)
    else:
        plot_widget._image_probe = None
    ctx._figure_apply_display_options(plot_dict, plot_widget)
    for axis in ("bottom", "left"):
        ctx._figure_set_original_ticks(plot_widget, axis, None)
    if missing_pyramid:
        # Some channels could not be drawn: say so in the plot itself.
        _figure_status_title(plot_widget,
            "Image pyramid not found for: "
            + ", ".join(missing_pyramid)
            + " — please check",
            color="#c0392b", size="10pt",
        )
    ctx._figure_update_image_legend(
        plot_widget, rendered_layers,
        show=plot_dict.get("show_legend", True),
    )


def _figure_display_toggles(ctx, layout, plot_dict, enabled, on_change) -> None:
    """Build + wire the display checkboxes and the three font sizes."""
    layout.addWidget(QLabel("Display"))
    for label, key in (
        ("Show axes", "show_axes"),
        ("Show axis names", "show_axis_names"),
        ("Show indices", "show_indices"),
        ("Show sample name", "show_sample_name"),
    ):
        cb = QCheckBox(label)
        cb.setChecked(plot_dict.get(key, True))
        cb.setEnabled(enabled)

        def toggle(checked, k=key):
            plot_dict[k] = checked
            on_change()

        cb.toggled.connect(toggle)
        layout.addWidget(cb)

    layout.addWidget(QLabel("Font sizes"))
    for label, key, default in (
        ("Axis names", "axis_name_font_size", 11),
        ("Indices", "index_font_size", 10),
        ("Sample name", "sample_name_font_size", 9),
    ):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(QLabel(label))
        spin = _NoWheelSpinBox()
        spin.setRange(6, 32)
        spin.setSuffix(" pt")
        spin.setValue(int(plot_dict.get(key, default) or default))
        spin.setEnabled(enabled)
        h.addWidget(spin)
        h.addStretch()
        layout.addWidget(row)

        def on_size(value, k=key):
            plot_dict[k] = int(value)
            on_change()

        spin.valueChanged.connect(on_size)


def _figure_apply_display_options(ctx, plot_dict: dict, plot_widget) -> None:
    """Apply axis labels, visibility, tick notation, and sample-name title."""
    plot_item = plot_widget.getPlotItem()
    if plot_dict.get("type") == "image":
        # Images are drawn in pixel coordinates; the selected channel only
        # provides the colour (and the cursor value), never an axis.
        x_name = "x"
        y_name = "y"
    else:
        x_name = plot_dict.get("x")
        y_name = (
            plot_dict.get("y")
            if _is_scatter_plot(plot_dict.get("type")) else "Count"
        )

    name_size = max(6, int(plot_dict.get("axis_name_font_size", 11) or 11))
    index_size = max(6, int(plot_dict.get("index_font_size", 10) or 10))
    sample_size = max(
        6, int(plot_dict.get("sample_name_font_size", 9) or 9)
    )
    show_axes = plot_dict.get("show_axes", True)
    show_names = plot_dict.get("show_axis_names", True)
    show_indices = plot_dict.get("show_indices", True)
    title = (
        plot_dict.get("file_name")
        if plot_dict.get("show_sample_name", True)
        else None
    )
    # Applying the same labels/styles again still re-lays out the axes; with
    # an aspect-locked image view that can change the range, which schedules
    # another LOD render and eventually loops forever. Skip unchanged calls.
    signature = (
        x_name, y_name, name_size, index_size, sample_size,
        show_axes, show_names, show_indices, title,
    )
    if getattr(plot_widget, "_display_signature", None) == signature:
        return
    plot_widget._display_signature = signature

    label_style = {"font-size": f"{name_size}pt", "color": "#000000"}
    plot_item.setLabel("bottom", x_name or "", **label_style)
    plot_item.setLabel("left", y_name or "", **label_style)

    tick_font = QFont()
    tick_font.setPointSize(index_size)
    for ax in ("bottom", "left"):
        axis = plot_item.getAxis(ax)
        plot_item.showAxis(ax, show_axes)
        axis.showLabel(show_names)
        axis.setStyle(showValues=show_indices, tickFont=tick_font)

    if title:
        plot_item.setTitle(
            title, color="#808080", size=f"{sample_size}pt",
        )
    else:
        plot_item.setTitle(None)


def _figure_scatter_color_settings(ctx, layout, plot_dict, plot_widget, columns, enabled, on_change, on_rebuild, task_id=None) -> None:
    """Build + wire color mode/param, palette/solid, and point-size rows.

    Shared by the Figure and Scaling scatter settings panels.
    """
    if plot_dict.get("type") == "image":
        ctx._figure_image_layer_settings(
            layout, plot_dict, columns, enabled, on_change, on_rebuild,
            task_id=task_id,
        )
        return

    layout.addWidget(QLabel("Color mode"))
    mode_combo = _NoWheelComboBox()
    for text, val in FIGURE_COLOR_MODES:
        mode_combo.addItem(text, val)
    idx = mode_combo.findData(plot_dict.get("color_mode", "density"))
    mode_combo.setCurrentIndex(max(0, idx))
    mode_combo.setEnabled(enabled)
    layout.addWidget(mode_combo)

    cp_combo = None
    if plot_dict.get("color_mode") == "parameter":
        layout.addWidget(QLabel("Color parameter"))
        cp_combo = ctx._figure_param_combo(columns, plot_dict.get("color_param"), enabled)
        layout.addWidget(cp_combo)

    palette_combo = None
    if plot_dict.get("color_mode") == "solid":
        def make_solid_button():
            cur = plot_dict.get("solid_color", (0, 0, 200))
            btn = QPushButton()
            btn.setFixedSize(36, 20)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(enabled)
            btn.setStyleSheet(
                f"background-color: rgb({cur[0]},{cur[1]},{cur[2]}); "
                "border: 1px solid #555;"
            )

            def on_pick(_=False):
                current = plot_dict.get("solid_color", (0, 0, 200))
                chosen = QColorDialog.getColor(
                    QColor(current[0], current[1], current[2]), ctx.host, "Choose color"
                )
                if chosen.isValid():
                    plot_dict["solid_color"] = (chosen.red(), chosen.green(), chosen.blue())
                    btn.setStyleSheet(
                        f"background-color: rgb({chosen.red()},{chosen.green()},{chosen.blue()}); "
                        "border: 1px solid #555;"
                    )
                    on_change()

            btn.clicked.connect(on_pick)
            return btn

        layout.addWidget(QLabel("Solid color"))
        layout.addWidget(make_solid_button())
    else:
        layout.addWidget(QLabel("Palette"))
        palette_combo = _NoWheelComboBox()
        ctx._populate_palette_combo(
            palette_combo, plot_dict.get("palette") or _default_palette()
        )
        palette_combo.setEnabled(enabled)
        layout.addWidget(palette_combo)

    # Point size (scatter only; an Image renders as a raster, so this row
    # is hidden for image plots).
    if plot_dict.get("type") != "image":
        ctx._figure_point_size_settings(
            layout, plot_dict, plot_widget, enabled, on_change
        )

    def on_mode_changed(_=0):
        plot_dict["color_mode"] = mode_combo.currentData()
        on_rebuild()
    mode_combo.currentIndexChanged.connect(on_mode_changed)

    if cp_combo is not None:
        def on_cp(v):
            plot_dict["color_param"] = v
            plot_dict["last_color_param"] = v
            on_change()
        cp_combo.currentTextChanged.connect(on_cp)

    if palette_combo is not None:
        def on_palette(_=0):
            plot_dict["palette"] = palette_combo.currentText()
            on_change()
        palette_combo.currentIndexChanged.connect(on_palette)


def _figure_render_scatter(ctx, plot_dict: dict, plot_widget, column_transforms=None, mask=None, task_id=None, extra_stages=None) -> None:
    import time as _time
    import numpy as np
    import pandas as pd

    started = _time.perf_counter()

    plot_widget.clear()
    plot_widget._fig_cancelled = False
    plot_widget._fig_task_id = task_id
    plot_widget._fig_column_transforms = column_transforms
    plot_widget._image_probe = None
    _figure_hide_cursor_readout(plot_widget)
    _figure_release_cursor_row(plot_widget)
    token = ctx._figure_bump_render_token(plot_widget)
    # A scatter render removes any cached image item; invalidate the image
    # state so the next image render re-adds it.
    plot_widget._img_fitted = False
    plot_widget._img_path = None
    rec = ctx._figure_record(plot_dict.get("file"))
    x_name = plot_dict.get("x")
    y_name = plot_dict.get("y")
    if not rec:
        _figure_status_title(plot_widget, "No data file", color="#c0392b", size="10pt")
        return
    if not x_name or not y_name:
        _figure_status_title(plot_widget, "Parameter not in this file", color="#c0392b", size="10pt")
        return

    # Display transforms (upstream Scale + this module's live scale edits).
    # The data itself is unscaled; scale is applied only to the drawn values.
    display_cache = {}

    def display_configs(name):
        if name not in display_cache:
            display_cache[name] = _figure_axis_scale_configs(
                ctx, task_id, rec["parquet"], name, column_transforms
            )
        return display_cache[name]

    overlay = plot_dict.get("type") == "overlay"
    columns = [x_name, y_name]
    mode = "overlay" if overlay else plot_dict.get("color_mode", "density")
    color_param = (
        plot_dict.get("color_param")
        if mode == "parameter" and not overlay else None
    )
    if color_param and color_param not in columns:
        columns.append(color_param)

    limit = plot_dict.get("max_points") or _setting(
        "plot", "max_points", 50000
    )
    max_points = max(1000, int(limit or 50000))

    # Pooled path: only `max_points` rows are ever drawn, so a fixed
    # priority-ordered subset of the file is enough for the density map and
    # the sample. Falls back to a full read when a column is unavailable
    # or a filter leaves fewer rows than the plot would draw.
    pooled_mode = False
    indices = None
    arrays = None
    if extra_stages is None:
        ready = ctx._pool_ready(
            rec["parquet"], task_id, columns, max_points=max_points
        )
        if ready is None:
            pending = ctx._pool_pending_keys(
                rec["parquet"], task_id, columns, max_points=max_points
            )
            if pending and not any(k in ctx._pool_failed for k in pending):
                ctx._pool_register(
                    pending[0],
                    lambda p=plot_dict, w=plot_widget,
                           t=column_transforms, m=mask, tid=task_id,
                           es=extra_stages, tk=token:
                    ctx._figure_render_when_ready(
                        p, w, t, m, tid, es, tk
                    ),
                )
                _figure_status_title(plot_widget,
                    "Loading data…", color="#6b7785", size="10pt"
                )
                return
        else:
            indices, arrays = ready
            pooled_mode = (
                x_name in arrays and y_name in arrays
                and (color_param is None or color_param in arrays)
            )

    if pooled_mode:
        count = int(ctx._data_row_count(rec["parquet"]) or 0)
        x = arrays[x_name].astype("float64")
        y = arrays[y_name].astype("float64")
        x = _apply_scale_transforms(x, display_configs(x_name))
        y = _apply_scale_transforms(y, display_configs(y_name))
        keep = np.isfinite(x) & np.isfinite(y)
        if mask is not None:
            mask_values = np.asarray(mask, dtype=bool)
            # The pool's columns are in `indices` order (a priority
            # permutation, not natural order), so the full-data mask must
            # be reindexed to align with them.
            if indices.size and int(indices.max()) < mask_values.size:
                keep = keep & mask_values[indices]
            else:
                pooled_mode = False
        if pooled_mode:
            kept = int(np.count_nonzero(keep))
            if kept < max_points and indices.size < count:
                # Too few rows survived the filter: read everything so the
                # plot keeps its full point budget.
                pooled_mode = False

    if pooled_mode:
        x, y = x[keep], y[keep]
        if x.size == 0:
            return
    else:
        try:
            df = ctx._read_columns(
                rec["parquet"], columns, task_id, extra_stages=extra_stages
            )
        except Exception:
            return
        if x_name not in df.columns or y_name not in df.columns:
            _figure_status_title(plot_widget,
                "Parameter not in this file", color="#c0392b", size="10pt"
            )
            return

        x = pd.to_numeric(df[x_name], errors="coerce").to_numpy(dtype="float64")
        y = pd.to_numeric(df[y_name], errors="coerce").to_numpy(dtype="float64")
        x = _apply_scale_transforms(x, display_configs(x_name))
        y = _apply_scale_transforms(y, display_configs(y_name))
        keep = np.isfinite(x) & np.isfinite(y)
        if mask is not None:
            keep = keep & np.asarray(mask, dtype=bool)
        x, y = x[keep], y[keep]
        if x.size == 0:
            return
    # Cache the data range so the settings panel can pre-fill Auto values.
    plot_widget._data_xmin = float(x.min())
    plot_widget._data_xmax = float(x.max())
    plot_widget._data_ymin = float(y.min())
    plot_widget._data_ymax = float(y.max())

    # Points are always drawn in screen pixels: a data-unit size would turn
    # into an ellipse whenever the X and Y axes have different scales.
    if plot_dict.get("fixed_point_size"):
        size = _point_size_px(plot_dict.get("point_size"))
    else:
        size = POINT_SIZE_DEFAULT
    px_mode = True

    # Colours are computed from every visible row (so density stays
    # accurate), then the rows to draw are chosen once.
    solid = None
    cmap = None
    values = None
    density_mode = not (mode == "solid") and not (
        mode == "parameter" and color_param
    )
    hist_xe_ye = None
    if overlay:
        # Overlay scatter colours points by layer after sampling (below).
        density_mode = False
    elif mode == "solid":
        # Solid: every point shares one user-chosen color.
        solid = plot_dict.get("solid_color", (0, 0, 200))
    else:
        cmap = _figure_colormap(plot_dict.get("palette") or _default_palette())
        if density_mode:
            grid = max(20, int(_setting("plot", "density_grid", 200) or 200))
            if pooled_mode:
                # Density from a bounded sample of the pool; the lookup is
                # done after the drawn rows are chosen, so the per-plot
                # cost stays flat for 1M-row pools.
                step = max(1, x.size // 300_000)
                hist, xe, ye = np.histogram2d(x[::step], y[::step], bins=grid)
                hist_xe_ye = (hist, xe, ye)
            else:
                hist, xe, ye = np.histogram2d(x, y, bins=grid)
                xi = np.clip(np.digitize(x, xe) - 1, 0, hist.shape[0] - 1)
                yi = np.clip(np.digitize(y, ye) - 1, 0, hist.shape[1] - 1)
                values = np.log1p(hist[xi, yi].astype(float))
        else:
            if pooled_mode:
                raw = arrays[color_param].astype("float64")[keep]
            else:
                raw = pd.to_numeric(
                    df[color_param], errors="coerce"
                ).to_numpy(dtype="float64")[keep]
            values = _apply_scale_transforms(
                raw, display_configs(color_param)
            )

    # Sample the rows to draw. Pool rows are already in priority order, so
    # the first `max_points` kept rows are exactly the stable sample.
    picked = None
    if pooled_mode:
        if x.size > max_points:
            x = x[:max_points]
            y = y[:max_points]
            if values is not None:
                values = values[:max_points]
        if hist_xe_ye is not None:
            hist, xe, ye = hist_xe_ye
            xi = np.clip(np.digitize(x, xe) - 1, 0, hist.shape[0] - 1)
            yi = np.clip(np.digitize(y, ye) - 1, 0, hist.shape[1] - 1)
            values = np.log1p(hist[xi, yi].astype(float))
    else:
        picked = _stable_sample_selection(keep, max_points)
        if picked is not None:
            x = x[picked]
            y = y[picked]
            if values is not None:
                values = values[picked]

    point_colors = None
    if overlay:
        point_colors = ctx._figure_overlay_point_colors(
            plot_dict, task_id, rec.get("parquet"), int(x.size),
            keep=keep,
            indices=indices if pooled_mode else None,
            pooled=pooled_mode, picked=picked,
            max_points=max_points,
        )

    ctx._figure_draw_colored_scatter(
        plot_widget, x, y, values, cmap, size=size, px_mode=px_mode,
        solid_color=solid, point_colors=point_colors,
    )
    ctx._figure_apply_ranges(
        plot_dict, plot_widget, task_id, column_transforms
    )
    ctx._figure_apply_display_options(plot_dict, plot_widget)
    # Scaled axes keep the transformed points but show original tick values.
    parquet = rec.get("parquet")
    ctx._figure_set_original_ticks(
        plot_widget, "bottom",
        ctx._figure_axis_scale_configs(
            task_id, parquet, x_name, column_transforms
        ),
        x,
    )
    ctx._figure_set_original_ticks(
        plot_widget, "left",
        ctx._figure_axis_scale_configs(
            task_id, parquet, y_name, column_transforms
        ),
        y,
    )
    elapsed = _time.perf_counter() - started
    if elapsed >= 0.35:  # only renders slow enough to be felt
        runlog.info(
            "render.scatter", points=int(x.size), pooled=bool(pooled_mode),
            task=str(task_id or ""), seconds=round(elapsed, 3),
        )


def _figure_attach_legend(ctx, plot_widget):
    """Create and attach the side legend widget for an image plot."""
    widget = _FigureLegendWidget()
    widget.setVisible(False)
    plot_widget._legend_widget = widget
    return widget


def _figure_image_layer_settings(ctx, layout, plot_dict, columns, enabled, on_change, on_rebuild, task_id=None) -> None:
    """Build the image channel table (show / parameter / colour / min / max / name)."""
    layout.addWidget(QLabel("Channels"))
    table = QTableWidget()
    table.setColumnCount(6)
    table.setHorizontalHeaderLabels(
        ["Show", "Parameter", "Color", "Min", "Max", "Name"]
    )
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
    table.setColumnWidth(0, 44)
    table.setColumnWidth(2, 48)
    table.setColumnWidth(3, 66)
    table.setColumnWidth(4, 66)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(30)
    table.setMinimumHeight(120)
    # The table grows with its rows instead of scrolling internally, so every
    # added channel stays visible; the settings panel's scroll area handles
    # the resulting height.
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    layout.addWidget(table)

    def fit_table_height() -> None:
        table.resizeRowsToContents()
        header = table.horizontalHeader()
        header_h = max(header.height(), header.sizeHint().height())
        rows_h = sum(
            table.rowHeight(row) for row in range(table.rowCount())
        )
        # Reserve room for a possible horizontal scrollbar so the last row
        # stays visible even when the settings panel is dragged narrow.
        height = header_h + rows_h + 2 * table.frameWidth() + 22
        table.setFixedHeight(max(120, height))

    # Channel signal histogram: helps pick sensible Min/Max levels.
    import numpy as np
    import pyqtgraph as pg

    rec = ctx._figure_record(plot_dict.get("file"))
    parquet_path = rec.get("parquet") if rec else None
    hist_state = {"row": None, "values": None}

    hist_plot = pg.PlotWidget()
    hist_plot.setBackground(_plot_background())
    hist_plot.showGrid(x=True, y=True, alpha=0.2)
    hist_plot.setMinimumHeight(110)
    hist_plot.setMaximumHeight(170)
    hist_plot.setMouseEnabled(False, False)
    hist_plot.setMenuEnabled(False)
    hist_plot.hideAxis("left")
    layout.addWidget(hist_plot)

    auto_btn = QPushButton("Auto set")
    auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    auto_btn.setEnabled(enabled)
    auto_btn.setToolTip(
        "Set Min/Max to the channel's 0.5% and 99.5% percentiles"
    )
    layout.addWidget(auto_btn)

    reset_btn = QPushButton("Reset")
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_btn.setEnabled(enabled)
    reset_btn.setToolTip("Set Min/Max to the channel's full data range")
    layout.addWidget(reset_btn)

    def update_histogram(row) -> None:
        layers = _figure_image_layers(plot_dict)
        if row < 0 or row >= len(layers):
            hist_plot.clear()
            hist_state["row"] = None
            hist_state["values"] = None
            return
        channel = layers[row].get("parameter")
        hist_state["row"] = row
        hist_state["values"] = None
        configs = ctx._figure_axis_scale_configs(
            task_id, parquet_path, channel
        )
        hist_plot.getPlotItem().setAxisItems({
            "bottom": (
                _make_original_axis(configs) if configs
                else pg.AxisItem("bottom")
            ),
        })
        if not channel or parquet_path is None:
            hist_plot.clear()
            return
        values = ctx._figure_channel_values(task_id, parquet_path, channel)
        if values is None or values.size == 0:
            hist_plot.clear()
            return
        # Levels are display units; the stored data itself is unscaled.
        values = _apply_scale_transforms(values, configs)
        hist_state["values"] = values
        hist, edges = np.histogram(values, bins=200)
        hist_plot.clear()
        hist_plot.plot(
            edges, hist, stepMode="center",
            fillLevel=0, brush=(90, 140, 220, 120),
            pen=pg.mkPen((50, 90, 160), width=1),
        )
        hist_plot.setTitle(str(channel), color="#5a6673", size="8pt")

    def on_auto_set(_=False) -> None:
        layers = _figure_image_layers(plot_dict)
        row = hist_state["row"]
        if row is None or row >= len(layers):
            return
        values = hist_state["values"]
        if values is None or values.size == 0:
            return
        lo, hi = np.percentile(values, [0.5, 99.5])
        lo, hi = _round_level(lo), _round_level(hi)
        layers[row]["min"] = lo
        layers[row]["max"] = hi
        plot_dict["layers"] = layers
        configs = ctx._figure_axis_scale_configs(
            task_id, parquet_path, layers[row].get("parameter")
        )
        min_edit = table.cellWidget(row, 3)
        max_edit = table.cellWidget(row, 4)
        if isinstance(min_edit, QLineEdit):
            min_edit.setText(
                _format_level(_inverse_scale_value(lo, configs))
            )
        if isinstance(max_edit, QLineEdit):
            max_edit.setText(
                _format_level(_inverse_scale_value(hi, configs))
            )
        on_change()

    def on_reset(_=False) -> None:
        layers = _figure_image_layers(plot_dict)
        row = hist_state["row"]
        if row is None or row >= len(layers):
            return
        channel = layers[row].get("parameter")
        bounds = ctx._channel_full_range(task_id, parquet_path, channel)
        min_edit = table.cellWidget(row, 3)
        max_edit = table.cellWidget(row, 4)
        if bounds is None:
            layers[row]["min"] = None
            layers[row]["max"] = None
            plot_dict["layers"] = layers
            if isinstance(min_edit, QLineEdit):
                min_edit.setText("")
            if isinstance(max_edit, QLineEdit):
                max_edit.setText("")
            on_change()
            ctx.statusBar().showMessage(
                "This channel has no finite values", 3000
            )
            return
        configs = ctx._figure_axis_scale_configs(
            task_id, parquet_path, channel
        )
        lo = _forward_scale_value(_round_level(bounds[0]), configs)
        hi = _forward_scale_value(_round_level(bounds[1]), configs)
        layers[row]["min"] = lo
        layers[row]["max"] = hi
        plot_dict["layers"] = layers
        configs = ctx._figure_axis_scale_configs(
            task_id, parquet_path, channel
        )
        if isinstance(min_edit, QLineEdit):
            min_edit.setText(
                _format_level(_inverse_scale_value(lo, configs))
            )
        if isinstance(max_edit, QLineEdit):
            max_edit.setText(
                _format_level(_inverse_scale_value(hi, configs))
            )
        on_change()

    btn_row = QHBoxLayout()
    add_btn = QPushButton("Add")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_btn.setEnabled(enabled)
    remove_btn = QPushButton("Remove")
    remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    remove_btn.setEnabled(enabled)
    btn_row.addWidget(add_btn)
    btn_row.addWidget(remove_btn)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    show_legend = QCheckBox("Show legend")
    show_legend.setChecked(plot_dict.get("show_legend", True))
    show_legend.setEnabled(enabled)
    layout.addWidget(show_legend)

    def on_param_changed(row):
        layers = _figure_image_layers(plot_dict)
        if row >= len(layers):
            return
        combo = table.cellWidget(row, 1)
        new_param = combo.currentText() or None
        old_param = layers[row].get("parameter")
        if not new_param or new_param == old_param:
            return
        layers[row]["parameter"] = new_param
        if layers[row].get("name") in (None, "", str(old_param)):
            layers[row]["name"] = new_param
        plot_dict["layers"] = layers
        on_rebuild()

    def on_pick_color(row):
        layers = _figure_image_layers(plot_dict)
        if row >= len(layers):
            return
        cur = tuple(layers[row].get("color", (255, 255, 255)))
        chosen = QColorDialog.getColor(
            QColor(cur[0], cur[1], cur[2]), ctx.host, "Choose color"
        )
        if chosen.isValid():
            color = (chosen.red(), chosen.green(), chosen.blue())
            layers[row]["color"] = color
            plot_dict["layers"] = layers
            # The legend is redrawn by on_change(), but the settings table is
            # not rebuilt (that would steal focus); update its swatch here.
            button = table.cellWidget(row, 2)
            if isinstance(button, QPushButton):
                button.setStyleSheet(
                    f"background-color: rgb({color[0]},{color[1]},"
                    f"{color[2]}); border: 1px solid #555;"
                )
            on_change()

    def on_item_changed(item):
        row = table.row(item)
        col = table.column(item)
        layers = _figure_image_layers(plot_dict)
        if row >= len(layers):
            return
        if col == 0:
            layers[row]["visible"] = (
                item.checkState() == Qt.CheckState.Checked
            )
        elif col == 5:
            layers[row]["name"] = item.text()
        else:
            return
        plot_dict["layers"] = layers
        on_change()

    def on_add(_=False):
        existing = {l.get("parameter") for l in _figure_image_layers(plot_dict)}
        available = [c for c in columns if c not in existing]
        chosen = ctx._pick_items_dialog(
            "Add channels", available, [], "Filter parameters..."
        )
        if chosen is None:
            return
        layers = _figure_image_layers(plot_dict)
        used = {tuple(l.get("color", (255, 255, 255))) for l in layers}
        for c in chosen:
            color = next(
                (col for col in IMAGE_LAYER_COLORS if col not in used),
                IMAGE_LAYER_COLORS[len(layers) % len(IMAGE_LAYER_COLORS)],
            )
            used.add(color)
            layers.append({
                "parameter": c, "color": color, "name": str(c), "visible": True,
            })
        plot_dict["layers"] = layers
        on_rebuild()

    def on_remove(_=False):
        rows = sorted(
            {idx.row() for idx in table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows:
            return
        layers = _figure_image_layers(plot_dict)
        for row in rows:
            if 0 <= row < len(layers):
                layers.pop(row)
        plot_dict["layers"] = layers
        on_rebuild()

    def on_show_legend(checked):
        plot_dict["show_legend"] = bool(checked)
        on_change()

    def populate():
        layers = _figure_image_layers(plot_dict)
        table.blockSignals(True)
        table.setRowCount(len(layers))
        for i, layer in enumerate(layers):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            chk_item.setCheckState(
                Qt.CheckState.Checked if layer.get("visible", True)
                else Qt.CheckState.Unchecked
            )
            table.setItem(i, 0, chk_item)

            combo = _NoWheelComboBox()
            for c in columns:
                combo.addItem(str(c))
            param = layer.get("parameter")
            idx = combo.findText(str(param)) if param is not None else -1
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setEnabled(enabled)
            combo.currentIndexChanged.connect(
                lambda _=0, row=i: on_param_changed(row)
            )
            table.setCellWidget(i, 1, combo)

            color = tuple(layer.get("color", (255, 255, 255)))
            cbtn = QPushButton()
            cbtn.setFixedSize(40, 20)
            cbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            cbtn.setEnabled(enabled)
            cbtn.setStyleSheet(
                f"background-color: rgb({color[0]},{color[1]},{color[2]}); "
                "border: 1px solid #555;"
            )
            cbtn.clicked.connect(lambda _=False, row=i: on_pick_color(row))
            table.setCellWidget(i, 2, cbtn)

            # Min / Max: empty means "auto" (the channel's natural
            # range). Stored in plot units, shown in original values.
            row_configs = ctx._figure_axis_scale_configs(
                task_id, parquet_path, layer.get("parameter")
            )
            for col, key in ((3, "min"), (4, "max")):
                edit = QLineEdit()
                value = layer.get(key)
                if value is not None and row_configs:
                    value = _inverse_scale_value(value, row_configs)
                edit.setText(
                    "" if value is None else _format_level(value)
                )
                edit.setPlaceholderText("Auto")
                edit.setAlignment(Qt.AlignmentFlag.AlignRight)
                edit.setEnabled(enabled)
                edit.setStyleSheet(
                    "QLineEdit { background: #ffffff; border: 1px solid #d0d7e2; "
                    "border-radius: 3px; padding: 1px 4px; }"
                )

                def on_minmax(_=False, row=i, key=key, edit=edit,
                              cfgs=row_configs):
                    layers = _figure_image_layers(plot_dict)
                    if row >= len(layers):
                        return
                    text = edit.text().strip()
                    if text == "":
                        layers[row].pop(key, None)
                    else:
                        rounded = _round_level(text)
                        if rounded is None:
                            return
                        layers[row][key] = _forward_scale_value(
                            rounded, cfgs
                        )
                    plot_dict["layers"] = layers
                    on_change()

                edit.editingFinished.connect(on_minmax)
                table.setCellWidget(i, col, edit)

            name_item = QTableWidgetItem(str(layer.get("name") or param or ""))
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )
            table.setItem(i, 5, name_item)
        table.blockSignals(False)
        fit_table_height()

    table.itemChanged.connect(on_item_changed)
    add_btn.clicked.connect(on_add)
    remove_btn.clicked.connect(on_remove)
    show_legend.toggled.connect(on_show_legend)
    table.currentCellChanged.connect(
        lambda r, _c, _pr, _pc: update_histogram(r)
    )
    auto_btn.clicked.connect(on_auto_set)
    reset_btn.clicked.connect(on_reset)

    populate()
    # Show the first channel's histogram right away.
    if table.rowCount():
        update_histogram(0)


def _figure_make_plot_widget(ctx, plot_dict: dict):
    """Create a scatter/histogram PlotWidget with sci axes and A/F buttons."""
    import pyqtgraph as pg
    plot_widget = pg.PlotWidget(plotItem=_make_fit_plot_item())
    plot_widget.setBackground(_plot_background())
    # The card's own right-click menu replaces pyqtgraph's.
    plot_widget.getPlotItem().setMenuEnabled(False)
    plot_widget.showGrid(x=True, y=True, alpha=0.2)
    plot_widget.getPlotItem().setAxisItems({
        "bottom": _make_sci_axis("bottom"),
        "left": _make_sci_axis("left"),
    })
    # Read-only by default; wheel/drag act on the canvas until focused.
    plot_widget.getPlotItem().getViewBox().setMouseEnabled(False, False)
    plot_item = plot_widget.getPlotItem()
    # Make the "A" button revert to the settings' min/max (not fit-all).
    try:
        plot_item.autoBtn.clicked.disconnect()
    except (RuntimeError, TypeError):
        pass
    plot_item.autoBtn.clicked.connect(
        lambda _=None: ctx._figure_apply_ranges(
            plot_dict, plot_widget,
            getattr(plot_widget, "_fig_task_id", None),
            getattr(plot_widget, "_fig_column_transforms", None),
        )
    )
    # "F" button fits to the full data extent (without changing settings).
    plot_item._on_fit = lambda: ctx._figure_fit_all(plot_dict, plot_widget)
    return plot_widget


def _figure_pad_range(lo, hi, margin=FIGURE_AXIS_MARGIN):
    """Widen ``(lo, hi)`` so the pair sits at ``margin``/``1 - margin`` of the
    visible axis.

    With the default 5 % margin, ``lo`` lands at 5 % and ``hi`` at 95 %, which
    leaves an even border around the drawn data. Returns ``None`` when the
    range cannot be padded (missing, non-finite or empty).
    """
    import math

    if lo is None or hi is None:
        return None
    try:
        lo = float(lo)
        hi = float(hi)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    pad = (hi - lo) * margin / (1.0 - 2.0 * margin)
    return lo - pad, hi + pad


def _figure_fit_all(ctx, plot_dict: dict, plot_widget) -> None:
    """Fit the view to the full data extent (without changing settings)."""
    vb = plot_widget.getPlotItem().getViewBox()
    if plot_dict.get("type") == "image":
        extent = getattr(plot_widget, "_img_extent", None)
        if extent:
            vb.disableAutoRange()
            vb.setRange(
                xRange=(extent[0], extent[1]),
                yRange=(extent[2], extent[3]),
                padding=0,
            )
        return
    xmin = getattr(plot_widget, "_data_xmin", None)
    xmax = getattr(plot_widget, "_data_xmax", None)
    ymin = getattr(plot_widget, "_data_ymin", None)
    ymax = getattr(plot_widget, "_data_ymax", None)
    xr = _figure_pad_range(xmin, xmax)
    yr = _figure_pad_range(ymin, ymax)
    if xr is not None:
        vb.setXRange(*xr, padding=0)
    else:
        vb.enableAutoRange("x", True)
    if yr is not None:
        vb.setYRange(*yr, padding=0)
    else:
        vb.enableAutoRange("y", True)


def _figure_axis_mode(plot_dict: dict, axis: str, ptype=None) -> str:
    """The saved range mode for one axis, with migration for old plots.

    Old plots only stored Min/Max (``None`` meant Auto). They migrate to
    Manual when values exist and to From-scale otherwise, preserving the
    old effective behaviour (Scale first, data second).
    """
    ptype = ptype or plot_dict.get("type")
    mode = plot_dict.get(f"{axis}_range_mode")
    if ptype == "histogram" and axis == "y":
        # The Y axis is the count axis: there is no channel to take from a
        # Scale, so only Manual and Auto apply.
        if mode == "manual":
            return "manual"
        if mode is None and (
            plot_dict.get("ymin") is not None
            or plot_dict.get("ymax") is not None
        ):
            return "manual"
        return "auto"
    # Older builds wrote Auto and set this flag when From scale had no
    # bounds.  Treat it as the From scale choice the user actually made.
    if mode == "auto" and plot_dict.get(f"{axis}_range_auto_fallback"):
        return "scale"
    if mode in ("manual", "scale", "auto"):
        return mode
    if (
        plot_dict.get(f"{axis}min") is not None
        or plot_dict.get(f"{axis}max") is not None
    ):
        return "manual"
    return "scale"


def _figure_display_range_value(value, configs):
    """A stored plot-unit range value in display (original) units.

    If the inverse transform overflows, return the stored value instead of
    ``nan``; old multi-selection edits could store raw values on a
    transformed axis, and showing a number lets the user correct it.
    """
    import math

    if value is None or not configs:
        return value
    converted = _inverse_scale_value(value, configs)
    try:
        if converted is not None and math.isfinite(float(converted)):
            return converted
    except (TypeError, ValueError):
        pass
    return value


def _figure_active_range_record(plot_dict, axis) -> dict:
    """The current axis range as a per-channel record."""
    min_key = f"{axis}min"
    max_key = f"{axis}max"
    mode = _figure_axis_mode(plot_dict, axis)
    return {
        "mode": mode,
        "min": plot_dict.get(min_key) if mode == "manual" else None,
        "max": plot_dict.get(max_key) if mode == "manual" else None,
        "fallback": False,
    }


def _figure_sync_range_record(plot_dict, axis):
    """Save the previous channel's range and load the current one's.

    A channel seen for the first time starts from the default ``scale``
    mode; its actual range then falls back to Auto when no Scale sets both
    sides. Old plots with a saved manual range migrate to a Manual record.
    """
    channel = plot_dict.get(axis)
    if not channel:
        return None
    records_key = f"{axis}_ranges"
    records = plot_dict.get(records_key)
    if not isinstance(records, dict):
        records = {}
        plot_dict[records_key] = records
    marker_key = f"{axis}_range_channel"
    previous = plot_dict.get(marker_key)
    if previous == channel:
        # Keep the record in step with edits that bypassed the row.
        records[channel] = _figure_active_range_record(plot_dict, axis)
    elif previous:
        records[previous] = _figure_active_range_record(plot_dict, axis)
    record = records.get(channel)
    if record is None:
        if previous:
            # The active values belong to the previous channel.
            record = {"mode": "scale", "min": None, "max": None,
                      "fallback": False}
        else:
            # Migration from an older project without per-channel records.
            active = _figure_active_range_record(plot_dict, axis)
            if active["mode"] == "manual":
                record = active
            elif active["mode"] == "auto" and not active["fallback"]:
                record = active  # an explicit Auto choice
            else:
                # Includes the old auto-fallback state: a first visit must
                # start from From scale instead of inheriting it.
                record = {"mode": "scale", "min": None, "max": None,
                          "fallback": False}
        records[channel] = record
    plot_dict[f"{axis}_range_mode"] = record.get("mode", "scale")
    plot_dict[f"{axis}min"] = record.get("min")
    plot_dict[f"{axis}max"] = record.get("max")
    if record.get("fallback"):
        plot_dict[f"{axis}_range_auto_fallback"] = True
    else:
        plot_dict.pop(f"{axis}_range_auto_fallback", None)
    plot_dict[marker_key] = channel
    return record


def _figure_save_range_record(plot_dict, axis, mode, fallback=False) -> None:
    """Store one channel's range after the row resolved its mode."""
    channel = plot_dict.get(axis)
    if not channel:
        return
    records = plot_dict.get(f"{axis}_ranges")
    if not isinstance(records, dict):
        records = {}
        plot_dict[f"{axis}_ranges"] = records
    records[channel] = {
        "mode": mode,
        "min": plot_dict.get(f"{axis}min") if mode == "manual" else None,
        "max": plot_dict.get(f"{axis}max") if mode == "manual" else None,
        "fallback": bool(fallback) and mode == "auto",
    }
    plot_dict[f"{axis}_range_channel"] = channel


def _figure_scale_range_config(ctx, task_id, parquet, column):
    """The nearest upstream Scale config that sets BOTH Min and Max.

    A Scale module that sets only one side does not qualify; the search
    continues towards the root. Returns the raw config (original units) or
    None.
    """
    if not task_id or not parquet or not column:
        return None
    try:
        stages = ctx._map_stages(
            task_id, include_scale=True, parquet_path=parquet
        ) or []
    except Exception:  # noqa: BLE001 - a missing pipeline must not break a plot
        return None
    for kind, stage in reversed(stages):
        if kind != "scale":
            continue
        files = stage.get("files")
        if files is not None and parquet not in files:
            continue
        candidate = (stage.get("columns") or {}).get(column)
        if not candidate:
            continue
        if candidate.get("min") is None or candidate.get("max") is None:
            continue
        return candidate
    return None


def _figure_scale_axis_bounds(ctx, task_id, parquet, column,
                              column_transforms=None):
    """Display bounds from the nearest fully configured upstream Scale.

    Returns ``(min, max)`` in plot units, or None when no Scale module sets
    both sides for this column. Callers then fall back to the data extent.
    """
    config = _figure_scale_range_config(ctx, task_id, parquet, column)
    if config is None:
        return None
    configs = _figure_axis_scale_configs(
        ctx, task_id, parquet, column, column_transforms
    )
    lo = _forward_scale_value(config.get("min"), configs)
    hi = _forward_scale_value(config.get("max"), configs)
    if lo is None or hi is None:
        return None
    return (lo, hi)


def _figure_axis_range(ctx, task_id, parquet, plot_dict, axis, plot_widget,
                       column_transforms=None):
    """Resolve one axis' (min, max) in plot units for the active mode."""
    ptype = plot_dict.get("type")
    min_key = f"{axis}min"
    max_key = f"{axis}max"
    data_min = getattr(plot_widget, f"_data_{axis}min", None)
    data_max = getattr(plot_widget, f"_data_{axis}max", None)
    mode = _figure_axis_mode(plot_dict, axis, ptype)
    if mode == "manual":
        lo = plot_dict.get(min_key)
        hi = plot_dict.get(max_key)
        if lo is None:
            lo = data_min
        if hi is None:
            hi = data_max
        return lo, hi
    if mode == "scale":
        bounds = _figure_scale_axis_bounds(
            ctx, task_id, parquet, plot_dict.get(axis),
            column_transforms,
        )
        if bounds is not None:
            return bounds
        # No fully configured Scale: behave as Auto.
        return data_min, data_max
    return data_min, data_max


def _figure_apply_ranges(ctx, plot_dict: dict, plot_widget,
                         task_id=None, column_transforms=None) -> None:
    """Apply manual/from-scale/auto X and Y ranges from the plot settings."""
    if plot_dict.get("type") == "image":
        # Image plots keep autoRange off: fit to the settings range, else
        # to the full image extent (so no auto-fit loop on a cropped image).
        extent = getattr(plot_widget, "_img_extent", None)
        xmin, xmax = plot_dict.get("xmin"), plot_dict.get("xmax")
        ymin, ymax = plot_dict.get("ymin"), plot_dict.get("ymax")
        if (xmin is None or xmax is None) and extent:
            xmin, xmax = extent[0], extent[1]
        if (ymin is None or ymax is None) and extent:
            ymin, ymax = extent[2], extent[3]
        vb = plot_widget.getPlotItem().getViewBox()
        vb.disableAutoRange()
        if (
            xmin is not None and xmax is not None and xmax > xmin
            and ymin is not None and ymax is not None and ymax > ymin
        ):
            vb.setRange(
                xRange=(float(xmin), float(xmax)),
                yRange=(float(ymin), float(ymax)),
                padding=0,
            )
        return
    parquet = plot_dict.get("file")
    xmin, xmax = _figure_axis_range(
        ctx, task_id, parquet, plot_dict, "x", plot_widget,
        column_transforms,
    )
    ymin, ymax = _figure_axis_range(
        ctx, task_id, parquet, plot_dict, "y", plot_widget,
        column_transforms,
    )
    # Keep a 5 % border so the configured Min/Max sit inside the frame
    # instead of on it. The saved and displayed values stay unpadded.
    xr = _figure_pad_range(xmin, xmax)
    yr = _figure_pad_range(ymin, ymax)
    if xr is not None:
        plot_widget.setXRange(*xr, padding=0)
    else:
        plot_widget.enableAutoRange("x", True)
    if yr is not None:
        plot_widget.setYRange(*yr, padding=0)
    else:
        plot_widget.enableAutoRange("y", True)


def _figure_render_histogram(ctx, plot_dict: dict, plot_widget, column_transforms=None, mask=None, task_id=None, extra_stages=None) -> None:
    import numpy as np
    import pandas as pd
    import pyqtgraph as pg

    plot_widget.clear()
    plot_widget._fig_cancelled = False
    plot_widget._fig_task_id = task_id
    plot_widget._fig_column_transforms = column_transforms
    plot_widget._image_probe = None
    _figure_hide_cursor_readout(plot_widget)
    _figure_release_cursor_row(plot_widget)
    token = ctx._figure_bump_render_token(plot_widget)
    # Same as scatter: invalidate any cached image item.
    plot_widget._img_fitted = False
    plot_widget._img_path = None
    rec = ctx._figure_record(plot_dict.get("file"))
    x_name = plot_dict.get("x")
    if not rec:
        _figure_status_title(plot_widget, "No data file", color="#c0392b", size="10pt")
        return
    if not x_name:
        _figure_status_title(plot_widget, "Parameter not in this file", color="#c0392b", size="10pt")
        return
    display_cache = {}

    def display_configs(name):
        if name not in display_cache:
            display_cache[name] = _figure_axis_scale_configs(
                ctx, task_id, rec["parquet"], name, column_transforms
            )
        return display_cache[name]

    # Same pooled data as the scatter views: one column is plenty for a
    # histogram, and the pool is already in memory for this file.  A
    # histogram has no per-plot point limit, so use the global scatter limit.
    limit = plot_dict.get("max_points") or _setting(
        "plot", "max_points", 50000
    )
    max_points = max(1000, int(limit or 50000))
    pooled_mode = False
    indices = None
    arrays = None
    if extra_stages is None:
        ready = ctx._pool_ready(
            rec["parquet"], task_id, [x_name], max_points=max_points
        )
        if ready is None:
            pending = ctx._pool_pending_keys(
                rec["parquet"], task_id, [x_name], max_points=max_points
            )
            if pending and not any(k in ctx._pool_failed for k in pending):
                ctx._pool_register(
                    pending[0],
                    lambda p=plot_dict, w=plot_widget,
                           t=column_transforms, m=mask, tid=task_id,
                           es=extra_stages, tk=token:
                    ctx._figure_render_histogram_when_ready(
                        p, w, t, m, tid, es, tk
                    ),
                )
                _figure_status_title(plot_widget,
                    "Loading data…", color="#6b7785", size="10pt"
                )
                return
        else:
            indices, arrays = ready
            pooled_mode = x_name in arrays

    if pooled_mode:
        x = arrays[x_name].astype("float64")
        x = _apply_scale_transforms(x, display_configs(x_name))
        keep = np.isfinite(x)
        if mask is not None:
            mask_values = np.asarray(mask, dtype=bool)
            # Same as scatter: pooled columns are in `indices` order.
            if indices.size and int(indices.max()) < mask_values.size:
                keep = keep & mask_values[indices]
            else:
                pooled_mode = False
        if pooled_mode:
            x = x[keep]
    else:
        try:
            df = ctx._read_columns(
                rec["parquet"], [x_name], task_id, extra_stages=extra_stages
            )
        except Exception:
            return
        if x_name not in df.columns:
            _figure_status_title(plot_widget,
                "Parameter not in this file", color="#c0392b", size="10pt"
            )
            return
        x = pd.to_numeric(df[x_name], errors="coerce").to_numpy(dtype="float64")
        x = _apply_scale_transforms(x, display_configs(x_name))
        keep = np.isfinite(x)
        if mask is not None:
            keep = keep & np.asarray(mask, dtype=bool)
        x = x[keep]
    if x.size == 0:
        return
    plot_widget._data_xmin = float(x.min())
    plot_widget._data_xmax = float(x.max())

    bins = max(2, int(plot_dict.get("bins", 20)))
    mode = plot_dict.get("hist_mode", "histogram")
    counts, edges = np.histogram(x, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0

    hist_color = plot_dict.get("hist_color", (0, 0, 200))
    curve_color = plot_dict.get("curve_color", (200, 40, 40))

    # Curve fit: Gaussian-smoothed version of the current histogram, so it
    # updates in step with the bin count. Upsampled for a smooth line.
    curve_x = None
    curve_y = None
    if mode in ("curve", "overlap"):
        from scipy.ndimage import gaussian_filter1d
        sigma = max(1.0, bins / 40.0)
        smooth = gaussian_filter1d(counts.astype(float), sigma=sigma, mode="nearest")
        n_up = max(bins * 8, 100)
        up_x = np.linspace(centers[0], centers[-1], n_up)
        curve_y = np.interp(up_x, centers, smooth)
        curve_x = up_x

    ymax = float(counts.max())
    if curve_y is not None and curve_y.size:
        ymax = max(ymax, float(np.nanmax(curve_y)))
    plot_widget._data_ymin = 0.0
    plot_widget._data_ymax = ymax

    cache = getattr(plot_widget, "_gate_cache_data", False)
    if mode in ("histogram", "overlap"):
        item = pg.PlotDataItem(
            edges, counts, stepMode="center", fillLevel=0,
            brush=(*hist_color, 60), pen=hist_color,
        )
        if cache:
            item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        plot_widget.addItem(item)
    if mode in ("curve", "overlap") and curve_y is not None:
        item = pg.PlotDataItem(
            curve_x, curve_y, pen=pg.mkPen(*curve_color, width=2)
        )
        if cache:
            item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        plot_widget.addItem(item)

    ctx._figure_apply_ranges(
        plot_dict, plot_widget, task_id, column_transforms
    )
    ctx._figure_apply_display_options(plot_dict, plot_widget)
    ctx._figure_set_original_ticks(
        plot_widget, "bottom",
        ctx._figure_axis_scale_configs(
            task_id, rec.get("parquet"), x_name, column_transforms
        ),
        x,
    )
    ctx._figure_set_original_ticks(plot_widget, "left", None)


def _figure_param_combo(ctx, columns, current, enabled: bool) -> QComboBox:
    """Build a combo listing parameter names, selecting `current` if present.

    A blank first item is shown when `current` is None (no parameter).
    """
    combo = _NoWheelComboBox()
    combo.addItem("", None)
    for col in columns:
        combo.addItem(str(col))
    if current is not None:
        idx = combo.findText(str(current))
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.setEnabled(enabled)
    return combo


