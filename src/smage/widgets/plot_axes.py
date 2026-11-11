"""Plot axes, tick labels, gate colours and scatter helpers."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import QGraphicsSimpleTextItem

from smage.core.formatting import _format_sci
from smage.core.scale_math import _inverse_scale_value


_GATE_HOVER_PEN = "#444444"
_GATE_COLOR_DEFAULT = "#000000"
_SCATTER_BRUSH_CACHE: dict = {}

def _make_original_axis(configs, orientation: str = "bottom"):
    """Axis whose tick labels are shown in the original (pre-Scale) values."""
    import pyqtgraph as pg

    class _OriginalAxis(pg.AxisItem):
        def tickStrings(self, values, scale, spacing):
            import math

            out = []
            for value in values:
                original = _inverse_scale_value(value * scale, configs)
                out.append(
                    "" if not math.isfinite(original) else _format_sci(original)
                )
            return out

    ax = _OriginalAxis(orientation)
    ax.setPen(pg.mkPen("#000000", width=2))
    ax.setTextPen(pg.mkPen("#000000"))
    return ax


def _make_sci_axis(orientation: str):
    """Return an axis item with scientific-notation, outward (non-crossing) ticks."""
    import pyqtgraph as pg

    class _SciAxis(pg.AxisItem):
        def tickStrings(self, values, scale, spacing):
            return [_format_sci(v * scale) for v in values]

        def generateDrawSpecs(self, p):
            specs = super().generateDrawSpecs(p)
            if specs is None:
                return specs
            axisSpec, tickSpecs, textSpecs = specs
            # Anchor each tick at the spine so it points outward without crossing,
            # and draw the ticks a little heavier than the axis line.
            tick_pen = pg.mkPen("#000000", width=2)
            if self.orientation in ("left", "right"):
                spine = axisSpec[1].x()
                tickSpecs = [(tick_pen, pg.Point(spine, p1.y()), p2)
                             for _pen, p1, p2 in tickSpecs]
            else:
                spine = axisSpec[1].y()
                tickSpecs = [(tick_pen, pg.Point(p1.x(), spine), p2)
                             for _pen, p1, p2 in tickSpecs]
            return axisSpec, tickSpecs, textSpecs

    ax = _SciAxis(orientation)
    ax.setStyle(tickLength=5)  # positive -> outward
    # Axis line, ticks and tick labels in black.
    ax.setPen(pg.mkPen("#000000", width=2))
    ax.setTextPen(pg.mkPen("#000000"))
    return ax


def _make_fixed_axis(orientation: str, decimals: int = 1):
    """Return an axis whose tick labels use plain decimals (no 1e+03 forms)."""
    import pyqtgraph as pg

    class _FixedAxis(pg.AxisItem):
        def tickStrings(self, values, scale, spacing):
            out = []
            for value in values:
                scaled = value * scale
                if scaled == 0:
                    scaled = 0.0  # avoid "-0.0"
                out.append(f"{scaled:.{decimals}f}")
            return out

        def generateDrawSpecs(self, p):
            specs = super().generateDrawSpecs(p)
            if specs is None:
                return specs
            axisSpec, tickSpecs, textSpecs = specs
            tick_pen = pg.mkPen("#000000", width=2)
            if self.orientation in ("left", "right"):
                spine = axisSpec[1].x()
                tickSpecs = [(tick_pen, pg.Point(spine, p1.y()), p2)
                             for _pen, p1, p2 in tickSpecs]
            else:
                spine = axisSpec[1].y()
                tickSpecs = [(tick_pen, pg.Point(p1.x(), spine), p2)
                             for _pen, p1, p2 in tickSpecs]
            return axisSpec, tickSpecs, textSpecs

    ax = _FixedAxis(orientation)
    ax.setStyle(tickLength=5)
    ax.setPen(pg.mkPen("#000000", width=2))
    ax.setTextPen(pg.mkPen("#000000"))
    return ax


def _make_rotated_axis(orientation: str = "bottom"):
    """Return an axis whose tick labels are rotated 90° (long names)."""
    import pyqtgraph as pg

    class _RotatedAxis(pg.AxisItem):
        def generateDrawSpecs(self, p):
            specs = super().generateDrawSpecs(p)
            if specs is None:
                return specs
            axisSpec, tickSpecs, textSpecs = specs
            tick_pen = pg.mkPen("#000000", width=2)
            tickSpecs = [(tick_pen, p1, p2) for _pen, p1, p2 in tickSpecs]
            return axisSpec, tickSpecs, textSpecs

        def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
            p.setRenderHint(p.RenderHint.Antialiasing, False)
            p.setRenderHint(p.RenderHint.TextAntialiasing, True)
            pen, p1, p2 = axisSpec
            p.setPen(pen)
            p.drawLine(p1, p2)
            for pen, p1, p2 in tickSpecs:
                p.setPen(pen)
                p.drawLine(p1, p2)
            if self.style['tickFont'] is not None:
                p.setFont(self.style['tickFont'])
            p.setPen(self.textPen())
            p.setClipRect(self.boundingRect().toAlignedRect())
            fm = p.fontMetrics()
            shift = (fm.ascent() - fm.descent()) / 2.0
            for rect, _flags, text in textSpecs:
                p.save()
                # Rotate counter-clockwise (-90°): the label reads bottom-to-top
                # and hangs below the axis, centred on its tick.
                p.translate(
                    rect.center().x() + shift,
                    rect.top() + rect.width(),
                )
                p.rotate(-90)
                p.drawText(QPointF(0, 0), text)
                p.restore()

    ax = _RotatedAxis(orientation)
    ax.setStyle(tickLength=5)
    ax.setPen(pg.mkPen("#000000", width=2))
    ax.setTextPen(pg.mkPen("#000000"))
    return ax


def _letter_pixmap(text: str, size: int = 14) -> QPixmap:
    """Build a small pixmap with a letter (for plot corner buttons)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#666666"))
    font = painter.font()
    font.setPixelSize(10)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pixmap


