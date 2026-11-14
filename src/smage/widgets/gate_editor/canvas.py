"""Gate canvas widgets: view box, ROIs, labels and interaction filters."""
from __future__ import annotations

import math

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsView, QMenu

from smage.core.settings import _log_exception
from smage.runlog import runlog
from smage.widgets.plot_axes import (
    _GATE_COLOR_DEFAULT, _gate_hover_color, _gate_roi_pens,
)


_MIN_GATE_SPAN = 1e-6
_POLY_CLOSE_RADIUS = 12  # pixels: click this close to the first anchor to close

class _GatingState:
    """Attribute bag holding one gate tree's state.

    Every gating method addresses its state as ``page._gating_*`` and only uses
    the page for those attributes, so passing one of these instead of the page
    lets the Unmix module embed several independent gate trees that reuse the
    same drawing, tree and settings code.
    """

    def __init__(self) -> None:
        self._gating_dirty = False


class _GateLabel(pg.TextItem):
    """A gate name label that the user can drag to offset from its gate."""

    def __init__(self, on_drag_finished=None, **kwargs):
        super().__init__(**kwargs)
        self._on_drag_finished = on_drag_finished
        self._dragging = False
        self._start_data = None
        self._down_data = None

    def hoverEvent(self, ev):
        # Claim the drag so the label stays grabbable even when it sits on top
        # of a gate ROI (which also accepts drags over its whole shape).
        if not ev.isExit() and self.isVisible():
            ev.acceptDrags(Qt.MouseButton.LeftButton)

    def mouseDragEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        if ev.isStart():
            self._dragging = True
            vb = self.getViewBox()
            self._start_data = self.pos()
            self._down_data = (
                vb.mapSceneToView(ev.buttonDownScenePos()) if vb is not None else None
            )
            ev.accept()
            return
        if not self._dragging:
            ev.ignore()
            return
        if ev.isFinish():
            self._dragging = False
            if self._on_drag_finished is not None:
                self._on_drag_finished(self)
        else:
            vb = self.getViewBox()
            if vb is not None and self._down_data is not None:
                cur = vb.mapSceneToView(ev.scenePos())
                self.setPos(self._start_data + (cur - self._down_data))
        ev.accept()


class _NoClickScatter(pg.ScatterPlotItem):
    """Scatter points that never consume mouse clicks.

    pyqtgraph delivers clicks to the topmost item that handles them, so a
    scatter point under the cursor would otherwise swallow the click that
    starts single-click gating tools (quadrant, split, range, polygon).
    """

    def mouseClickEvent(self, ev) -> None:
        ev.ignore()


