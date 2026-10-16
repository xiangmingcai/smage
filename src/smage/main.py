"""Smage — desktop application entry point and main window.

Phase 1 UI skeleton: a File/Help menu bar and a single tabbed workspace.
The first tab is the Workflow (always open); further tabs are modules with
their own toolbar (Save/Settings/Close) and content. The "+" button on a task
opens an "Add Module" dialog with placeholder module types. Module
functionality comes in later phases.
"""
from __future__ import annotations

import copy
import math
import shutil
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

# Make the `smage` package importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smage.dimensions import parameter_names, read_dimensions
from smage.importer import import_file
from smage.project import Project

import pyqtgraph as pg

APP_NAME = "Smage"

# Light-blue "tech" theme, loosely inspired by Positron's blue accents.
APP_STYLE = """
QMainWindow { background: #f5f8fb; }

/* menu bar */
QMenuBar { background: #ffffff; border-bottom: 1px solid #e3e8ef; }
QMenuBar::item { padding: 0px 10px; }
QMenuBar::item:selected { background: #e6f0fb; color: #1d4f7a; }

/* status bar */
QStatusBar { background: #ffffff; border-top: 1px solid #e3e8ef; color: #5a6673; }

/* dropdown menus */
QMenu { background: #ffffff; border: 1px solid #dbe3ec; padding: 4px 0px; }
QMenu::item { padding: 5px 24px; color: #2c3a48; }
QMenu::item:selected { background: #e6f0fb; color: #1d4f7a; }

/* flat tabs — no 3D effect, active tab tinted light blue */
QTabWidget::pane { border: none; }
QTabBar::tab {
    background: #eef1f5;
    color: #5a6673;
    border: none;
    padding: 5px 16px;
    margin-right: 3px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:hover:!selected { background: #e4eaf0; }
QTabBar::tab:selected { background: #e1ecf9; color: #1a4a76; }

/* module toolbar */
QFrame#moduleBar { background: #e1ecf9; border-bottom: 1px solid #d4e2f1; }

/* buttons — flat, light-blue accent */
QPushButton {
    background: #ffffff;
    border: 1px solid #c9d5e2;
    border-radius: 4px;
    padding: 3px 12px;
    color: #2c3a48;
}
QPushButton:hover { background: #f0f7fe; border-color: #5aa0e0; }
QPushButton:pressed { background: #dbeafa; }
QPushButton:checked { background: #c9ddf2; border-color: #4a90d9; }

/* workflow tree */
QTreeWidget { background: #f5f8fb; border: none; }

/* parameter panel inputs */
QCheckBox { spacing: 6px; color: #2c3a48; }
QLineEdit {
    background: #ffffff;
    border: 1px solid #c9d5e2;
    border-radius: 4px;
    padding: 3px 8px;
    color: #2c3a48;
}
QLineEdit:focus { border-color: #5aa0e0; }
"""


def _dot_pixmap(color: str) -> QPixmap:
    """Build a small colored-dot pixmap (placeholder for an icon)."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 12, 12)
    painter.end()
    return pixmap


def _lock_pixmap(color: str = "#b58900", size: int = 14) -> QPixmap:
    """Build a small padlock pixmap for locked modules."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Shackle (top half arc).
    painter.drawArc(QRectF(3.5, 1.5, size - 7.0, size - 7.0), 0, 180 * 16)
    # Body.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(2.0, 5.0, size - 4.0, size - 5.0), 1.6, 1.6)
    painter.end()
    return pixmap


# Known task types: type key -> (display name, color).
# "import" is the fixed first task; ADDABLE_TYPES appear in the Add-Module dialog.
TASK_TYPES = {
    "import": ("Data Import", "#9aa0a6"),
    "gating": ("Gating", "#f4b400"),
    "figure": ("Figure", "#34a853"),
    "scale": ("Scale", "#4285f4"),
    "rename": ("Rename", "#8e9aab"),
    "unmix": ("Unmix", "#7b61ff"),
    "segmentation": ("Segmentation", "#00acc1"),
    "compensation": ("Compensation", "#fb8c00"),
    "spectrum": ("Spectrum", "#e91e63"),
}

ADDABLE_TYPES = [
    "gating",
    "figure",
    "scale",
    "rename",
    "unmix",
    "segmentation",
    "compensation",
    "spectrum",
]

ROOT_COLOR = "#5f6368"
FALLBACK_COLOR = "#a142f4"

# Import status colors (the dot shown before each file in the Input panel).
IMPORT_STATUS_COLORS = {
    "pending": "#9aa0a6",    # just selected (default)
    "peeked": "#4285f4",     # dimensions read (Peek)
    "processed": "#34a853",  # imported / converted
    "error": "#e53935",      # Peek or import failed
}

# Human-readable status descriptions (shown as the status dot's tooltip).
STATUS_DESCRIPTIONS = {
    "pending": "Not peeked yet",
    "peeked": "Peeked — dimensions read",
    "processed": "Imported successfully",
    "error": "Error",
}

# Width of the "Original name" column in the Parameter panel.
PARAM_ORIG_WIDTH = 150

# Figure module settings: scatter color modes and palette choices.
FIGURE_COLOR_MODES = [
    ("Density", "density"),
    ("Parameter", "parameter"),
    ("Solid", "solid"),
]
FIGURE_PALETTES = [
    "viridis", "plasma", "magma", "inferno", "cividis",
    "turbo", "jet", "coolwarm", "grayscale",
]

# Figure module histogram display modes.
FIGURE_HISTOGRAM_MODES = [
    ("Histogram", "histogram"),
    ("Curve fit", "curve"),
    ("Overlap", "overlap"),
]

# Scaling (map) module: available transforms.
SCALING_METHODS = ["linear", "log10", "arcsinh"]


def _apply_scale_transform(x, config):
    """Apply a scaling transform (linear/log10/arcsinh) to a numeric array.

    log10 and arcsinh are applied to the magnitude with the sign restored, so
    negatives stay negative and zero stays zero.
    """
    import numpy as np
    if config is None:
        return x
    method = config.get("method", "linear")
    if method == "log10":
        abs_x = np.abs(x)
        with np.errstate(divide="ignore", invalid="ignore"):
            y = np.log10(abs_x)
        y = np.where(abs_x == 0, 0.0, y)  # zero stays zero
        return np.sign(x) * y
    if method == "arcsinh":
        cofactor = config.get("param")
        try:
            cofactor = float(cofactor) if cofactor else 5.0
        except (TypeError, ValueError):
            cofactor = 5.0
        abs_x = np.abs(x)
        y = np.arcsinh(abs_x / cofactor)
        return np.sign(x) * y
    return x


def _apply_scale_transforms(x, configs):
    """Apply a chain of scaling transforms (in order) to a numeric array."""
    for config in (configs or []):
        x = _apply_scale_transform(x, config)
    return x


def _point_in_polygon(x, y, points):
    """Vectorized ray-casting point-in-polygon test (boolean mask)."""
    import numpy as np
    px = np.asarray([p[0] for p in points], dtype=float)
    py = np.asarray([p[1] for p in points], dtype=float)
    n = len(px)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    inside = np.zeros(x.shape, dtype=bool)
    for i in range(n):
        xi, yi = px[i], py[i]
        xj, yj = px[(i + 1) % n], py[(i + 1) % n]
        cond = (yi > y) != (yj > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
        inside ^= cond & (x < x_cross)
    return inside


def _gate_mask(x, y, gate):
    """Return a boolean mask for the points inside `gate` (in data space)."""
    import numpy as np
    t = gate.get("type")
    if t == "rect":
        x0, y0 = gate["pos"]
        w, h = gate["size"]
        return (x >= x0) & (x <= x0 + w) & (y >= y0) & (y <= y0 + h)
    if t == "ellipse":
        x0, y0 = gate["pos"]
        w, h = gate["size"]
        cx = x0 + w / 2.0
        cy = y0 + h / 2.0
        rx = w / 2.0
        ry = h / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0
    if t == "polygon":
        return _point_in_polygon(x, y, gate["points"])
    if t == "quadrant":
        vx = gate.get("vx", 0.0)
        hy = gate.get("hy", 0.0)
        q = gate.get("quadrant", 1)
        if q == 1:
            return (x >= vx) & (y >= hy)
        if q == 2:
            return (x < vx) & (y >= hy)
        if q == 3:
            return (x < vx) & (y < hy)
        if q == 4:
            return (x >= vx) & (y < hy)
    if t == "range":
        x1 = min(gate["x1"], gate["x2"])
        x2 = max(gate["x1"], gate["x2"])
        return (x >= x1) & (x <= x2)
    if t == "split":
        vx = gate.get("vx", 0.0)
        if gate.get("side") == "left":
            return x < vx
        return x >= vx
    if t == "skewed_quadrant":
        cx, cy = gate["center"]
        vx = x - cx
        vy = y - cy
        dirs = []
        for key in ("p_right", "p_up", "p_left", "p_down"):
            px, py = gate[key]
            dirs.append((px - cx, py - cy))
        d_r, d_u, d_l, d_dn = dirs

        def wedge(a, b):
            # a point is in the wedge from arm a to arm b (counter-clockwise)
            return (a[0] * vy - a[1] * vx >= 0) & (vx * b[1] - vy * b[0] >= 0)

        q = gate.get("quadrant", 1)
        if q == 1:
            return wedge(d_r, d_u)
        if q == 2:
            return wedge(d_u, d_l)
        if q == 3:
            return wedge(d_l, d_dn)
        if q == 4:
            return wedge(d_dn, d_r)
    return np.ones(x.shape, dtype=bool)


def _figure_colormap(name: str):
    """Return a pyqtgraph ColorMap for a named palette, with a fallback."""
    import pyqtgraph as pg
    try:
        return pg.colormap.get(name)
    except Exception:
        return pg.ColorMap([0.0, 1.0], [(0, 0, 255), (255, 0, 0)])


_SUPERSCRIPT = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def _format_sci(value: float) -> str:
    """Format a number as mantissa×10^exponent with a superscript exponent."""
    if value == 0:
        return "0"
    mant, exp = f"{value:.1e}".split("e")
    return f"{mant}×10{str(int(exp)).translate(_SUPERSCRIPT)}"


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
            # Anchor each tick at the spine so it points outward without crossing.
            if self.orientation in ("left", "right"):
                spine = axisSpec[1].x()
                tickSpecs = [(pen, pg.Point(spine, p1.y()), p2)
                             for pen, p1, p2 in tickSpecs]
            else:
                spine = axisSpec[1].y()
                tickSpecs = [(pen, pg.Point(p1.x(), spine), p2)
                             for pen, p1, p2 in tickSpecs]
            return axisSpec, tickSpecs, textSpecs

    ax = _SciAxis(orientation)
    ax.setStyle(tickLength=5)  # positive -> outward
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
            self.fitBtn = pg.ButtonItem(_letter_pixmap("F"), 14, self)
            self.fitBtn.clicked.connect(lambda _=None: self._fit_clicked())

        def _fit_clicked(self):
            if self._on_fit is not None:
                self._on_fit()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            w = self.autoBtn.boundingRect().width()
            y = self.size().height() - self.fitBtn.boundingRect().height()
            self.fitBtn.setPos(w + 2, y)

    return _FitPlotItem()


_GATE_PEN = "#2f81f7"
_GATE_HOVER_PEN = "#1555b3"
_POLY_CLOSE_RADIUS = 12  # pixels: click this close to the first anchor to close


def _gate_roi_pens() -> dict:
    """Pens for a gate ROI: blue edge, darker+thicker blue on hover."""
    return dict(
        pen=pg.mkPen(_GATE_PEN, width=2),
        hoverPen=pg.mkPen(_GATE_HOVER_PEN, width=3),
        handlePen=pg.mkPen(_GATE_PEN, width=1),
        handleHoverPen=pg.mkPen(_GATE_HOVER_PEN, width=2),
    )


class _GateViewBox(pg.ViewBox):
    """Scatter ViewBox that draws gate ROIs (rectangle / ellipse / polygon)."""

    def __init__(self, on_gate_created, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_gate_created = on_gate_created
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

    def set_gate_tool(self, tool) -> None:
        self._cancel_preview()
        self._tool = tool
        self._polygon_pts = []
        self._range_x1 = None

    def _cancel_preview(self) -> None:
        if self._preview is not None:
            self.removeItem(self._preview)
            self._preview = None
        if self._rubber is not None:
            self.removeItem(self._rubber)
            self._rubber = None
        if self._first_highlight is not None:
            self.removeItem(self._first_highlight)
            self._first_highlight = None
        if self._range_anchor is not None:
            self.removeItem(self._range_anchor)
            self._range_anchor = None
        if self._range_band is not None:
            self.removeItem(self._range_band)
            self._range_band = None
        if self._range_arrow is not None:
            self.removeItem(self._range_arrow)
            self._range_arrow = None
        self._start = None
        self._polygon_pts = []
        self._range_x1 = None
        self._range_y1 = None

    def _pt(self, ev):
        return self.mapSceneToView(ev.scenePos())

    # -- rectangle / ellipse (click-drag) -----------------------------------
    def mouseDragEvent(self, ev, axis=None) -> None:
        if self._tool in ("rect", "ellipse"):
            if self._preview is None:
                start = self.mapSceneToView(ev.buttonDownScenePos())
                self._start = start
                self._preview = self._make_shape(self._tool, (start.x(), start.y()), (0.0, 0.0))
                self.addItem(self._preview)
            p = self._pt(ev)
            x0, y0 = self._start.x(), self._start.y()
            x1, y1 = p.x(), p.y()
            pos = (min(x0, x1), min(y0, y1))
            size = (abs(x1 - x0), abs(y1 - y0))
            self._preview.setPos(pos)
            self._preview.setSize(size)
            if ev.isFinish():
                roi = self._preview
                self._preview = None
                self._start = None
                if roi.size().x() > 0 and roi.size().y() > 0:
                    self._on_gate_created(self._tool, roi)
                else:
                    self.removeItem(roi)
            ev.accept()
            return
        super().mouseDragEvent(ev, axis)

    # -- polygon / quadrant / split / range / skewed-quadrant ----------------
    def mouseClickEvent(self, ev) -> None:
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
                pen=pg.mkPen(_GATE_PEN, width=2, style=Qt.PenStyle.DashLine),
            )
            self.addItem(self._range_anchor)
        if self._range_band is None:
            self._range_band = self._make_preview_item(
                pen=pg.mkPen(_GATE_PEN, width=2, style=Qt.PenStyle.DashLine),
            )
            self.addItem(self._range_band)
        if self._range_arrow is None:
            self._range_arrow = pg.ArrowItem(
                angle=0, headLen=10, tipAngle=30, tailLen=None,
                brush=pg.mkBrush(_GATE_PEN), pen=None, pxMode=True,
            )
            self.addItem(self._range_arrow)
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
            roi = _GatePolygonROI(pts, closed=True, **_gate_roi_pens())
            self.addItem(roi)
            self._on_gate_created("polygon", roi)
        else:
            self._cancel_preview()

    def hoverEvent(self, ev) -> None:
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
                pen=pg.mkPen(_GATE_PEN, width=2),
                symbol="o", symbolSize=9, symbolBrush=_GATE_PEN,
            )
            self.addItem(self._preview)
        if self._rubber is None:
            self._rubber = self._make_preview_item(
                pen=pg.mkPen(_GATE_PEN, width=1, style=Qt.PenStyle.DashLine),
            )
            self.addItem(self._rubber)
        if self._first_highlight is None:
            self._first_highlight = self._make_preview_item(
                pen=None,
                symbol="o", symbolSize=15,
                symbolPen=pg.mkPen(_GATE_HOVER_PEN, width=2.5),
                symbolBrush=pg.mkBrush(None),
            )
            self.addItem(self._first_highlight)

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
            return pg.EllipseROI(pos, size, **_gate_roi_pens())
        return pg.RectROI(pos, size, **_gate_roi_pens())


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
        for seg in self.segments:
            try:
                seg.sigClicked.disconnect(self.segmentClicked)
            except (TypeError, RuntimeError):
                pass
        for h in self.handles:
            h["item"].setDeletable(False)

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
        """Empty hit region: the rays span far beyond the data, so the default
        shape (== boundingRect) would cover the whole plot and swallow every
        later click, blocking new gates. Only the five handles are interactive."""
        return QPainterPath()


class _ClickFilter(QObject):
    """Select a plot card when its widget (or embedded plot) is clicked."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._callback()
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


class _PlotDragHandle(QFrame):
    """Title bar of a plot card (drag-to-move is handled by the card proxy)."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("plotTitle")
        self.setStyleSheet(
            "#plotTitle { background: #eef2f7; border: none; border-radius: 3px; }"
        )
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        tb = QHBoxLayout(self)
        tb.setContentsMargins(6, 3, 6, 3)
        tb.setSpacing(4)
        tb.addWidget(QLabel(f"<b>{title}</b>"))
        tb.addStretch()
        hint = QLabel("⋮⋮")
        hint.setStyleSheet("color: #9aa0a6;")
        tb.addWidget(hint)


class _CardProxy(QGraphicsProxyWidget):
    """A card proxy that supports dragging the card by its title bar."""

    TITLE_HEIGHT = 30

    def __init__(self, on_select, on_drop) -> None:
        super().__init__()
        self._on_select = on_select
        self._on_drop = on_drop
        self._dragging = False
        self._press_scene = None
        self._start_pos = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.pos().y() <= self.TITLE_HEIGHT:
            self._dragging = True
            self._press_scene = event.scenePos()
            self._start_pos = self.pos()
            self.setZValue(1)
            self._on_select()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.setPos(self._start_pos + (event.scenePos() - self._press_scene))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.setZValue(0)
            self._on_drop(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ZoomableGraphicsView(QGraphicsView):
    """A canvas view that zooms the whole canvas with the mouse wheel."""

    def __init__(self, scene) -> None:
        super().__init__(scene)
        self.enter_callback = None
        self.delete_callback = None
        self.unfocus_callback = None
        self.plot_wheel_cb = None
        self.plot_pan_start_cb = None
        self.plot_pan_move_cb = None
        self.plot_pan_end_cb = None
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#eef2f7"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.Antialiasing)

    def wheelEvent(self, event) -> None:
        # Route the wheel to a focused plot, else zoom the canvas.
        if self.plot_wheel_cb is not None:
            item = self.itemAt(event.position().toPoint())
            if self.plot_wheel_cb(item, event.angleDelta().y()):
                event.accept()
                return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            scene_pos = self.mapToScene(event.position().toPoint())
            if self.plot_pan_start_cb is not None and self.plot_pan_start_cb(item, scene_pos):
                event.accept()
                return
            if item is None and self.unfocus_callback is not None:
                self.unfocus_callback()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.plot_pan_move_cb is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            if self.plot_pan_move_cb(scene_pos):
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.plot_pan_end_cb is not None and self.plot_pan_end_cb():
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.enter_callback is not None:
                self.enter_callback()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.delete_callback is not None:
                self.delete_callback()
            event.accept()
            return
        super().keyPressEvent(event)


class _FigureCard(QFrame):
    """A bordered plot card (click to select, double-click to focus)."""

    _BASE = "#plotCard { background: #ffffff; border: 1px solid #c9d5e2; border-radius: 6px; }"
    _SELECTED = "#plotCard { background: #ffffff; border: 2px solid #4285f4; border-radius: 6px; }"
    _FOCUSED = "#plotCard { background: #ffffff; border: 2px solid #34a853; border-radius: 6px; }"

    def __init__(self, title: str, plot_widget, index: int, select_callback) -> None:
        super().__init__()
        self._index = index
        self._select_callback = select_callback
        self._plot_widget = plot_widget
        self._proxy = None
        self._selected = False
        self._focused = False
        self.setObjectName("plotCard")
        self.setMinimumSize(200, 200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(4)
        self._title_bar = _PlotDragHandle(title)
        lay.addWidget(self._title_bar)
        lay.addWidget(plot_widget, 1)
        self._apply_style()

    def _apply_style(self) -> None:
        if self._focused:
            self.setStyleSheet(self._FOCUSED)
        elif self._selected:
            self.setStyleSheet(self._SELECTED)
        else:
            self.setStyleSheet(self._BASE)

    def index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index

    def plot_widget(self):
        return self._plot_widget

    def proxy(self):
        return self._proxy

    def select(self) -> None:
        self._select_callback(self._index)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._apply_style()


def _format_number(value) -> str:
    """Format a number compactly (no trailing zeros) for the range fields."""
    if value is None:
        return ""
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.10g}"


class _SelectAllFilter(QObject):
    """Select all text after a mouse click into the line edit."""

    def __init__(self, line_edit) -> None:
        super().__init__()
        self._line_edit = line_edit

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self._line_edit.selectAll)
        return False


