"""Figure renderers: scatter, image, histogram, ranges and their settings."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
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
    _default_palette, _plot_background, _setting,
)
from smage.runlog import runlog
from smage.widgets.colormap import _figure_colormap
from smage.widgets.controls import (
    _LogStepSpinBox, _NoWheelComboBox, _NoWheelSpinBox,
)
from smage.widgets.plot_axes import (
    _figure_image_layers, _is_scatter_plot, _make_fit_plot_item,
    _make_original_axis, _make_sci_axis,
)


FIGURE_COLOR_MODES = [
    ("Density", "density"),
    ("Parameter", "parameter"),
    ("Solid", "solid"),
]
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


def _figure_render(ctx, plot_dict: dict, plot_widget, column_transforms=None, task_id=None, extra_stages=None) -> None:
    """(Re)draw a plot widget from its plot_dict."""
    if plot_dict.get("type") == "image":
        ctx._figure_render_image(
            plot_dict, plot_widget, column_transforms, task_id, extra_stages
        )
        return
    mask, error = ctx._figure_filter_mask(plot_dict, task_id)
    if error:
        plot_widget.clear()
        plot_widget.setTitle(error, color="#c0392b", size="10pt")
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


def _figure_range_row(ctx, plot_dict: dict, axis: str, plot_widget, enabled: bool, on_commit=None, auto_checked: bool=True, configs=None, task_id=None, column_transforms=None) -> QWidget:
    """Build an Auto/Min/Max row for one axis range.

    The spin boxes show the axis in ORIGINAL (pre-Scale) values even
    though the plot itself draws transformed values: entries are mapped
    through the column's scale configs on the way in and out.
    """
    min_key = f"{axis}min"
    max_key = f"{axis}max"
    data_min = getattr(plot_widget, f"_data_{axis}min", None)
    data_max = getattr(plot_widget, f"_data_{axis}max", None)
    if configs:
        data_min = (
            None if data_min is None
            else _inverse_scale_value(data_min, configs)
        )
        data_max = (
            None if data_max is None
            else _inverse_scale_value(data_max, configs)
        )

    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    auto = QCheckBox("Auto")
    auto.setChecked(
        auto_checked
        and plot_dict.get(min_key) is None
        and plot_dict.get(max_key) is None
    )
    auto.setEnabled(enabled)

    min_val = plot_dict.get(min_key)
    max_val = plot_dict.get(max_key)
    if min_val is not None and configs:
        min_val = _inverse_scale_value(min_val, configs)
    if max_val is not None and configs:
        max_val = _inverse_scale_value(max_val, configs)
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
        # A range change only affects the view, so apply the range directly
        # instead of re-reading and re-rendering the plot's data.
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


def _figure_render_image(ctx, plot_dict: dict, plot_widget, column_transforms=None, task_id=None, extra_stages=None, row_mask=None) -> None:
    """Render an Image plot as a raster (LOD pyramid), ImageJ-style.

    `row_mask` is an optional boolean/uint8 array aligned to the image's
    pixel rows; pixels it marks False are hidden (used by Gating's subset
    view to show only the points inside the active gate).
    """
    import numpy as np
    ctx._figure_bump_render_token(plot_widget)
    ctx._figure_update_image_legend(plot_widget, [])
    rec = ctx._figure_record(plot_dict.get("file"))
    if not rec:
        plot_widget.clear()
        plot_widget._img_fitted = False
        plot_widget.setTitle("No data file", color="#c0392b", size="10pt")
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
        plot_widget.setTitle(
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
                plot_widget.setTitle(err, color="#c0392b", size="10pt")
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
                )
            plot_widget.setTitle("Applying filter…", color="#b58900", size="10pt")
            return
        mask_ent = cache[key]
        if isinstance(mask_ent, str):
            plot_widget.setTitle(mask_ent, color="#c0392b", size="10pt")
            return
        if mask_ent is None:
            plot_widget.setTitle(
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
            plot_widget.setTitle(
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
    ref_shape = None
    ref_rect = None
    has_mask = False
    mask_accum = None
    unavailable = []
    # Mask columns exposed by an upstream Segmentation module are not part
    # of the image index; they are rasterised straight from the mask file.
    seg_cols = ctx._seg_applied_columns(task_id, parquet_path) if task_id else {}
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
            # the Min/Max controls share one unit system.
            post = _stages_after(stages, "unmix", rec.get("task_id"))
            if post:
                evaluated = _evaluate_stages(
                    post, {channel: chan}, [channel],
                    parquet_path=parquet_path,
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
                    kind == "unmix" and channel in stage["names"]
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
            chan, rect, frac = ctx._image_raster(
                parquet_path, manifest, (x0, x1, y0, y1), spacing, channel,
                mask_ent, raster_stages,
            )
            if chan is None:
                continue
            cname = channel if raster_stages is None else None
            ranges = manifest.get("color_ranges", {}) or {}
            lo, hi = ranges.get(cname, (None, None))
            if lo is None or hi is None or not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo = float(np.nanmin(chan))
                hi = float(np.nanmax(chan))
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
        if ref_shape is None:
            ref_shape = norm.shape
            ref_rect = rect

    if not rasters or ref_shape is None:
        img.setVisible(False)
        ctx._figure_update_image_legend(plot_widget, [])
        if missing_pyramid:
            plot_widget.setTitle(
                "Image pyramid not found for: "
                + ", ".join(missing_pyramid)
                + " — please check",
                color="#c0392b", size="10pt",
            )
        elif unavailable:
            plot_widget.setTitle(
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
    img.setRect(ref_rect)
    img.setVisible(True)
    ctx._figure_apply_display_options(plot_dict, plot_widget)
    for axis in ("bottom", "left"):
        ctx._figure_set_original_ticks(plot_widget, axis, None)
    if missing_pyramid:
        # Some channels could not be drawn: say so in the plot itself.
        plot_widget.setTitle(
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
    x_name = plot_dict.get("x")
    y_name = plot_dict.get("y") if _is_scatter_plot(plot_dict.get("type")) else "Count"

    name_size = max(6, int(plot_dict.get("axis_name_font_size", 11) or 11))
    index_size = max(6, int(plot_dict.get("index_font_size", 10) or 10))
    sample_size = max(
        6, int(plot_dict.get("sample_name_font_size", 9) or 9)
    )
    label_style = {"font-size": f"{name_size}pt", "color": "#000000"}
    plot_item.setLabel("bottom", x_name or "", **label_style)
    plot_item.setLabel("left", y_name or "", **label_style)

    show_axes = plot_dict.get("show_axes", True)
    show_names = plot_dict.get("show_axis_names", True)
    show_indices = plot_dict.get("show_indices", True)
    tick_font = QFont()
    tick_font.setPointSize(index_size)
    for ax in ("bottom", "left"):
        axis = plot_item.getAxis(ax)
        plot_item.showAxis(ax, show_axes)
        axis.showLabel(show_names)
        axis.setStyle(showValues=show_indices, tickFont=tick_font)

    if plot_dict.get("show_sample_name", True) and plot_dict.get("file_name"):
        plot_item.setTitle(
            plot_dict["file_name"], color="#808080",
            size=f"{sample_size}pt",
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
    token = ctx._figure_bump_render_token(plot_widget)
    # A scatter render removes any cached image item; invalidate the image
    # state so the next image render re-adds it.
    plot_widget._img_fitted = False
    plot_widget._img_path = None
    rec = ctx._figure_record(plot_dict.get("file"))
    x_name = plot_dict.get("x")
    y_name = plot_dict.get("y")
    if not rec:
        plot_widget.setTitle("No data file", color="#c0392b", size="10pt")
        return
    if not x_name or not y_name:
        plot_widget.setTitle("Parameter not in this file", color="#c0392b", size="10pt")
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
        ready = ctx._pool_ready(rec["parquet"], task_id, columns)
        if ready is None:
            pending = ctx._pool_pending_keys(
                rec["parquet"], task_id, columns
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
                plot_widget.setTitle(
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
            plot_widget.setTitle(
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

    if plot_dict.get("fixed_point_size"):
        # Fixed: size is in data units, so points scale with the zoom.
        size = float(plot_dict.get("point_size", 4))
        px_mode = False
    else:
        # Not fixed: constant on-screen pixel size.
        size = 4.0
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
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    layout.addWidget(table, 1)

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
            layers[row]["color"] = (chosen.red(), chosen.green(), chosen.blue())
            plot_dict["layers"] = layers
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
        table.resizeRowsToContents()

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
    if xmin is not None and xmax is not None and xmax > xmin:
        pad = 0.02 * (xmax - xmin)
        vb.setXRange(xmin - pad, xmax + pad, padding=0)
    else:
        vb.enableAutoRange("x", True)
    if ymin is not None and ymax is not None and ymax > ymin:
        pad = 0.02 * (ymax - ymin)
        vb.setYRange(ymin - pad, ymax + pad, padding=0)
    else:
        vb.enableAutoRange("y", True)


def _figure_scale_axis_bounds(ctx, task_id, parquet, column,
                              column_transforms=None):
    """Default (min, max) display bounds from the nearest upstream Scale.

    The nearest Scale module that stores a Min and/or Max for the column
    wins; a side that is not configured is returned as None so the caller can
    fall back to the data extent. Bounds are stored in original units and
    converted through the same display transforms the plot uses.
    """
    if not task_id or not parquet or not column:
        return None
    try:
        stages = ctx._map_stages(
            task_id, include_scale=True, parquet_path=parquet
        ) or []
    except Exception:  # noqa: BLE001 - a missing pipeline must not break a plot
        return None
    config = None
    for kind, stage in reversed(stages):
        if kind != "scale":
            continue
        files = stage.get("files")
        if files is not None and parquet not in files:
            continue
        candidate = (stage.get("columns") or {}).get(column)
        if not candidate:
            continue
        if candidate.get("min") is None and candidate.get("max") is None:
            continue
        config = candidate
        break
    if config is None:
        return None
    configs = _figure_axis_scale_configs(
        ctx, task_id, parquet, column, column_transforms
    )
    return (
        _forward_scale_value(config.get("min"), configs),
        _forward_scale_value(config.get("max"), configs),
    )


def _figure_apply_ranges(ctx, plot_dict: dict, plot_widget,
                         task_id=None, column_transforms=None) -> None:
    """Apply manual/auto X and Y ranges from the plot settings."""
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
    xmin, xmax = plot_dict.get("xmin"), plot_dict.get("xmax")
    ymin, ymax = plot_dict.get("ymin"), plot_dict.get("ymax")
    # Auto axes default to the nearest upstream Scale's Min/Max; a side that
    # Scale does not configure falls back to the rendered data extent.
    # Overlay plots and histogram counts keep their existing behaviour.
    ptype = plot_dict.get("type")
    parquet = plot_dict.get("file")
    if ptype in ("scatter", "histogram") and task_id and parquet:
        if xmin is None or xmax is None:
            bounds = _figure_scale_axis_bounds(
                ctx, task_id, parquet, plot_dict.get("x"),
                column_transforms,
            )
            if bounds is not None:
                if xmin is None:
                    xmin = bounds[0]
                if xmax is None:
                    xmax = bounds[1]
                if xmin is None:
                    xmin = getattr(plot_widget, "_data_xmin", None)
                if xmax is None:
                    xmax = getattr(plot_widget, "_data_xmax", None)
        if ptype == "scatter" and (ymin is None or ymax is None):
            bounds = _figure_scale_axis_bounds(
                ctx, task_id, parquet, plot_dict.get("y"),
                column_transforms,
            )
            if bounds is not None:
                if ymin is None:
                    ymin = bounds[0]
                if ymax is None:
                    ymax = bounds[1]
                if ymin is None:
                    ymin = getattr(plot_widget, "_data_ymin", None)
                if ymax is None:
                    ymax = getattr(plot_widget, "_data_ymax", None)
    if xmin is not None and xmax is not None and xmax > xmin:
        plot_widget.setXRange(float(xmin), float(xmax), padding=0)
    else:
        plot_widget.enableAutoRange("x", True)
    if ymin is not None and ymax is not None and ymax > ymin:
        plot_widget.setYRange(float(ymin), float(ymax), padding=0)
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
    token = ctx._figure_bump_render_token(plot_widget)
    # Same as scatter: invalidate any cached image item.
    plot_widget._img_fitted = False
    plot_widget._img_path = None
    rec = ctx._figure_record(plot_dict.get("file"))
    x_name = plot_dict.get("x")
    if not rec:
        plot_widget.setTitle("No data file", color="#c0392b", size="10pt")
        return
    if not x_name:
        plot_widget.setTitle("Parameter not in this file", color="#c0392b", size="10pt")
        return
    display_cache = {}

    def display_configs(name):
        if name not in display_cache:
            display_cache[name] = _figure_axis_scale_configs(
                ctx, task_id, rec["parquet"], name, column_transforms
            )
        return display_cache[name]

    # Same pooled data as the scatter views: one column is plenty for a
    # histogram, and the pool is already in memory for this file.
    pooled_mode = False
    indices = None
    arrays = None
    if extra_stages is None:
        ready = ctx._pool_ready(rec["parquet"], task_id, [x_name])
        if ready is None:
            pending = ctx._pool_pending_keys(
                rec["parquet"], task_id, [x_name]
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
                plot_widget.setTitle(
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
            plot_widget.setTitle(
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


