"""Contrast curve editor used by the Calculation module.

The curve maps a normalized input x in [0, 1] to a normalized output y in
[0, 1].  The two endpoints have a fixed x (0 and 1) but an adjustable y;
clicking the plot adds an interior anchor, dragging moves one, and a
right-click removes an interior anchor.  The editor never forces the curve
to be monotonic, so inverted ("negative") curves are possible.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from smage.core.calculation import _anchor_points, _curve_lut

_MARGIN = 9.0
_ANCHOR_RADIUS = 4.5
_HIT_RADIUS = 9.0
_MIN_GAP = 0.012


class _CurveEditor(QWidget):
    """A small click/drag editor for a contrast transfer curve."""

    changed = Signal()

    def __init__(self, anchors=None, parent=None) -> None:
        super().__init__(parent)
        self._anchors = [list(point) for point in _anchor_points(anchors)]
        self._drag_index = -1
        self.setMinimumSize(170, 130)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(
            "Click to add an anchor, drag an anchor to move it, "
            "right-click an interior anchor to remove it."
        )

    # -- data -----------------------------------------------------------
    def anchors(self) -> list:
        return [list(point) for point in self._anchors]

    def set_anchors(self, anchors) -> None:
        self._anchors = [list(point) for point in _anchor_points(anchors)]
        self._drag_index = -1
        self.update()

    def reset_identity(self) -> None:
        self.set_anchors([[0.0, 0.0], [1.0, 1.0]])
        self.changed.emit()

    def sizeHint(self) -> QSize:
        return QSize(260, 160)

    # -- geometry -------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        return QRectF(
            _MARGIN, _MARGIN,
            max(1.0, self.width() - 2 * _MARGIN),
            max(1.0, self.height() - 2 * _MARGIN),
        )

    def _to_px(self, x: float, y: float) -> QPointF:
        rect = self._plot_rect()
        return QPointF(
            rect.left() + x * rect.width(),
            rect.bottom() - y * rect.height(),
        )

    def _from_px(self, pos) -> tuple[float, float]:
        rect = self._plot_rect()
        x = (pos.x() - rect.left()) / rect.width()
        y = (rect.bottom() - pos.y()) / rect.height()
        return (
            min(1.0, max(0.0, x)),
            min(1.0, max(0.0, y)),
        )

    def _hit_anchor(self, pos) -> int:
        best = -1
        best_d = _HIT_RADIUS
        for index, (x, y) in enumerate(self._anchors):
            px = self._to_px(x, y)
            distance = (
                (px.x() - pos.x()) ** 2 + (px.y() - pos.y()) ** 2
            ) ** 0.5
            if distance <= best_d:
                best, best_d = index, distance
        return best

    # -- mouse ----------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            index = self._hit_anchor(pos)
            if 0 < index < len(self._anchors) - 1:
                self._anchors.pop(index)
                self._drag_index = -1
                self.update()
                self.changed.emit()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        index = self._hit_anchor(pos)
        if index < 0:
            x, y = self._from_px(pos)
            # Keep the new anchor away from the endpoints and its neighbours.
            if x < _MIN_GAP or x > 1.0 - _MIN_GAP:
                event.accept()
                return
            index = 0
            for i, (ax, _ay) in enumerate(self._anchors):
                if ax < x:
                    index = i + 1
                else:
                    break
            if 0 < index < len(self._anchors):
                if abs(self._anchors[index - 1][0] - x) < _MIN_GAP or (
                    index < len(self._anchors)
                    and abs(self._anchors[index][0] - x) < _MIN_GAP
                ):
                    event.accept()
                    return
            self._anchors.insert(index, [x, y])
        self._drag_index = index
        self.update()
        self.changed.emit()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_index < 0:
            hover = self._hit_anchor(event.position())
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if hover >= 0
                else Qt.CursorShape.CrossCursor
            )
            super().mouseMoveEvent(event)
            return
        pos = event.position()
        x, y = self._from_px(pos)
        index = self._drag_index
        if index == 0:
            x = 0.0
        elif index == len(self._anchors) - 1:
            x = 1.0
        else:
            lower = self._anchors[index - 1][0] + _MIN_GAP
            upper = self._anchors[index + 1][0] - _MIN_GAP
            x = min(upper, max(lower, x))
        self._anchors[index] = [x, y]
        self.update()
        self.changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_index = -1
        self.setCursor(Qt.CursorShape.CrossCursor)
        super().mouseReleaseEvent(event)

    # -- paint ----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        import numpy as np

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d7dee8"), 1))
        painter.drawRect(rect)
        # Quarter grid.
        for step in (1, 2, 3):
            x = rect.left() + rect.width() * step / 4.0
            y = rect.top() + rect.height() * step / 4.0
            painter.setPen(QPen(QColor("#eef2f7"), 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        # Identity reference.
        painter.setPen(QPen(QColor("#c9d3df"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(
            self._to_px(0.0, 0.0), self._to_px(1.0, 1.0)
        )
        # Curve.
        lut = _curve_lut(self._anchors, 256)
        grid = np.linspace(0.0, 1.0, lut.size)
        painter.setPen(QPen(QColor("#1f5fa8"), 2))
        points = [
            self._to_px(float(x), float(y)) for x, y in zip(grid, lut)
        ]
        painter.drawPolyline(points)
        # Anchors.
        for index, (x, y) in enumerate(self._anchors):
            centre = self._to_px(x, y)
            interior = 0 < index < len(self._anchors) - 1
            painter.setPen(QPen(QColor("#1f5fa8"), 1.5))
            painter.setBrush(
                QColor("#1f5fa8") if interior else QColor("#ffffff")
            )
            painter.drawEllipse(centre, _ANCHOR_RADIUS, _ANCHOR_RADIUS)
        painter.end()
