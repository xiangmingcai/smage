"""Image channel Min/Max controls (original-value fields + level history)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)

from smage.core.formatting import _format_level, _round_level
from smage.core.scale_math import (
    _apply_scale_transforms, _forward_scale_value, _inverse_scale_value,
)
from smage.core.settings import _plot_background
from smage.widgets.plot_axes import _make_original_axis


def _image_signal_histogram(ctx, layout, channel, task_id, parquet_path,
                            extra_stages=None, configs=None, title=None,
                            values=None):
    """A read-only signal histogram for one channel.

    Used where a second (for example "Before") distribution is shown next to
    the Min/Max controls, so it has no inputs, Auto set or Reset buttons.
    ``values`` may hold an already loaded (sampled) distribution; otherwise
    the channel is read through the context.
    """
    import numpy as np
    import pyqtgraph as pg

    if configs is None and channel:
        configs = ctx._figure_axis_scale_configs(
            task_id, parquet_path, channel
        )
    hist_plot = pg.PlotWidget()
    hist_plot.setBackground(_plot_background())
    hist_plot.showGrid(x=True, y=True, alpha=0.2)
    hist_plot.setMinimumHeight(100)
    hist_plot.setMaximumHeight(160)
    hist_plot.setMouseEnabled(False, False)
    hist_plot.setMenuEnabled(False)
    hist_plot.hideAxis("left")
    if configs:
        hist_plot.getPlotItem().setAxisItems({
            "bottom": _make_original_axis(configs),
        })
    layout.addWidget(hist_plot)
    if not channel or parquet_path is None:
        return hist_plot
    if values is None:
        values = ctx._figure_channel_values(
            task_id, parquet_path, channel, extra_stages=extra_stages
        )
    if values is None or values.size == 0:
        return hist_plot
    values = _apply_scale_transforms(values, configs)
    hist, edges = np.histogram(values, bins=200)
    hist_plot.plot(
        edges, hist, stepMode="center",
        fillLevel=0, brush=(90, 140, 220, 120),
        pen=pg.mkPen((50, 90, 160), width=1),
    )
    hist_plot.setTitle(
        str(channel if title is None else title),
        color="#5a6673", size="8pt",
    )
    return hist_plot


def _image_level_controls(ctx, layout, channel, min_val, max_val, enabled, on_minmax, task_id, parquet_path, extra_stages=None, configs=None, hist_caption=None, values=None) -> None:
    """Min/Max inputs + signal histogram + Auto set / Reset for a channel.

    `on_minmax(lo, hi)` receives the bounds in PLOT units (None = auto);
    the inputs and the histogram labels show ORIGINAL (pre-Scale) values,
    converted through `configs`.

    ``values`` may hold an already loaded (sampled) distribution; Auto set
    and Reset then use it instead of reading the whole channel.
    """
    import numpy as np
    import pyqtgraph as pg

    if configs is None:
        configs = ctx._figure_axis_scale_configs(
            task_id, parquet_path, channel
        )
    state = {"values": None}

    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    h.addWidget(QLabel("Min"))
    min_edit = QLineEdit()
    display_min = (
        None if min_val is None
        else _inverse_scale_value(min_val, configs)
    )
    min_edit.setText(
        "" if display_min is None else _format_level(display_min)
    )
    min_edit.setPlaceholderText("Auto")
    min_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    min_edit.setEnabled(enabled)
    h.addWidget(min_edit, 1)
    h.addWidget(QLabel("Max"))
    max_edit = QLineEdit()
    display_max = (
        None if max_val is None
        else _inverse_scale_value(max_val, configs)
    )
    max_edit.setText(
        "" if display_max is None else _format_level(display_max)
    )
    max_edit.setPlaceholderText("Auto")
    max_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    max_edit.setEnabled(enabled)
    h.addWidget(max_edit, 1)
    layout.addWidget(row)

    if hist_caption:
        caption = QLabel(str(hist_caption))
        caption.setStyleSheet(
            "color: #5a6673; font-size: 8pt; font-weight: bold;"
        )
        layout.addWidget(caption)

    hist_plot = pg.PlotWidget()
    hist_plot.setBackground(_plot_background())
    hist_plot.showGrid(x=True, y=True, alpha=0.2)
    hist_plot.setMinimumHeight(100)
    hist_plot.setMaximumHeight(160)
    hist_plot.setMouseEnabled(False, False)
    hist_plot.setMenuEnabled(False)
    hist_plot.hideAxis("left")
    if configs:
        hist_plot.getPlotItem().setAxisItems({
            "bottom": _make_original_axis(configs),
        })
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

    def load_values() -> None:
        state["values"] = None
        if not channel or parquet_path is None:
            hist_plot.clear()
            return
        loaded = values
        if loaded is None:
            loaded = ctx._figure_channel_values(
                task_id, parquet_path, channel, extra_stages=extra_stages
            )
        if loaded is None or loaded.size == 0:
            hist_plot.clear()
            return
        # Levels are in display units: apply the Scale (log/arcsinh) configs,
        # which only affect how the data is shown.
        values_display = _apply_scale_transforms(loaded, configs)
        state["values"] = values_display
        hist, edges = np.histogram(values_display, bins=200)
        hist_plot.clear()
        hist_plot.plot(
            edges, hist, stepMode="center",
            fillLevel=0, brush=(90, 140, 220, 120),
            pen=pg.mkPen((50, 90, 160), width=1),
        )
        hist_plot.setTitle(str(channel), color="#5a6673", size="8pt")

    def read_edits():
        lo = hi = None
        text = min_edit.text().strip()
        if text:
            try:
                lo = float(text)
            except ValueError:
                pass
        text = max_edit.text().strip()
        if text:
            try:
                hi = float(text)
            except ValueError:
                pass
        return lo, hi

    def to_plot(value):
        if value is None:
            return None
        rounded = _round_level(value)
        return _forward_scale_value(rounded, configs)

    def on_edit() -> None:
        lo, hi = read_edits()
        on_minmax(to_plot(lo), to_plot(hi))

    min_edit.editingFinished.connect(on_edit)
    max_edit.editingFinished.connect(on_edit)

    def on_auto(_=False) -> None:
        values = state["values"]
        if values is None or values.size == 0:
            return
        lo, hi = np.percentile(values, [0.5, 99.5])
        lo, hi = _round_level(lo), _round_level(hi)
        min_edit.setText(
            _format_level(_inverse_scale_value(lo, configs))
        )
        max_edit.setText(
            _format_level(_inverse_scale_value(hi, configs))
        )
        on_minmax(lo, hi)

    def on_reset(_=False) -> None:
        if values is not None and state["values"] is not None:
            # A sampled distribution is already in display units; use it
            # instead of reading the whole channel just to find its range.
            sampled = state["values"]
            if sampled.size == 0:
                return
            lo = float(np.min(sampled))
            hi = float(np.max(sampled))
        else:
            bounds = ctx._channel_full_range(
                task_id, parquet_path, channel, extra_stages=extra_stages
            )
            if bounds is None:
                min_edit.setText("")
                max_edit.setText("")
                on_minmax(None, None)
                ctx.statusBar().showMessage(
                    "This channel has no finite values", 3000
                )
                return
            lo = _forward_scale_value(_round_level(bounds[0]), configs)
            hi = _forward_scale_value(_round_level(bounds[1]), configs)
        min_edit.setText(
            _format_level(_inverse_scale_value(lo, configs))
        )
        max_edit.setText(
            _format_level(_inverse_scale_value(hi, configs))
        )
        on_minmax(lo, hi)

    auto_btn.clicked.connect(on_auto)
    reset_btn.clicked.connect(on_reset)
    load_values()