def _triangle_icon(direction: str) -> QIcon:
    """A filled triangle pointing 'up' or 'down'."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#37474f"))
    if direction == "up":
        pts = [QPointF(8, 3), QPointF(3, 13), QPointF(13, 13)]
    else:
        pts = [QPointF(8, 13), QPointF(3, 3), QPointF(13, 3)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    return QIcon(pm)


def _gate_tool_icon(key: str) -> QIcon:
    """Build a small shape icon for a gating tool button."""
    pm = QPixmap(22, 22)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#37474f"))
    pen.setWidthF(1.7)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    m = 3.0
    r = QRectF(m, m, 22 - 2 * m, 22 - 2 * m)
    cx, cy = 11.0, 11.0
    if key == "rect":
        p.drawRect(r)
    elif key == "ellipse":
        p.drawEllipse(r)
    elif key == "polygon":
        pts = [
            QPointF(5, 15), QPointF(8, 7), QPointF(15, 5),
            QPointF(18, 11), QPointF(14, 17), QPointF(9, 16),
        ]
        p.drawPolygon(QPolygonF(pts))
    elif key == "quadrant":
        p.drawLine(QPointF(cx, m), QPointF(cx, 22 - m))
        p.drawLine(QPointF(m, cy), QPointF(22 - m, cy))
    elif key == "skewed_quadrant":
        p.drawLine(QPointF(cx, cy), QPointF(m, cy))       # left
        p.drawLine(QPointF(cx, cy), QPointF(22 - m, cy))  # right
        p.drawLine(QPointF(cx, cy), QPointF(cx, 22 - m))  # down
        p.drawLine(QPointF(cx, cy), QPointF(cx + 5, m))   # up, tilted right
    elif key == "split":
        p.drawLine(QPointF(cx, m), QPointF(cx, 22 - m))
    elif key == "range":
        p.drawLine(QPointF(cx - 5, m), QPointF(cx - 5, 22 - m))
        p.drawLine(QPointF(cx + 5, m), QPointF(cx + 5, 22 - m))
    p.end()
    return QIcon(pm)


def _make_fit_plot_item(viewbox=None):
    """Return a PlotItem with an extra "F" button (fit to full data)."""
    import pyqtgraph as pg

    class _FitPlotItem(pg.PlotItem):
        def __init__(self):
            super().__init__(viewBox=viewbox)
            self._on_fit = None
            self._readout_row_reserved = False
            self.fitBtn = pg.ButtonItem(_letter_pixmap("F"), 14, self)
            self.fitBtn.clicked.connect(lambda _=None: self._fit_clicked())
            # Cursor readout: x/y and the displayed channel values, shown on
            # its own row below the X axis name (plain text, no frame).
            self.readoutText = QGraphicsSimpleTextItem("", self)
            font = QFont()
            font.setPointSizeF(8.5)
            self.readoutText.setFont(font)
            self.readoutText.setBrush(QBrush(QColor(60, 70, 85)))
            self.readoutText.setVisible(False)

        def reserve_readout_row(self) -> None:
            """Keep a blank row under the X axis for the cursor readout."""
            if self._readout_row_reserved:
                return
            left, top, right, bottom = self.getContentsMargins()
            self.setContentsMargins(left, top, right, bottom + 20)
            self._readout_row_reserved = True
            self._place_buttons()

        def release_readout_row(self) -> None:
            """Give the extra bottom row back when the plot is not an image."""
            if not self._readout_row_reserved:
                return
            left, top, right, bottom = self.getContentsMargins()
            self.setContentsMargins(left, top, right, max(0, bottom - 20))
            self._readout_row_reserved = False
            self._place_buttons()

        def set_readout(self, text: str) -> None:
            """Show/hide the cursor readout (empty text hides it)."""
            text = (text or "").strip()
            if not text:
                self.readoutText.setVisible(False)
                return
            self.readoutText.setText(text)
            self.readoutText.setVisible(True)
            self._position_readout()

        def _position_readout(self) -> None:
            # The row below the X axis name; its left edge never moves, so
            # changing digit counts cannot make the readout jitter.
            y = self.size().height() - 19
            self.readoutText.setPos(4, max(2, y))

        def _button_row_y(self, button) -> float:
            """Bottom of the X axis name row (above the readout row)."""
            base = self.size().height()
            if self._readout_row_reserved:
                try:
                    base -= self.getContentsMargins()[3]
                except (AttributeError, TypeError):
                    base -= 20
            return base - button.boundingRect().height()

        def _fit_clicked(self):
            if self._on_fit is not None:
                self._on_fit()

        def _place_buttons(self) -> None:
            w = self.autoBtn.boundingRect().width()
            self.autoBtn.setPos(0, self._button_row_y(self.autoBtn))
            self.fitBtn.setPos(w + 2, self._button_row_y(self.fitBtn))
            self._position_readout()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            self._place_buttons()

    return _FitPlotItem()


def _gate_color(preview) -> str:
    """The user's gate drawing colour (defaults to black)."""
    color = (preview or {}).get("gate_color")
    return color if isinstance(color, str) and color else _GATE_COLOR_DEFAULT