class _PinnedFirstTabBar(QTabBar):
    """A tab bar whose first tab can be selected but not dragged to reorder."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pressed_index = -1

    def mousePressEvent(self, event) -> None:
        self._pressed_index = self.tabAt(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._pressed_index = -1

    def mouseMoveEvent(self, event) -> None:
        if self._pressed_index == 0:
            return  # block dragging the pinned first (Workflow) tab
        super().mouseMoveEvent(event)


class _SizeField(QWidget):
    """Integer size field: a plain line edit with ±10 step buttons."""

    def __init__(self, value, on_change, minimum: int = 200, maximum: int = 4000) -> None:
        super().__init__()
        self._value = int(value)
        self._on_change = on_change
        self._min = minimum
        self._max = maximum
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        self.edit = QLineEdit()
        self.edit.setFixedWidth(56)
        self.edit.setText(str(self._value))
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.edit.editingFinished.connect(self._commit)
        # Select all after a click so typing replaces the old value.
        self._filter = _SelectAllFilter(self.edit)
        self.edit.installEventFilter(self._filter)
        lay.addWidget(self.edit)

        btns = QVBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(0)
        self._up = QToolButton()
        self._up.setText("▲")
        self._up.setFixedSize(16, 12)
        self._up.setCursor(Qt.CursorShape.PointingHandCursor)
        self._up.clicked.connect(lambda: self._step(10))
        self._down = QToolButton()
        self._down.setText("▼")
        self._down.setFixedSize(16, 12)
        self._down.setCursor(Qt.CursorShape.PointingHandCursor)
        self._down.clicked.connect(lambda: self._step(-10))
        btns.addWidget(self._up)
        btns.addWidget(self._down)
        lay.addLayout(btns)

    def value(self) -> int:
        return self._value

    def set_enabled(self, enabled: bool) -> None:
        self.edit.setEnabled(enabled)
        self._up.setEnabled(enabled)
        self._down.setEnabled(enabled)

    def _clamp(self, value) -> int:
        return max(self._min, min(self._max, int(value)))

    def _step(self, delta: int) -> None:
        self._value = self._clamp(self._value + delta)
        self.edit.setText(str(self._value))
        if self._on_change is not None:
            self._on_change(self._value)

    def _commit(self) -> None:
        try:
            self._value = self._clamp(int(self.edit.text().strip()))
        except (ValueError, TypeError):
            self.edit.setText(str(self._value))
            return
        self.edit.setText(str(self._value))
        if self._on_change is not None:
            self._on_change(self._value)


class _LogStepSpinBox(QWidget):
    """Narrow numeric field: text shown as typed; ▲/▼ step ×10 / ÷10."""

    def __init__(self, value, on_change) -> None:
        super().__init__()
        self._value = value
        self._on_change = on_change
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        self.edit = QLineEdit()
        self.edit.setFixedWidth(64)
        self.edit.setText(_format_number(value))
        self.edit.editingFinished.connect(self._commit_edit)
        self.edit.returnPressed.connect(self._commit_edit)
        lay.addWidget(self.edit)

        btns = QVBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(0)
        self._up = QToolButton()
        self._up.setText("▲")
        self._up.setFixedSize(16, 12)
        self._up.setAutoRaise(True)
        self._up.setCursor(Qt.CursorShape.PointingHandCursor)
        self._up.clicked.connect(lambda: self._step(10.0))
        self._down = QToolButton()
        self._down.setText("▼")
        self._down.setFixedSize(16, 12)
        self._down.setAutoRaise(True)
        self._down.setCursor(Qt.CursorShape.PointingHandCursor)
        self._down.clicked.connect(lambda: self._step(0.1))
        btns.addWidget(self._up)
        btns.addWidget(self._down)
        lay.addLayout(btns)

    def value(self):
        return self._value

    def set_enabled(self, enabled: bool) -> None:
        self.edit.setEnabled(enabled)
        self._up.setEnabled(enabled)
        self._down.setEnabled(enabled)

    def set_value(self, value) -> None:
        self._value = value
        self.edit.setText(_format_number(value))

    def _current_value(self) -> float:
        try:
            return float(self.edit.text().strip())
        except (ValueError, TypeError):
            return 0.0

    def _step(self, factor: float) -> None:
        base = 1.0 if self.edit.text().strip() == "" else self._current_value()
        self._value = base * factor
        self.edit.setText(_format_number(self._value))
        if self._on_change is not None:
            self._on_change(self._value)

    def _commit_edit(self) -> None:
        text = self.edit.text().strip()
        try:
            self._value = float(text)
        except (ValueError, TypeError):
            self.edit.setText(_format_number(self._value))
            return
        if self._on_change is not None:
            self._on_change(self._value)


def _task_color(task_type: str) -> str:
    """Return the color for a task type."""
    if task_type == "root":
        return ROOT_COLOR
    return TASK_TYPES.get(task_type, ("Task", FALLBACK_COLOR))[1]


# Module kinds: every non-import task is a map, generate, or visualize module.
# kind is derived from type (not stored in the manifest), so old projects need
# no migration.
MODULE_KINDS = {
    "gating": "map",
    "scale": "map",
    "rename": "map",
    "compensation": "map",
    "unmix": "map",
    "segmentation": "generate",
    "figure": "visualize",
    "spectrum": "visualize",
}


def _task_kind(task_type: str) -> str:
    """Return a task's kind: import / map / generate / visualize."""
    if task_type == "import":
        return "import"
    return MODULE_KINDS.get(task_type, "map")


# Open-module registry states: a module is unmodified, modified-but-unsaved, or
# just saved (transient, used to trigger a downstream update before returning
# to unmodified).
MODULE_STATE_UNMODIFIED = "unmodified"
MODULE_STATE_MODIFIED = "modified"
MODULE_STATE_SAVED = "saved"


def _task_is_locked(task: dict) -> bool:
    """True if the task (or any descendant) is a generate module with saved data."""
    if (
        _task_kind(task.get("type", "")) == "generate"
        and task.get("output", {}).get("saved")
    ):
        return True
    return any(_task_is_locked(child) for child in task.get("children", []))