class _GateViewBox(pg.ViewBox):
    """Scatter ViewBox that draws gate ROIs (rectangle / ellipse / polygon)."""

    def __init__(self, on_gate_created, on_empty_click=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_gate_created = on_gate_created
        self._on_empty_click = on_empty_click
        # Colour used while drawing gates (kept in sync with the settings).
        self.gate_color = _GATE_COLOR_DEFAULT
        self._tool = None  # "rect" | "ellipse" | "polygon" | ...
        self._start = None
        self._preview = None
        self._rubber = None
        self._first_highlight = None
        self._polygon_pts = []
        self._range_x1 = None
        self._range_y1 = None
        self._range_anchor = None
        self._range_band = None
        self._range_arrow = None
        # View range frozen while a click-drag gate is being drawn, plus the
        # auto-range flags to restore afterwards.
        self._draw_bounds = None
        self._draw_auto = None

    def set_gate_tool(self, tool) -> None:
        self._cancel_preview()
        self._tool = tool
        self._polygon_pts = []
        self._range_x1 = None

    def _cancel_preview(self) -> None:
        # Items whose C++ object was already destroyed (e.g. by a rebuild) are
        # skipped: calling removeItem on them would be a use-after-free.
        from shiboken6 import isValid

        def drop(item):
            if item is None:
                return
            try:
                if isValid(item):
                    self.removeItem(item)
            except (RuntimeError, AttributeError):
                pass

        drop(self._preview)
        self._preview = None
        drop(self._rubber)
        self._rubber = None
        drop(self._first_highlight)
        self._first_highlight = None
        drop(self._range_anchor)
        self._range_anchor = None
        drop(self._range_band)
        self._range_band = None
        drop(self._range_arrow)
        self._range_arrow = None
        self._start = None
        self._polygon_pts = []
        self._range_x1 = None
        self._range_y1 = None
        self._release_view()

    def _freeze_view(self) -> None:
        """Lock the view range while a gate is dragged out.

        The growing preview shape must not resize the axes: without this the
        auto-ranged view expands as the cursor moves past the edge.
        """
        if self._draw_bounds is not None:
            return
        self._draw_bounds = self.viewRange()
        try:
            auto = self.state["autoRange"]
            self._draw_auto = (bool(auto[0]), bool(auto[1]))
        except (KeyError, IndexError, TypeError):
            self._draw_auto = (False, False)
        self.disableAutoRange()

    def _release_view(self) -> None:
        """Restore the auto-range flags that were on before drawing."""
        auto = self._draw_auto
        self._draw_bounds = None
        self._draw_auto = None
        if auto is None:
            return
        if auto[0]:
            self.enableAutoRange("x", True)
        if auto[1]:
            self.enableAutoRange("y", True)

    def _clamp_to_bounds(self, point):
        """Clamp a view coordinate to the frozen axes of the current drag."""
        bounds = self._draw_bounds
        if bounds is None:
            return point
        xr, yr = bounds
        x = min(max(float(point.x()), min(xr)), max(xr))
        y = min(max(float(point.y()), min(yr)), max(yr))
        return pg.Point(x, y)

    def _pt(self, ev):
        return self.mapSceneToView(ev.scenePos())

    # -- rectangle / ellipse (click-drag) -----------------------------------
    def mouseDragEvent(self, ev, axis=None) -> None:
        if self._tool in ("rect", "ellipse"):
            # Exceptions must never escape a Qt virtual override: PySide6
            # terminates the whole application when that happens, which looks
            # like a crash while the user is drawing a gate.
            try:
                self._drag_shape(ev)
            except Exception as exc:  # noqa: BLE001
                _log_exception(exc)
                self._cancel_preview()
                self._release_view()
            ev.accept()
            return
        super().mouseDragEvent(ev, axis)

    def _drag_shape(self, ev) -> None:
        """Update the rect/ellipse preview and finish the gate on release."""
        if ev.isStart() and self._preview is not None:
            # A previous drag never finished (e.g. the release happened
            # outside the widget); start clean instead of reusing stale state.
            self.removeItem(self._preview)
            self._preview = None
            self._start = None
            self._release_view()
        if self._preview is None:
            runlog.action("gating.drag_start", tool=str(self._tool))
            self._freeze_view()
            runlog.action("gating.drag_frozen")
            start = self._clamp_to_bounds(
                self.mapSceneToView(ev.buttonDownScenePos())
            )
            self._start = start
            self._preview = self._make_shape(
                self._tool, (start.x(), start.y()), (0.0, 0.0)
            )
            runlog.action("gating.drag_shape")
            self.addItem(self._preview, ignoreBounds=True)
            runlog.action("gating.drag_added")
        p = self._clamp_to_bounds(self._pt(ev))
        x0, y0 = self._start.x(), self._start.y()
        x1, y1 = p.x(), p.y()
        pos = (min(x0, x1), min(y0, y1))
        raw_w, raw_h = abs(x1 - x0), abs(y1 - y0)
        size = (max(raw_w, _MIN_GATE_SPAN), max(raw_h, _MIN_GATE_SPAN))
        self._preview.setPos(pos)
        self._preview.setSize(size)
        self._drag_updates = getattr(self, "_drag_updates", 0) + 1
        if self._drag_updates <= 3 or self._drag_updates % 25 == 0:
            runlog.action("gating.drag_update", n=self._drag_updates,
                          size=(round(size[0], 3), round(size[1], 3)))
        if ev.isFinish():
            roi = self._preview
            self._preview = None
            self._start = None
            runlog.action(
                "gating.drag_finish", tool=str(self._tool),
                size=(size[0], size[1]),
            )
            try:
                if raw_w > 0 and raw_h > 0:
                    self._on_gate_created(self._tool, roi)
                else:
                    self.removeItem(roi)
            finally:
                self._release_view()

    # -- polygon / quadrant / split / range / skewed-quadrant ----------------
    def mouseClickEvent(self, ev) -> None:
        try:
            self._handle_click(ev)
        except Exception as exc:  # noqa: BLE001
            # Never let an exception escape into Qt (PySide6 aborts then).
            _log_exception(exc)
            self._cancel_preview()
            self._release_view()
            ev.accept()

    def _handle_click(self, ev) -> None:
        if self._tool:
            # Breadcrumb: click-based tools (polygon/quadrant/split/range) are
            # otherwise invisible in the log until a gate finishes.
            try:
                pos = self._pt(ev)
                runlog.action("gating.click", tool=str(self._tool),
                              x=round(pos.x(), 3), y=round(pos.y(), 3))
            except Exception:
                runlog.action("gating.click", tool=str(self._tool))
        if self._tool == "polygon" and ev.button() == Qt.MouseButton.RightButton:
            self._finish_polygon()
            ev.accept()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseClickEvent(ev)
            return
        p = self._pt(ev)
        if self._tool == "polygon":
            if len(self._polygon_pts) >= 3 and self._near_first_anchor(p):
                self._finish_polygon()
            else:
                self._polygon_pts.append((p.x(), p.y()))
                self._update_polygon_preview()
            ev.accept()
            return
        if self._tool == "quadrant":
            self._on_gate_created("quadrant", (p.x(), p.y()))
            ev.accept()
            return
        if self._tool == "split":
            self._on_gate_created("split", (p.x(), p.y()))
            ev.accept()
            return
        if self._tool == "range":
            if self._range_x1 is None:
                self._range_x1 = p.x()
                self._range_y1 = p.y()
                self._update_range_preview((p.x(), p.y()))
            else:
                self._on_gate_created("range", (self._range_x1, p.x()))
            ev.accept()
            return
        if self._tool == "skewed_quadrant":
            import math
            cx, cy = p.x(), p.y()
            xr, yr = self.viewRange()
            dx = (xr[1] - xr[0]) / 4.0
            dy = (yr[1] - yr[0]) / 4.0
            p_right = (cx + dx, cy)
            p_left = (cx - dx, cy)
            p_down = (cx, cy - dy)
            # The top point is tilted 30 degrees to the right of vertical.
            p_up = (cx + dy * math.sin(math.radians(30)), cy + dy * math.cos(math.radians(30)))
            self._on_gate_created(
                "skewed_quadrant",
                ((cx, cy), p_right, p_up, p_left, p_down),
            )
            ev.accept()
            return
        if self._on_empty_click is not None:
            self._on_empty_click()
        super().mouseClickEvent(ev)

    def _near_first_anchor(self, p) -> bool:
        """True when the cursor is within a few pixels of the polygon's first anchor."""
        if not self._polygon_pts:
            return False
        first = pg.Point(self._polygon_pts[0][0], self._polygon_pts[0][1])
        f_dev = self.mapViewToDevice(first)
        p_dev = self.mapViewToDevice(p)
        dx = p_dev.x() - f_dev.x()
        dy = p_dev.y() - f_dev.y()
        return dx * dx + dy * dy <= _POLY_CLOSE_RADIUS * _POLY_CLOSE_RADIUS

    def _update_range_preview(self, mouse_pos=None) -> None:
        if self._range_x1 is None:
            return
        if self._range_anchor is None:
            self._range_anchor = pg.InfiniteLine(
                pos=self._range_x1, angle=90, movable=False,
                pen=pg.mkPen(self.gate_color, width=2, style=Qt.PenStyle.DashLine),
            )
            self.addItem(self._range_anchor, ignoreBounds=True)
        if self._range_band is None:
            self._range_band = self._make_preview_item(
                pen=pg.mkPen(self.gate_color, width=2, style=Qt.PenStyle.DashLine),
            )
            self.addItem(self._range_band, ignoreBounds=True)
        if self._range_arrow is None:
            self._range_arrow = pg.ArrowItem(
                angle=0, headLen=10, tipAngle=30, tailLen=None,
                brush=pg.mkBrush(self.gate_color), pen=None, pxMode=True,
            )
            self.addItem(self._range_arrow, ignoreBounds=True)
        x0, y0 = self._range_x1, self._range_y1
        if mouse_pos is not None:
            self._range_band.setData([x0, mouse_pos[0]], [y0, mouse_pos[1]])
            s_dev = self.mapViewToDevice(pg.Point(x0, y0))
            m_dev = self.mapViewToDevice(pg.Point(mouse_pos[0], mouse_pos[1]))
            dx = m_dev.x() - s_dev.x()
            dy = m_dev.y() - s_dev.y()
            ang = math.degrees(math.atan2(dy, dx)) + 180.0
            self._range_arrow.setStyle(angle=ang)
            self._range_arrow.setPos(mouse_pos[0], mouse_pos[1])
            self._range_arrow.setVisible(True)
        else:
            self._range_band.setData([x0], [y0])
            self._range_arrow.setVisible(False)

    def _finish_polygon(self) -> None:
        pts = self._polygon_pts
        if len(pts) >= 3:
            self._polygon_pts = []
            if self._preview is not None:
                self.removeItem(self._preview)
                self._preview = None
            if self._rubber is not None:
                self.removeItem(self._rubber)
                self._rubber = None
            if self._first_highlight is not None:
                self.removeItem(self._first_highlight)
                self._first_highlight = None
            roi = _GatePolygonROI(
                pts, closed=True, **_gate_roi_pens(self.gate_color)
            )
            self.addItem(roi, ignoreBounds=True)
            self._on_gate_created("polygon", roi)
        else:
            self._cancel_preview()

    def hoverEvent(self, ev) -> None:
        try:
            if self._tool == "polygon":
                mouse_pos = None
                if not ev.isExit() and self._polygon_pts:
                    p = self.mapSceneToView(ev.scenePos())
                    mouse_pos = (p.x(), p.y())
                self._update_polygon_preview(mouse_pos)
            elif self._tool == "range":
                mouse_pos = None
                if not ev.isExit() and self._range_x1 is not None:
                    p = self.mapSceneToView(ev.scenePos())
                    mouse_pos = (p.x(), p.y())
                self._update_range_preview(mouse_pos)
        except Exception as exc:  # noqa: BLE001
            # A hover handler runs inside Qt; never let exceptions escape.
            _log_exception(exc)

    def _make_preview_item(self, **kwargs) -> pg.PlotDataItem:
        """A visual-only PlotDataItem that never swallows mouse events."""
        item = pg.PlotDataItem(**kwargs)
        item.setEnabled(False)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.scatter.setEnabled(False)
        item.curve.setEnabled(False)
        return item

    def _ensure_polygon_items(self) -> None:
        if self._preview is None:
            self._preview = self._make_preview_item(
                pen=pg.mkPen(self.gate_color, width=2),
                symbol="o", symbolSize=9, symbolBrush=self.gate_color,
            )
            self.addItem(self._preview, ignoreBounds=True)
        if self._rubber is None:
            self._rubber = self._make_preview_item(
                pen=pg.mkPen(self.gate_color, width=1, style=Qt.PenStyle.DashLine),
            )
            self.addItem(self._rubber, ignoreBounds=True)
        if self._first_highlight is None:
            self._first_highlight = self._make_preview_item(
                pen=None,
                symbol="o", symbolSize=15,
                symbolPen=pg.mkPen(_gate_hover_color(self.gate_color), width=2.5),
                symbolBrush=pg.mkBrush(None),
            )
            self.addItem(self._first_highlight, ignoreBounds=True)

    def _update_polygon_preview(self, mouse_pos=None) -> None:
        pts = self._polygon_pts
        if not pts:
            if self._preview is not None:
                self._preview.setData([], [])
            if self._rubber is not None:
                self._rubber.setData([], [])
            if self._first_highlight is not None:
                self._first_highlight.setData([], [])
            return
        self._ensure_polygon_items()
        self._preview.setData([p[0] for p in pts], [p[1] for p in pts])
        # Highlight the first anchor when the cursor is close enough to close.
        near_first = (
            len(pts) >= 3
            and mouse_pos is not None
            and self._near_first_anchor(pg.Point(mouse_pos[0], mouse_pos[1]))
        )
        if near_first:
            self._first_highlight.setData([pts[0][0]], [pts[0][1]])
        else:
            self._first_highlight.setData([], [])
        if mouse_pos is not None:
            last = pts[-1]
            self._rubber.setData([last[0], mouse_pos[0]], [last[1], mouse_pos[1]])
        else:
            self._rubber.setData([], [])

    def _make_shape(self, tool, pos, size):
        if tool == "ellipse":
            return pg.EllipseROI(pos, size, **_gate_roi_pens(self.gate_color))
        return pg.RectROI(pos, size, **_gate_roi_pens(self.gate_color))


class _GatePolygonROI(pg.PolyLineROI):
    """Polygon gate ROI whose vertices are edited via a right-click menu only."""

    def __init__(self, positions, **kwargs):
        super().__init__(positions, **kwargs)
        self._click_pos = None
        self._menu = None
        self._fix_editing_mode()

    def _fix_editing_mode(self) -> None:
        # Don't insert a vertex when a segment is clicked; don't show the
        # per-handle "remove" button. Both are handled by the context menu.
        # Instead, an edge click selects the polygon.
        import warnings

        for seg in self.segments:
            # Shiboken emits a RuntimeWarning (not an exception) when a
            # connection does not exist; silence it while replacing handlers.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    seg.sigClicked.disconnect(self.segmentClicked)
                except (TypeError, RuntimeError):
                    pass
                try:
                    seg.sigClicked.disconnect(self._edge_clicked)
                except (TypeError, RuntimeError):
                    pass
            seg.sigClicked.connect(self._edge_clicked)
        for h in self.handles:
            h["item"].setDeletable(False)

    def _edge_clicked(self, segment, ev=None) -> None:
        # Forward an edge click to the polygon's own click signal.
        self.sigClicked.emit(self, ev)

    def hoverEvent(self, ev) -> None:
        # Highlight the whole polygon, not just the hovered edge.
        self.setMouseHover(not ev.isExit())
        if not ev.isExit():
            ev.acceptDrags(Qt.MouseButton.LeftButton)

    def addSegment(self, h1, h2, index=None):
        super().addSegment(h1, h2, index=index)
        self._fix_editing_mode()

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.RightButton:
            self._click_pos = ev.pos()
            self._menu = self.getMenu()
            self._menu.popup(ev.screenPos().toPoint())
            ev.accept()
            return
        super().mouseClickEvent(ev)

    def getMenu(self):
        menu = QMenu()
        menu.setTitle("Polygon")
        add_act = QAction("Add point", menu)
        add_act.triggered.connect(self._add_point_clicked)
        menu.addAction(add_act)
        rem_act = QAction("Remove point", menu)
        rem_act.triggered.connect(self._remove_point_clicked)
        menu.addAction(rem_act)
        return menu

    def _add_point_clicked(self) -> None:
        if self._click_pos is None:
            return
        seg = self._nearest_segment(self._click_pos)
        if seg is not None:
            self.segmentClicked(seg, pos=self._click_pos)

    def _remove_point_clicked(self) -> None:
        if self._click_pos is None or len(self.handles) <= 3:
            return
        handle = self._nearest_handle(self._click_pos)
        if handle is not None:
            self.removeHandle(handle)

    def _nearest_segment(self, pos):
        best, best_d = None, None
        for seg in self.segments:
            h0 = seg.handles[0]["item"].pos()
            h1 = seg.handles[1]["item"].pos()
            mid = h0 + (h1 - h0) / 2
            d = (mid - pos).length()
            if best_d is None or d < best_d:
                best_d, best = d, seg
        return best

    def _nearest_handle(self, pos):
        best, best_d = None, None
        for h in self.handles:
            hp = h["item"].pos()
            d = (hp - pos).length()
            if best_d is None or d < best_d:
                best_d, best = d, h["item"]
        return best


class _SkewedQuadrantROI(pg.ROI):
    """A bent crosshair: 5 draggable points (center + 4 arms) joined by rays."""

    def __init__(self, center, p_right, p_up, p_left, p_down, **args):
        super().__init__([0, 0], [1, 1], removable=False, **args)
        self.addFreeHandle(center)
        self.addFreeHandle(p_right)
        self.addFreeHandle(p_up)
        self.addFreeHandle(p_left)
        self.addFreeHandle(p_down)
        self._bounds = None

    def _handles(self):
        return [h.pos() for h in self.getHandles()]

    def viewTransformChanged(self):
        # Ray endpoints depend on the visible range; recompute on pan/zoom.
        self._bounds = None
        super().viewTransformChanged()

    def boundingRect(self):
        if self._bounds is None:
            self._bounds = self._compute_bounds()
        return self._bounds

    def _compute_bounds(self):
        pts = self._handles()
        if not pts:
            return QRectF()
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        vr = self.viewRect()
        if vr is not None:
            # Include the visible range so the rays (drawn to the view edge)
            # are never clipped away by a stale bounding rect.
            xs += [vr.left(), vr.right()]
            ys += [vr.top(), vr.bottom()]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def paint(self, p, *args):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(self.currentPen)
        pts = self._handles()
        if len(pts) < 5:
            return
        center = pts[0]
        vr = self.viewRect()
        for sp in pts[1:]:
            dx = sp.x() - center.x()
            dy = sp.y() - center.y()
            if dx == 0 and dy == 0:
                continue
            if vr is None:
                far = pg.Point(center.x() + 9 * dx, center.y() + 9 * dy)
            else:
                far = self._ray_to_rect(center, dx, dy, vr)
            p.drawLine(center, far)

    @staticmethod
    def _ray_to_rect(center, dx, dy, rect):
        """Point where the ray (center, direction (dx, dy)) leaves ``rect``."""
        hits = []
        if dx != 0:
            for x in (rect.left(), rect.right()):
                t = (x - center.x()) / dx
                if t >= 0:
                    y = center.y() + t * dy
                    if rect.top() <= y <= rect.bottom():
                        hits.append(t)
        if dy != 0:
            for y in (rect.top(), rect.bottom()):
                t = (y - center.y()) / dy
                if t >= 0:
                    x = center.x() + t * dx
                    if rect.left() <= x <= rect.right():
                        hits.append(t)
        if not hits:
            return pg.Point(center.x(), center.y())
        t = max(hits)
        return pg.Point(center.x() + t * dx, center.y() + t * dy)

    def shape(self):
        """Hit region along the rays and around the five handles.

        The default shape (== boundingRect) would span the whole plot and
        swallow later clicks, so instead we cover only the thin rays plus the
        handle points, keeping the rest of the plot clickable.
        """
        import math
        pts = self._handles()
        if len(pts) < 5:
            return QPainterPath()
        center = pts[0]
        vr = self.viewRect()
        max_len = max(
            math.hypot(p.x() - center.x(), p.y() - center.y()) for p in pts[1:]
        ) or 1.0
        r = max_len * 0.12
        w = max_len * 0.08
        path = QPainterPath()
        for p in pts:
            path.addEllipse(p.x() - r, p.y() - r, 2 * r, 2 * r)
        for sp in pts[1:]:
            dx = sp.x() - center.x()
            dy = sp.y() - center.y()
            if dx == 0 and dy == 0:
                continue
            far = self._ray_to_rect(center, dx, dy, vr) if vr is not None else sp
            length = math.hypot(far.x() - center.x(), far.y() - center.y())
            if length == 0:
                continue
            ux = (far.x() - center.x()) / length
            uy = (far.y() - center.y()) / length
            px, py = -uy, ux
            path.moveTo(center.x() + px * w, center.y() + py * w)
            path.lineTo(far.x() + px * w, far.y() + py * w)
            path.lineTo(far.x() - px * w, far.y() - py * w)
            path.lineTo(center.x() - px * w, center.y() - py * w)
            path.closeSubpath()
        return path


class _ReleaseFilter(QObject):
    """Run a callback after a mouse button is released."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self._callback)
        return False


class _ClickFilter(QObject):
    """Select a plot card when its widget (or embedded plot) is clicked."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._callback(ctrl)
        return False  # let the event continue (plots still pan/zoom)


class _DoubleClickFilter(QObject):
    """Focus a plot card when its widget (or embedded plot) is double-clicked."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self._callback()
        return False


class _GatingCanvasView(QGraphicsView):
    """Canvas holding the gating plot card.

    The wheel zooms the canvas itself when the cursor is over the empty canvas,
    while a wheel over the plot keeps zooming the data (it is forwarded to the
    embedded plot through the scene, exactly as before).
    """

    MIN_SCALE = 0.35
    MAX_SCALE = 3.0
    ZOOM_STEP = 1.15

    def __init__(self, scene, plot_widget, card, gate_overlay=None, parent=None) -> None:
        super().__init__(scene, parent)
        self._plot_widget = plot_widget
        self._plot_viewport = plot_widget.viewport()
        self._card = card
        self._gate_overlay = gate_overlay
        self._zoom = 1.0
        self._plot_pressed = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#eef2f7"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _over_plot(self, view_pos) -> bool:
        """True when the viewport point falls on the plot inside the card."""
        proxy = self._card.graphicsProxyWidget()
        if proxy is None:
            return False
        local = proxy.mapFromScene(self.mapToScene(view_pos))
        top_left = self._plot_widget.mapTo(self._card, QPoint(0, 0))
        rect = QRectF(
            float(top_left.x()), float(top_left.y()),
            float(self._plot_widget.width()), float(self._plot_widget.height()),
        )
        return rect.contains(local)

    def _send_mouse(self, event) -> None:
        """Forward a mouse event to the plot's viewport (translated)."""
        view_pos = event.position().toPoint()
        global_pos = self.viewport().mapToGlobal(view_pos)
        local = self._plot_viewport.mapFromGlobal(global_pos)
        forwarded = QMouseEvent(
            event.type(), QPointF(local),
            QPointF(self._plot_viewport.mapToGlobal(local)),
            event.button(), event.buttons(), event.modifiers(),
        )
        QApplication.sendEvent(self._plot_viewport, forwarded)

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._over_plot(event.position().toPoint())
        ):
            self._plot_pressed = True
            self._send_mouse(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._plot_pressed or self._over_plot(event.position().toPoint()):
            # Forward drags and plain hover moves alike: pyqtgraph needs hover
            # events to highlight handles and to let gate ROIs be dragged.
            self._send_mouse(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._plot_pressed:
            self._plot_pressed = False
            self._send_mouse(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._over_plot(event.position().toPoint()):
            # Keep forwarding the trailing release too: pyqtgraph delivers the
            # queued double-click when the release arrives.
            self._plot_pressed = True
            self._send_mouse(event)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        if self._over_plot(event.position().toPoint()):
            # The plot zooms its own data (pyqtgraph), anchored at the cursor.
            super().wheelEvent(event)
            return
        factor = self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP
        new_zoom = self._zoom * factor
        if new_zoom < self.MIN_SCALE or new_zoom > self.MAX_SCALE:
            event.accept()
            return
        self._zoom = new_zoom
        self.scale(factor, factor)
        event.accept()

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 1.0