def _gate_hover_color(color: str) -> str:
    """A shifted shade of `color` used when the cursor is over a gate."""
    qcolor = QColor(color)
    if not qcolor.isValid():
        return _GATE_HOVER_PEN
    if qcolor.lightness() > 128:
        return qcolor.darker(150).name()
    return qcolor.lighter(170).name()


def _gate_roi_pens(color: str | None = None) -> dict:
    """Pens for a gate ROI: the gate colour, a shifted shade on hover."""
    base = color or _GATE_COLOR_DEFAULT
    hover = _gate_hover_color(base)
    return dict(
        pen=pg.mkPen(base, width=2),
        hoverPen=pg.mkPen(hover, width=3),
        handlePen=pg.mkPen(base, width=1),
        handleHoverPen=pg.mkPen(hover, width=2),
    )


def _detach_gate_item(item) -> None:
    """Drop a gate item's signal handlers before it leaves the scene.

    The handlers are lambdas that capture the item itself, so removing the
    item left a reference cycle; collecting it later (at an arbitrary moment)
    could delete a Qt object while the scene/event loop still touched it.
    Disconnecting here makes the deletion happen at a safe, known point.
    """
    from shiboken6 import isValid

    if item is None or not isValid(item):
        return
    for name in (
        "sigRegionChangeFinished", "sigRegionChangeStarted", "sigRegionChanged",
        "sigClicked",
    ):
        signal = getattr(item, name, None)
        if signal is None:
            continue
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass


def _scatter_brush(r: int, g: int, b: int, alpha: int = 200):
    """A shared QBrush for one colour (pyqtgraph caches one sprite per brush)."""
    key = (r, g, b, alpha)
    brush = _SCATTER_BRUSH_CACHE.get(key)
    if brush is None:
        import pyqtgraph as pg

        if len(_SCATTER_BRUSH_CACHE) > 4096:
            _SCATTER_BRUSH_CACHE.clear()
        brush = pg.mkBrush(r, g, b, alpha)
        _SCATTER_BRUSH_CACHE[key] = brush
    return brush


def _figure_image_layers(plot_dict: dict) -> list:
    """Return the normalized image colour layers of a plot dict.

    Each layer is ``{"parameter": str, "color": (r, g, b), "name": str}``.
    Legacy single-parameter image plots (``color_param``) are migrated in place.
    """
    layers = plot_dict.get("layers")
    if layers:
        out = []
        for layer in layers:
            if not layer or not layer.get("parameter"):
                continue
            param = layer.get("parameter")
            out.append({
                "parameter": param,
                "color": tuple(layer.get("color", (255, 255, 255))),
                "name": str(layer.get("name") or param),
                "visible": bool(layer.get("visible", True)),
                "min": layer.get("min"),
                "max": layer.get("max"),
            })
        return out
    param = plot_dict.get("color_param")
    if not param:
        return []
    color = plot_dict.get("image_color", (255, 255, 255))
    return [{
        "parameter": param,
        "color": tuple(color),
        "name": str(param),
        "visible": True,
        "min": plot_dict.get("image_min"),
        "max": plot_dict.get("image_max"),
    }]


def _is_scatter_plot(ptype: str) -> bool:
    """Whether a Figure plot type is scatter-like (plots x/y data points)."""
    return ptype in ("scatter", "image", "overlay")


def _figure_default_size(ptype: str | None) -> list:
    """Default card size: scatter/histogram are square, image is wider.

    All three share the same height (400), so a row of new plots lines up.
    """
    if ptype in ("scatter", "histogram", "overlay"):
        return [400, 400]
    return [440, 400]