class MainWindow(QMainWindow):
    """Main window: File/Help menu bar and a single tabbed workspace."""

    def __init__(self) -> None:
        super().__init__()
        self.project = None
        self._clipboard = None
        self._import_files = []
        self._processed_files = []
        self._column_index = {}
        self._updating_checks = False
        self._updating_sample = False
        self._current_entry = None
        self._module_registry = {}
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self._build_menu_bar()
        self._build_main_area()
        self.statusBar().showMessage("Ready")

    def closeEvent(self, event) -> None:
        """Warn before closing the app if any open module has unsaved changes."""
        if self._has_unsaved_module_changes():
            reply = QMessageBox.question(
                self,
                "Close Smage",
                "There are unsaved changes.\nReally close? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()

    def _has_unsaved_module_changes(self) -> bool:
        for index in range(1, self.tabs.count()):  # skip Workflow at index 0
            widget = self.tabs.widget(index)
            if (
                getattr(widget, "_fig_dirty", False)
                or getattr(widget, "_scaling_dirty", False)
                or getattr(widget, "_gating_dirty", False)
            ):
                return True
        return False

    # ----- menu bar -------------------------------------------------
    def _build_menu_bar(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")

        new_action = QAction("New Project", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project…", self)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        save_as_action = QAction("Save Project As…", self)
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(QAction("About Smage", self))

    # ----- project actions ------------------------------------------
    def _new_project(self) -> None:
        """Create a new project folder (name.smage with project.json + data/)."""
        parent_dir = QFileDialog.getExistingDirectory(
            self, "Choose a location for the project"
        )
        if not parent_dir:
            return

        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        name = name.strip()
        if not ok or not name:
            return

        try:
            self.project = Project.create(Path(parent_dir), name)
        except OSError as error:
            QMessageBox.warning(self, "New Project", f"Could not create project:\n{error}")
            return

        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._column_index = self.project.load_columns()
        self._reset_module_tabs()
        self._load_import_files()
        self._refresh_workflow_tree()
        self.statusBar().showMessage(f"Project created: {self.project.path}")

    def _open_project(self) -> None:
        """Open an existing project folder."""
        folder = QFileDialog.getExistingDirectory(self, "Open Project")
        if not folder:
            return
        try:
            self.project = Project.load(Path(folder))
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "Open Project", f"Could not open project:\n{error}")
            return

        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._column_index = self.project.load_columns()
        self._reset_module_tabs()
        self._load_import_files()
        self._refresh_workflow_tree()
        self.statusBar().showMessage(f"Opened: {self.project.path}")

    def _save_project_as(self) -> None:
        """Save the current project under a new name/location."""
        if self.project is None:
            QMessageBox.information(self, "Save Project As", "No project is open yet.")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "Choose a location")
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(
            self, "Save Project As", "Project name:", text=self.project.name
        )
        name = name.strip()
        if not ok or not name:
            return

        try:
            new_project = Project.create(Path(parent_dir), name)
            new_project.samples = copy.deepcopy(self.project.samples)
            new_project.workflow = copy.deepcopy(self.project.workflow)
            for item in self.project.data_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, new_project.data_dir / item.name)
            new_project.save()
        except OSError as error:
            QMessageBox.warning(self, "Save Project As", f"Could not save project:\n{error}")
            return

        self.project = new_project
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self.statusBar().showMessage(f"Saved as: {self.project.path}")

    def _reset_module_tabs(self) -> None:
        """Close all module tabs, keeping only the Workflow tab."""
        while self.tabs.count() > 1:
            self.tabs.removeTab(self.tabs.count() - 1)

    def _import_task(self):
        """Return the fixed 'import' task, or None."""
        if self.project is None:
            return None
        for task in self.project.workflow.get("tasks", []):
            if task.get("type") == "import":
                return task
        return None

    def _import_locked(self) -> bool:
        """True if the Data Import task is locked (a downstream generate saved data)."""
        task = self._import_task()
        return task is not None and _task_is_locked(task)

    def _find_task_by_id(self, task_id: str | None):
        """Return the workflow task dict with the given id, or None."""
        if self.project is None or not task_id:
            return None

        def walk(tasks):
            for task in tasks:
                if task.get("id") == task_id:
                    return task
                found = walk(task.get("children", []))
                if found is not None:
                    return found
            return None

        return walk(self.project.workflow.get("tasks", []))

    def _load_import_files(self) -> None:
        """Load the Data Import file list and processed results from the project."""
        task = self._import_task()
        if task is None:
            self._import_files = []
            self._processed_files = []
            return
        params = task.setdefault("params", {})
        files = params.get("files")
        if files is None:
            files = []
            params["files"] = files
        self._import_files = files
        for entry in self._import_files:
            entry.setdefault("id", uuid4().hex)
        processed = params.get("processed")
        if processed is None:
            processed = []
            params["processed"] = processed
        self._processed_files = processed

    def _persist_import_files(self) -> None:
        """Save the project (the import file list lives in the import task's params)."""
        if self.project is not None:
            self.project.save()

    # ----- main area: a single tabbed workspace ---------------------
    def _build_main_area(self) -> None:
        self.tabs = QTabWidget()
        # Module tabs (everything except Workflow) can be dragged to reorder.
        self.tabs.setTabBar(_PinnedFirstTabBar())
        self.tabs.setMovable(True)
        self.setCentralWidget(self.tabs)

        self._build_workflow_tab()

    # ----- workflow tree --------------------------------------------
    def _build_workflow_tab(self) -> None:
        workflow_panel = QWidget()
        wl = QVBoxLayout(workflow_panel)
        wl.setContentsMargins(8, 8, 8, 8)

        self.workflow_tree = QTreeWidget()
        self.workflow_tree.setHeaderHidden(True)
        self.workflow_tree.setIndentation(22)
        wl.addWidget(self.workflow_tree)

        self.tabs.addTab(workflow_panel, "Workflow")
        self._refresh_workflow_tree()

    def _task_item_widget(
        self, name: str, color: str, is_root: bool = False,
        on_add_child=None, on_actions=None, task_type: str | None = None,
        task_id: str | None = None, locked: bool = False,
    ) -> QWidget:
        """Build a card (icon + name + action buttons) for one task."""
        card = QFrame()
        card.setObjectName("taskCard")
        card.setStyleSheet(
            "QFrame#taskCard { background: #ffffff; "
            "border: 1px solid #dbe3ec; border-radius: 6px; }"
        )

        lay = QHBoxLayout(card)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        icon = QLabel()
        icon.setPixmap(_dot_pixmap(color))
        icon.setFixedSize(16, 16)
        lay.addWidget(icon)

        title = QLabel(name)
        if locked:
            title.setStyleSheet("color: #9aa0a6;")
        lay.addWidget(title)
        lay.addSpacing(12)

        if not is_root:
            buttons = QHBoxLayout()
            buttons.setSpacing(2)
            buttons.setContentsMargins(0, 0, 0, 0)

            open_btn = QPushButton("Open")
            open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_btn.clicked.connect(
                lambda _=False, tid=task_id, t=task_type, n=name: self._open_module(tid, t, n)
            )
            buttons.addWidget(open_btn)

            actions_btn = QPushButton("Actions ▾")
            actions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_actions is not None:
                actions_btn.clicked.connect(on_actions)
            buttons.addWidget(actions_btn)

            add_btn = QPushButton("+")
            add_btn.setFixedWidth(36)
            add_btn.setToolTip("Add child module")
            add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            add_btn.setEnabled(not locked)
            if on_add_child is not None:
                add_btn.clicked.connect(on_add_child)
            buttons.addWidget(add_btn)

            lay.addLayout(buttons)

        if locked:
            lock_icon = QLabel()
            lock_icon.setPixmap(_lock_pixmap())
            lock_icon.setFixedSize(14, 14)
            lock_icon.setToolTip("Locked — a downstream generate module has saved data")
            lay.addWidget(lock_icon)

        # Wrap the card so it only takes its content width (left-aligned),
        # with a little vertical margin so cards don't touch.
        container = QWidget()
        wrap = QVBoxLayout(container)
        wrap.setContentsMargins(0, 2, 0, 2)
        wrap.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
        return container

    def _show_actions_menu(self, task_def: dict, parent_task_def) -> None:
        """Show the task actions menu."""
        locked = _task_is_locked(task_def)
        menu = QMenu(self)

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(lambda: self._copy_task(task_def, False))
        menu.addAction(copy_action)

        copy_branch_action = QAction("Copy branch", self)
        copy_branch_action.triggered.connect(lambda: self._copy_task(task_def, True))
        menu.addAction(copy_branch_action)

        menu.addSeparator()

        paste_action = QAction("Paste", self)
        paste_action.setEnabled(not locked)
        paste_action.triggered.connect(lambda: self._paste_task(task_def, False))
        menu.addAction(paste_action)

        paste_branch_action = QAction("Paste branch", self)
        paste_branch_action.setEnabled(not locked)
        paste_branch_action.triggered.connect(lambda: self._paste_task(task_def, True))
        menu.addAction(paste_branch_action)

        menu.addSeparator()

        rename_action = QAction("Rename", self)
        rename_action.setEnabled(not locked)
        rename_action.triggered.connect(lambda: self._rename_task(task_def))
        menu.addAction(rename_action)

        delete_action = QAction("Delete", self)
        delete_action.setEnabled(not locked)
        delete_action.triggered.connect(
            lambda: self._delete_task(task_def, parent_task_def, False)
        )
        menu.addAction(delete_action)

        delete_branch_action = QAction("Delete branch", self)
        delete_branch_action.setEnabled(not locked)
        delete_branch_action.triggered.connect(
            lambda: self._delete_task(task_def, parent_task_def, True)
        )
        menu.addAction(delete_branch_action)

        menu.exec(QCursor.pos())

    def _copy_task(self, task_def: dict, with_branch: bool = False) -> None:
        """Copy a single task (or, with_branch, its whole branch) to the clipboard."""
        copied = copy.deepcopy(task_def)
        if not with_branch:
            copied["children"] = []
        self._clipboard = copied

    def _paste_task(self, task_def: dict, with_branch: bool = False) -> None:
        """Paste a single task (or, with_branch, its whole branch) as a child."""
        if self._clipboard is None:
            return
        new_task = copy.deepcopy(self._clipboard)
        if not with_branch:
            new_task["children"] = []
        self._reassign_ids(new_task)
        task_def.setdefault("children", []).append(new_task)
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()

    def _reassign_ids(self, task: dict) -> None:
        """Give a task (and its subtree) fresh unique ids."""
        task["id"] = f"{task.get('type', 'task')}-{uuid4().hex[:8]}"
        for child in task.get("children", []):
            self._reassign_ids(child)

    def _rename_task(self, task_def: dict) -> None:
        """Rename a task."""
        name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=task_def.get("name", "")
        )
        name = name.strip()
        if not ok or not name:
            return
        task_def["name"] = name
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()

    def _delete_task(self, task_def: dict, parent_task_def, with_branch: bool = False) -> None:
        """Delete a single task (or, with_branch, its whole branch)."""
        name = task_def.get("name", "task")
        message = (
            f"Delete branch '{name}' and its sub-modules?"
            if with_branch
            else f"Delete '{name}'?"
        )
        reply = QMessageBox.question(
            self,
            "Delete",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        siblings = (
            self.project.workflow.get("tasks", [])
            if parent_task_def is None
            else parent_task_def.get("children", [])
        )
        if task_def not in siblings:
            return

        index = siblings.index(task_def)
        if with_branch:
            del siblings[index]
        else:
            # Remove only this task; promote its children into its place.
            siblings[index:index + 1] = task_def.get("children", [])

        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()

    def _add_task(self, parent, name: str, color: str, is_root: bool = False,
                  on_add_child=None, on_actions=None, task_type: str | None = None,
                  task_id: str | None = None, locked: bool = False):
        """Add a task card to the tree and return its item."""
        item = QTreeWidgetItem([""])
        if parent is None:
            self.workflow_tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self.workflow_tree.setItemWidget(
            item, 0, self._task_item_widget(
                name, color, is_root, on_add_child, on_actions, task_type, task_id,
                locked,
            )
        )
        return item

    def _refresh_workflow_tree(self) -> None:
        """Rebuild the workflow tree from the current project's workflow."""
        self.workflow_tree.clear()
        root = self._add_task(None, "Workflow", ROOT_COLOR, is_root=True)
        root.setExpanded(True)

        if self.project is None:
            return
        for task in self.project.workflow.get("tasks", []):
            self._add_task_from_definition(root, task, None)

    def _add_task_from_definition(self, parent, task_def: dict, parent_task_def=None):
        """Add a task card (and its children) from a workflow definition dict."""
        task_type = task_def.get("type", "")
        name = task_def.get("name") or TASK_TYPES.get(task_type, ("Task",))[0]
        color = _task_color(task_type)
        item = self._add_task(
            parent,
            name,
            color,
            on_add_child=lambda checked=False, t=task_def: self._show_add_module_dialog(t),
            on_actions=lambda checked=False, t=task_def, p=parent_task_def: self._show_actions_menu(t, p),
            task_type=task_type,
            task_id=task_def.get("id", ""),
            locked=_task_is_locked(task_def),
        )
        item.setExpanded(True)
        for child in task_def.get("children", []):
            self._add_task_from_definition(item, child, task_def)

    # ----- add-module dialog ----------------------------------------
    def _show_add_module_dialog(self, parent_task_def: dict) -> None:
        """Open the Add-Module dialog; the chosen module becomes a child task."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Module")
        grid = QGridLayout(dialog)

        for i, mtype in enumerate(ADDABLE_TYPES):
            name, color = TASK_TYPES[mtype]
            btn = QToolButton()
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setText(name)
            btn.setIcon(QIcon(_dot_pixmap(color)))
            btn.setIconSize(QSize(28, 28))
            btn.setFixedSize(96, 72)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda checked=False, t=mtype: self._add_module(parent_task_def, t, dialog)
            )
            grid.addWidget(btn, i // 4, i % 4)

        dialog.exec()

    def _add_module(self, parent_task_def: dict, mtype: str, dialog: QDialog) -> None:
        """Add a placeholder module of the given type as a child task."""
        name, _ = TASK_TYPES[mtype]
        new_task = {
            "id": f"{mtype}-{uuid4().hex[:8]}",
            "type": mtype,
            "name": name,
            "params": {},
            "children": [],
        }
        if _task_kind(mtype) == "generate":
            new_task["output"] = {"saved": False}
        parent_task_def.setdefault("children", []).append(new_task)
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()
        dialog.accept()

    # ----- module tabs ----------------------------------------------
    def _build_module_toolbar(self, name: str, page: QWidget, include_save: bool = True,
                              locked: bool = False, task_id: str | None = None) -> QFrame:
        """Build the module toolbar (title + Save/Settings/Close, plus a lock badge)."""
        bar = QFrame()
        bar.setObjectName("moduleBar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(4)
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_id, name)}</b>"))
        if locked:
            lock = QLabel()
            lock.setPixmap(_lock_pixmap())
            lock.setFixedSize(14, 14)
            lock.setToolTip("Locked — a downstream generate module has saved data")
            tl.addWidget(lock)
            draft = QLabel("Draft (locked)")
            draft.setStyleSheet("color: #b58900; font-weight: bold;")
            tl.addWidget(draft)
        tl.addStretch()
        if include_save:
            save_btn = QPushButton("Save")
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setEnabled(not locked)
            tl.addWidget(save_btn)
        settings_btn = QPushButton("Settings")
        close_btn = QPushButton("Close")
        for b in (settings_btn, close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda _=False, p=page: self._close_module_page(p))
        tl.addWidget(settings_btn)
        tl.addWidget(close_btn)
        return bar

    def _build_module_page(self, name: str, locked: bool = False,
                           task_id: str | None = None) -> QWidget:
        """Build a generic (placeholder) module page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_module_toolbar(name, page, locked=locked, task_id=task_id))

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        if locked:
            note = QLabel("Draft (locked) — changes here are temporary and cannot be saved.")
            note.setStyleSheet("color: #b58900;")
            cl.addWidget(note)
        hint = QLabel("Module content goes here (placeholder)")
        hint.setStyleSheet("color: gray;")
        cl.addWidget(hint)
        cl.addStretch()
        layout.addWidget(content, 1)
        return page

    def _build_generate_page(self, name: str, task_def: dict) -> QWidget:
        """Build a generate-module page with Save (params) and Run (params + data)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("moduleBar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(4)
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_def.get('id'), name)}</b>"))
        lock_label = QLabel()
        lock_label.setPixmap(_lock_pixmap())
        lock_label.setFixedSize(14, 14)
        lock_label.setToolTip("Locked — generated data has been saved")
        tl.addWidget(lock_label)
        draft_label = QLabel("Draft (locked)")
        draft_label.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft_label)
        tl.addStretch()
        save_btn = QPushButton("Save")
        run_btn = QPushButton("Run")
        close_btn = QPushButton("Close")
        for b in (save_btn, run_btn, close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda _=False, p=page: self._close_module_page(p))
        tl.addWidget(save_btn)
        tl.addWidget(run_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        status = QLabel()
        status.setWordWrap(True)
        cl.addWidget(status)
        hint = QLabel("Algorithm parameters (placeholder)")
        hint.setStyleSheet("color: gray;")
        cl.addWidget(hint)
        cl.addStretch()
        layout.addWidget(content, 1)

        def refresh() -> None:
            locked_now = bool(task_def.get("output", {}).get("saved"))
            lock_label.setVisible(locked_now)
            draft_label.setVisible(locked_now)
            save_btn.setEnabled(not locked_now)
            run_btn.setEnabled(not locked_now)
            if locked_now:
                status.setText("Generated data saved ✓ — module and its upstream are locked.")
            else:
                status.setText(
                    "No generated data yet. Use Save to store parameters, "
                    "or Run to generate and save data."
                )

        save_btn.clicked.connect(lambda _=False: self._generate_save(task_def))
        run_btn.clicked.connect(lambda _=False: self._generate_run(task_def, refresh))
        refresh()
        return page

    def _generate_save(self, task_def: dict) -> None:
        """Save a generate module's parameters without generating data."""
        task_def.setdefault("output", {"saved": False})
        if self.project is not None:
            self.project.save()
        self.statusBar().showMessage("Parameters saved (no data generated)")

    def _generate_run(self, task_def: dict, refresh) -> None:
        """Generate data, save it, and lock the module and its upstream."""
        task_def["output"] = {"saved": True}
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()
        refresh()
        self.statusBar().showMessage("Data generated and saved — module locked")

    def _build_data_import_page(self) -> QWidget:
        """Build the Data Import page: Input | Parameter | Processed."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        locked = self._import_locked()
        layout.addWidget(self._build_module_toolbar("Data Import", page, include_save=False, locked=locked))

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(12)
        middle_panel = self._build_middle_panel()
        input_panel = self._build_input_panel()
        processed_panel = self._build_processed_panel()
        cl.addWidget(input_panel, 1)
        cl.addWidget(middle_panel, 1)
        cl.addWidget(processed_panel, 1)
        layout.addWidget(content, 1)
        if locked:
            self._set_import_controls_enabled(False)
        return page

    def _build_panel_frame(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("modulePanel")
        panel.setStyleSheet(
            "QFrame#modulePanel { background: #ffffff; "
            "border: 1px solid #dbe3ec; border-radius: 6px; }"
        )
        return panel

    def _build_placeholder_panel(self, title: str) -> QFrame:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        hint = QLabel("(to be implemented)")
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        lay.addStretch()
        return panel

    def _build_middle_panel(self) -> QWidget:
        """Middle column: Parameter (top, 3) + Sample (bottom, 1)."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_parameter_panel(), 3)
        layout.addWidget(self._build_sample_panel(), 1)
        return container

    def _build_sample_panel(self) -> QFrame:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Sample</b>"))

        desc = QLabel("Sampling settings for the selected file.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #5a6673;")
        lay.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.sample_combo = QComboBox()
        self.sample_combo.addItem("Full sampling", "full")
        self.sample_combo.addItem("Partial random sampling", "partial")
        row.addWidget(self.sample_combo)

        self.sample_percent_spin = QSpinBox()
        self.sample_percent_spin.setRange(1, 100)
        self.sample_percent_spin.setValue(10)
        self.sample_percent_spin.setSuffix(" %")
        self.sample_percent_spin.setVisible(False)
        self.sample_percent_spin.setMaximumWidth(80)
        row.addWidget(self.sample_percent_spin)

        row.addStretch()

        self.sample_apply_btn = QPushButton("Apply to")
        self.sample_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sample_apply_btn.clicked.connect(self._apply_sampling_to_files)
        row.addWidget(self.sample_apply_btn)
        lay.addLayout(row)

        lay.addStretch()

        self.sample_combo.currentIndexChanged.connect(self._on_sampling_changed)
        self.sample_percent_spin.valueChanged.connect(self._on_sampling_changed)
        return panel

    def _build_processed_panel(self) -> QFrame:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Processed</b>"))
        self.processed_list = QListWidget()
        self.processed_list.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self.processed_list)
        self._refresh_processed_panel()
        return panel

    def _refresh_processed_panel(self) -> None:
        self.processed_list.clear()
        for record in self._processed_files:
            name = record.get("name", "")
            rows = record.get("rows", "?")
            item = QListWidgetItem(f"{name}  —  {rows} rows")
            item.setToolTip(record.get("parquet", ""))
            self.processed_list.addItem(item)

    def _set_import_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable the Data Import controls (used when the task is locked)."""
        widgets = [
            self.select_btn, self.select_all_box, self.peek_btn, self.import_btn,
            self.delete_btn, self.copy_params_btn, self.paste_params_btn,
            self.apply_params_btn, self.sample_combo, self.sample_percent_spin,
            self.sample_apply_btn,
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(enabled)

    def _build_parameter_panel(self) -> QFrame:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Parameter</b>"))

        self.parameter_summary = QLabel("Select a file to see its parameters.")
        self.parameter_summary.setWordWrap(True)
        self.parameter_summary.setStyleSheet("color: #5a6673;")
        lay.addWidget(self.parameter_summary)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self.copy_params_btn = QPushButton("Copy")
        self.paste_params_btn = QPushButton("Paste")
        self.apply_params_btn = QPushButton("Apply to")
        for b in (self.copy_params_btn, self.paste_params_btn, self.apply_params_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_params_btn.clicked.connect(self._copy_parameters)
        self.paste_params_btn.clicked.connect(self._paste_parameters)
        self.apply_params_btn.clicked.connect(self._apply_to_files)
        button_row.addWidget(self.copy_params_btn)
        button_row.addWidget(self.paste_params_btn)
        button_row.addWidget(self.apply_params_btn)
        button_row.addStretch()
        lay.addLayout(button_row)

        self.parameter_scroll = QScrollArea()
        self.parameter_scroll.setWidgetResizable(True)
        self.parameter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.parameter_scroll.setStyleSheet("background: transparent;")

        self.parameter_rows = QWidget()
        self.parameter_rows.setStyleSheet("background: transparent;")
        self.parameter_rows_layout = QVBoxLayout(self.parameter_rows)
        self.parameter_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.parameter_rows_layout.setSpacing(2)
        self.parameter_scroll.setWidget(self.parameter_rows)
        lay.addWidget(self.parameter_scroll, 1)
        return panel

    def _build_input_panel(self) -> QFrame:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Input</b>"))
        header.addStretch()
        self.select_btn = QPushButton("Select Files…")
        self.select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_btn.clicked.connect(self._select_import_files)
        header.addWidget(self.select_btn)
        lay.addLayout(header)

        self.select_all_box = QCheckBox("Select all")
        self.select_all_box.setTristate(True)
        lay.addWidget(self.select_all_box)

        self.import_list = QListWidget()
        self.import_list.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self.import_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.peek_btn = QPushButton("Peek")
        self.peek_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.peek_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.peek_btn.clicked.connect(self._peek_files)
        btn_row.addWidget(self.peek_btn, 1)
        self.import_btn = QPushButton("Import")
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.import_btn.clicked.connect(self._run_import)
        btn_row.addWidget(self.import_btn, 1)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delete_btn.clicked.connect(self._delete_selected_files)
        btn_row.addWidget(self.delete_btn, 1)
        lay.addLayout(btn_row)

        # Reset checkboxes to unchecked each time the module opens, so an
        # accidental Peek doesn't overwrite carefully edited parameter names.
        for entry in self._import_files:
            entry["checked"] = False

        self._refresh_import_list()

        self.select_all_box.stateChanged.connect(self._on_select_all_state_changed)
        self.import_list.itemChanged.connect(self._on_item_check_changed)
        self.import_list.itemSelectionChanged.connect(self._on_import_selection_changed)
        return panel

    def _select_import_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select data files",
            "",
            "Data files (*.fcs *.csv *.tif *.tiff *.parquet);;All files (*)",
        )
        if not files:
            return
        for path in files:
            self._import_files.append(
                {
                    "id": uuid4().hex,
                    "path": path,
                    "status": "pending",
                    "checked": True,
                    "dimensions": None,
                    "sampling": "full",
                    "sampling_percent": 10,
                }
            )
        self._persist_import_files()
        self._refresh_import_list()

    def _delete_selected_files(self) -> None:
        """Remove the checked files from Input and their imported files from Processed."""
        checked = [
            i for i in range(self.import_list.count())
            if self.import_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not checked:
            return

        reply = QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(checked)} file(s)?\n"
            "This will also delete their corresponding imported files (in Processed).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for i in sorted(checked, reverse=True):
            source = self._import_files[i]["path"]
            for record in list(self._processed_files):
                if record.get("source") == source:
                    parquet = record.get("parquet")
                    if parquet:
                        try:
                            Path(parquet).unlink(missing_ok=True)
                        except OSError:
                            pass
                        self._column_index.pop(self._rel_data_key(parquet), None)
            self._processed_files[:] = [
                r for r in self._processed_files if r.get("source") != source
            ]
            del self._import_files[i]

        self.project.save_columns(self._column_index)
        self._persist_import_files()
        self._refresh_import_list()
        self._refresh_processed_panel()
        self.statusBar().showMessage(f"Deleted {len(checked)} file(s)")

    def _refresh_import_list(self) -> None:
        self._updating_checks = True
        self.import_list.clear()
        name_counts = Counter(Path(e["path"]).name for e in self._import_files)
        for entry in self._import_files:
            path = entry["path"]
            status = entry.get("status", "pending")
            base = Path(path).name
            if name_counts[base] > 1:
                text = f"{base}  —  {Path(path).parent}"
            else:
                text = base

            item = QListWidgetItem(text)
            item.setToolTip(self._status_tooltip(entry))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if entry.get("checked", True)
                else Qt.CheckState.Unchecked
            )
            color = IMPORT_STATUS_COLORS.get(status, IMPORT_STATUS_COLORS["pending"])
            item.setIcon(QIcon(_dot_pixmap(color)))
            self.import_list.addItem(item)
        self._updating_checks = False
        self._update_select_all_state()

    def _status_tooltip(self, entry) -> str:
        """Tooltip describing the status dot (and the error, if any)."""
        status = entry.get("status", "pending")
        tip = STATUS_DESCRIPTIONS.get(status, status)
        if status == "error":
            error = entry.get("error", "")
            if error:
                tip += f"\n{error}"
        return tip

    def _on_select_all_state_changed(self, state: int) -> None:
        if self._updating_checks:
            return
        if state == Qt.CheckState.PartiallyChecked.value:
            return
        check_state = (
            Qt.CheckState.Checked
            if state == Qt.CheckState.Checked.value
            else Qt.CheckState.Unchecked
        )
        self._updating_checks = True
        for i in range(self.import_list.count()):
            self.import_list.item(i).setCheckState(check_state)
        self._updating_checks = False

    def _on_item_check_changed(self, item) -> None:
        if self._updating_checks:
            return
        index = self.import_list.row(item)
        if 0 <= index < len(self._import_files):
            checked = item.checkState() == Qt.CheckState.Checked
            if self._import_files[index].get("checked", True) != checked:
                self._import_files[index]["checked"] = checked
                self._persist_import_files()
        self._update_select_all_state()

    def _update_select_all_state(self) -> None:
        total = self.import_list.count()
        checked = sum(
            1
            for i in range(total)
            if self.import_list.item(i).checkState() == Qt.CheckState.Checked
        )
        if total == 0 or checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == total:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self._updating_checks = True
        self.select_all_box.setCheckState(state)
        self._updating_checks = False

    def _peek_files(self) -> None:
        """Read dimension info for each checked file, with a progress bar."""
        checked = [
            i for i in range(self.import_list.count())
            if self.import_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not checked:
            return

        reply = QMessageBox.question(
            self,
            "Peek",
            f"Peek will overwrite the parameter name settings of "
            f"{len(checked)} selected file(s). Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog("Peeking files...", "Cancel", 0, len(checked), self)
        progress.setWindowTitle("Peek")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        for step, i in enumerate(checked):
            if progress.wasCanceled():
                break
            entry = self._import_files[i]
            try:
                entry["dimensions"] = read_dimensions(entry["path"])
                entry["parameters"] = [
                    {"name": n, "extract": True, "rename": n}
                    for n in parameter_names(entry["dimensions"])
                ]
                entry["status"] = "peeked"
                entry["error"] = None
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
            progress.setValue(step + 1)
            QApplication.processEvents()

        self._persist_import_files()
        self._refresh_import_list()
        progress.close()

    def _run_import(self) -> None:
        """Import each checked file: extract+rename columns, sample, write Parquet."""
        if self.project is None:
            return
        checked = [
            i for i in range(self.import_list.count())
            if self.import_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not checked:
            return

        reply = QMessageBox.question(
            self,
            "Import",
            "Importing will overwrite already-imported files. "
            "Are you sure you want to import?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        reply = QMessageBox.question(
            self,
            "Import",
            f"You have checked {len(checked)} file(s). Are these the correct files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        not_peeked = [
            Path(self._import_files[i]["path"]).name
            for i in checked
            if not self._import_files[i].get("parameters")
        ]
        if not_peeked:
            QMessageBox.warning(
                self,
                "Import",
                "The following files have not been peeked yet and need to be "
                "peeked first:\n" + "\n".join(not_peeked),
            )
            return

        progress = QProgressDialog("Importing files...", "Cancel", 0, len(checked), self)
        progress.setWindowTitle("Import")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        for step, i in enumerate(checked):
            if progress.wasCanceled():
                break
            entry = self._import_files[i]
            try:
                out_path = self.project.data_dir / f"{entry['id']}.parquet"
                result = import_file(
                    entry["path"],
                    entry.get("parameters") or [],
                    entry.get("sampling", "full"),
                    entry.get("sampling_percent", 10),
                    out_path,
                )
                entry["status"] = "processed"
                entry["error"] = None
                entry["imported"] = {
                    "parquet": str(out_path),
                    "rows": result["rows"],
                }
                # Publish column names to the lightweight index.
                self._column_index[self._rel_data_key(out_path)] = result["columns"]
                self.project.save_columns(self._column_index)
                self._processed_files[:] = [
                    r for r in self._processed_files if r.get("source") != entry["path"]
                ]
                self._processed_files.append({
                    "name": Path(entry["path"]).name,
                    "source": entry["path"],
                    "parquet": str(out_path),
                    "rows": result["rows"],
                })
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
                entry["imported"] = None
            progress.setValue(step + 1)
            QApplication.processEvents()

        self._persist_import_files()
        self._refresh_import_list()
        self._refresh_processed_panel()
        progress.close()

    def _on_import_selection_changed(self) -> None:
        item = self.import_list.currentItem()
        if item is None:
            self._show_file_parameters(None)
            return
        index = self.import_list.row(item)
        if 0 <= index < len(self._import_files):
            self._show_file_parameters(self._import_files[index])
        else:
            self._show_file_parameters(None)

    def _show_file_parameters(self, entry) -> None:
        self._current_entry = entry
        self._show_file_sampling(entry)
        self._clear_parameter_rows()
        if entry is None:
            self.parameter_summary.setText("Select a file to see its parameters.")
            self.parameter_rows_layout.addStretch()
            return

        dims = entry.get("dimensions")
        base = Path(entry["path"]).name
        if dims is None:
            if entry.get("status") == "error":
                self.parameter_summary.setText(f"{base}\nStatus: error (Peek failed)")
            else:
                self.parameter_summary.setText(
                    f"{base}\nStatus: not peeked yet — click Peek first"
                )
            self.parameter_rows_layout.addStretch()
            return

        kind = dims.get("kind")
        if kind == "fcs":
            info = (
                f"FCS  ·  {dims.get('rows')} events  ·  "
                f"{len(dims.get('columns', []))} channels"
            )
        elif kind == "image":
            info = (
                f"Image  ·  shape {tuple(dims.get('shape', []))}  ·  "
                f"{dims.get('channels')} channels"
            )
        else:
            info = (
                f"Table  ·  {dims.get('rows')} rows  ·  "
                f"{len(dims.get('columns', []))} columns"
            )
        self.parameter_summary.setText(f"{base}\n{info}")

        params = entry.get("parameters")
        if params is None:
            params = [
                {"name": n, "extract": True, "rename": n}
                for n in parameter_names(dims)
            ]
            entry["parameters"] = params
            self._persist_import_files()

        # Column headers
        header = QWidget()
        hh = QHBoxLayout(header)
        hh.setContentsMargins(0, 2, 0, 4)
        hh.setSpacing(6)
        header_style = "font-weight: bold; color: #5a6673;"
        h_orig = QLabel("Original name")
        h_orig.setMinimumWidth(PARAM_ORIG_WIDTH)
        h_orig.setStyleSheet(header_style)
        hh.addWidget(h_orig)
        h_inc = QLabel("Extract")
        h_inc.setStyleSheet(header_style)
        hh.addWidget(h_inc)
        h_new = QLabel("Imported name")
        h_new.setStyleSheet(header_style)
        hh.addWidget(h_new, 1)
        self.parameter_rows_layout.addWidget(header)

        locked = self._import_locked()
        for param in params:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(6)

            orig_label = QLabel(param.get("name", ""))
            orig_label.setMinimumWidth(PARAM_ORIG_WIDTH)
            orig_label.setStyleSheet("color: #2c3a48;")
            h.addWidget(orig_label)

            check = QCheckBox()
            check.setChecked(param.get("extract", True))
            check.setToolTip("Extract this parameter")
            check.setEnabled(not locked)
            h.addWidget(check)

            line_edit = QLineEdit(param.get("rename", param.get("name", "")))
            line_edit.setPlaceholderText("parameter name")
            line_edit.setEnabled((not locked) and param.get("extract", True))
            line_edit.setToolTip("Rename this parameter (used after import)")
            h.addWidget(line_edit, 1)

            check.toggled.connect(
                lambda checked, pp=param, ll=line_edit:
                    self._on_param_extract_toggled(pp, ll, checked)
            )
            line_edit.editingFinished.connect(
                lambda pp=param, ll=line_edit: self._on_param_renamed(pp, ll)
            )

            self.parameter_rows_layout.addWidget(row)

        self.parameter_rows_layout.addStretch()

    def _clear_parameter_rows(self) -> None:
        while self.parameter_rows_layout.count():
            item = self.parameter_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # avoid a visible top-level window while detaching
                widget.setParent(None)
                widget.deleteLater()

    def _on_param_extract_toggled(self, param, line_edit, checked) -> None:
        param["extract"] = checked
        line_edit.setEnabled(checked)
        self._persist_import_files()

    def _on_param_renamed(self, param, line_edit) -> None:
        param["rename"] = line_edit.text().strip() or param.get("name", "")
        self._persist_import_files()

    def _copy_parameters(self) -> None:
        self._flash_button(self.copy_params_btn)
        entry = self._current_entry
        if entry is None or not entry.get("parameters"):
            QMessageBox.information(self, "Copy", "No parameters to copy. Peek a file first.")
            return
        lines = ["Original name\tExtract\tImported name"]
        for p in entry["parameters"]:
            extract = "True" if p.get("extract", True) else "False"
            lines.append(f"{p.get('name', '')}\t{extract}\t{p.get('rename', '')}")
        QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage("Parameters copied to clipboard")

    def _paste_parameters(self) -> None:
        self._flash_button(self.paste_params_btn)
        entry = self._current_entry
        if entry is None or not entry.get("parameters"):
            QMessageBox.information(self, "Paste", "No parameters to paste into. Peek a file first.")
            return

        text = QApplication.clipboard().text().strip()
        if not text:
            QMessageBox.warning(self, "Paste", "Clipboard is empty.")
            return

        rows = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) != 3:
                QMessageBox.warning(
                    self,
                    "Paste",
                    f"Format error on line {line_number}: expected 3 tab-separated "
                    f"columns (Original name, Extract, Imported name), got {len(fields)}.",
                )
                return
            rows.append(fields)

        # Skip the header row if present (e.g. from a previous Copy).
        if (
            rows
            and rows[0][0].strip().lower() == "original name"
            and rows[0][1].strip().lower() in ("extract", "include")
        ):
            rows = rows[1:]

        params = entry["parameters"]
        if len(rows) != len(params):
            QMessageBox.warning(
                self,
                "Paste",
                f"Count mismatch: this file has {len(params)} parameters, "
                f"but you pasted {len(rows)} rows.",
            )
            return

        by_name = {r[0].strip(): r for r in rows}
        if len(by_name) != len(rows):
            QMessageBox.warning(self, "Paste", "Duplicate original names in pasted data.")
            return

        pasted_names = set(by_name)
        param_names = {p.get("name", "") for p in params}
        if pasted_names != param_names:
            missing = sorted(param_names - pasted_names)
            extra = sorted(pasted_names - param_names)
            message = "Original names do not match."
            if missing:
                message += f"\nMissing from paste: {', '.join(missing)}"
            if extra:
                message += f"\nUnexpected in paste: {', '.join(extra)}"
            QMessageBox.warning(self, "Paste", message)
            return

        for p in params:
            name = p.get("name", "")
            extract_text = by_name[name][1].strip()
            rename = by_name[name][2].strip()
            p["extract"] = extract_text.lower() in ("true", "1", "yes", "y", "t")
            p["rename"] = rename or name

        self._persist_import_files()
        self._show_file_parameters(entry)
        self.statusBar().showMessage("Parameters pasted")

    def _flash_button(self, button) -> None:
        """Briefly highlight a button so the user sees the click registered."""
        if button is None:
            return
        button.setStyleSheet(
            "QPushButton { background: #dbeafa; border: 1px solid #5aa0e0; color: #1d4f7a; }"
        )
        QTimer.singleShot(250, lambda: button.setStyleSheet(""))

    def _apply_to_files(self) -> None:
        self._flash_button(self.apply_params_btn)
        entry = self._current_entry
        if entry is None or not entry.get("parameters"):
            QMessageBox.information(self, "Apply to", "No parameters to apply. Peek a file first.")
            return

        candidates = self._find_same_parameter_files(entry)
        if not candidates:
            QMessageBox.information(
                self, "Apply to", "No other files have the same parameter names."
            )
            return

        selected = self._show_apply_dialog(candidates)
        if not selected:
            return

        self._apply_parameter_settings(entry, selected)
        self._persist_import_files()
        self.statusBar().showMessage(f"Applied to {len(selected)} file(s)")

    def _find_same_parameter_files(self, entry):
        """Return indices of files whose parameter names match the given entry's."""
        current_names = {p["name"] for p in entry["parameters"]}
        candidates = []
        for i, other in enumerate(self._import_files):
            if other is entry:
                continue
            params = other.get("parameters")
            if params and {p["name"] for p in params} == current_names:
                candidates.append(i)
        return candidates

    def _show_apply_dialog(self, candidates):
        """Show a dialog to choose which files to apply the settings to."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Apply to")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Apply current parameter settings to:"))

        file_list = QListWidget()
        for idx in candidates:
            name = Path(self._import_files[idx]["path"]).name
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            file_list.addItem(item)
        layout.addWidget(file_list)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        confirm_btn = QPushButton("Confirm")
        cancel_btn = QPushButton("Cancel")
        confirm_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(confirm_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        dialog.exec()
        if dialog.result() != QDialog.DialogCode.Accepted.value:
            return None

        selected = []
        for i in range(file_list.count()):
            item = file_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def _apply_parameter_settings(self, entry, indices) -> None:
        """Copy the entry's extract/rename settings to the given files by name."""
        settings = {
            p["name"]: (p.get("extract", True), p.get("rename", p.get("name", "")))
            for p in entry["parameters"]
        }
        for idx in indices:
            other = self._import_files[idx]
            for p in other["parameters"]:
                name = p["name"]
                if name in settings:
                    extract, rename = settings[name]
                    p["extract"] = extract
                    p["rename"] = rename

    def _show_file_sampling(self, entry) -> None:
        self._updating_sample = True
        if entry is None:
            self.sample_combo.setCurrentIndex(0)
            self.sample_percent_spin.setVisible(False)
        else:
            sampling = entry.get("sampling", "full")
            index = self.sample_combo.findData(sampling)
            if index < 0:
                index = 0
            self.sample_combo.setCurrentIndex(index)
            self.sample_percent_spin.setValue(int(entry.get("sampling_percent", 10)))
            self.sample_percent_spin.setVisible(sampling == "partial")
        self._updating_sample = False

    def _on_sampling_changed(self) -> None:
        if self._updating_sample:
            return
        entry = self._current_entry
        if entry is None:
            return
        sampling = self.sample_combo.currentData()
        entry["sampling"] = sampling
        entry["sampling_percent"] = self.sample_percent_spin.value()
        self.sample_percent_spin.setVisible(sampling == "partial")
        self._persist_import_files()

    def _apply_sampling_to_files(self) -> None:
        self._flash_button(self.sample_apply_btn)
        entry = self._current_entry
        if entry is None:
            QMessageBox.information(self, "Apply to", "No file selected.")
            return

        sampling = entry.get("sampling", "full")
        percent = int(entry.get("sampling_percent", 10))

        candidates = [i for i, other in enumerate(self._import_files) if other is not entry]
        if not candidates:
            QMessageBox.information(self, "Apply to", "No other files to apply to.")
            return

        selected = self._show_apply_dialog(candidates)
        if not selected:
            return

        for idx in selected:
            self._import_files[idx]["sampling"] = sampling
            self._import_files[idx]["sampling_percent"] = percent

        self._persist_import_files()
        self.statusBar().showMessage(f"Applied sampling to {len(selected)} file(s)")

    # ----- figure module (visualize) ---------------------------------
    def _imported_files(self) -> list:
        """Return the imported (Processed) file records from the import task."""
        task = self._import_task()
        if task is None:
            return []
        return task.get("params", {}).get("processed", [])

    def _rel_data_key(self, parquet_path) -> str:
        """Return the parquet path relative to the data dir (index key)."""
        try:
            return Path(parquet_path).relative_to(self.project.data_dir).as_posix()
        except ValueError:
            return Path(parquet_path).name

    def _columns_for(self, parquet_path) -> list:
        """Return the column names for a parquet file from the column index."""
        if not parquet_path:
            return []
        return list(self._column_index.get(self._rel_data_key(parquet_path), []))

    def _figure_record(self, file_path):
        """Return the imported-file record matching a parquet path, or None."""
        for rec in self._imported_files():
            if rec.get("parquet") == file_path:
                return rec
        return None

    def _task_chain(self, task_id) -> list:
        """Return the workflow task chain from root down to (and including) task_id."""
        if self.project is None or not task_id:
            return []
        result = []

        def walk(tasks, path):
            for task in tasks:
                new_path = path + [task]
                if task.get("id") == task_id:
                    result.extend(new_path)
                    return True
                if walk(task.get("children", []), new_path):
                    return True
            return False

        walk(self.project.workflow.get("tasks", []), [])
        return result

    def _parent_task_name(self, task_id) -> str | None:
        """Return the name of the parent task in the workflow, or None (root)."""
        chain = self._task_chain(task_id)
        if len(chain) >= 2:
            return chain[-2].get("name")
        return None

    def _module_display_title(self, task_id, name: str) -> str:
        """Return the toolbar title as '<parent> → <name>' when a parent exists."""
        parent = self._parent_task_name(task_id) if task_id else None
        if parent:
            return f"{parent} → {name}"
        return name

    def _upstream_scale_transforms(self, task_id) -> dict:
        """Return {column: [config, ...]} for every scale module strictly upstream
        of task_id, in chain order (root first), so transforms compose correctly."""
        transforms = {}
        for task in self._task_chain(task_id):
            if task.get("id") == task_id:
                continue  # exclude the module itself
            if task.get("type") == "scale":
                for col in task.get("params", {}).get("columns", []):
                    if col.get("name"):
                        transforms.setdefault(col["name"], []).append(col)
        return transforms

    def _clear_layout(self, layout) -> None:
        """Remove and delete every widget/layout inside a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # avoid a visible top-level window while detaching
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    def _build_figure_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
        """Build the Figure page: Figures canvas (3) | Settings (1)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Toolbar with a Save button (figure changes are saved only on click).
        bar = QFrame()
        bar.setObjectName("moduleBar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(4)
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_def.get('id'), name)}</b>"))
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
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda _=False: self._figure_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(12)

        # Left: zoomable flow canvas (Word-like, auto-wrapping rows).
        fig_panel = self._build_panel_frame()
        fl = QVBoxLayout(fig_panel)
        fl.setContentsMargins(10, 10, 10, 10)
        fl.setSpacing(6)
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Figures</b>"))
        header.addStretch()
        add_scatter = QPushButton("Add Scatter")
        add_hist = QPushButton("Add Histogram")
        add_scatter.setCursor(Qt.CursorShape.PointingHandCursor)
        add_hist.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(add_scatter)
        header.addWidget(add_hist)
        fl.addLayout(header)

        scene = QGraphicsScene()
        canvas_view = _ZoomableGraphicsView(scene)
        fl.addWidget(canvas_view, 1)

        # Right: settings panel.
        settings_panel = self._build_panel_frame()
        sl = QVBoxLayout(settings_panel)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(6)
        sl.addWidget(QLabel("<b>Settings</b>"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        settings_layout = QVBoxLayout(host)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)
        scroll.setWidget(host)
        sl.addWidget(scroll, 1)

        cl.addWidget(fig_panel, 3)
        cl.addWidget(settings_panel, 1)
        layout.addWidget(content, 1)

        # Per-page state (each Figure module instance has its own).
        page._fig_snapshot = copy.deepcopy(task_def.get("params", {}))
        page._fig_task_def = task_def
        page._fig_task_id = task_def.get("id")
        page._fig_column_transforms = self._upstream_scale_transforms(page._fig_task_id)
        page._fig_plots = task_def.setdefault("params", {}).setdefault("plots", [])
        page._fig_cards = []
        page._fig_scene = scene
        page._fig_view = canvas_view
        page._fig_selected = -1
        page._fig_settings_layout = settings_layout
        page._fig_locked = locked
        page._fig_focused = None
        page._fig_pan_scene = None
        page._fig_pan_range = None
        page._fig_save_btn = save_btn
        page._fig_dirty = False
        save_btn.clicked.connect(lambda _=False: self._figure_save_now(page))
        self._figure_update_save_btn(page)
        canvas_view.enter_callback = lambda: self._figure_toggle_break(page, page._fig_selected)
        canvas_view.delete_callback = lambda: self._figure_delete_selected(page)
        canvas_view.unfocus_callback = lambda: self._figure_unfocus(page)
        canvas_view.plot_wheel_cb = lambda item, d: self._figure_plot_wheel(page, item, d)
        canvas_view.plot_pan_start_cb = lambda item, sp: self._figure_plot_pan_start(page, item, sp)
        canvas_view.plot_pan_move_cb = lambda sp: self._figure_plot_pan_move(page, sp)
        canvas_view.plot_pan_end_cb = lambda: self._figure_plot_pan_end(page)

        for plot_dict in page._fig_plots:
            self._figure_new_card(page, plot_dict)

        add_scatter.clicked.connect(lambda _=False: self._figure_add_plot(page, "scatter"))
        add_hist.clicked.connect(lambda _=False: self._figure_add_plot(page, "histogram"))

        if locked:
            add_scatter.setEnabled(False)
            add_hist.setEnabled(False)

        self._figure_relayout(page)
        if page._fig_plots:
            self._figure_select(page, 0)
        else:
            self._figure_rebuild_settings(page)
        return page

    def _figure_make_plot_widget(self, plot_dict: dict):
        """Create a scatter/histogram PlotWidget with sci axes and A/F buttons."""
        import pyqtgraph as pg
        plot_widget = pg.PlotWidget(plotItem=_make_fit_plot_item())
        plot_widget.setBackground("w")
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
            lambda _=None: self._figure_apply_ranges(plot_dict, plot_widget)
        )
        # "F" button fits to the full data extent (without changing settings).
        plot_item._on_fit = lambda: self._figure_fit_all(plot_dict, plot_widget)
        return plot_widget

    def _figure_new_card(self, page, plot_dict) -> int:
        """Create a plot card for one plot, wrap it in a proxy, and render."""
        plot_widget = self._figure_make_plot_widget(plot_dict)
        title = plot_dict.get("title") or plot_dict.get("type", "Plot").title()

        index = len(page._fig_cards)
        size = plot_dict.get("size")
        if size is None:
            size = [440, 340]
            plot_dict["size"] = list(size)
        w, h = int(size[0]), int(size[1])

        card = _FigureCard(
            title, plot_widget, index,
            select_callback=lambda i: self._figure_select(page, i),
        )
        proxy = _CardProxy(
            on_select=lambda: self._figure_select(page, card.index()),
            on_drop=lambda pos: self._figure_reorder(page, card.index(), pos),
        )
        proxy.setWidget(card)
        proxy.resize(w, h)
        page._fig_scene.addItem(proxy)
        card._proxy = proxy

        # Clicking the plot (not just the title bar) also selects the card.
        click_filter = _ClickFilter(card.select)
        card.installEventFilter(click_filter)
        plot_widget.viewport().installEventFilter(click_filter)
        page._fig_filters = getattr(page, "_fig_filters", [])
        page._fig_filters.append(click_filter)

        # Double-click focuses the plot (wheel/drag act on its data).
        dbl_click_filter = _DoubleClickFilter(lambda: self._figure_focus(page, card.index()))
        card.installEventFilter(dbl_click_filter)
        card._title_bar.installEventFilter(dbl_click_filter)
        plot_widget.viewport().installEventFilter(dbl_click_filter)
        page._fig_filters.append(dbl_click_filter)

        page._fig_cards.append(card)
        self._figure_render(plot_dict, plot_widget, column_transforms=page._fig_column_transforms)
        self._figure_relayout(page)
        return index

    def _figure_resize_card(self, page, index: int, w: int, h: int) -> None:
        if 0 <= index < len(page._fig_plots):
            page._fig_plots[index]["size"] = [int(w), int(h)]
            try:
                page._fig_cards[index].proxy().resize(int(w), int(h))
            except RuntimeError:
                return  # proxy already deleted (e.g. during teardown)
            self._figure_relayout(page)

    def _figure_mark_dirty(self, page) -> None:
        page._fig_dirty = True
        self._figure_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _figure_save_now(self, page) -> None:
        if self.project is not None:
            self.project.save()
        page._fig_snapshot = copy.deepcopy(page._fig_task_def.get("params", {}))
        page._fig_dirty = False
        self._figure_update_save_btn(page)
        self.statusBar().showMessage("Figure saved")
        self._module_saved(page)

    def _figure_close(self, page) -> None:
        """Close the page, prompting first if there are unsaved changes."""
        if page._fig_dirty:
            reply = QMessageBox.question(
                self,
                "Close Figure",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._fig_task_def["params"] = page._fig_snapshot
        self._close_module_page(page)

    def _figure_update_save_btn(self, page) -> None:
        page._fig_save_btn.setEnabled(page._fig_dirty and not page._fig_locked)

    def _figure_reorder(self, page, index: int, scene_pos) -> None:
        plots = page._fig_plots
        cards = page._fig_cards
        if not (0 <= index < len(plots)):
            return
        new_index = self._figure_drop_index(page, index, scene_pos)
        if new_index == index:
            self._figure_relayout(page)
            return
        plots.insert(new_index, plots.pop(index))
        cards.insert(new_index, cards.pop(index))
        for i, card in enumerate(cards):
            card.set_index(i)
        page._fig_selected = new_index
        self._figure_relayout(page)
        self._figure_mark_dirty(page)

    def _figure_flow_positions(self, cards, plots) -> list:
        """Compute (x, y, w, h) flow positions, wrapping on explicit line breaks."""
        margin, gap = 16, 12
        positions = []
        x, y, row_h = margin, margin, 0
        first = True
        for i, card in enumerate(cards):
            proxy = card.proxy()
            w = int(proxy.size().width())
            h = int(proxy.size().height())
            if not first and plots[i].get("break_before", False):
                x = margin
                y += row_h + gap
                row_h = 0
            positions.append((x, y, w, h))
            x += w + gap
            row_h = max(row_h, h)
            first = False
        return positions

    def _figure_drop_index(self, page, index: int, scene_pos) -> int:
        """Compute the insertion index for the dragged card at `scene_pos`."""
        others_cards = [c for i, c in enumerate(page._fig_cards) if i != index]
        others_plots = [p for i, p in enumerate(page._fig_plots) if i != index]
        if not others_cards:
            return 0
        pos = self._figure_flow_positions(others_cards, others_plots)
        px, py = scene_pos.x(), scene_pos.y()
        idx = 0
        for i, (x0, y0, w, h) in enumerate(pos):
            cx, cy = x0 + w / 2.0, y0 + h / 2.0
            if cy + h / 2.0 < py:
                idx = i + 1
            elif cy - h / 2.0 <= py:
                if cx < px:
                    idx = i + 1
                else:
                    break
            else:
                break
        return idx

    def _figure_toggle_break(self, page, index: int) -> None:
        """Toggle a line break before the selected plot (like pressing Enter)."""
        if 0 <= index < len(page._fig_plots):
            p = page._fig_plots[index]
            p["break_before"] = not p.get("break_before", False)
            self._figure_relayout(page)
            self._figure_mark_dirty(page)

    def _figure_delete_selected(self, page) -> None:
        """Delete the currently selected plot."""
        index = page._fig_selected
        if not (0 <= index < len(page._fig_plots)):
            return
        page._fig_plots.pop(index)
        card = page._fig_cards.pop(index)
        proxy = card.proxy()
        page._fig_scene.removeItem(proxy)
        proxy.deleteLater()
        for i, c in enumerate(page._fig_cards):
            c.set_index(i)
        if page._fig_cards:
            page._fig_selected = min(index, len(page._fig_cards) - 1)
            self._figure_relayout(page)
            self._figure_select(page, page._fig_selected)
        else:
            page._fig_selected = -1
            self._figure_relayout(page)
            self._figure_rebuild_settings(page)
        self._figure_mark_dirty(page)

    def _figure_relayout(self, page) -> None:
        """Flow-layout the cards (Word-like): one row, wrapped by line breaks."""
        positions = self._figure_flow_positions(page._fig_cards, page._fig_plots)
        for (x, y, w, h), card in zip(positions, page._fig_cards):
            card.proxy().setPos(x, y)
        self._figure_update_scene_rect(page)

    def _figure_update_scene_rect(self, page) -> None:
        if not page._fig_cards:
            page._fig_scene.setSceneRect(0, 0, 800, 600)
            return
        rect = page._fig_scene.itemsBoundingRect()
        page._fig_scene.setSceneRect(rect.adjusted(-16, -16, 16, 16))

    def _figure_add_plot(self, page, ptype: str) -> None:
        """Append a new plot (scatter or histogram) and select it."""
        records = self._imported_files()
        rec = records[0] if records else None
        columns = self._columns_for(rec.get("parquet")) if rec else []
        x = columns[0] if columns else None
        y = columns[1] if len(columns) > 1 else x
        count = sum(1 for p in page._fig_plots if p.get("type") == ptype)
        plot_dict = {
            "type": ptype,
            "title": f"{ptype.title()} {count + 1}",
            "file": rec.get("parquet") if rec else None,
            "file_name": rec.get("name") if rec else None,
            "x": x,
            "y": y if ptype == "scatter" else None,
            "color_mode": "density",
            "color_param": x,
            "palette": "viridis",
            "xmin": None,
            "xmax": None,
            "ymin": None,
            "ymax": None,
            "break_before": False,
            "show_axes": True,
            "show_axis_names": True,
            "show_indices": True,
            "show_sample_name": True,
            "point_size": 4,
            "fixed_point_size": False,
            "bins": 20,
            "hist_mode": "histogram",
            "hist_color": (0, 0, 200),
            "curve_color": (200, 40, 40),
            "solid_color": (0, 0, 200),
        }
        page._fig_plots.append(plot_dict)
        index = self._figure_new_card(page, plot_dict)
        self._figure_mark_dirty(page)
        self._figure_select(page, index)

    def _figure_render(self, plot_dict: dict, plot_widget, column_transforms=None) -> None:
        """(Re)draw a plot widget from its plot_dict."""
        if plot_dict.get("type") == "histogram":
            self._figure_render_histogram(plot_dict, plot_widget, column_transforms)
        else:
            self._figure_render_scatter(plot_dict, plot_widget, column_transforms)

    def _figure_render_scatter(self, plot_dict: dict, plot_widget, column_transforms=None,
                               mask=None) -> None:
        import numpy as np
        import pandas as pd

        plot_widget.clear()
        rec = self._figure_record(plot_dict.get("file"))
        x_name = plot_dict.get("x")
        y_name = plot_dict.get("y")
        if not rec or not x_name or not y_name:
            return

        transforms = column_transforms or {}

        columns = [x_name, y_name]
        mode = plot_dict.get("color_mode", "density")
        color_param = plot_dict.get("color_param") if mode == "parameter" else None
        if color_param and color_param not in columns:
            columns.append(color_param)

        try:
            df = pd.read_parquet(rec["parquet"], columns=columns)
        except Exception:
            return

        x = pd.to_numeric(df[x_name], errors="coerce").to_numpy(dtype="float64")
        y = pd.to_numeric(df[y_name], errors="coerce").to_numpy(dtype="float64")
        x = _apply_scale_transforms(x, transforms.get(x_name))
        y = _apply_scale_transforms(y, transforms.get(y_name))
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

        if mode == "solid":
            # Solid: every point shares one user-chosen color.
            solid = plot_dict.get("solid_color", (0, 0, 200))
            self._figure_draw_colored_scatter(
                plot_widget, x, y, None, None, size=size, px_mode=px_mode, solid_color=solid
            )
        else:
            cmap = _figure_colormap(plot_dict.get("palette", "viridis"))
            if mode == "parameter" and color_param:
                values = pd.to_numeric(df[color_param], errors="coerce").to_numpy(dtype="float64")[keep]
                values = _apply_scale_transforms(values, transforms.get(color_param))
            else:
                # Density: color each actual point by its local (binned) density,
                # without painting empty regions of the plot.
                hist, xe, ye = np.histogram2d(x, y, bins=200)
                xi = np.clip(np.digitize(x, xe) - 1, 0, hist.shape[0] - 1)
                yi = np.clip(np.digitize(y, ye) - 1, 0, hist.shape[1] - 1)
                values = np.log1p(hist[xi, yi].astype(float))
            self._figure_draw_colored_scatter(plot_widget, x, y, values, cmap, size=size, px_mode=px_mode)
        self._figure_apply_ranges(plot_dict, plot_widget)
        self._figure_apply_display_options(plot_dict, plot_widget)

    def _figure_draw_colored_scatter(self, plot_widget, x, y, values, cmap,
                                     max_points: int = 50000, size: float = 4,
                                     px_mode: bool = True, solid_color=None) -> None:
        """Draw points colored by `values`, normalized through the colormap.

        When `solid_color` is given, every point uses that single color instead.
        """
        import numpy as np
        import pyqtgraph as pg

        if x.size > max_points:
            rng = np.random.default_rng(42)
            idx = rng.choice(x.size, max_points, replace=False)
            x = x[idx]
            y = y[idx]
            if values is not None:
                values = values[idx]

        if solid_color is not None:
            brushes = pg.mkBrush(
                int(solid_color[0]), int(solid_color[1]), int(solid_color[2]), 200
            )
        else:
            lo = float(np.nanmin(values))
            hi = float(np.nanmax(values))
            if not np.isfinite(lo):
                lo = 0.0
            if not np.isfinite(hi) or hi <= lo:
                hi = lo + 1.0
            norm = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
            rgba = cmap.map(norm, mode="byte")
            brushes = [pg.mkBrush(int(r), int(g), int(b), 200) for r, g, b, _ in rgba]

        plot_widget.addItem(
            pg.ScatterPlotItem(x=x, y=y, symbol="o", size=size, pen=None, brush=brushes, pxMode=px_mode)
        )

    def _figure_render_histogram(self, plot_dict: dict, plot_widget, column_transforms=None) -> None:
        import numpy as np
        import pandas as pd
        import pyqtgraph as pg

        plot_widget.clear()
        rec = self._figure_record(plot_dict.get("file"))
        x_name = plot_dict.get("x")
        if not rec or not x_name:
            return
        try:
            df = pd.read_parquet(rec["parquet"], columns=[x_name])
        except Exception:
            return
        x = pd.to_numeric(df[x_name], errors="coerce").to_numpy(dtype="float64")
        x = _apply_scale_transforms(x, (column_transforms or {}).get(x_name))
        x = x[np.isfinite(x)]
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

        if mode in ("histogram", "overlap"):
            plot_widget.addItem(
                pg.PlotDataItem(
                    edges, counts, stepMode="center", fillLevel=0,
                    brush=(*hist_color, 60), pen=hist_color,
                )
            )
        if mode in ("curve", "overlap") and curve_y is not None:
            plot_widget.addItem(
                pg.PlotDataItem(curve_x, curve_y, pen=pg.mkPen(*curve_color, width=2))
            )

        self._figure_apply_ranges(plot_dict, plot_widget)
        self._figure_apply_display_options(plot_dict, plot_widget)

    def _figure_apply_ranges(self, plot_dict: dict, plot_widget) -> None:
        """Apply manual/auto X and Y ranges from the plot settings."""
        xmin, xmax = plot_dict.get("xmin"), plot_dict.get("xmax")
        if xmin is not None and xmax is not None and xmax > xmin:
            plot_widget.setXRange(float(xmin), float(xmax), padding=0)
        else:
            plot_widget.enableAutoRange("x", True)
        ymin, ymax = plot_dict.get("ymin"), plot_dict.get("ymax")
        if ymin is not None and ymax is not None and ymax > ymin:
            plot_widget.setYRange(float(ymin), float(ymax), padding=0)
        else:
            plot_widget.enableAutoRange("y", True)

    def _figure_fit_all(self, plot_dict: dict, plot_widget) -> None:
        """Fit the view to the full data extent (without changing settings)."""
        vb = plot_widget.getPlotItem().getViewBox()
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

    def _figure_apply_display_options(self, plot_dict: dict, plot_widget) -> None:
        """Apply axis labels, visibility, tick notation, and sample-name title."""
        plot_item = plot_widget.getPlotItem()
        x_name = plot_dict.get("x")
        y_name = plot_dict.get("y") if plot_dict.get("type") == "scatter" else "Count"

        plot_item.setLabel("bottom", x_name or "")
        plot_item.setLabel("left", y_name or "")

        show_axes = plot_dict.get("show_axes", True)
        show_names = plot_dict.get("show_axis_names", True)
        show_indices = plot_dict.get("show_indices", True)
        for ax in ("bottom", "left"):
            plot_item.showAxis(ax, show_axes)
            plot_item.getAxis(ax).showLabel(show_names)
            plot_item.getAxis(ax).setStyle(showValues=show_indices)

        if plot_dict.get("show_sample_name", True) and plot_dict.get("file_name"):
            plot_item.setTitle(plot_dict["file_name"], color="#808080", size="9pt")
        else:
            plot_item.setTitle(None)

    def _figure_apply_change(self, page, index: int, plot_dict: dict) -> None:
        """Re-render a plot after a settings change (marks the page dirty)."""
        if 0 <= index < len(page._fig_cards):
            self._figure_render(
                plot_dict, page._fig_cards[index].plot_widget(),
                column_transforms=page._fig_column_transforms,
            )
        self._figure_mark_dirty(page)

    def _figure_refresh_all(self, page) -> None:
        """Re-render every plot card (used when an upstream module saved)."""
        page._fig_column_transforms = self._upstream_scale_transforms(page._fig_task_id)
        for i, card in enumerate(page._fig_cards):
            if i < len(page._fig_plots):
                self._figure_render(
                    page._fig_plots[i], card.plot_widget(),
                    column_transforms=page._fig_column_transforms,
                )

    def _figure_select(self, page, index: int) -> None:
        """Mark the plot at `index` selected and refresh its settings."""
        page._fig_selected = index
        for i, card in enumerate(page._fig_cards):
            card.set_selected(i == index)
        self._figure_rebuild_settings(page)
        page._fig_view.setFocus()
        if page._fig_focused != index:
            self._figure_unfocus(page)

    def _figure_focus(self, page, index: int) -> None:
        """Focus a plot: wheel/drag act on its data instead of the canvas."""
        if not (0 <= index < len(page._fig_cards)):
            return
        page._fig_focused = index
        page._fig_selected = index
        for i, card in enumerate(page._fig_cards):
            card.set_selected(i == index)
            card.set_focused(i == index)
        self._figure_rebuild_settings(page)
        self._figure_update_plot_mouse(page)
        page._fig_view.setFocus()

    def _figure_unfocus(self, page) -> None:
        """Leave focus mode (wheel/drag act on the canvas again)."""
        page._fig_focused = None
        for card in page._fig_cards:
            card.set_focused(False)
        self._figure_update_plot_mouse(page)

    def _figure_update_plot_mouse(self, page) -> None:
        for i, card in enumerate(page._fig_cards):
            enabled = (i == page._fig_focused)
            card.plot_widget().getPlotItem().getViewBox().setMouseEnabled(enabled, enabled)

    def _figure_plot_wheel(self, page, item, delta_y) -> bool:
        focused = page._fig_focused
        if focused is None or not (0 <= focused < len(page._fig_cards)):
            return False
        card = page._fig_cards[focused]
        if item is not card.proxy():
            return False
        factor = 1.15 if delta_y > 0 else 1 / 1.15
        card.plot_widget().getPlotItem().getViewBox().scaleBy((1 / factor, 1 / factor))
        return True

    def _figure_plot_pan_start(self, page, item, scene_pos) -> bool:
        focused = page._fig_focused
        if focused is None or not (0 <= focused < len(page._fig_cards)):
            return False
        card = page._fig_cards[focused]
        if item is not card.proxy():
            return False
        local = card.proxy().mapFromScene(scene_pos)
        if local.y() <= _CardProxy.TITLE_HEIGHT:
            return False  # title bar -> reorder, not pan
        # Corner buttons (bottom-left): "A" resets to settings range, "F" fits all.
        if local.y() >= card.height() - 40:
            if local.x() <= 21:
                self._figure_apply_ranges(page._fig_plots[focused], card.plot_widget())
                return True
            if local.x() <= 40:
                self._figure_fit_all(page._fig_plots[focused], card.plot_widget())
                return True
        page._fig_pan_scene = scene_pos
        page._fig_pan_range = card.plot_widget().getPlotItem().getViewBox().viewRange()
        return True

    def _figure_plot_pan_move(self, page, scene_pos) -> bool:
        if page._fig_pan_scene is None:
            return False
        focused = page._fig_focused
        if focused is None or not (0 <= focused < len(page._fig_cards)):
            return False
        card = page._fig_cards[focused]
        vb = card.plot_widget().getPlotItem().getViewBox()
        rect = card.proxy().boundingRect()
        plot_h = max(1.0, rect.height() - _CardProxy.TITLE_HEIGHT)
        xr = page._fig_pan_range[0]
        yr = page._fig_pan_range[1]
        d = scene_pos - page._fig_pan_scene
        x_scale = (xr[1] - xr[0]) / rect.width() if rect.width() else 1.0
        y_scale = (yr[1] - yr[0]) / plot_h if plot_h else 1.0
        vb.setXRange(xr[0] - d.x() * x_scale, xr[1] - d.x() * x_scale, padding=0)
        vb.setYRange(yr[0] + d.y() * y_scale, yr[1] + d.y() * y_scale, padding=0)
        return True

    def _figure_plot_pan_end(self, page) -> bool:
        was_panning = page._fig_pan_scene is not None
        page._fig_pan_scene = None
        page._fig_pan_range = None
        return was_panning

    def _figure_rebuild_settings(self, page) -> None:
        """Rebuild the right-hand settings panel for the selected plot."""
        layout = page._fig_settings_layout
        self._clear_layout(layout)
        index = page._fig_selected
        if index < 0 or index >= len(page._fig_plots):
            hint = QLabel("Add a plot to see its settings.")
            hint.setStyleSheet("color: gray;")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            layout.addStretch()
            return

        plot_dict = page._fig_plots[index]
        locked = page._fig_locked
        enabled = not locked
        plot_widget = page._fig_cards[index].plot_widget()
        if locked:
            note = QLabel("Draft (locked) — settings are read-only.")
            note.setStyleSheet("color: #b58900;")
            note.setWordWrap(True)
            layout.addWidget(note)

        records = self._imported_files()
        rec = self._figure_record(plot_dict.get("file"))
        columns = self._columns_for(rec.get("parquet")) if rec else []

        # File.
        layout.addWidget(QLabel("File"))
        file_combo = QComboBox()
        for r in records:
            file_combo.addItem(r.get("name", ""), r.get("parquet", ""))
        if plot_dict.get("file") is not None:
            idx = file_combo.findData(plot_dict.get("file"))
            if idx >= 0:
                file_combo.setCurrentIndex(idx)
        file_combo.setEnabled(enabled)
        layout.addWidget(file_combo)

        # X parameter.
        layout.addWidget(QLabel("X parameter"))
        x_combo = self._figure_param_combo(columns, plot_dict.get("x"), enabled)
        layout.addWidget(x_combo)

        y_combo = None
        bins_spin = None
        hist_mode_combo = None

        if plot_dict.get("type") == "scatter":
            layout.addWidget(QLabel("Y parameter"))
            y_combo = self._figure_param_combo(columns, plot_dict.get("y"), enabled)
            layout.addWidget(y_combo)

            self._figure_scatter_color_settings(
                layout, plot_dict, plot_widget, columns, enabled,
                on_change=lambda: self._figure_apply_change(page, index, plot_dict),
                on_rebuild=lambda: (
                    self._figure_rebuild_settings(page),
                    self._figure_apply_change(page, index, plot_dict),
                ),
            )

        if plot_dict.get("type") == "histogram":
            # Number of bins for the histogram.
            layout.addWidget(QLabel("Bins"))
            bins_spin = QSpinBox()
            bins_spin.setRange(2, 2000)
            bins_spin.setValue(int(plot_dict.get("bins", 20)))
            bins_spin.setKeyboardTracking(False)
            bins_spin.setEnabled(enabled)
            layout.addWidget(bins_spin)

            # Display mode: histogram bars, fitted curve, or both overlaid.
            layout.addWidget(QLabel("Display mode"))
            hist_mode_combo = QComboBox()
            for text, val in FIGURE_HISTOGRAM_MODES:
                hist_mode_combo.addItem(text, val)
            idx = hist_mode_combo.findData(plot_dict.get("hist_mode", "histogram"))
            hist_mode_combo.setCurrentIndex(max(0, idx))
            hist_mode_combo.setEnabled(enabled)
            layout.addWidget(hist_mode_combo)

            # Colors: a swatch button opens a color picker.
            def make_color_button(key, default):
                cur = plot_dict.get(key, default)
                btn = QPushButton()
                btn.setFixedSize(36, 20)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setEnabled(enabled)
                btn.setStyleSheet(
                    f"background-color: rgb({cur[0]},{cur[1]},{cur[2]}); "
                    "border: 1px solid #555;"
                )

                def on_pick(_=False):
                    current = plot_dict.get(key, default)
                    chosen = QColorDialog.getColor(
                        QColor(current[0], current[1], current[2]), self, "Choose color"
                    )
                    if chosen.isValid():
                        plot_dict[key] = (chosen.red(), chosen.green(), chosen.blue())
                        btn.setStyleSheet(
                            f"background-color: rgb({chosen.red()},{chosen.green()},{chosen.blue()}); "
                            "border: 1px solid #555;"
                        )
                        self._figure_apply_change(page, index, plot_dict)

                btn.clicked.connect(on_pick)
                return btn

            layout.addWidget(QLabel("Histogram color"))
            layout.addWidget(make_color_button("hist_color", (0, 0, 200)))
            layout.addWidget(QLabel("Curve color"))
            layout.addWidget(make_color_button("curve_color", (200, 40, 40)))

        # X range.
        layout.addWidget(QLabel("X range"))
        layout.addWidget(self._figure_range_row(
            plot_dict, "x", plot_widget, enabled, lambda: self._figure_mark_dirty(page)
        ))

        if plot_dict.get("type") == "scatter":
            layout.addWidget(QLabel("Y range"))
            layout.addWidget(self._figure_range_row(
                plot_dict, "y", plot_widget, enabled, lambda: self._figure_mark_dirty(page)
            ))

        # Size (width/height in canvas units).
        layout.addWidget(QLabel("Size (px)"))
        size_row = QWidget()
        sh = QHBoxLayout(size_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(4)
        size = plot_dict.get("size") or [440, 340]
        width_field = _SizeField(size[0], lambda v: on_size_changed())
        height_field = _SizeField(size[1], lambda v: on_size_changed())
        width_field.set_enabled(enabled)
        height_field.set_enabled(enabled)
        sh.addWidget(QLabel("W"))
        sh.addWidget(width_field)
        sh.addWidget(QLabel("H"))
        sh.addWidget(height_field)
        sh.addStretch()
        layout.addWidget(size_row)

        # Display options.
        self._figure_display_toggles(
            layout, plot_dict, enabled,
            on_change=lambda: self._figure_apply_change(page, index, plot_dict),
        )

        layout.addStretch()

        # Signal wiring (after initial values are set).
        def on_file_changed():
            plot_dict["file"] = file_combo.currentData()
            plot_dict["file_name"] = file_combo.currentText()
            new_rec = self._figure_record(plot_dict["file"])
            new_cols = self._columns_for(new_rec.get("parquet")) if new_rec else []
            plot_dict["x"] = new_cols[0] if new_cols else None
            if plot_dict.get("type") == "scatter":
                plot_dict["y"] = new_cols[1] if len(new_cols) > 1 else (new_cols[0] if new_cols else None)
                plot_dict["color_param"] = plot_dict["x"]
            self._figure_rebuild_settings(page)
            self._figure_apply_change(page, index, plot_dict)

        file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())

        def on_axis_param(attr):
            def handler(value):
                plot_dict[attr] = value
                # Reset the axis range to Auto so the plot refits to the new
                # parameter and the settings panel shows the updated range.
                if attr == "x":
                    plot_dict["xmin"] = None
                    plot_dict["xmax"] = None
                elif attr == "y":
                    plot_dict["ymin"] = None
                    plot_dict["ymax"] = None
                self._figure_apply_change(page, index, plot_dict)
                self._figure_rebuild_settings(page)
            return handler

        x_combo.currentTextChanged.connect(on_axis_param("x"))
        if y_combo is not None:
            y_combo.currentTextChanged.connect(on_axis_param("y"))

        if bins_spin is not None:
            def on_bins(v):
                plot_dict["bins"] = int(v)
                self._figure_apply_change(page, index, plot_dict)
            bins_spin.valueChanged.connect(on_bins)

        if hist_mode_combo is not None:
            def on_hist_mode(_=0):
                plot_dict["hist_mode"] = hist_mode_combo.currentData()
                self._figure_apply_change(page, index, plot_dict)
            hist_mode_combo.currentIndexChanged.connect(on_hist_mode)

        def on_size_changed():
            self._figure_resize_card(page, index, width_field.value(), height_field.value())
            self._figure_mark_dirty(page)

    def _figure_param_combo(self, columns, current, enabled: bool) -> QComboBox:
        """Build a combo listing parameter names, selecting `current` if present."""
        combo = QComboBox()
        for col in columns:
            combo.addItem(str(col))
        if current is not None:
            idx = combo.findText(str(current))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.setEnabled(enabled)
        return combo

    def _figure_scatter_color_settings(self, layout, plot_dict, plot_widget, columns,
                                       enabled, on_change, on_rebuild) -> None:
        """Build + wire color mode/param, palette/solid, and point-size rows.

        Shared by the Figure and Scaling scatter settings panels.
        """
        layout.addWidget(QLabel("Color mode"))
        mode_combo = QComboBox()
        for text, val in FIGURE_COLOR_MODES:
            mode_combo.addItem(text, val)
        idx = mode_combo.findData(plot_dict.get("color_mode", "density"))
        mode_combo.setCurrentIndex(max(0, idx))
        mode_combo.setEnabled(enabled)
        layout.addWidget(mode_combo)

        cp_combo = None
        if plot_dict.get("color_mode") == "parameter":
            layout.addWidget(QLabel("Color parameter"))
            cp_combo = self._figure_param_combo(columns, plot_dict.get("color_param"), enabled)
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
                        QColor(current[0], current[1], current[2]), self, "Choose color"
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
            palette_combo = QComboBox()
            palette_combo.addItems(FIGURE_PALETTES)
            idx = palette_combo.findText(plot_dict.get("palette", "viridis"))
            if idx >= 0:
                palette_combo.setCurrentIndex(idx)
            palette_combo.setEnabled(enabled)
            layout.addWidget(palette_combo)

        # Point size: constant on-screen size (unchecked), or a fixed size in
        # data units that scales with the zoom (checked).
        def on_point_size(v):
            plot_dict["point_size"] = float(v)
            on_change()

        layout.addWidget(QLabel("Point size"))
        ps_row = QWidget()
        ph = QHBoxLayout(ps_row)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(4)
        point_fixed = QCheckBox("Fixed")
        point_fixed.setChecked(plot_dict.get("fixed_point_size", False))
        point_fixed.setEnabled(enabled)
        point_spin = _LogStepSpinBox(plot_dict.get("point_size", 4), on_point_size)
        point_spin.set_enabled(enabled and point_fixed.isChecked())
        ph.addWidget(point_fixed)
        ph.addWidget(point_spin)
        ph.addStretch()
        layout.addWidget(ps_row)

        def on_point_fixed(toggled):
            plot_dict["fixed_point_size"] = toggled
            point_spin.set_enabled(enabled and toggled)
            if toggled:
                # Seed a sensible data-unit size from the current x-range.
                xmin = getattr(plot_widget, "_data_xmin", None)
                xmax = getattr(plot_widget, "_data_xmax", None)
                if xmin is not None and xmax is not None and xmax > xmin:
                    auto_size = 0.01 * (xmax - xmin)
                    point_spin.set_value(auto_size)
                    plot_dict["point_size"] = auto_size
            on_change()
        point_fixed.toggled.connect(on_point_fixed)

        def on_mode_changed(_=0):
            plot_dict["color_mode"] = mode_combo.currentData()
            on_rebuild()
        mode_combo.currentIndexChanged.connect(on_mode_changed)

        if cp_combo is not None:
            def on_cp(v):
                plot_dict["color_param"] = v
                on_change()
            cp_combo.currentTextChanged.connect(on_cp)

        if palette_combo is not None:
            def on_palette(_=0):
                plot_dict["palette"] = palette_combo.currentText()
                on_change()
            palette_combo.currentIndexChanged.connect(on_palette)

    def _figure_display_toggles(self, layout, plot_dict, enabled, on_change) -> None:
        """Build + wire the show axes/names/indices/sample-name checkboxes."""
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

    def _figure_range_row(self, plot_dict: dict, axis: str, plot_widget,
                          enabled: bool, on_commit=None, auto_checked: bool = True) -> QWidget:
        """Build an Auto/Min/Max row for one axis range."""
        min_key = f"{axis}min"
        max_key = f"{axis}max"
        data_min = getattr(plot_widget, f"_data_{axis}min", None)
        data_max = getattr(plot_widget, f"_data_{axis}max", None)

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
        if min_val is None:
            min_val = data_min
        if max_val is None:
            max_val = data_max

        min_spin = _LogStepSpinBox(min_val, lambda v: commit())
        max_spin = _LogStepSpinBox(max_val, lambda v: commit())

        def commit():
            if auto.isChecked():
                plot_dict[min_key] = None
                plot_dict[max_key] = None
                min_spin.set_value(data_min)
                max_spin.set_value(data_max)
            else:
                plot_dict[min_key] = min_spin.value()
                plot_dict[max_key] = max_spin.value()
            min_spin.set_enabled(enabled and not auto.isChecked())
            max_spin.set_enabled(enabled and not auto.isChecked())
            # A range change only affects the view, so apply the range directly
            # instead of re-reading and re-rendering the plot's data.
            self._figure_apply_ranges(plot_dict, plot_widget)
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

    # ----- scaling module (map) -------------------------------------
    def _scaling_column_set(self) -> list:
        """Return the sorted, de-duplicated set of input column names."""
        cols = set()
        for rec in self._imported_files():
            for c in self._columns_for(rec.get("parquet")):
                cols.add(c)
        return sorted(cols)

    def _scaling_load_config(self, task_def: dict) -> list:
        """Return the per-column scaling config, merged with the current columns."""
        cols = self._scaling_column_set()
        params = task_def.setdefault("params", {})
        existing = params.get("columns") or []
        by_name = {c.get("name"): c for c in existing}
        merged = []
        for name in cols:
            c = by_name.get(name)
            if c is None:
                merged.append({"name": name, "method": "linear",
                               "param": None, "min": None, "max": None})
            else:
                merged.append({
                    "name": name,
                    "method": c.get("method", "linear"),
                    "param": c.get("param"),
                    "min": c.get("min"),
                    "max": c.get("max"),
                })
        params["columns"] = merged
        return merged

    def _build_scaling_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
        """Build the Scale page: Columns table (2) | Preview (1)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Toolbar with a Save button (scale changes are saved only on click).
        bar = QFrame()
        bar.setObjectName("moduleBar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(4)
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_def.get('id'), name)}</b>"))
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
        close_btn.clicked.connect(lambda _=False: self._scaling_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(12)

        # Left: columns table.
        colnames_panel = self._build_panel_frame()
        cll = QVBoxLayout(colnames_panel)
        cll.setContentsMargins(10, 10, 10, 10)
        cll.setSpacing(6)
        cll.addWidget(QLabel("<b>Columns</b>"))
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Name", "Method", "Param", "Min", "Max"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: none; }"
            "QTableWidget::item:selected { background: #dbeafa; }"
        )
        cll.addWidget(table, 1)

        # Build the preview config first so the plot widget's A/F buttons can
        # bind to it (same dict object is mutated in place later).
        records = self._imported_files()
        rec = records[0] if records else None
        cols = self._columns_for(rec.get("parquet")) if rec else []
        page._scaling_preview = {
            "type": "scatter",
            "file": rec.get("parquet") if rec else None,
            "file_name": rec.get("name") if rec else None,
            "x": cols[0] if cols else None,
            "y": cols[1] if len(cols) > 1 else (cols[0] if cols else None),
            "color_mode": "density",
            "color_param": None,
            "palette": "viridis",
            "xmin": None,
            "xmax": None,
            "ymin": None,
            "ymax": None,
            "point_size": 4,
            "fixed_point_size": False,
            "solid_color": (0, 0, 200),
            "show_axes": True,
            "show_axis_names": True,
            "show_indices": True,
            "show_sample_name": True,
        }

        # Right: preview (scatter + settings).
        preview_panel = self._build_panel_frame()
        pl = QVBoxLayout(preview_panel)
        pl.setContentsMargins(10, 10, 10, 10)
        pl.setSpacing(6)
        pl.addWidget(QLabel("<b>Preview</b>"))
        plot_widget = self._figure_make_plot_widget(page._scaling_preview)
        # The preview is a normal interactive plot (unlike a figure card).
        plot_widget.getPlotItem().getViewBox().setMouseEnabled(True, True)

        # Overlay shown when a selected column is missing from the current file.
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
        pl.addWidget(plot_host, 3)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setStyleSheet("background: transparent;")
        settings_host = QWidget()
        settings_host.setStyleSheet("background: transparent;")
        settings_layout = QVBoxLayout(settings_host)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)
        settings_scroll.setWidget(settings_host)
        pl.addWidget(settings_scroll, 2)

        cl.addWidget(colnames_panel, 2)
        cl.addWidget(preview_panel, 1)
        layout.addWidget(content, 1)

        page._scaling_task_def = task_def
        page._scaling_task_id = task_def.get("id")
        page._scaling_columns = self._scaling_load_config(task_def)
        page._scaling_table = table
        page._scaling_plot_widget = plot_widget
        page._scaling_settings_layout = settings_layout
        page._scaling_locked = locked
        page._scaling_overlay = overlay
        page._scaling_overlay_box = overlay_box
        page._scaling_save_btn = save_btn
        page._scaling_dirty = False
        page._scaling_minmax_filled = set()
        page._scaling_snapshot = copy.deepcopy(task_def.get("params", {}))
        save_btn.clicked.connect(lambda _=False: self._scaling_save_now(page))
        self._scaling_update_save_btn(page)

        self._scaling_populate_table(page)
        table.itemSelectionChanged.connect(lambda: self._scaling_on_selection(page))
        self._scaling_on_selection(page)
        return page

    def _scaling_populate_table(self, page) -> None:
        table = page._scaling_table
        locked = page._scaling_locked
        table.setRowCount(0)
        page._scaling_param_edits = {}
        page._scaling_min_edits = {}
        page._scaling_max_edits = {}
        for row_idx, config in enumerate(page._scaling_columns):
            table.insertRow(row_idx)
            name_item = QTableWidgetItem(config["name"])
            name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row_idx, 0, name_item)

            method_combo = QComboBox()
            method_combo.addItems(SCALING_METHODS)
            idx = method_combo.findText(config.get("method", "linear"))
            method_combo.setCurrentIndex(max(0, idx))
            method_combo.setEnabled(not locked)
            method_combo.currentIndexChanged.connect(
                lambda _=0, r=row_idx, c=config, m=method_combo:
                    self._scaling_method_changed(page, r, c, m)
            )
            table.setCellWidget(row_idx, 1, method_combo)

            # Param (arcsinh cofactor) is only editable for arcsinh.
            param_edit = QLineEdit()
            param_val = config.get("param")
            param_edit.setText("" if param_val is None else str(param_val))
            param_edit.editingFinished.connect(
                lambda e=param_edit, c=config: self._scaling_field_edited(page, c, "param", e)
            )
            table.setCellWidget(row_idx, 2, param_edit)
            page._scaling_param_edits[row_idx] = param_edit
            self._scaling_update_param_cell(page, config, param_edit)

            for col_idx, key in ((3, "min"), (4, "max")):
                edit = QLineEdit()
                val = config.get(key)
                edit.setText("" if val is None else str(val))
                edit.setEnabled(not locked)
                edit.editingFinished.connect(
                    lambda e=edit, c=config, k=key: self._scaling_field_edited(page, c, k, e)
                )
                table.setCellWidget(row_idx, col_idx, edit)
                if key == "min":
                    page._scaling_min_edits[row_idx] = edit
                else:
                    page._scaling_max_edits[row_idx] = edit

        if table.rowCount() > 0:
            table.selectRow(0)

    def _scaling_update_param_cell(self, page, config, param_edit) -> None:
        """Enable the Param cell only for arcsinh; gray it out otherwise."""
        is_arcsinh = config.get("method") == "arcsinh"
        param_edit.setEnabled((not page._scaling_locked) and is_arcsinh)
        param_edit.setStyleSheet(
            "QLineEdit:disabled { background: #e0e0e0; color: #9ca3af; }"
        )
        if is_arcsinh and config.get("param") is None:
            # Show the default cofactor when the user hasn't set one yet.
            param_edit.setText("5")

    def _scaling_method_changed(self, page, row_idx, config, combo) -> None:
        config["method"] = combo.currentText()
        param_edit = page._scaling_param_edits.get(row_idx)
        if param_edit is not None:
            self._scaling_update_param_cell(page, config, param_edit)
        self._scaling_mark_dirty(page)
        self._scaling_render_preview(page)
        self._scaling_rebuild_settings(page)

    def _scaling_field_edited(self, page, config, key, edit) -> None:
        text = edit.text().strip()
        if text == "":
            config[key] = None
        else:
            try:
                if key in ("min", "max"):
                    # Min/max are display ranges shown as integers only.
                    config[key] = int(float(text))
                    edit.setText(str(config[key]))
                else:
                    config[key] = float(text)
            except ValueError:
                edit.setText("" if config.get(key) is None else str(config[key]))
                return
        self._scaling_mark_dirty(page)
        self._scaling_render_preview(page)
        self._scaling_rebuild_settings(page)

    def _scaling_mark_dirty(self, page) -> None:
        page._scaling_dirty = True
        self._scaling_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _scaling_save_now(self, page) -> None:
        page._scaling_task_def.setdefault("params", {})["columns"] = page._scaling_columns
        if self.project is not None:
            self.project.save()
        page._scaling_snapshot = copy.deepcopy(page._scaling_task_def.get("params", {}))
        page._scaling_dirty = False
        self._scaling_update_save_btn(page)
        self.statusBar().showMessage("Scale saved")
        self._module_saved(page)

    def _scaling_update_save_btn(self, page) -> None:
        page._scaling_save_btn.setEnabled(page._scaling_dirty and not page._scaling_locked)

    def _scaling_close(self, page) -> None:
        """Close the page, prompting first if there are unsaved changes."""
        if page._scaling_dirty:
            reply = QMessageBox.question(
                self,
                "Close Scale",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._scaling_task_def["params"] = page._scaling_snapshot
        self._close_module_page(page)

    def _scaling_on_selection(self, page) -> None:
        row = page._scaling_table.currentRow()
        if row < 0 or row >= len(page._scaling_columns):
            return
        page._scaling_preview["x"] = page._scaling_columns[row]["name"]
        self._scaling_autofill_minmax(page, row)
        self._scaling_render_preview(page)
        self._scaling_rebuild_settings(page)

    def _scaling_config_for(self, page, column):
        """Return the config dict for `column`, or None."""
        for c in page._scaling_columns:
            if c.get("name") == column:
                return c
        return None

    def _scaling_autofill_minmax(self, page, row_idx) -> None:
        """Fill a row's min/max from the preview sample's data (once)."""
        if row_idx in page._scaling_minmax_filled:
            return
        page._scaling_minmax_filled.add(row_idx)
        config = page._scaling_columns[row_idx]
        name = config.get("name")
        rec = self._figure_record(page._scaling_preview.get("file"))
        if not rec or not name:
            return
        import numpy as np
        import pandas as pd
        try:
            df = pd.read_parquet(rec["parquet"], columns=[name])
        except Exception:
            return  # column not in this file -> leave empty
        vals = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype="float64")
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            return
        config["min"] = int(vals.min())
        config["max"] = int(vals.max())
        min_edit = page._scaling_min_edits.get(row_idx)
        max_edit = page._scaling_max_edits.get(row_idx)
        if min_edit is not None:
            min_edit.setText(_format_number(config["min"]))
        if max_edit is not None:
            max_edit.setText(_format_number(config["max"]))

    def _scaling_render_preview(self, page) -> None:
        preview = page._scaling_preview
        rec = self._figure_record(preview.get("file"))
        cols = self._columns_for(rec.get("parquet")) if rec else []
        missing = [
            str(n) for n in (preview.get("x"), preview.get("y"))
            if n and n not in cols
        ]
        if missing:
            page._scaling_overlay_box.setText(
                "Parameter not in this file:\n" + ", ".join(missing)
            )
            page._scaling_overlay.show()
        else:
            page._scaling_overlay.hide()
        # Drive the preview axes from each column's min/max (if set).
        x_cfg = self._scaling_config_for(page, preview.get("x"))
        y_cfg = self._scaling_config_for(page, preview.get("y"))
        preview["xmin"] = x_cfg.get("min") if x_cfg else None
        preview["xmax"] = x_cfg.get("max") if x_cfg else None
        preview["ymin"] = y_cfg.get("min") if y_cfg else None
        preview["ymax"] = y_cfg.get("max") if y_cfg else None
        # Compose transforms: upstream scale modules (chain order), then this
        # module's own current (possibly unsaved) config on top.
        transforms = self._upstream_scale_transforms(page._scaling_task_id)
        for c in page._scaling_columns:
            if c.get("name"):
                transforms.setdefault(c["name"], []).append(c)
        self._figure_render_scatter(preview, page._scaling_plot_widget, column_transforms=transforms)

    def _scaling_rebuild_settings(self, page) -> None:
        layout = page._scaling_settings_layout
        self._clear_layout(layout)
        preview = page._scaling_preview
        enabled = not page._scaling_locked
        plot_widget = page._scaling_plot_widget

        records = self._imported_files()
        rec = self._figure_record(preview.get("file"))
        columns = self._columns_for(rec.get("parquet")) if rec else []

        layout.addWidget(QLabel("File"))
        file_combo = QComboBox()
        for r in records:
            file_combo.addItem(r.get("name", ""), r.get("parquet", ""))
        if preview.get("file") is not None:
            idx = file_combo.findData(preview["file"])
            if idx >= 0:
                file_combo.setCurrentIndex(idx)
        file_combo.setEnabled(enabled)
        layout.addWidget(file_combo)

        layout.addWidget(QLabel("X parameter"))
        x_combo = self._figure_param_combo(columns, preview.get("x"), enabled)
        layout.addWidget(x_combo)

        layout.addWidget(QLabel("Y parameter"))
        y_combo = self._figure_param_combo(columns, preview.get("y"), enabled)
        layout.addWidget(y_combo)

        self._figure_scatter_color_settings(
            layout, preview, plot_widget, columns, enabled,
            on_change=lambda: self._scaling_render_preview(page),
            on_rebuild=lambda: (
                self._scaling_rebuild_settings(page),
                self._scaling_render_preview(page),
            ),
        )

        layout.addWidget(QLabel("X range"))
        layout.addWidget(self._figure_range_row(preview, "x", plot_widget, enabled, auto_checked=False))
        layout.addWidget(QLabel("Y range"))
        layout.addWidget(self._figure_range_row(preview, "y", plot_widget, enabled, auto_checked=False))

        self._figure_display_toggles(
            layout, preview, enabled,
            on_change=lambda: self._scaling_render_preview(page),
        )
        layout.addStretch()

        def on_file_changed():
            preview["file"] = file_combo.currentData()
            preview["file_name"] = file_combo.currentText()
            new_rec = self._figure_record(preview["file"])
            new_cols = self._columns_for(new_rec.get("parquet")) if new_rec else []
            preview["x"] = new_cols[0] if new_cols else None
            preview["y"] = new_cols[1] if len(new_cols) > 1 else (new_cols[0] if new_cols else None)
            preview["color_param"] = preview["x"]
            self._scaling_render_preview(page)
            self._scaling_rebuild_settings(page)
        file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())

        def on_param(attr):
            def handler(value):
                preview[attr] = value
                self._scaling_render_preview(page)
            return handler
        x_combo.currentTextChanged.connect(on_param("x"))
        y_combo.currentTextChanged.connect(on_param("y"))

    # ----- open-module registry & save->update propagation --------------
    def _module_register(self, task_id, task_type, page) -> None:
        """Register an opened module with its initial (unmodified) state."""
        self._module_registry[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "page": page,
            "state": MODULE_STATE_UNMODIFIED,
        }

    def _module_set_state(self, page, state) -> None:
        """Update an open module's registry state."""
        task_id = page.property("task_id")
        if task_id and task_id in self._module_registry:
            self._module_registry[task_id]["state"] = state

    def _module_descendant_ids(self, task_id) -> list:
        """Return open descendants of `task_id`, top-down (pre-order)."""
        task = self._find_task_by_id(task_id)
        ordered = []

        def walk(tasks):
            for t in tasks:
                ordered.append(t.get("id"))
                walk(t.get("children", []))

        if task is not None:
            walk(task.get("children", []))
        return [tid for tid in ordered if tid in self._module_registry]

    def _module_saved(self, page) -> None:
        """Mark a module as saved and push updates to its open downstream modules."""
        self._module_set_state(page, MODULE_STATE_SAVED)
        task_id = page.property("task_id")
        for dtid in self._module_descendant_ids(task_id):
            reg = self._module_registry[dtid]
            self._module_refresh_from_upstream(reg["page"], dtid, reg["task_type"])
        self._module_set_state(page, MODULE_STATE_UNMODIFIED)

    def _module_refresh_from_upstream(self, page, task_id, task_type) -> None:
        """Refresh a downstream module's content (visualization only) after an
        upstream save. This module's own unsaved edits are left untouched."""
        if task_type == "figure":
            self._figure_refresh_all(page)
        elif task_type == "scale":
            self._scaling_render_preview(page)
        elif task_type == "gating":
            self._gating_render(page)
        # Other module types have no live visualization to refresh yet.

    # ----- gating module (map) -----------------------------------------
    def _build_gating_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
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
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_def.get('id'), name)}</b>"))
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
        close_btn.clicked.connect(lambda _=False: self._gating_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        # Per-page state (preview config first so the plot widget can bind A/F).
        records = self._imported_files()
        rec = records[0] if records else None
        cols = self._columns_for(rec.get("parquet")) if rec else []
        page._gating_preview = {
            "type": "scatter",
            "file": rec.get("parquet") if rec else None,
            "file_name": rec.get("name") if rec else None,
            "x": cols[0] if cols else None,
            "y": cols[1] if len(cols) > 1 else (cols[0] if cols else None),
            "color_mode": "density",
            "color_param": None,
            "palette": "viridis",
            "xmin": None,
            "xmax": None,
            "ymin": None,
            "ymax": None,
            "point_size": 4,
            "fixed_point_size": False,
            "solid_color": (0, 0, 200),
            "show_axes": True,
            "show_axis_names": True,
            "show_indices": True,
            "show_sample_name": True,
        }
        page._gating_task_def = task_def
        page._gating_task_id = task_def.get("id")
        page._gating_gates = task_def.setdefault("params", {}).setdefault("gates", [])
        page._gating_rois = []
        page._gating_crosshairs = {}
        page._gating_split_lines = {}
        page._gating_skewed_lines = {}
        page._gating_active_gate_id = None
        page._gating_context_gate_id = None
        # The x/y that the user configured in Plot settings (independent of a
        # gate click, which temporarily switches to that gate's axes).
        page._gating_settings_x = page._gating_preview.get("x")
        page._gating_settings_y = page._gating_preview.get("y")
        page._gating_render_timer = None
        page._gating_locked = locked
        page._gating_parent_id = None
        page._gating_save_btn = save_btn
        page._gating_dirty = False
        page._gating_snapshot = copy.deepcopy(task_def.get("params", {}))
        save_btn.clicked.connect(lambda _=False: self._gating_save_now(page))
        self._gating_update_save_btn(page)

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(12)

        # Left: gate tree (placeholder for now).
        tree_panel = self._build_panel_frame()
        tree_lay = QVBoxLayout(tree_panel)
        tree_lay.setContentsMargins(10, 10, 10, 10)
        tree_lay.setSpacing(6)
        tree_lay.addWidget(QLabel("<b>Gates</b>"))
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setStyleSheet(
            "QTreeWidget { background: #ffffff; border: none; }"
            "QTreeWidget::item:selected { background: #dbeafa; }"
        )
        tree_lay.addWidget(tree, 1)
        page._gating_tree = tree

        # Middle: tool area (top) + scatter (bottom).
        mid_panel = self._build_panel_frame()
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
                self._gating_set_tool(page, None if page._gating_tool == k else k)

            btn.clicked.connect(on_clicked)
            tools_lay.addWidget(btn)
            tool_buttons[key] = btn
        tools_lay.addStretch()
        mid_lay.addWidget(tools, 1)

        plot_widget, viewbox = self._gating_make_plot(page)
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
        mid_lay.addWidget(plot_host, 5)
        page._gating_plot_widget = plot_widget
        page._gating_viewbox = viewbox
        page._gating_overlay = overlay
        page._gating_overlay_box = overlay_box
        page._gating_tool = None
        page._gating_tool_buttons = tool_buttons

        # Right: scatter settings.
        settings_panel = self._build_panel_frame()
        settings_lay = QVBoxLayout(settings_panel)
        settings_lay.setContentsMargins(10, 10, 10, 10)
        settings_lay.setSpacing(6)
        settings_lay.addWidget(QLabel("<b>Plot settings</b>"))
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setStyleSheet("background: transparent;")
        settings_host = QWidget()
        settings_host.setStyleSheet("background: transparent;")
        settings_layout = QVBoxLayout(settings_host)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)
        settings_scroll.setWidget(settings_host)
        settings_lay.addWidget(settings_scroll, 1)
        page._gating_settings_layout = settings_layout

        cl.addWidget(tree_panel, 1)
        cl.addWidget(mid_panel, 3)
        cl.addWidget(settings_panel, 2)
        layout.addWidget(content, 1)

        tree.itemClicked.connect(lambda item, col=0: self._gating_on_tree_selection(page, item, col))
        tree.itemDoubleClicked.connect(lambda item, col=0: self._gating_on_tree_double_clicked(page, item, col))
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self._gating_tree_context_menu(page, pos))
        self._gating_load_rois(page)
        self._gating_rebuild_tree(page)
        self._gating_render(page)
        self._gating_rebuild_settings(page)
        return page

    def _gating_make_plot(self, page):
        """Create the gating scatter plot (sci axes + A/F buttons + gate ViewBox)."""
        viewbox = _GateViewBox(
            on_gate_created=lambda tool, roi: self._gating_add_gate(page, tool, roi)
        )
        plot_item = _make_fit_plot_item(viewbox=viewbox)
        plot_widget = pg.PlotWidget(plotItem=plot_item)
        plot_widget.setBackground("w")
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
            lambda _=None: self._figure_apply_ranges(page._gating_preview, plot_widget)
        )
        plot_item._on_fit = lambda: self._figure_fit_all(page._gating_preview, plot_widget)
        return plot_widget, viewbox

    def _gating_set_tool(self, page, tool) -> None:
        page._gating_tool = tool
        page._gating_viewbox.set_gate_tool(tool)
        for key, btn in page._gating_tool_buttons.items():
            btn.setChecked(key == tool)

    def _gating_sync_gate(self, gate: dict, roi) -> None:
        t = gate.get("type")
        if t in ("rect", "ellipse"):
            gate["pos"] = [roi.pos().x(), roi.pos().y()]
            gate["size"] = [roi.size().x(), roi.size().y()]
        elif t == "polygon":
            # Handles are in ROI-local coords; add the ROI position for absolute.
            ox = roi.pos().x()
            oy = roi.pos().y()
            gate["points"] = [[p.x() + ox, p.y() + oy] for p in roi.getState()["points"]]

    def _gating_quadrant_sync(self, page, crosshair_id, key, line) -> None:
        for g in page._gating_gates:
            if g.get("crosshair_id") == crosshair_id:
                g[key] = line.value()

    def _gating_split_sync(self, page, split_id, line) -> None:
        for g in page._gating_gates:
            if g.get("split_id") == split_id:
                g["vx"] = line.value()

    def _gating_skewed_sync(self, page, skewed_id) -> None:
        entry = page._gating_skewed_lines.get(skewed_id)
        if entry is None:
            return
        roi = entry["roi"]
        pts = [h.pos() for h in roi.getHandles()]
        if len(pts) < 5:
            return
        center = pts[0]
        keys = ("p_right", "p_up", "p_left", "p_down")
        for g in page._gating_gates:
            if g.get("skewed_id") == skewed_id:
                g["center"] = [center.x(), center.y()]
                for i, key in enumerate(keys):
                    sp = pts[i + 1]
                    g[key] = [sp.x(), sp.y()]

    def _gating_default_name(self, page, tool: str) -> str:
        prefixes = {"rect": "Rect", "ellipse": "Oval", "polygon": "Poly", "range": "Range"}
        count = sum(1 for g in page._gating_gates if g.get("type") == tool) + 1
        return f"{prefixes.get(tool, 'Gate')} {count}"

    def _gating_add_gate(self, page, tool, arg) -> None:
        preview = page._gating_preview
        xr, yr = page._gating_viewbox.viewRange()
        if tool == "quadrant":
            x0, y0 = arg
            crosshair_id = uuid4().hex[:8]
            pen = pg.mkPen(_GATE_PEN, width=2)
            hover = pg.mkPen(_GATE_HOVER_PEN, width=3)
            vline = pg.InfiniteLine(pos=x0, angle=90, movable=True, pen=pen, hoverPen=hover)
            hline = pg.InfiniteLine(pos=y0, angle=0, movable=True, pen=pen, hoverPen=hover)
            page._gating_viewbox.addItem(vline)
            page._gating_viewbox.addItem(hline)
            page._gating_crosshairs[crosshair_id] = {
                "lines": [vline, hline],
                "x": preview.get("x"),
                "y": preview.get("y"),
            }
            for qnum in (1, 2, 3, 4):
                gate = {
                    "id": uuid4().hex[:8],
                    "name": f"Q{qnum}",
                    "type": "quadrant",
                    "quadrant": qnum,
                    "vx": x0,
                    "hy": y0,
                    "crosshair_id": crosshair_id,
                    "x": preview.get("x"),
                    "y": preview.get("y"),
                    "parent": page._gating_parent_id,
                    "xmin": xr[0], "xmax": xr[1],
                    "ymin": yr[0], "ymax": yr[1],
                }
                page._gating_gates.append(gate)
                page._gating_rois.append([])
            vline.sigPositionChangeFinished.connect(
                lambda l=vline, cid=crosshair_id: self._gating_quadrant_sync(page, cid, "vx", l)
            )
            hline.sigPositionChangeFinished.connect(
                lambda l=hline, cid=crosshair_id: self._gating_quadrant_sync(page, cid, "hy", l)
            )
            self._gating_update_roi_visibility(page)
            self._gating_rebuild_tree(page)
            self._gating_set_tool(page, None)
            self._gating_mark_dirty(page)
            return

        if tool == "range":
            x1, x2 = arg
            gate = {
                "id": uuid4().hex[:8],
                "name": self._gating_default_name(page, "range"),
                "type": "range",
                "x": preview.get("x"),
                "y": preview.get("y"),
                "x1": x1,
                "x2": x2,
                "parent": page._gating_parent_id,
                "xmin": xr[0], "xmax": xr[1],
                "ymin": yr[0], "ymax": yr[1],
            }
            pen = pg.mkPen(_GATE_PEN, width=2)
            hover = pg.mkPen(_GATE_HOVER_PEN, width=3)
            vline1 = pg.InfiniteLine(pos=x1, angle=90, movable=True, pen=pen, hoverPen=hover)
            vline2 = pg.InfiniteLine(pos=x2, angle=90, movable=True, pen=pen, hoverPen=hover)
            page._gating_viewbox.addItem(vline1)
            page._gating_viewbox.addItem(vline2)
            vline1.sigPositionChangeFinished.connect(
                lambda l=vline1, g=gate: g.__setitem__("x1", l.value())
            )
            vline2.sigPositionChangeFinished.connect(
                lambda l=vline2, g=gate: g.__setitem__("x2", l.value())
            )
            page._gating_gates.append(gate)
            page._gating_rois.append([vline1, vline2])
            self._gating_set_items_handles_visible([vline1, vline2], False)
            self._gating_update_roi_visibility(page)
            self._gating_rebuild_tree(page)
            self._gating_set_tool(page, None)
            self._gating_mark_dirty(page)
            return

        if tool == "split":
            x0, _ = arg
            split_id = uuid4().hex[:8]
            pen = pg.mkPen(_GATE_PEN, width=2)
            hover = pg.mkPen(_GATE_HOVER_PEN, width=3)
            vline = pg.InfiniteLine(pos=x0, angle=90, movable=True, pen=pen, hoverPen=hover)
            page._gating_viewbox.addItem(vline)
            page._gating_split_lines[split_id] = {
                "lines": [vline],
                "x": preview.get("x"),
                "y": preview.get("y"),
            }
            for side, name in (("left", "Left"), ("right", "Right")):
                gate = {
                    "id": uuid4().hex[:8],
                    "name": name,
                    "type": "split",
                    "side": side,
                    "vx": x0,
                    "split_id": split_id,
                    "x": preview.get("x"),
                    "y": preview.get("y"),
                    "parent": page._gating_parent_id,
                    "xmin": xr[0], "xmax": xr[1],
                    "ymin": yr[0], "ymax": yr[1],
                }
                page._gating_gates.append(gate)
                page._gating_rois.append([])
            vline.sigPositionChangeFinished.connect(
                lambda l=vline, cid=split_id: self._gating_split_sync(page, cid, l)
            )
            self._gating_update_roi_visibility(page)
            self._gating_rebuild_tree(page)
            self._gating_set_tool(page, None)
            self._gating_mark_dirty(page)
            return

        if tool == "skewed_quadrant":
            center, p_right, p_up, p_left, p_down = arg
            cx, cy = center
            skewed_id = uuid4().hex[:8]
            roi = _SkewedQuadrantROI(
                center, p_right, p_up, p_left, p_down, **_gate_roi_pens(),
            )
            self._gating_style_handles(roi)
            page._gating_viewbox.addItem(roi, ignoreBounds=True)
            page._gating_skewed_lines[skewed_id] = {
                "roi": roi,
                "x": preview.get("x"),
                "y": preview.get("y"),
            }
            for qnum in (1, 2, 3, 4):
                gate = {
                    "id": uuid4().hex[:8],
                    "name": f"Q{qnum}",
                    "type": "skewed_quadrant",
                    "quadrant": qnum,
                    "center": [cx, cy],
                    "p_right": list(p_right),
                    "p_up": list(p_up),
                    "p_left": list(p_left),
                    "p_down": list(p_down),
                    "skewed_id": skewed_id,
                    "x": preview.get("x"),
                    "y": preview.get("y"),
                    "parent": page._gating_parent_id,
                    "xmin": xr[0], "xmax": xr[1],
                    "ymin": yr[0], "ymax": yr[1],
                }
                page._gating_gates.append(gate)
                page._gating_rois.append([])
            roi.sigRegionChangeFinished.connect(
                lambda: self._gating_skewed_sync(page, skewed_id)
            )
            self._gating_set_items_handles_visible([roi], False)
            self._gating_update_roi_visibility(page)
            self._gating_rebuild_tree(page)
            self._gating_set_tool(page, None)
            self._gating_mark_dirty(page)
            return

        gate = {
            "id": uuid4().hex[:8],
            "name": self._gating_default_name(page, tool),
            "type": tool,
            "x": preview.get("x"),
            "y": preview.get("y"),
            "parent": page._gating_parent_id,
            "xmin": xr[0], "xmax": xr[1],
            "ymin": yr[0], "ymax": yr[1],
        }
        self._gating_sync_gate(gate, arg)
        self._gating_style_handles(arg)
        arg.sigRegionChangeFinished.connect(
            lambda r=arg, g=gate: self._gating_sync_gate(g, r)
        )
        page._gating_gates.append(gate)
        page._gating_rois.append([arg])
        self._gating_set_items_handles_visible([arg], False)
        self._gating_update_roi_visibility(page)
        self._gating_rebuild_tree(page)
        self._gating_set_tool(page, None)
        self._gating_mark_dirty(page)

    def _gating_style_handles(self, roi) -> None:
        """Make a gate ROI's handles round and a bit larger."""
        roi.handleSize = 10
        for h in roi.handles:
            item = h["item"]
            item.radius = 10
            item.sides = 24
            item.buildPath()

    def _gating_set_items_handles_visible(self, items, visible: bool) -> None:
        """Show a gate's handles/nodes only when selected."""
        for item in items:
            if isinstance(item, pg.InfiniteLine):
                item.setMovable(visible)
            else:
                for h in item.handles:
                    h["item"].setVisible(visible)

    def _gating_descendant_ids(self, page, gate_id) -> set:
        """Return the set of a gate's descendant ids (not including itself)."""
        by_parent = {}
        for g in page._gating_gates:
            by_parent.setdefault(g.get("parent"), []).append(g)
        result = set()
        stack = [gate_id]
        while stack:
            pid = stack.pop()
            for child in by_parent.get(pid, []):
                if child["id"] not in result:
                    result.add(child["id"])
                    stack.append(child["id"])
        return result

    def _gating_visible_ids(self, page):
        """Return the set of visible gate ids for the current view mode, or None."""
        active = page._gating_active_gate_id
        if active:
            # Subset view: show only the gate's descendants (not the gate/ancestors).
            return self._gating_descendant_ids(page, active)
        context = page._gating_context_gate_id
        if context:
            ctx = next((g for g in page._gating_gates if g["id"] == context), None)
            if ctx is not None:
                p = ctx.get("parent")
                return {g["id"] for g in page._gating_gates if g.get("parent") == p}
            return set()
        return None

    def _gating_update_roi_visibility(self, page) -> None:
        """Show gates bound to the current (x, y) axes and gating-tree context."""
        x = page._gating_preview.get("x")
        y = page._gating_preview.get("y")
        visible_ids = self._gating_visible_ids(page)
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
                item.setVisible(visible)
        for cid, entry in page._gating_crosshairs.items():
            xy_ok = entry.get("x") == x and entry.get("y") == y
            hier_ok = visible_crosshairs is None or cid in visible_crosshairs
            visible = xy_ok and hier_ok
            for line in entry["lines"]:
                line.setVisible(visible)
        for sid, entry in page._gating_split_lines.items():
            xy_ok = entry.get("x") == x and entry.get("y") == y
            hier_ok = visible_splits is None or sid in visible_splits
            visible = xy_ok and hier_ok
            for line in entry["lines"]:
                line.setVisible(visible)
        for sid, entry in page._gating_skewed_lines.items():
            xy_ok = entry.get("x") == x and entry.get("y") == y
            hier_ok = visible_skewed is None or sid in visible_skewed
            visible = xy_ok and hier_ok
            entry["roi"].setVisible(visible)

    def _gating_load_rois(self, page) -> None:
        viewbox = page._gating_viewbox
        page._gating_rois = []
        page._gating_crosshairs = {}
        page._gating_split_lines = {}
        page._gating_skewed_lines = {}
        for gate in page._gating_gates:
            items = []
            t = gate.get("type")
            try:
                if t == "rect":
                    roi = pg.RectROI(
                        (gate["pos"][0], gate["pos"][1]),
                        (gate["size"][0], gate["size"][1]),
                        **_gate_roi_pens(),
                    )
                    self._gating_style_handles(roi)
                    roi.sigRegionChangeFinished.connect(
                        lambda r=roi, g=gate: self._gating_sync_gate(g, r)
                    )
                    items = [roi]
                elif t == "ellipse":
                    roi = pg.EllipseROI(
                        (gate["pos"][0], gate["pos"][1]),
                        (gate["size"][0], gate["size"][1]),
                        **_gate_roi_pens(),
                    )
                    self._gating_style_handles(roi)
                    roi.sigRegionChangeFinished.connect(
                        lambda r=roi, g=gate: self._gating_sync_gate(g, r)
                    )
                    items = [roi]
                elif t == "polygon":
                    roi = _GatePolygonROI(
                        [tuple(p) for p in gate["points"]],
                        closed=True, **_gate_roi_pens(),
                    )
                    self._gating_style_handles(roi)
                    roi.sigRegionChangeFinished.connect(
                        lambda r=roi, g=gate: self._gating_sync_gate(g, r)
                    )
                    items = [roi]
                elif t == "quadrant":
                    cid = gate.get("crosshair_id")
                    if cid and cid not in page._gating_crosshairs:
                        pen = pg.mkPen(_GATE_PEN, width=2)
                        hover = pg.mkPen(_GATE_HOVER_PEN, width=3)
                        vline = pg.InfiniteLine(pos=gate.get("vx", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                        hline = pg.InfiniteLine(pos=gate.get("hy", 0), angle=0, movable=True, pen=pen, hoverPen=hover)
                        viewbox.addItem(vline)
                        viewbox.addItem(hline)
                        page._gating_crosshairs[cid] = {
                            "lines": [vline, hline],
                            "x": gate.get("x"),
                            "y": gate.get("y"),
                        }
                        vline.sigPositionChangeFinished.connect(
                            lambda l=vline, c=cid: self._gating_quadrant_sync(page, c, "vx", l)
                        )
                        hline.sigPositionChangeFinished.connect(
                            lambda l=hline, c=cid: self._gating_quadrant_sync(page, c, "hy", l)
                        )
                    items = []
                elif t == "range":
                    pen = pg.mkPen(_GATE_PEN, width=2)
                    hover = pg.mkPen(_GATE_HOVER_PEN, width=3)
                    vline1 = pg.InfiniteLine(pos=gate.get("x1", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                    vline2 = pg.InfiniteLine(pos=gate.get("x2", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                    viewbox.addItem(vline1)
                    viewbox.addItem(vline2)
                    vline1.sigPositionChangeFinished.connect(
                        lambda l=vline1, g=gate: g.__setitem__("x1", l.value())
                    )
                    vline2.sigPositionChangeFinished.connect(
                        lambda l=vline2, g=gate: g.__setitem__("x2", l.value())
                    )
                    items = [vline1, vline2]
                elif t == "split":
                    sid = gate.get("split_id")
                    if sid and sid not in page._gating_split_lines:
                        pen = pg.mkPen(_GATE_PEN, width=2)
                        hover = pg.mkPen(_GATE_HOVER_PEN, width=3)
                        vline = pg.InfiniteLine(pos=gate.get("vx", 0), angle=90, movable=True, pen=pen, hoverPen=hover)
                        viewbox.addItem(vline)
                        page._gating_split_lines[sid] = {
                            "lines": [vline],
                            "x": gate.get("x"),
                            "y": gate.get("y"),
                        }
                        vline.sigPositionChangeFinished.connect(
                            lambda l=vline, c=sid: self._gating_split_sync(page, c, l)
                        )
                    items = []
                elif t == "skewed_quadrant":
                    sid = gate.get("skewed_id")
                    if sid and sid not in page._gating_skewed_lines:
                        cx, cy = gate.get("center", (0, 0))
                        roi = _SkewedQuadrantROI(
                            (cx, cy),
                            gate.get("p_right", (cx, cy)),
                            gate.get("p_up", (cx, cy)),
                            gate.get("p_left", (cx, cy)),
                            gate.get("p_down", (cx, cy)),
                            **_gate_roi_pens(),
                        )
                        self._gating_style_handles(roi)
                        viewbox.addItem(roi, ignoreBounds=True)
                        page._gating_skewed_lines[sid] = {
                            "roi": roi,
                            "x": gate.get("x"),
                            "y": gate.get("y"),
                        }
                        roi.sigRegionChangeFinished.connect(
                            lambda c=sid: self._gating_skewed_sync(page, c)
                        )
                    items = []
            except (KeyError, IndexError, TypeError):
                items = []
            for item in items:
                viewbox.addItem(item)
            self._gating_set_items_handles_visible(items, False)
            page._gating_rois.append(items)

    def _gating_delete_selected(self, page) -> None:
        items = page._gating_tree.selectedItems()
        if not items:
            return
        idx = items[0].data(0, Qt.ItemDataRole.UserRole)
        self._gating_delete_gate(page, idx, cascade=True)

    def _gating_delete_gate(self, page, idx, cascade: bool) -> None:
        """Delete a gate. If `cascade`, also delete all its descendants."""
        if idx is None or idx < 0 or idx >= len(page._gating_gates):
            return
        gate = page._gating_gates[idx]
        gate_id = gate["id"]
        parent_id = gate.get("parent")

        to_delete = {gate_id}
        if cascade:
            changed = True
            while changed:
                changed = False
                for g in page._gating_gates:
                    if g.get("parent") in to_delete and g["id"] not in to_delete:
                        to_delete.add(g["id"])
                        changed = True
        else:
            # Keep children; re-parent them to this gate's parent.
            for g in page._gating_gates:
                if g.get("parent") == gate_id:
                    g["parent"] = parent_id

        indices = [i for i, g in enumerate(page._gating_gates) if g["id"] in to_delete]
        for i in reversed(indices):
            for item in page._gating_rois[i]:
                page._gating_viewbox.removeItem(item)
            del page._gating_gates[i]
            del page._gating_rois[i]
        self._gating_prune_crosshairs(page)
        page._gating_parent_id = None
        page._gating_active_gate_id = None
        page._gating_context_gate_id = None
        self._gating_rebuild_tree(page)
        self._gating_mark_dirty(page)

    def _gating_prune_crosshairs(self, page) -> None:
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

    def _gating_gate_chain(self, page, gate_id) -> list:
        """Return the gates from root to `gate_id` (inclusive), in order."""
        by_id = {g["id"]: g for g in page._gating_gates}
        chain = []
        cur = by_id.get(gate_id)
        while cur is not None:
            chain.append(cur)
            cur = by_id.get(cur.get("parent"))
        chain.reverse()
        return chain

    def _gating_compute_mask(self, page, gate_id, parquet_path):
        """Return the AND mask of a gate and all its ancestors (on transformed data)."""
        import numpy as np
        import pandas as pd
        chain = self._gating_gate_chain(page, gate_id)
        transforms = self._upstream_scale_transforms(page._gating_task_id)
        needed = set()
        for g in chain:
            if g.get("x"):
                needed.add(g["x"])
            if g.get("y"):
                needed.add(g["y"])
        if not needed:
            return None
        try:
            df = pd.read_parquet(parquet_path, columns=list(needed))
        except Exception:
            return None
        mask = None
        for g in chain:
            x_name = g.get("x")
            y_name = g.get("y")
            if not x_name or not y_name:
                continue
            x = pd.to_numeric(df[x_name], errors="coerce").to_numpy(dtype="float64")
            y = pd.to_numeric(df[y_name], errors="coerce").to_numpy(dtype="float64")
            x = _apply_scale_transforms(x, transforms.get(x_name))
            y = _apply_scale_transforms(y, transforms.get(y_name))
            gm = _gate_mask(x, y, g)
            mask = gm if mask is None else (mask & gm)
        return mask

    def _gating_context_mask(self, page, gate_id, parquet_path):
        """Return the parent's population mask (the points the parent includes)."""
        gate = next((g for g in page._gating_gates if g["id"] == gate_id), None)
        if gate is None or not gate.get("parent"):
            return None  # top-level gate -> all events
        return self._gating_compute_mask(page, gate.get("parent"), parquet_path)

    def _gating_on_tree_double_clicked(self, page, item, column) -> None:
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        preview = page._gating_preview
        self._gating_cancel_pending_render(page)
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
            if gate.get("x"):
                preview["x"] = gate["x"]
            if gate.get("y"):
                preview["y"] = gate["y"]
        self._gating_render(page)
        self._gating_rebuild_settings(page)

    def _gating_tree_context_menu(self, page, pos) -> None:
        tree = page._gating_tree
        item = tree.itemAt(pos)
        if item is None:
            return
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if idx is None or idx < 0 or idx >= len(page._gating_gates):
            return
        menu = QMenu(tree)
        ren_act = QAction("Rename", menu)
        ren_act.triggered.connect(lambda: self._gating_rename_gate(page, idx))
        menu.addAction(ren_act)
        del_one = QAction("Delete this gate", menu)
        del_one.triggered.connect(lambda: self._gating_delete_gate(page, idx, cascade=False))
        menu.addAction(del_one)
        del_all = QAction("Delete gate and all children", menu)
        del_all.triggered.connect(lambda: self._gating_delete_gate(page, idx, cascade=True))
        menu.addAction(del_all)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _gating_on_tree_selection(self, page, item, column) -> None:
        """Single-click: open the gate's drawing context (axes + range + parent points)."""
        idx = item.data(0, Qt.ItemDataRole.UserRole) if item else -1
        page._gating_active_gate_id = None  # single-click clears any subset view
        preview = page._gating_preview
        if idx is not None and isinstance(idx, int) and 0 <= idx < len(page._gating_gates):
            gate = page._gating_gates[idx]
            page._gating_context_gate_id = gate["id"]
            # New gates drawn here are siblings of this gate (same parent level).
            page._gating_parent_id = gate.get("parent")
            if gate.get("x"):
                preview["x"] = gate["x"]
            if gate.get("y"):
                preview["y"] = gate["y"]
            preview["xmin"] = gate.get("xmin")
            preview["xmax"] = gate.get("xmax")
            preview["ymin"] = gate.get("ymin")
            preview["ymax"] = gate.get("ymax")
        else:
            page._gating_context_gate_id = None
            page._gating_parent_id = None
            # "All Events": show all points using the Plot settings' axes/file.
            preview["x"] = page._gating_settings_x
            preview["y"] = page._gating_settings_y
            preview["xmin"] = None
            preview["xmax"] = None
            preview["ymin"] = None
            preview["ymax"] = None
        selected = idx if isinstance(idx, int) and idx >= 0 else -1
        for i, gate_items in enumerate(page._gating_rois):
            self._gating_set_items_handles_visible(gate_items, i == selected)
        # Skewed-quadrant handles live on a shared ROI, not per-gate items.
        selected_skewed = None
        if isinstance(idx, int) and 0 <= idx < len(page._gating_gates):
            selected_skewed = page._gating_gates[idx].get("skewed_id")
        for sid, entry in page._gating_skewed_lines.items():
            self._gating_set_items_handles_visible(
                [entry["roi"]], sid == selected_skewed
            )
        # Defer the (heavy) render so a double-click on the same item is
        # detected first and can take over the view.
        self._gating_cancel_pending_render(page)
        page._gating_render_timer = QTimer()
        page._gating_render_timer.setSingleShot(True)
        page._gating_render_timer.setInterval(QApplication.doubleClickInterval())
        page._gating_render_timer.timeout.connect(
            lambda: self._gating_render_and_settings(page)
        )
        page._gating_render_timer.start()

    def _gating_cancel_pending_render(self, page) -> None:
        timer = getattr(page, "_gating_render_timer", None)
        if timer is not None:
            timer.stop()
            page._gating_render_timer = None

    def _gating_render_and_settings(self, page) -> None:
        self._gating_render(page)
        self._gating_rebuild_settings(page)

    def _gating_rename_gate(self, page, idx) -> None:
        if idx is None or idx < 0 or idx >= len(page._gating_gates):
            return
        gate = page._gating_gates[idx]
        text, ok = QInputDialog.getText(
            self, "Rename gate", "Name:", QLineEdit.EchoMode.Normal,
            gate.get("name", ""),
        )
        if ok and text.strip():
            gate["name"] = text.strip()
            self._gating_rebuild_tree(page)
            self._gating_mark_dirty(page)

    def _gating_rebuild_tree(self, page) -> None:
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

        def add_children(parent_item, parent_id):
            for idx, gate in by_parent.get(parent_id, []):
                item = QTreeWidgetItem([gate.get("name", f"Gate {idx + 1}")])
                item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )
                item.setData(0, Qt.ItemDataRole.UserRole, idx)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, gate["id"])
                parent_item.addChild(item)
                add_children(item, gate["id"])

        add_children(root, None)
        root.setExpanded(True)
        tree.blockSignals(False)
        for gate_items in page._gating_rois:
            self._gating_set_items_handles_visible(gate_items, False)
        for entry in page._gating_skewed_lines.values():
            self._gating_set_items_handles_visible([entry["roi"]], False)

    def _gating_render(self, page) -> None:
        """Render the gating scatter for the current view mode."""
        transforms = self._upstream_scale_transforms(page._gating_task_id)
        rec = self._figure_record(page._gating_preview.get("file"))
        preview = page._gating_preview

        # Show a gray overlay when the current file lacks x/y parameters.
        cols = self._columns_for(rec.get("parquet")) if rec else []
        missing = [
            str(n) for n in (preview.get("x"), preview.get("y"))
            if n and n not in cols
        ]
        if missing:
            page._gating_overlay_box.setText(
                "Parameter not in this file:\n" + ", ".join(missing)
            )
            page._gating_overlay.show()
        else:
            page._gating_overlay.hide()

        mask = None
        active = page._gating_active_gate_id
        context = page._gating_context_gate_id
        if active:
            # Subset view: show only the double-clicked gate's points.
            if rec:
                mask = self._gating_compute_mask(page, active, rec["parquet"])
        elif context:
            # Editing context: show the parent's points (not the gate's own).
            if rec:
                mask = self._gating_context_mask(page, context, rec["parquet"])
        # Preserve the current view range for the subset view (don't auto-fit).
        preserved = None
        if active:
            preserved = page._gating_viewbox.viewRange()
        self._figure_render_scatter(
            page._gating_preview, page._gating_plot_widget,
            column_transforms=transforms, mask=mask,
        )
        if preserved is not None:
            xr, yr = preserved
            page._gating_viewbox.setXRange(xr[0], xr[1], padding=0)
            page._gating_viewbox.setYRange(yr[0], yr[1], padding=0)
        self._gating_update_roi_visibility(page)

    def _gating_param_combo(self, columns, current, enabled):
        """A parameter combo that also shows `current` when it isn't in `columns`."""
        combo = self._figure_param_combo(columns, current, enabled)
        if current is not None and combo.findText(str(current)) < 0:
            combo.addItem(str(current))
            combo.setCurrentIndex(combo.count() - 1)
        return combo

    def _gating_rebuild_settings(self, page) -> None:
        layout = page._gating_settings_layout
        self._clear_layout(layout)
        preview = page._gating_preview
        enabled = not page._gating_locked
        plot_widget = page._gating_plot_widget

        records = self._imported_files()
        rec = self._figure_record(preview.get("file"))
        columns = self._columns_for(rec.get("parquet")) if rec else []

        layout.addWidget(QLabel("File"))
        file_combo = QComboBox()
        for r in records:
            file_combo.addItem(r.get("name", ""), r.get("parquet", ""))
        if preview.get("file") is not None:
            idx = file_combo.findData(preview["file"])
            if idx >= 0:
                file_combo.setCurrentIndex(idx)
        file_combo.setEnabled(enabled)
        layout.addWidget(file_combo)

        layout.addWidget(QLabel("X parameter"))
        x_combo = self._gating_param_combo(columns, preview.get("x"), enabled)
        layout.addWidget(x_combo)

        layout.addWidget(QLabel("Y parameter"))
        y_combo = self._gating_param_combo(columns, preview.get("y"), enabled)
        layout.addWidget(y_combo)

        self._figure_scatter_color_settings(
            layout, preview, plot_widget, columns, enabled,
            on_change=lambda: self._gating_render(page),
            on_rebuild=lambda: (
                self._gating_rebuild_settings(page),
                self._gating_render(page),
            ),
        )

        layout.addWidget(QLabel("X range"))
        layout.addWidget(self._figure_range_row(preview, "x", plot_widget, enabled))
        layout.addWidget(QLabel("Y range"))
        layout.addWidget(self._figure_range_row(preview, "y", plot_widget, enabled))

        self._figure_display_toggles(
            layout, preview, enabled,
            on_change=lambda: self._gating_render(page),
        )
        layout.addStretch()

        def on_file_changed():
            preview["file"] = file_combo.currentData()
            preview["file_name"] = file_combo.currentText()
            page._gating_active_gate_id = None
            page._gating_context_gate_id = None
            page._gating_parent_id = None
            # Keep the current x/y axes and visible range when switching files.
            vb = page._gating_viewbox
            xr, yr = vb.viewRange()
            self._gating_render(page)
            vb.setXRange(xr[0], xr[1], padding=0)
            vb.setYRange(yr[0], yr[1], padding=0)
            self._gating_rebuild_settings(page)
        file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())

        def on_param(attr):
            def handler(value):
                preview[attr] = value
                if attr == "x":
                    page._gating_settings_x = value
                elif attr == "y":
                    page._gating_settings_y = value
                page._gating_active_gate_id = None
                page._gating_context_gate_id = None
                page._gating_parent_id = None
                self._gating_render(page)
            return handler
        x_combo.currentTextChanged.connect(on_param("x"))
        y_combo.currentTextChanged.connect(on_param("y"))

    def _gating_mark_dirty(self, page) -> None:
        page._gating_dirty = True
        self._gating_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _gating_save_now(self, page) -> None:
        page._gating_task_def.setdefault("params", {})["gates"] = page._gating_gates
        if self.project is not None:
            self.project.save()
        page._gating_snapshot = copy.deepcopy(page._gating_task_def.get("params", {}))
        page._gating_dirty = False
        self._gating_update_save_btn(page)
        self.statusBar().showMessage("Gating saved")
        self._module_saved(page)

    def _gating_update_save_btn(self, page) -> None:
        page._gating_save_btn.setEnabled(page._gating_dirty and not page._gating_locked)

    def _gating_cleanup(self, page) -> None:
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

    def _gating_close(self, page) -> None:
        if page._gating_dirty:
            reply = QMessageBox.question(
                self,
                "Close Gating",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._gating_task_def["params"] = page._gating_snapshot
        self._gating_cleanup(page)
        self._close_module_page(page)

    def _open_module(self, task_id: str | None, task_type: str | None, name: str) -> None:
        """Open (or focus) the module tab for the given task (identified by id)."""
        for index in range(1, self.tabs.count()):  # skip Workflow at index 0
            page = self.tabs.widget(index)
            if page.property("task_id") == task_id:
                self.tabs.setCurrentIndex(index)
                return

        task_def = self._find_task_by_id(task_id)
        locked = task_def is not None and _task_is_locked(task_def)
        kind = _task_kind(task_type or "")

        if task_type == "import":
            page = self._build_data_import_page()
        elif task_type == "figure":
            page = self._build_figure_page(name, task_def, locked)
        elif task_type == "scale":
            page = self._build_scaling_page(name, task_def, locked)
        elif task_type == "gating":
            page = self._build_gating_page(name, task_def, locked)
        elif kind == "generate":
            page = self._build_generate_page(name, task_def)
        else:
            page = self._build_module_page(name, locked=locked, task_id=task_id)
        page.setProperty("task_id", task_id)
        self.tabs.addTab(page, name)
        self.tabs.setCurrentWidget(page)
        if task_id:
            self._module_register(task_id, task_type, page)

    def _close_module_page(self, page: QWidget) -> None:
        """Close a module page's tab (never the Workflow tab)."""
        task_id = page.property("task_id")
        if task_id:
            self._module_registry.pop(task_id, None)
        index = self.tabs.indexOf(page)
        if index > 0:
            self.tabs.removeTab(index)


def main() -> int:
    """Run the application."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyleSheet(APP_STYLE)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
