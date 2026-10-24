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
import queue
import shutil
import json
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Make the `smage` package importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
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
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
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


class _NoWheelComboBox(QComboBox):
    """A combo box that ignores the mouse wheel, so scrolling the page never
    accidentally changes the selection. Options are changed only by clicking."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class _NoWheelSpinBox(QSpinBox):
    """A number field that ignores the mouse wheel, so scrolling a page never
    accidentally changes a value. It is still edited by typing or the arrows."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    """A floating-point field that ignores the mouse wheel."""

    def wheelEvent(self, event) -> None:
        event.ignore()


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


def _drag_handle_pixmap(color: str = "#8a94a6", size: int = 14) -> QPixmap:
    """Build the grip icon shown before a draggable list item."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    pen = QPen(QColor(color))
    pen.setWidth(1)
    painter.setPen(pen)
    for y in (4, 7, 10):
        painter.drawLine(3, y, size - 4, y)
    painter.end()
    return pixmap


def _comp_cell_color(value, max_positive, min_negative) -> QColor:
    """Compensation-matrix cell colour: positive red, negative blue, 0 white."""
    if value > 0 and max_positive > 0:
        frac = min(1.0, float(value) / float(max_positive))
        shade = int(255 - 190 * frac)
        return QColor(255, shade, shade)
    if value < 0 and min_negative < 0:
        frac = min(1.0, float(value) / float(min_negative))
        shade = int(255 - 190 * frac)
        return QColor(shade, shade, 255)
    return QColor(255, 255, 255)


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
    "illumination": ("Illumination Correction", "#8d6e63"),
    "feature": ("Feature", "#ec407a"),
    "subset": ("Subset", "#5c6bc0"),
    "subsample": ("Subsample", "#78909c"),
    "dimred": ("Dimension Reduction", "#ab47bc"),
    "cluster": ("Cluster", "#ef5350"),
    "export": ("Export", "#66bb6a"),
}

ADDABLE_TYPES = [
    "gating",
    "figure",
    "scale",
    "rename",
    "unmix",
    "segmentation",
    "compensation",
    "illumination",
    "feature",
    "subset",
    "subsample",
    "dimred",
    "cluster",
    "export",
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

# Distinct colours auto-assigned to image channels as they are overlaid.
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

# Figure module histogram display modes.
FIGURE_HISTOGRAM_MODES = [
    ("Histogram", "histogram"),
    ("Curve fit", "curve"),
    ("Overlap", "overlap"),
]

# Sentinel used by the Figure multi-select settings panel: the selected plots
# disagree on a value, so the field shows blank instead of any one value.
_MIXED = object()

# Scaling (map) module: available transforms.
SCALING_METHODS = ["linear", "log10", "arcsinh"]

# Segmentation module: methods and mask storage.
SEG_METHODS = [
    ("Nuclear Expansion", "nuclear_expansion"),
    ("Mask Input", "mask_input"),
    ("Cellpose-CPU", "cellpose-cpu"),
    ("Cellpose-GPU", "cellpose-gpu"),
    ("StarDist-CPU", "stardist-cpu"),
]
# Per-file mask status shown in the file table.
SEG_STATUS_NO_MASK = "no mask"
SEG_STATUS_PENDING = "pending"
SEG_STATUS_APPLIED = "applied"
# Mask Input import states (shown in the mask-input table).
SEG_IMPORT_SELECT = "Select a file"
SEG_IMPORT_WAITING = "Waiting for import"
SEG_IMPORT_DONE = "Imported"
SEG_IMPORT_FORMAT = "Format error — choose another file"
SEG_IMPORT_SIZE = "Size mismatch — choose another file"
SEG_IMPORT_UNKNOWN = "Unknown error — try again"
# Default Nuclear Expansion parameters.
SEG_DEFAULT_PARAMS = {
    "channel": None,
    "threshold_mode": "otsu",
    "threshold": 0.0,
    "min_area": 30,
    "expand": 5,
}

# Extensions: external Python environments managed outside the workflow. Each
# entry describes one optional method plus how to run/install it. `deps` are
# the heavy framework packages that must also be present (installed first,
# checked by Detect).
EXTENSION_DEFS = [
    {
        "id": "cellpose-cpu",
        "name": "Cellpose-CPU",
        "family": "cellpose",
        "device": "cpu",
        "pip": "cellpose",
        "deps": ["torch", "torchvision"],
        "runner": "cellpose_runner.py",
        "model": "cpsam",
        "desc": "Cellpose (SAM-based) cell segmentation on CPU",
    },
    {
        "id": "cellpose-gpu",
        "name": "Cellpose-GPU",
        "family": "cellpose",
        "device": "gpu",
        "pip": "cellpose",
        "deps": ["torch", "torchvision"],
        "runner": "cellpose_runner.py",
        "model": "cpsam",
        "desc": "Cellpose (SAM-based) cell segmentation on GPU (CUDA)",
    },
    {
        "id": "stardist-cpu",
        "name": "StarDist-CPU",
        "family": "stardist",
        "device": "cpu",
        "pip": "stardist",
        "deps": ["tensorflow"],
        "runner": "stardist_runner.py",
        "model": "2D_versatile_fluo",
        "desc": "StarDist star-convex cell segmentation on CPU",
    },
]


def _extension_def(ext_id: str) -> dict | None:
    """Return the registry entry for an extension id, or None."""
    for entry in EXTENSION_DEFS:
        if entry["id"] == ext_id:
            return entry
    return None


# PyTorch publishes CUDA wheels on a per-CUDA-version index. A machine may use
# an index only if its NVIDIA driver supports that CUDA version (or newer).
# Ordered newest-first so the first compatible entry is the "best" choice.
_PYTORCH_CUDA_INDEXES = [
    (12.8, "cu128"),
    (12.6, "cu126"),
    (12.4, "cu124"),
    (12.1, "cu121"),
    (11.8, "cu118"),
]


def _nvidia_cuda_version():
    """Return the CUDA version (float) the installed NVIDIA driver supports,
    or None when no NVIDIA driver is found. Queries ``nvidia-smi``."""
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi") or "nvidia-smi"
    try:
        out = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        if "CUDA Version:" in line:
            try:
                return float(line.split("CUDA Version:")[1].split()[0])
            except (ValueError, IndexError):
                return None
    return None


def _pick_cuda_index(driver_cuda):
    """Return the best cuXXX index dir for a driver's CUDA version, or None."""
    if driver_cuda is None:
        return None
    for minor, dirname in _PYTORCH_CUDA_INDEXES:
        if minor <= driver_cuda:
            return dirname
    return None


def _otsu_threshold(values) -> float:
    """Return an Otsu threshold for a finite 1-D array (0.0 when undecidable)."""
    import numpy as np

    v = np.asarray(values, dtype="float64")
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0
    vmin, vmax = float(v.min()), float(v.max())
    if not vmax > vmin:
        return vmin
    hist, edges = np.histogram(v, bins=256, range=(vmin, vmax))
    hist = hist.astype("float64")
    centers = (edges[:-1] + edges[1:]) / 2.0
    weight = np.cumsum(hist)
    total = weight[-1]
    if total <= 0:
        return vmin
    mean_bg = np.cumsum(hist * centers)
    mean_total = mean_bg[-1]
    # Between-class variance for every split (ignore empty sides).
    w0 = weight[:-1]
    w1 = total - w0
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return vmin
    m0 = np.zeros_like(w0)
    m0[valid] = mean_bg[:-1][valid] / w0[valid]
    m1 = np.zeros_like(w1)
    m1[valid] = (mean_total - mean_bg[:-1][valid]) / w1[valid]
    between = np.zeros_like(w0)
    between[valid] = w0[valid] * w1[valid] * (m0[valid] - m1[valid]) ** 2
    return float(centers[int(np.argmax(between))])


def _relabel_sequential(labels, min_area=1):
    """Drop labels smaller than `min_area` and renumber them 1..N (0 = background)."""
    import numpy as np
    from scipy import ndimage

    if labels.size == 0:
        return labels
    counts = np.bincount(labels.ravel())
    keep = np.zeros(counts.size, dtype=bool)
    if counts.size > 1:
        keep[1:] = counts[1:] >= max(1, int(min_area))
    if not keep.any():
        return np.zeros_like(labels)
    # Map old ids to 1..N in ascending order.
    remap = np.zeros(counts.size, dtype=np.int32)
    remap[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    out = remap[labels]
    # `ndimage.label` already yields contiguous ids, but filtering can leave gaps.
    return out.astype(np.int32)



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


# --- Ordered map-module pipeline (Scale / Compensation / Unmix) -----------

def _pipeline_touches(stages, name) -> bool:
    """Whether any map stage changes or produces `name`."""
    for kind, stage in stages:
        if kind == "scale" and name in stage["columns"]:
            return True
        if kind in ("compensation", "unmix") and name in stage["names"]:
            return True
        if kind == "illumination":
            for sub in stage.get("submodules", []):
                if sub.get("channel") == name:
                    return True
    return False


def _pipeline_dependencies(stages, names) -> set:
    """Columns needed before the stages to obtain `names` after them."""
    needed = set(names)
    for kind, stage in reversed(stages):
        if kind == "compensation":
            if needed & set(stage["names"]):
                needed |= set(stage["names"])
        elif kind == "unmix":
            if needed & set(stage["names"]):
                needed |= set(stage["columns"])
        elif kind == "illumination":
            for sub in stage.get("submodules", []):
                if sub.get("channel") in needed:
                    if sub.get("x"):
                        needed.add(sub["x"])
                    if sub.get("y"):
                        needed.add(sub["y"])
    needed.discard(None)
    return needed


def _linear_combine(sources, weights):
    """Combine arrays with a (outputs × sources) weight matrix.

    Works for 1D arrays and for 2D arrays (image rasters).
    """
    import numpy as np

    if not sources:
        return []
    if sources[0].ndim == 1:
        matrix = np.column_stack(sources)
        return [matrix @ weights[i] for i in range(weights.shape[0])]
    stacked = np.stack(sources, axis=0)
    combined = np.tensordot(weights, stacked, axes=(1, 0))
    return [combined[i] for i in range(combined.shape[0])]


def _evaluate_stages(stages, values, names, parquet_path=None, cache=None,
                     origin=None):
    """Apply map-module stages in order to a {column: array} mapping."""
    import numpy as np

    values = dict(values)
    for kind, stage in stages:
        if kind == "scale":
            for column, config in stage["columns"].items():
                if column in values:
                    values[column] = _apply_scale_transform(
                        values[column], config
                    )
            continue
        if kind == "compensation":
            if parquet_path is not None and parquet_path not in stage["files"]:
                continue
            sources = stage["names"]
            if not all(name in values for name in sources):
                continue
            key = ("comp", stage.get("id"), parquet_path)
            out = cache.get(key) if cache is not None else None
            if out is None:
                combined = _linear_combine(
                    [values[name] for name in sources], stage["matrix"]
                )
                out = dict(zip(sources, combined))
                if cache is not None:
                    cache[key] = out
            values.update(out)
            continue
        if kind == "unmix":
            if parquet_path is not None and parquet_path not in stage["samples"]:
                continue
            sources = stage["columns"]
            if not all(name in values for name in sources):
                continue
            key = ("unmix", stage.get("task_id"), parquet_path)
            out = cache.get(key) if cache is not None else None
            if out is None:
                pinv = stage.get("_pinv")
                if pinv is None:
                    pinv = np.linalg.pinv(stage["matrix"])
                    stage["_pinv"] = pinv
                # C = X · pinv(M): output k = Σ_i X_i · pinv[i, k]
                combined = _linear_combine(
                    [values[name] for name in sources], pinv.T
                )
                out = dict(zip(stage["names"], combined))
                if cache is not None:
                    cache[key] = out
            values.update(out)
            continue
        if kind == "illumination":
            for sub in stage.get("submodules", []):
                if parquet_path is not None and parquet_path not in sub["files"]:
                    continue
                channel = sub.get("channel")
                if channel not in values:
                    continue
                x_values = values.get(sub.get("x"))
                y_values = values.get(sub.get("y"))
                mask = sub.get("mask")
                if x_values is None or y_values is None or mask is None:
                    continue
                if origin is not None:
                    x_origin, y_origin = origin
                else:
                    try:
                        x_origin = float(np.nanmin(x_values))
                        y_origin = float(np.nanmin(y_values))
                    except (TypeError, ValueError):
                        continue
                xi = np.mod(
                    np.rint(np.asarray(x_values, dtype="float64") - x_origin)
                    .astype(np.int64),
                    int(sub["tile_w"]),
                )
                yi = np.mod(
                    np.rint(np.asarray(y_values, dtype="float64") - y_origin)
                    .astype(np.int64),
                    int(sub["tile_h"]),
                )
                factor_map = mask[yi, xi]
                threshold = float(sub.get("T") or 0.0)
                factor = np.where(
                    factor_map > 0,
                    threshold / np.where(factor_map > 0, factor_map, 1.0),
                    1.0,
                )
                values[channel] = values[channel] * factor
    return {name: values[name] for name in names if name in values}


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


def _gate_source_ids(gate):
    """Return the ids of the source gates a boolean gate depends on."""
    if gate.get("type") == "not":
        s = gate.get("source")
        return [s] if s else []
    if gate.get("type") in ("and", "or"):
        return list(gate.get("sources", []))
    return []


def _gate_needed_columns(gate, by_id):
    """Return the x/y column names needed to compute a gate's own mask."""
    cols = set()
    for key in ("x", "y"):
        v = gate.get(key)
        if v:
            cols.add(v)
    for sid in _gate_source_ids(gate):
        src = by_id.get(sid)
        if src is not None:
            for key in ("x", "y"):
                v = src.get(key)
                if v:
                    cols.add(v)
    return cols


def _gate_own_mask(gate, by_id, get_col, transforms, existing):
    """Return a gate's own boolean mask (transformed data), handling boolean
    gates (NOT / AND / OR)."""
    if gate.get("type") == "not":
        src = by_id.get(gate.get("source"))
        if src is None:
            return None
        sx, sy = src.get("x"), src.get("y")
        if not sx or not sy or sx not in existing or sy not in existing:
            return None
        x = _apply_scale_transforms(get_col(sx), transforms.get(sx))
        y = _apply_scale_transforms(get_col(sy), transforms.get(sy))
        return ~_gate_mask(x, y, src)
    if gate.get("type") in ("and", "or"):
        combine_and = gate.get("type") == "and"
        result = None
        for sid in gate.get("sources", []):
            src = by_id.get(sid)
            if src is None:
                continue
            sx, sy = src.get("x"), src.get("y")
            if not sx or not sy or sx not in existing or sy not in existing:
                continue
            x = _apply_scale_transforms(get_col(sx), transforms.get(sx))
            y = _apply_scale_transforms(get_col(sy), transforms.get(sy))
            sm = _gate_mask(x, y, src)
            if result is None:
                result = sm
            elif combine_and:
                result = result & sm
            else:
                result = result | sm
        return result
    x_name, y_name = gate.get("x"), gate.get("y")
    if not x_name or not y_name or x_name not in existing or y_name not in existing:
        return None
    x = _apply_scale_transforms(get_col(x_name), transforms.get(x_name))
    y = _apply_scale_transforms(get_col(y_name), transforms.get(y_name))
    return _gate_mask(x, y, gate)


def _compute_gate_filter_mask(gates, gate_id, parquet_path, transforms,
                              existing=None, extra=None):
    """Return the AND mask of a gate and all its ancestors (transformed data).

    `existing` lists the usable column names and `extra` supplies values for
    columns that are computed on the fly (unmixed parameters). Returns None if
    the gate cannot be computed (no x/y columns). Raises KeyError if a needed
    column is missing.
    """
    import numpy as np
    import pandas as pd
    by_id = {g["id"]: g for g in gates}
    chain = []
    cur = by_id.get(gate_id)
    while cur is not None:
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    chain.reverse()
    if not chain:
        return None
    needed = set()
    for g in chain:
        needed |= _gate_needed_columns(g, by_id)
    if not needed:
        return None
    extra = extra or {}
    if existing is None:
        base_cols = [c for c in sorted(needed) if c not in extra]
        df = pd.read_parquet(parquet_path, columns=base_cols)
        existing = set(df.columns) | set(extra)
    else:
        existing = set(existing)
        base_cols = [
            c for c in sorted(needed) if c in existing and c not in extra
        ]
        df = (
            pd.read_parquet(parquet_path, columns=base_cols)
            if base_cols else pd.DataFrame()
        )
    cache = {}

    def get_col(name):
        if name not in cache:
            if name in extra:
                cache[name] = np.asarray(extra[name], dtype="float64")
            else:
                cache[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).to_numpy(dtype="float64")
        return cache[name]

    mask = None
    for g in chain:
        gm = _gate_own_mask(g, by_id, get_col, transforms, existing)
        if gm is None:
            continue
        mask = gm if mask is None else (mask & gm)
    return mask


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


def _default_gating_preview(rec, cols) -> dict:
    """A fresh scatter preview config for the gating tree (first file + axes)."""
    return {
        "type": "scatter",
        "file": rec.get("parquet") if rec else None,
        "file_name": rec.get("name") if rec else None,
        "x": cols[0] if cols else None,
        "y": cols[1] if len(cols) > 1 else (cols[0] if cols else None),
        "last_x": cols[0] if cols else None,
        "last_y": cols[1] if len(cols) > 1 else (cols[0] if cols else None),
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
        "label_font_size": 20,
        "label_bold": False,
        "label_bg_opacity": 50,
        "show_gate_labels": True,
        "label_show_percent": True,
    }


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


class _GatingStatsWorker:
    """Background worker that computes gate statistics with column/mask caches.

    The worker owns its caches so no shared mutable state is touched by the
    main thread. Requests are queued FIFO; each carries a generation number so
    the main thread can discard stale results.
    """

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._columns = {}   # {parquet_path: {col: float64 ndarray}}
        self._current_path = None
        self._masks = {}     # {gate_id: population bool ndarray}
        self._counts = {}    # {gate_id: int}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, generation, parquet_path, gates, stages, base_columns,
                affected, virtual=None):
        self._req_q.put(
            (generation, parquet_path, gates, stages, set(base_columns),
             affected, dict(virtual or {}))
        )

    def poll(self):
        """Return any finished (generation, stats) results."""
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                req = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                res = self._compute(*req)
            except Exception:
                res = None
            self._res_q.put(res)

    def _compute(self, generation, parquet_path, gates, stages, base_columns,
                 affected, virtual=None):
        import numpy as np
        import pandas as pd
        if not parquet_path or not gates:
            return (generation, {})
        if self._current_path != parquet_path:
            self._columns = {}
            self._masks = {}
            self._counts = {}
            self._current_path = parquet_path
        if affected is None:
            # Full recompute: the gate set may have changed, so reset caches.
            self._masks = {}
            self._counts = {}
        by_id = {g["id"]: g for g in gates}
        needed = set()
        for g in gates:
            needed |= _gate_needed_columns(g, by_id)
        if not needed:
            return (generation, {})
        dependencies = _pipeline_dependencies(stages, needed)
        base_needed = [c for c in dependencies if c in base_columns]
        cols = self._columns
        missing = set(base_needed) - set(cols)
        if missing:
            try:
                df = pd.read_parquet(parquet_path, columns=sorted(missing))
            except Exception:
                return (generation, {})
            for name in missing:
                cols[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).to_numpy(dtype="float64")
            self._columns = cols
        arrays = {name: cols[name] for name in base_needed if name in cols}
        for name, values_arr in (virtual or {}).items():
            arrays.setdefault(name, values_arr)
        arrays = _evaluate_stages(stages, arrays, needed, parquet_path)
        existing = set(arrays)
        if not arrays:
            return (generation, {})
        n_total = len(next(iter(arrays.values())))
        by_parent = {}
        for g in gates:
            by_parent.setdefault(g.get("parent"), []).append(g)

        def own_mask(g):
            return _gate_own_mask(
                g, by_id, lambda name: arrays[name], {}, existing
            )

        def compute_subtree(gid):
            stack = [gid]
            while stack:
                cur = stack.pop()
                g = by_id.get(cur)
                if g is None:
                    continue
                om = own_mask(g)
                if om is None:
                    self._masks[cur] = None
                    self._counts[cur] = None
                    continue
                parent_id = g.get("parent")
                pm = self._masks.get(parent_id) if parent_id else None
                m = om if pm is None else (pm & om)
                self._masks[cur] = m
                self._counts[cur] = int(np.sum(m))
                for child in by_parent.get(cur, []):
                    stack.append(child["id"])

        if affected is None:
            for top in by_parent.get(None, []):
                compute_subtree(top["id"])
        else:
            affected_set = set(affected)
            tops = [
                gid for gid in affected_set
                if by_id.get(gid, {}).get("parent") not in affected_set
            ]
            for gid in tops:
                compute_subtree(gid)

        result = {}
        for g in gates:
            c = self._counts.get(g["id"])
            if c is None:
                continue
            pid = g.get("parent")
            if pid:
                pc = self._counts.get(pid)
                if pc is None:
                    pc = n_total
            else:
                pc = n_total
            pct = (c / pc * 100.0) if pc else 0.0
            result[g["id"]] = (c, pct)
        return (generation, result)


def _figure_colormap(name: str):
    """Return a pyqtgraph ColorMap for a named palette, with a fallback."""
    import pyqtgraph as pg
    try:
        return pg.colormap.get(name)
    except Exception:
        return pg.ColorMap([0.0, 1.0], [(0, 0, 255), (255, 0, 0)])


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
    }]


def _is_scatter_plot(ptype: str) -> bool:
    """Whether a Figure plot type is scatter-like (plots x/y data points)."""
    return ptype in ("scatter", "image")


# --- Image plot: level-of-detail sample index (disk-cached) ----------------

_IMAGE_FINEST_CAP = 2_000_000   # most points in the finest precomputed level
_IMAGE_COARSEST = 4096          # stop coarsening once a level is this small
_IMAGE_POINT_CAP = 200_000      # most points drawn per frame


def _image_index_dir(parquet_path) -> Path:
    return Path(str(parquet_path) + ".imgidx")


def _image_geometry(parquet_path):
    """Return (xmin, xmax, ymin, ymax, total_rows, columns) for an image Parquet.

    x/y extents come from row-group statistics when present (no data is read);
    otherwise the x/y columns are streamed once to compute min/max.
    """
    import numpy as np
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(parquet_path)
    names = list(pf.schema_arrow.names)
    total = int(pf.metadata.num_rows)

    def col_stat(col):
        idx = names.index(col)
        cmin = float("inf")
        cmax = float("-inf")
        found = False
        for rg in range(pf.metadata.num_row_groups):
            st = pf.metadata.row_group(rg).column(idx).statistics
            if (
                st is not None
                and getattr(st, "min", None) is not None
                and getattr(st, "max", None) is not None
            ):
                cmin = min(cmin, float(st.min))
                cmax = max(cmax, float(st.max))
                found = True
        if found:
            return cmin, cmax
        return None

    xs = col_stat("x")
    ys = col_stat("y")
    if xs is not None and ys is not None and total > 0:
        return xs[0], xs[1], ys[0], ys[1], total, names

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for batch in pf.iter_batches(batch_size=1_000_000, columns=["x", "y"]):
        x = batch.column("x").to_numpy()
        y = batch.column("y").to_numpy()
        if x.size:
            xmin = min(xmin, float(np.nanmin(x)))
            xmax = max(xmax, float(np.nanmax(x)))
            ymin = min(ymin, float(np.nanmin(y)))
            ymax = max(ymax, float(np.nanmax(y)))
    if not np.isfinite(xmin):
        raise ValueError("image file has no x/y coordinates")
    return xmin, xmax, ymin, ymax, total, names


def _image_write_level(idx_dir, block, means2d):
    import numpy as np
    fname = f"level_{block}.npy"
    np.save(idx_dir / fname, means2d.astype(np.float32))
    return {"block": int(block), "file": fname}


def _build_image_index_files(parquet_path, progress_cb=None):
    """Build the multi-level raster pyramid for one image file (disk-cached).

    Each level is a 2D array `(H/b, W/b, C)` of per-block channel means (a
    downsampled image). The finest level's block size `b0` is chosen so the
    level fits _IMAGE_FINEST_CAP; coarser levels double the block via 2x2
    pooling. Only one level is materialised in memory at a time.
    """
    import json
    import math
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq

    xmin, xmax, ymin, ymax, total, names = _image_geometry(parquet_path)
    # Store per-block means for every column (including x/y, so the user may
    # color the image by a coordinate too). The default color parameter is
    # still the first non-x/y column, chosen in _figure_add_plot.
    color_cols = list(names)
    width = xmax - xmin + 1.0
    height = ymax - ymin + 1.0
    if width < 1 or height < 1 or total == 0:
        raise ValueError("image file has no pixels")

    block = 1
    while True:
        nbx = int(math.ceil(width / block))
        nby = int(math.ceil(height / block))
        if nbx * nby <= _IMAGE_FINEST_CAP:
            break
        block *= 2
    b0 = block

    idx_dir = _image_index_dir(parquet_path)
    idx_dir.mkdir(parents=True, exist_ok=True)

    pf = pq.ParquetFile(parquet_path)
    ncol = len(color_cols)

    # Finest level: aggregate raw pixels into b0-sized blocks (single pass).
    nbx = int(math.ceil(width / b0))
    nby = int(math.ceil(height / b0))
    nblocks = nbx * nby
    cnt = np.zeros(nblocks, dtype=np.int64)
    sm = np.zeros((nblocks, ncol), dtype=np.float64)
    ch_min = np.full(ncol, np.inf)
    ch_max = np.full(ncol, -np.inf)
    processed = 0
    for batch in pf.iter_batches(batch_size=1_000_000, columns=names):
        tbl = batch.to_pandas()
        x = tbl["x"].to_numpy(dtype="float64")
        y = tbl["y"].to_numpy(dtype="float64")
        bi = ((x - xmin) // b0).astype(np.int64) + ((y - ymin) // b0).astype(np.int64) * nbx
        np.clip(bi, 0, nblocks - 1, out=bi)
        cnt += np.bincount(bi, minlength=nblocks)
        for j, cc in enumerate(color_cols):
            v = pd.to_numeric(tbl[cc], errors="coerce").to_numpy(dtype="float64")
            sm[:, j] += np.bincount(bi, weights=np.nan_to_num(v), minlength=nblocks)
            fin = np.isfinite(v)
            if fin.any():
                ch_min[j] = min(ch_min[j], float(v[fin].min()))
                ch_max[j] = max(ch_max[j], float(v[fin].max()))
        processed += x.size
        if progress_cb is not None:
            progress_cb(min(1.0, processed / total))

    means = (sm / np.maximum(cnt, 1)[:, None]).reshape(nby, nbx, ncol)
    levels = [_image_write_level(idx_dir, b0, means)]

    # Coarser levels by 2x2 count-weighted pooling of the previous level.
    prev = means
    prev_cnt = cnt.reshape(nby, nbx)
    prev_b = b0
    while prev_b < max(width, height):
        nb = prev_b * 2
        h, w = prev.shape[0], prev.shape[1]
        nbx2 = int(math.ceil(w / 2.0))
        nby2 = int(math.ceil(h / 2.0))
        ph, pw = nby2 * 2, nbx2 * 2
        pad_m = np.zeros((ph, pw, ncol), dtype=np.float64)
        pad_c = np.zeros((ph, pw), dtype=np.float64)
        pad_m[:h, :w] = prev * prev_cnt[:, :, None]
        pad_c[:h, :w] = prev_cnt
        sum_m = pad_m.reshape(nby2, 2, nbx2, 2, ncol).sum(axis=(1, 3))
        sum_c = pad_c.reshape(nby2, 2, nbx2, 2).sum(axis=(1, 3))
        prev = sum_m / np.maximum(sum_c, 1)[:, :, None]
        prev_cnt = sum_c
        levels.append(_image_write_level(idx_dir, nb, prev))
        prev_b = nb
        if nby2 * nbx2 <= _IMAGE_COARSEST:
            break

    color_ranges = {}
    for j, cc in enumerate(color_cols):
        color_ranges[cc] = [
            float(ch_min[j]) if np.isfinite(ch_min[j]) else 0.0,
            float(ch_max[j]) if np.isfinite(ch_max[j]) else 1.0,
        ]

    manifest = {
        "version": 2,
        "file_size": int(Path(parquet_path).stat().st_size),
        "file_mtime": float(Path(parquet_path).stat().st_mtime),
        "xmin": float(xmin),
        "xmax": float(xmax),
        "ymin": float(ymin),
        "ymax": float(ymax),
        "total": int(total),
        "color_cols": color_cols,
        "color_ranges": color_ranges,
        "levels": levels,
    }
    with open(idx_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    if progress_cb is not None:
        progress_cb(1.0)
    return manifest


def _image_load_level(parquet_path, info):
    import numpy as np
    return np.load(_image_index_dir(parquet_path) / info["file"])


def _pool_mask(mask, target_blocks):
    """Pool a (H, W) uint8 mask into {block: (nby, nbx) float32 pass-fraction}."""
    import numpy as np
    targets = sorted(int(b) for b in target_blocks)
    if not targets:
        return {}
    out = {}
    m = mask.astype(np.float64)
    c = np.ones(m.shape, dtype=np.float64)
    block = 1
    tset = set(targets)
    if block in tset:
        out[block] = (m / np.maximum(c, 1.0)).astype(np.float32)
    while block < targets[-1]:
        block *= 2
        h, w = m.shape
        nby = (h + 1) // 2
        nbx = (w + 1) // 2
        ph, pw = nby * 2, nbx * 2
        pm = np.zeros((ph, pw), dtype=np.float64)
        pc = np.zeros((ph, pw), dtype=np.float64)
        pm[:h, :w] = m
        pc[:h, :w] = c
        m = pm.reshape(nby, 2, nbx, 2).sum(axis=(1, 3))
        c = pc.reshape(nby, 2, nbx, 2).sum(axis=(1, 3))
        if block in tset:
            out[block] = (m / np.maximum(c, 1.0)).astype(np.float32)
    return out


def _illumination_stage_item(sub):
    """Convert one Illumination submodule into a pipeline stage entry."""
    import numpy as np

    channel = sub.get("channel")
    mask_data = sub.get("mask")
    if not channel or not mask_data:
        return None
    try:
        mask = np.asarray(mask_data, dtype="float64")
    except (TypeError, ValueError):
        return None
    if mask.ndim != 2 or mask.size == 0:
        return None
    finite = mask[np.isfinite(mask)]
    if finite.size == 0:
        return None
    method = sub.get("method", "median")
    if method == "down":
        threshold = float(finite.min())
    elif method == "up":
        threshold = float(finite.max())
    else:
        threshold = float(np.median(finite))
    return {
        "channel": channel,
        "x": sub.get("x"),
        "y": sub.get("y"),
        "files": set(sub.get("files") or []),
        "tile_w": int(sub.get("tile_w", 256)),
        "tile_h": int(sub.get("tile_h", 256)),
        "mask": mask,
        "T": threshold,
    }


class _FlowArrowBand(QWidget):
    """Narrow vertical strip with downward arrows (data-flow hint)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(22)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#9aa4b2"))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        center = self.width() / 2.0
        y = 10
        while y + 13 < self.height():
            painter.drawLine(int(center), y, int(center), y + 9)
            painter.drawPolyline(QPolygonF([
                QPointF(center - 4, y + 8),
                QPointF(center, y + 13),
                QPointF(center + 4, y + 8),
            ]))
            y += 30
        painter.end()


class _ProcessWorker:
    """Background worker that runs one subprocess command to completion.

    Used to run external Python commands (venv creation, pip install, model
    download, verification and segmentation) without blocking the UI.
    """

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def run(self, key, argv, cwd=None):
        self._req_q.put((key, list(argv), cwd))

    def poll(self):
        """Return finished events: ``(key, "line", text)`` for streaming output
        and ``(key, "done", returncode, text)`` when the command completes."""
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def clear(self):
        """Drop any unread results (e.g. from a previous, finished operation)."""
        while True:
            try:
                self._res_q.get_nowait()
            except queue.Empty:
                break

    def kill(self):
        """Terminate the in-flight subprocess (if any)."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _run(self):
        import subprocess

        while not self._stop.is_set():
            try:
                key, argv, cwd = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    # Run without a console window (the external python is a
                    # console app; this hides the flash that would otherwise
                    # pop up on Windows).
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._proc = proc
                chunks = []
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    if line:
                        chunks.append(line)
                        self._res_q.put((key, "line", line))
                proc.wait()
                self._res_q.put(
                    (key, "done", int(proc.returncode), "\n".join(chunks))
                )
            except Exception as exc:  # noqa: BLE001
                self._res_q.put((key, "done", -1, str(exc)))
            finally:
                self._proc = None


class _ExtManagerDialog(QDialog):
    """Extension Manager dialog that warns before closing mid-operation."""

    def __init__(self, parent, busy_check) -> None:
        super().__init__(parent)
        self._busy_check = busy_check

    def closeEvent(self, event) -> None:
        if self._busy_check():
            reply = QMessageBox.question(
                self,
                "Operation in progress",
                "An installation is still running.\n"
                "Closing now will interrupt it and may leave the environment "
                "incomplete. Really close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        # Reject (not just accept) so the `finished` signal fires, which stops
        # the poll timer. Otherwise the timer keeps draining the shared worker
        # queue and steals results from other consumers (e.g. segmentation).
        self.reject()
        event.accept()


class _IlluminationWorker:
    """Background worker that estimates an illumination mask from tiles."""

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, key, stages, base_columns, files, channel, x_name,
                y_name, tile_w, tile_h, bins, resolution):
        self._req_q.put((
            key, stages, base_columns, list(files), channel, x_name, y_name,
            int(tile_w), int(tile_h), int(bins), int(resolution),
        ))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                req = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                res = self._compute(*req)
            except Exception as exc:  # noqa: BLE001
                res = (req[0], None, f"Could not calculate the mask: {exc}")
            self._res_q.put(res)

    def _compute(self, key, stages, base_columns, files, channel, x_name,
                 y_name, tile_w, tile_h, bins, resolution):
        import numpy as np
        import pandas as pd
        from scipy.interpolate import RectBivariateSpline, griddata

        if not files:
            return (key, None, "No files selected for the calculation.")
        n_blocks = bins * bins
        sums = np.zeros(n_blocks, dtype="float64")
        counts = np.zeros(n_blocks, dtype="float64")
        used_files = 0
        for path in files:
            needed = {x_name, y_name, channel}
            dependencies = _pipeline_dependencies(stages, needed)
            base = base_columns.get(path) or set()
            if not base:
                try:
                    import pyarrow.parquet as pq
                    base = set(pq.ParquetFile(path).schema.names)
                except Exception:  # noqa: BLE001
                    continue
            base = [c for c in dependencies if c in base]
            values = {}
            if base:
                try:
                    df = pd.read_parquet(path, columns=base)
                except Exception:  # noqa: BLE001
                    continue
                for name in base:
                    values[name] = pd.to_numeric(
                        df[name], errors="coerce"
                    ).to_numpy(dtype="float64")
            values = _evaluate_stages(stages, values, needed, path)
            if not all(name in values for name in needed):
                continue
            x = np.asarray(values[x_name], dtype="float64")
            y = np.asarray(values[y_name], dtype="float64")
            v = np.asarray(values[channel], dtype="float64")
            finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(v)
            x, y, v = x[finite], y[finite], v[finite]
            if v.size == 0:
                continue
            x0 = float(x.min())
            y0 = float(y.min())
            width = float(x.max() - x0) + 1.0
            height = float(y.max() - y0) + 1.0
            ntx = int(width // tile_w)
            nty = int(height // tile_h)
            if ntx < 1 or nty < 1:
                continue
            ti = np.floor((x - x0) / tile_w).astype(np.int64)
            tj = np.floor((y - y0) / tile_h).astype(np.int64)
            keep = (ti >= 0) & (ti < ntx) & (tj >= 0) & (tj < nty)
            if not keep.any():
                continue
            x, y, v = x[keep], y[keep], v[keep]
            ti, tj = ti[keep], tj[keep]
            tile_id = tj * ntx + ti
            wx = (x - x0) - ti * tile_w
            wy = (y - y0) - tj * tile_h
            bx = np.clip((wx * bins / tile_w).astype(np.int64), 0, bins - 1)
            by = np.clip((wy * bins / tile_h).astype(np.int64), 0, bins - 1)
            block = by * bins + bx
            frame = pd.DataFrame({"tile": tile_id, "block": block, "v": v})
            # 10th percentile threshold per tile; darker points are background.
            thresholds = frame.groupby("tile")["v"].quantile(0.1)
            frame["thr"] = frame["tile"].map(thresholds)
            frame = frame[frame["v"] >= frame["thr"]]
            if frame.empty:
                continue
            # Median per block within each tile, then mean over tiles.
            medians = frame.groupby(["tile", "block"])["v"].median()
            per_block = medians.groupby(level="block").mean()
            indices = per_block.index.to_numpy(dtype=np.int64)
            sums[indices] += per_block.to_numpy()
            counts[indices] += 1.0
            used_files += 1

        if used_files == 0 or not (counts > 0).any():
            return (key, None, "No tile data was found for the selected parameters.")

        grid = np.full(n_blocks, np.nan)
        valid = counts > 0
        grid[valid] = sums[valid] / counts[valid]
        grid = grid.reshape(bins, bins)

        # Smooth the bin x bin fields to resolution x resolution, then to a
        # full tile-sized mask.
        gy, gx = np.mgrid[0:bins, 0:bins]
        flat_valid = valid.reshape(-1)
        points = np.column_stack([gx.ravel()[flat_valid],
                                  gy.ravel()[flat_valid]])
        values_grid = grid.reshape(-1)[flat_valid]
        target_x = np.linspace(0, bins - 1, resolution)
        target_y = np.linspace(0, bins - 1, resolution)
        tx, ty = np.meshgrid(target_x, target_y)
        smooth = griddata(points, values_grid, (tx, ty), method="linear")
        if np.isnan(smooth).any():
            nearest = griddata(points, values_grid, (tx, ty), method="nearest")
            smooth = np.where(np.isnan(smooth), nearest, smooth)
        spline = RectBivariateSpline(
            np.arange(resolution), np.arange(resolution), smooth, kx=1, ky=1
        )
        mask = spline(
            np.linspace(0, resolution - 1, tile_h),
            np.linspace(0, resolution - 1, tile_w),
        )
        mask = np.asarray(mask, dtype="float64")
        if mask.shape != (tile_h, tile_w):
            return (key, None, "Could not build the mask at the requested size.")
        return (key, {
            "mask": mask.tolist(),
            "tile_w": int(tile_w),
            "tile_h": int(tile_h),
            "bin": int(bins),
            "resolution": int(resolution),
            "files": list(files),
        }, None)


class _ImageFilterWorker:
    """Background worker that computes gate-filter masks for Image plots."""

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, key, parquet_path, gates, gate_id, stages, base_columns,
                height, width, blocks):
        self._req_q.put((
            key, parquet_path, gates, gate_id, stages, set(base_columns),
            height, width, blocks,
        ))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                req = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                res = self._compute(*req)
            except Exception as exc:  # noqa: BLE001
                res = (req[0], f"Filter could not be applied: {exc}")
            self._res_q.put(res)

    def _compute(self, key, parquet_path, gates, gate_id, stages, base_columns,
                 height, width, blocks):
        import numpy as np
        import pandas as pd
        by_id = {g["id"]: g for g in gates}
        needed = set()
        for g in gates:
            needed |= _gate_needed_columns(g, by_id)
        dependencies = _pipeline_dependencies(stages, needed)
        base = [c for c in dependencies if c in base_columns]
        values = {}
        if base:
            try:
                df = pd.read_parquet(parquet_path, columns=base)
            except Exception as exc:  # noqa: BLE001
                return (key, f"Filter could not be applied: {exc}")
            for name in base:
                values[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).to_numpy(dtype="float64")
        values = _evaluate_stages(stages, values, needed, parquet_path)
        try:
            mask = _compute_gate_filter_mask(
                gates, gate_id, parquet_path, {},
                existing=set(values), extra=values,
            )
        except KeyError as exc:
            return (key, f"Filter parameter not found: {exc}")
        except Exception as exc:  # noqa: BLE001
            return (key, f"Filter could not be applied: {exc}")
        if mask is None:
            return (key, "Filter could not be computed (gate has no usable parameters)")
        mask = np.asarray(mask, dtype=np.uint8)
        total = int(height) * int(width)
        if mask.size != total:
            return (key, f"Filter mask size mismatch ({mask.size} vs {total})")
        mask_2d = mask.reshape(int(height), int(width))
        levels = _pool_mask(mask_2d, blocks)
        return (key, {"mask_2d": mask_2d, "levels": levels})


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


def _make_rotated_axis(orientation: str = "bottom"):
    """Return an axis whose tick labels are rotated 90° (long names)."""
    import pyqtgraph as pg

    class _RotatedAxis(pg.AxisItem):
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


_GATE_PEN = "#000000"
_GATE_HOVER_PEN = "#444444"
_POLY_CLOSE_RADIUS = 12  # pixels: click this close to the first anchor to close


def _gate_roi_pens() -> dict:
    """Pens for a gate ROI: blue edge, darker+thicker blue on hover."""
    return dict(
        pen=pg.mkPen(_GATE_PEN, width=2),
        hoverPen=pg.mkPen(_GATE_HOVER_PEN, width=3),
        handlePen=pg.mkPen(_GATE_PEN, width=1),
        handleHoverPen=pg.mkPen(_GATE_HOVER_PEN, width=2),
    )


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
        # Instead, an edge click selects the polygon.
        for seg in self.segments:
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
    # Pixels the pointer must travel before a title-bar click becomes a drag,
    # so a plain click never reorders the cards.
    DRAG_THRESHOLD = 6

    def __init__(self, on_select, on_drop, on_drag=None, on_drag_end=None) -> None:
        super().__init__()
        self._on_select = on_select
        self._on_drop = on_drop
        self._on_drag = on_drag
        self._on_drag_end = on_drag_end
        self._dragging = False
        self._moved = False
        self._press_scene = None
        self._start_pos = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.pos().y() <= self.TITLE_HEIGHT:
            self._dragging = True
            self._moved = False
            self._press_scene = event.scenePos()
            self._start_pos = self.pos()
            self.setZValue(1)
            self._on_select(bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.scenePos() - self._press_scene
            if not self._moved:
                if (
                    abs(delta.x()) < self.DRAG_THRESHOLD
                    and abs(delta.y()) < self.DRAG_THRESHOLD
                ):
                    event.accept()
                    return
                self._moved = True
            self.setPos(self._start_pos + delta)
            if self._on_drag is not None:
                self._on_drag(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.setZValue(0)
            moved = self._moved
            self._moved = False
            if moved:
                self._on_drop(event.scenePos())
            else:
                # A plain click selects only: never move the card.
                self.setPos(self._start_pos)
                if self._on_drag_end is not None:
                    self._on_drag_end()
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
        self.rubber_select_cb = None
        self.rubber_clear_cb = None
        self._rubber_band = None
        self._rubber_origin = None
        self._rubber_ctrl = False
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
            if item is None:
                if self.unfocus_callback is not None:
                    self.unfocus_callback()
                self._begin_rubber_band(event.position().toPoint(), event.modifiers())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rubber_origin is not None:
            self._rubber_band.setGeometry(
                QRect(self._rubber_origin, event.position().toPoint()).normalized()
            )
            event.accept()
            return
        if self.plot_pan_move_cb is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            if self.plot_pan_move_cb(scene_pos):
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._rubber_origin is not None:
            self._finish_rubber_band(event.position().toPoint())
            event.accept()
            return
        if self.plot_pan_end_cb is not None and self.plot_pan_end_cb():
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _begin_rubber_band(self, view_pos, modifiers) -> None:
        self._rubber_origin = view_pos
        self._rubber_ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if self._rubber_band is None:
            self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._rubber_band.setGeometry(QRect(view_pos, QSize()))
        self._rubber_band.show()

    def _finish_rubber_band(self, view_pos) -> None:
        rect = QRect(self._rubber_origin, view_pos).normalized()
        self._rubber_origin = None
        self._rubber_band.hide()
        if rect.width() < 4 and rect.height() < 4:
            # A plain click on empty canvas: clear the selection.
            if self.rubber_clear_cb is not None:
                self.rubber_clear_cb()
            return
        top_left = self.mapToScene(rect.topLeft())
        bottom_right = self.mapToScene(rect.bottomRight())
        scene_rect = QRectF(top_left, bottom_right).normalized()
        if self.rubber_select_cb is not None:
            self.rubber_select_cb(scene_rect, self._rubber_ctrl)

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

    def __init__(self, title: str, plot_widget, index: int, select_callback,
                 side_widget=None) -> None:
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
        if side_widget is not None:
            body = QWidget()
            bh = QHBoxLayout(body)
            bh.setContentsMargins(0, 0, 0, 0)
            bh.setSpacing(4)
            bh.addWidget(plot_widget, 1)
            bh.addWidget(side_widget, 0)
            lay.addWidget(body, 1)
        else:
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
        self._value = None if value is None else int(value)
        self._on_change = on_change
        self._min = minimum
        self._max = maximum
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        self.edit = QLineEdit()
        self.edit.setFixedWidth(56)
        self.edit.setText("" if self._value is None else str(self._value))
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

    def set_value(self, value) -> None:
        """Set the displayed value without triggering on_change."""
        if value is None:
            self._value = None
            self.edit.setText("")
        else:
            self._value = self._clamp(value)
            self.edit.setText(str(self._value))

    def _clamp(self, value) -> int:
        return max(self._min, min(self._max, int(value)))

    def _step(self, delta: int) -> None:
        base = self._value if self._value is not None else self._min
        self._value = self._clamp(base + delta)
        self.edit.setText(str(self._value))
        if self._on_change is not None:
            self._on_change(self._value)

    def _commit(self) -> None:
        text = self.edit.text().strip()
        if text == "":
            self._value = None
            self.edit.setText("")
            return
        try:
            self._value = self._clamp(int(text))
        except (ValueError, TypeError):
            self.edit.setText("" if self._value is None else str(self._value))
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
    "illumination": "map",
    "unmix": "map",
    "segmentation": "generate",
    "figure": "visualize",
    # Placeholder modules (no implementation yet).
    "feature": "map",
    "subset": "map",
    "subsample": "map",
    "dimred": "generate",
    "cluster": "generate",
    "export": "map",
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
    """True if a descendant generate module has saved data.

    Saved generated output (for example segmentation masks) locks every module
    *above* it: those modules can still be opened and explored, but their
    changes cannot be saved. The generate module itself is never locked, and
    neither is anything below it.
    """

    def walk(node) -> bool:
        for child in node.get("children", []):
            if (
                _task_kind(child.get("type", "")) == "generate"
                and child.get("output", {}).get("saved")
            ):
                return True
            if walk(child):
                return True
        return False

    return walk(task)


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
        # On-the-fly Unmix configs and computed map-stage outputs.
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._stage_cache = {}
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
        # Mask-edge images are temporary: drop this module's cache on exit.
        self._seg_clear_edge_cache()
        event.accept()

    def _has_unsaved_module_changes(self) -> bool:
        for index in range(1, self.tabs.count()):  # skip Workflow at index 0
            widget = self.tabs.widget(index)
            if (
                getattr(widget, "_fig_dirty", False)
                or getattr(widget, "_scaling_dirty", False)
                or getattr(widget, "_gating_dirty", False)
                or getattr(widget, "_unmix_dirty", False)
                or getattr(widget, "_comp_dirty", False)
                or getattr(widget, "_illum_dirty", False)
                or getattr(widget, "_seg_dirty", False)
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

        ext_menu = bar.addMenu("&Extension")
        manage_ext_action = QAction("Manage Extension…", self)
        manage_ext_action.triggered.connect(self._open_extension_manager)
        ext_menu.addAction(manage_ext_action)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(QAction("About Smage", self))

    # ----- extensions (external Python environments) ------------------
    def _ext_dir(self) -> Path:
        """User-level folder holding Smage's global (non-project) settings."""
        import os

        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Smage"

    def _ext_config_path(self) -> Path:
        return self._ext_dir() / "extensions.json"

    def _ext_config(self) -> dict:
        cache = getattr(self, "_ext_config_cache", None)
        if cache is None:
            try:
                cache = json.loads(
                    self._ext_config_path().read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                cache = {}
            if not isinstance(cache, dict):
                cache = {}
            self._ext_config_cache = cache
            self._migrate_ext_config(cache)
        return cache

    def _migrate_ext_config(self, cache) -> None:
        """Rename legacy extension ids to their new -cpu names.

        Cellpose/StarDist are now CPU methods (``cellpose-cpu`` /
        ``stardist-cpu``) with GPU variants arriving separately, so re-key any
        configuration saved under the old ids.
        """
        for old, new in (("cellpose", "cellpose-cpu"), ("stardist", "stardist-cpu")):
            if new not in cache and old in cache:
                cache[new] = cache.pop(old)

    def _ext_save_config(self) -> None:
        path = self._ext_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._ext_config(), indent=2), encoding="utf-8"
        )

    def _ext_entry(self, ext_id: str) -> dict:
        return self._ext_config().setdefault(ext_id, {})

    def _ext_python(self, ext_id: str) -> str:
        return str(self._ext_entry(ext_id).get("python") or "")

    def _ext_ready(self, ext_id: str) -> bool:
        """Whether an extension's environment, library and model are set up."""
        e = self._ext_entry(ext_id)
        return bool(
            e.get("python") and e.get("verified") and e.get("model_ready")
        )

    def _ext_runner_path(self, ext_id: str) -> Path:
        entry = _extension_def(ext_id)
        return Path(__file__).parent / entry["runner"]

    def _detect_python_interpreters(self) -> list:
        """Scan the system for Python interpreters (Windows).

        Returns a list of ``{"name": str, "path": str}`` entries, de-duplicated
        by path, ordered most-relevant first.
        """
        import glob
        import os
        import re
        import shutil
        import subprocess

        found = {}  # path -> display name

        def add(name, path):
            if not path:
                return
            path = str(path)
            # The WindowsApps python.exe/python3.exe are Store stubs, not real
            # interpreters; skip them so users don't pick a non-working Python.
            if "\\WindowsApps\\" in path or "/WindowsApps/" in path:
                return
            if path not in found and Path(path).is_file():
                found[path] = name

        # 1. Interpreters reachable on PATH and the `py` launcher.
        for cmd in ("python", "python3"):
            p = shutil.which(cmd)
            if p:
                add(f"Python on PATH ({cmd})", p)
        py_launcher = shutil.which("py")

        # 2. The `py` launcher lists every registered interpreter.
        if py_launcher:
            add("Python launcher (py)", py_launcher)
            try:
                out = subprocess.run(
                    [py_launcher, "-0p"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in (out.stdout or "").splitlines():
                    for token in line.split():
                        if re.match(r"^[A-Za-z]:[\\/].*python.*\.exe$", token):
                            add("Python (py launcher)", token)
            except Exception:  # noqa: BLE001
                pass

        # 3. Common install locations (python.org and conda).
        local = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("PROGRAMFILES")
        home = str(Path.home())

        if local:
            add(
                "Python (python.org)",
                Path(local) / "Programs" / "Python" / "python.exe",
            )
        for pattern in (
            (local and str(Path(local) / "Programs" / "Python" / "Python*" / "python.exe")),
            "C:/Python*/python.exe",
            "C:/Python*/Scripts/python.exe",
            (program_files and f"{program_files}/Python*/python.exe"),
        ):
            if pattern:
                for path in glob.glob(pattern):
                    add("Python (python.org)", path)

        for base in {home, local or home}:
            for name in ("miniconda3", "anaconda3", "Miniconda3", "Anaconda3"):
                add(name, Path(base) / name / "python.exe")
                add(name, Path(base) / name / "Scripts" / "python.exe")

        return [
            {"name": name, "path": path}
            for path, name in found.items()
        ]

    def _ext_worker(self):
        worker = getattr(self, "_ext_worker_obj", None)
        if worker is None:
            worker = _ProcessWorker()
            self._ext_worker_obj = worker
        return worker

    def _open_extension_manager(self) -> None:
        """Open the Extension Manager dialog (global, outside the workflow)."""
        dlg = _ExtManagerDialog(
            self, lambda: getattr(self, "_ext_busy", False)
        )
        dlg.setWindowTitle("Manage Extensions")
        dlg.resize(720, 460)
        lay = QHBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        list_widget = QListWidget()
        list_widget.setFixedWidth(160)
        list_widget.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        for entry in EXTENSION_DEFS:
            list_widget.addItem(entry["name"])
        lay.addWidget(list_widget)

        host = QWidget()
        host.setLayout(QVBoxLayout())
        host.layout().setContentsMargins(0, 0, 0, 0)
        lay.addWidget(host, 1)

        def stop_timer():
            timer = getattr(host, "_ext_poll_timer", None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                host._ext_poll_timer = None

        def show(index):
            stop_timer()
            self._clear_layout(host.layout())
            if index < 0:
                return
            self._ext_detail_panel(host, EXTENSION_DEFS[index])

        list_widget.currentRowChanged.connect(show)
        if EXTENSION_DEFS:
            list_widget.setCurrentRow(0)

        # Stop the poll timer as soon as the manager is closed, so its callback
        # never touches widgets from a destroyed panel.
        dlg.finished.connect(lambda _=0: stop_timer())
        dlg.exec()

    def _ext_detail_panel(self, host, entry) -> None:
        """Build the detail panel for one extension in the manager."""
        import os

        ext_id = entry["id"]
        e = self._ext_entry(ext_id)
        lay = host.layout()
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)

        lay.addWidget(QLabel(f"<b>{entry['name']}</b>"))
        lay.addWidget(QLabel(entry["desc"]))
        lay.addWidget(QLabel(
            "Runs in a separate Python environment; Smage does not bundle "
            "TensorFlow / PyTorch."
        ))
        self_note = QLabel(
            "You don't have to use the buttons below — you can install "
            "everything yourself in any Python environment, then set that "
            "environment's python.exe above. This only needs to be done once; "
            "after it succeeds, it keeps working. If installation proves too "
            "difficult, you can instead run the cell segmentation in other "
            "software and import the resulting mask files with the Segmentation "
            "module's Mask Input."
        )
        self_note.setWordWrap(True)
        self_note.setStyleSheet("color: #6b7785;")
        lay.addWidget(self_note)
        lay.addWidget(QLabel(""))
        lay.addWidget(QLabel("<b>1. Python environment</b>"))

        detect_python_btn = QPushButton("Detect installed Python…")
        detect_python_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(detect_python_btn)

        env_row = QWidget()
        eh = QHBoxLayout(env_row)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.setSpacing(4)
        python_combo = QComboBox()
        python_combo.setEditable(True)
        python_combo.setPlaceholderText("Path to python.exe")
        if e.get("python"):
            python_combo.addItem(e.get("python"))
            python_combo.setEditText(e.get("python"))
        eh.addWidget(python_combo, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        eh.addWidget(browse_btn)
        lay.addWidget(env_row)

        install_box = QGroupBox("No suitable Python?")
        ib = QVBoxLayout(install_box)
        ib.setContentsMargins(8, 8, 8, 8)
        ib.setSpacing(4)
        box_hint = QLabel(
            "If you don't find a suitable python.exe, you can install Python "
            "or create an environment yourself. Otherwise ignore this box."
        )
        box_hint.setWordWrap(True)
        box_hint.setStyleSheet("color: #6b7785;")
        ib.addWidget(box_hint)
        ibtn = QHBoxLayout()
        install_python_btn = QPushButton("Install Python…")
        install_python_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn = QPushButton("Create venv…")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ibtn.addWidget(install_python_btn)
        ibtn.addWidget(create_btn)
        ibtn.addStretch()
        ib.addLayout(ibtn)
        lay.addWidget(install_box)

        detect_btn = QPushButton("Detect library")
        detect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        detect_btn.setToolTip(
            f"Check that the chosen Python has the {entry['pip']} library installed"
        )
        lay.addWidget(detect_btn)

        status = QLabel(self._ext_status_text(entry, e))
        status.setWordWrap(True)
        status.setStyleSheet("color: #37474f;")
        lay.addWidget(status)

        lay.addWidget(QLabel(""))
        lay.addWidget(QLabel("<b>2. Install library</b>"))
        install_btn = QPushButton(f"Install {entry['pip']}")
        install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(install_btn)

        lay.addWidget(QLabel("<b>3. Install dependencies</b>"))
        if entry.get("device") == "gpu":
            gpu_note = QLabel(
                "The GPU build needs an NVIDIA GPU and a matching CUDA version "
                "of PyTorch, and the right version depends on your graphics "
                "driver. Smage can auto-detect the driver and pick the right "
                "version; if that does not work, choose a specific CUDA version "
                "below, or ask an AI assistant / an experienced colleague to "
                "install the environment for you. It only needs to succeed once."
            )
            gpu_note.setWordWrap(True)
            gpu_note.setStyleSheet("color: #6b7785;")
            lay.addWidget(gpu_note)
            lay.addWidget(QLabel("CUDA version"))
            cuda_combo = QComboBox()
            cuda_combo.addItem("Auto-detect (recommended)", "auto")
            cuda_combo.addItem("CPU only", "cpu")
            for minor, dirname in _PYTORCH_CUDA_INDEXES:
                cuda_combo.addItem(f"CUDA {minor:g} ({dirname})", dirname)
            idx = cuda_combo.findData(e.get("cuda", "auto"))
            cuda_combo.setCurrentIndex(max(0, idx))
            lay.addWidget(cuda_combo)

            def on_cuda_changed(value):
                e["cuda"] = value
                self._ext_save_config()

            cuda_combo.currentIndexChanged.connect(
                lambda _=0: on_cuda_changed(cuda_combo.currentData())
            )
        install_deps_btn = QPushButton("Install dependencies")
        install_deps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        install_deps_btn.setToolTip(
            "Force-reinstall the framework packages ("
            + ", ".join(entry.get("deps") or []) + ") even if already present"
        )
        lay.addWidget(install_deps_btn)

        lay.addWidget(QLabel("<b>4. Download model</b>"))
        model_btn = QPushButton("Download model")
        model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(model_btn)

        progress = QProgressBar()
        progress.setRange(0, 0)  # indeterminate while a command is running
        progress.setTextVisible(False)
        progress.setFixedHeight(14)
        progress.hide()
        lay.addWidget(progress)

        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setPlaceholderText("Output appears here…")
        log.setStyleSheet("background: #1e1e1e; color: #d4d4d4; font-family: Consolas, monospace;")
        lay.addWidget(log, 1)

        worker = self._ext_worker()
        page_state = {"busy": False}

        def set_busy(busy, show_progress=False):
            page_state["busy"] = busy
            self._ext_busy = bool(busy)
            if busy and show_progress:
                progress.setRange(0, 0)
                progress.show()
            else:
                progress.hide()
            for b in (
                browse_btn, detect_python_btn, install_python_btn, create_btn,
                detect_btn, install_btn, install_deps_btn, model_btn,
            ):
                b.setEnabled(not busy)

        def current_python():
            p = python_combo.currentText().strip()
            if not p and e.get("python"):
                p = e.get("python")
            return p

        def on_detect():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Detect", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True)  # detect is quick: no progress bar
            log.clear()
            log.appendPlainText(
                f"Checking {entry['pip']} and its dependencies…"
            )
            # Check package metadata only (fast); do not import the library,
            # since importing TensorFlow / PyTorch can take tens of seconds.
            modules = list(entry.get("deps") or []) + [entry["pip"]]
            probe = "\n".join([
                "import importlib.metadata as md",
                "mods = " + repr(modules),
                "for n in mods:",
                "    try:",
                "        print(n, md.version(n))",
                "    except Exception:",
                "        print(n, 'MISSING')",
            ])
            # GPU extensions additionally verify that PyTorch/TensorFlow can
            # actually use CUDA (this imports the framework, so it is slower).
            if entry.get("device") == "gpu":
                log.appendPlainText("Also checking CUDA availability…")
                probe += "\n" + "\n".join([
                    "try:",
                    "    import torch",
                    "    print('gpu-torch-version', torch.__version__)",
                    "    print('gpu-cuda-version', torch.version.cuda or 'NONE')",
                    "    print('gpu-cuda-available', "
                    "'True' if torch.cuda.is_available() else 'False')",
                    "    if torch.cuda.is_available():",
                    "        print('gpu-device-name', torch.cuda.get_device_name(0))",
                    "    else:",
                    "        print('gpu-device-name', '(none)')",
                    "except Exception as exc:",
                    "    print('gpu-check-error', repr(exc))",
                ])
            worker.run(("detect", ext_id), [python, "-c", probe])

        def on_install():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Install", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True, show_progress=True)
            log.clear()
            log.appendPlainText(
                f"Installing {entry['pip']} into {python}…\n"
                "This can take several minutes."
            )
            worker.run(
                ("install", ext_id),
                [python, "-m", "pip", "install", entry["pip"]],
            )

        def on_install_deps():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Install dependencies", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True, show_progress=True)
            log.clear()
            deps = list(entry.get("deps") or [])
            argv = [python, "-m", "pip", "install", "--force-reinstall"]
            index_url = None
            if entry.get("device") == "gpu":
                choice = e.get("cuda", "auto")
                if choice == "cpu":
                    log.appendPlainText(
                        "CPU only selected — installing the default (CPU) build."
                    )
                elif choice.startswith("cu"):
                    index_url = f"https://download.pytorch.org/whl/{choice}"
                else:  # auto-detect the driver's supported CUDA version
                    driver_cuda = _nvidia_cuda_version()
                    if driver_cuda is None:
                        log.appendPlainText(
                            "No NVIDIA driver detected — installing CPU-only "
                            "build. If you have an NVIDIA GPU, install/update "
                            "its driver, or choose a specific CUDA version."
                        )
                    else:
                        picked = _pick_cuda_index(driver_cuda)
                        if picked is None:
                            log.appendPlainText(
                                f"Driver CUDA {driver_cuda:g} is too old for any "
                                "supported CUDA build — installing CPU-only build."
                            )
                        else:
                            index_url = f"https://download.pytorch.org/whl/{picked}"
                            log.appendPlainText(
                                f"Detected NVIDIA driver CUDA {driver_cuda:g} → "
                                f"using {picked}."
                            )
            if index_url:
                argv += ["--index-url", index_url]
            argv += deps
            log.appendPlainText(
                f"Reinstalling {' '.join(deps)} into {python}…\n"
                + (f"Using index: {index_url}\n" if index_url else "")
                + "This can take several minutes."
            )
            worker.run(("deps", ext_id), argv)

        def on_download_model():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Download model", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True, show_progress=True)
            log.clear()
            log.appendPlainText(f"Downloading {entry['model']} model…")
            worker.run(
                ("model", ext_id),
                [python, str(self._ext_runner_path(ext_id)),
                 "download-model", "--model", entry["model"]],
            )

        def browse():
            path, _ = QFileDialog.getOpenFileName(
                self, "Select python.exe", "",
                "Python (python.exe);;All files (*)",
            )
            if path:
                python_combo.setEditText(path)

        def detect_python():
            found = self._detect_python_interpreters()
            if not found:
                QMessageBox.information(
                    self,
                    "Python not found",
                    "No Python installation was found on this computer.\n\n"
                    "Click 'Install Python…' to download and install Python, "
                    "then come back and click 'Detect installed Python…'.",
                )
                return
            current = python_combo.currentText().strip()
            python_combo.clear()
            for item in found:
                python_combo.addItem(item["path"])
            if current in [python_combo.itemText(i) for i in range(python_combo.count())]:
                python_combo.setCurrentText(current)
            else:
                python_combo.setCurrentIndex(0)

        def install_python():
            QDesktopServices.openUrl(QUrl("https://www.python.org/downloads/"))
            QMessageBox.information(
                self,
                "Install Python",
                "1. Download the installer from python.org.\n"
                "2. Run it and tick 'Add python.exe to PATH'.\n"
                "3. After installing, click 'Detect installed Python…' here "
                "to use the new Python.",
            )

        def create_venv():
            base, _ = QFileDialog.getOpenFileName(
                self, "Select a base python.exe to create a venv", "",
                "Python (python.exe);;All files (*)",
            )
            if not base:
                return
            target = QFileDialog.getExistingDirectory(
                self, "Choose a folder for the new environment"
            )
            if not target:
                return
            venv = str(Path(target) / f"smage-{ext_id}")
            log.clear()
            log.appendPlainText(f"Creating venv at {venv}…")
            set_busy(True, show_progress=True)
            worker.run(
                ("create", ext_id, venv),
                [base, "-m", "venv", venv],
            )

        def refresh_status():
            status.setText(self._ext_status_text(entry, e))
            if self._ext_ready(entry["id"]):
                status.setStyleSheet("color: #1a7f37; font-weight: bold;")
            elif e.get("verified") is False and (e.get("missing") or not e.get("python")):
                status.setStyleSheet("color: #c0392b; font-weight: bold;")
            else:
                status.setStyleSheet("color: #37474f;")

        def on_poll():
            for item in worker.poll():
                if item[1] == "line":
                    log.appendPlainText(item[2])
                    continue
                _tag, kind, code, text = item[0], item[1], item[2], item[3]
                key = kind  # first element of the request key tuple
                set_busy(False)
                # Output was already streamed line-by-line above; only surface
                # the captured text when the subprocess died without streaming
                # anything (e.g. an OSError before Popen), signalled by code -1.
                if code < 0 and text:
                    log.appendPlainText(text.rstrip())
                if isinstance(_tag, tuple) and _tag[0] == "detect":
                    missing = []
                    version = ""
                    gpu = {}
                    for line in text.strip().splitlines():
                        parts = line.split(None, 1)
                        if len(parts) != 2:
                            continue
                        name, value = parts[0], parts[1]
                        if name.startswith("gpu-"):
                            gpu[name] = value
                            continue
                        if value == "MISSING":
                            missing.append(name)
                        elif name == entry["pip"]:
                            version = value
                    if code == 0 and not missing:
                        e["verified"] = True
                        e["version"] = version
                        e["missing"] = []
                    else:
                        e["verified"] = False
                        e["version"] = ""
                        e["missing"] = missing
                        if missing:
                            log.appendPlainText(
                                "Missing: " + ", ".join(missing) + ".\n"
                                f"Run 'Install {entry['pip']}' to install them."
                            )
                        else:
                            log.appendPlainText(
                                "Could not check this environment. "
                                "Verify the Python path and try again."
                            )
                    # GPU extensions: report whether CUDA is actually usable.
                    if entry.get("device") == "gpu":
                        torch_ver = gpu.get("gpu-torch-version", "")
                        cuda_ver = gpu.get("gpu-cuda-version", "NONE")
                        avail = gpu.get("gpu-cuda-available", "False") == "True"
                        dev = gpu.get("gpu-device-name", "(none)")
                        err = gpu.get("gpu-check-error")
                        cpu_build = (cuda_ver == "NONE") or ("+cpu" in torch_ver)
                        log.appendPlainText("")
                        log.appendPlainText("GPU check:")
                        if err is not None:
                            log.appendPlainText(
                                f"  ✗ Could not import torch: {err}"
                            )
                            e["gpu_ok"] = False
                        else:
                            if cpu_build:
                                log.appendPlainText(
                                    f"  ✗ PyTorch build: CPU-only ({torch_ver})"
                                )
                            else:
                                log.appendPlainText(
                                    f"  ✓ PyTorch build: GPU (CUDA {cuda_ver})"
                                )
                            if avail:
                                log.appendPlainText(
                                    f"  ✓ CUDA available: True ({dev})"
                                )
                            else:
                                log.appendPlainText("  ✗ CUDA available: False")
                            e["gpu_ok"] = (not cpu_build) and avail
                        if e.get("gpu_ok"):
                            log.appendPlainText("  ✓ Ready to run on GPU")
                        else:
                            log.appendPlainText(
                                "  ✗ Not ready for GPU — will fall back to CPU"
                            )
                    self._ext_save_config()
                elif isinstance(_tag, tuple) and _tag[0] == "install":
                    e["verified"] = code == 0
                    if code == 0:
                        log.appendPlainText("Installed. Run Detect library to confirm.")
                    else:
                        log.appendPlainText(
                            "Installation failed. Check the messages above."
                        )
                    self._ext_save_config()
                elif isinstance(_tag, tuple) and _tag[0] == "deps":
                    if code == 0:
                        log.appendPlainText(
                            "Dependencies installed. Run Detect library to confirm."
                        )
                    else:
                        log.appendPlainText(
                            "Dependency installation failed. Check the messages above."
                        )
                elif isinstance(_tag, tuple) and _tag[0] == "model":
                    e["model_ready"] = code == 0
                    self._ext_save_config()
                elif isinstance(_tag, tuple) and _tag[0] == "create":
                    if code == 0:
                        venv_python = str(
                            Path(_tag[2]) / ("Scripts" if os.name == "nt" else "bin")
                            / ("python.exe" if os.name == "nt" else "python")
                        )
                        python_combo.setEditText(venv_python)
                        e["python"] = venv_python
                        self._ext_save_config()
                        log.appendPlainText(f"Environment created: {venv_python}")
                    else:
                        log.appendPlainText("Could not create the environment.")
                refresh_status()

        poll_timer = QTimer(host)
        poll_timer.setInterval(150)
        poll_timer.timeout.connect(on_poll)
        poll_timer.start()

        browse_btn.clicked.connect(browse)
        detect_python_btn.clicked.connect(detect_python)
        install_python_btn.clicked.connect(install_python)
        create_btn.clicked.connect(create_venv)
        detect_btn.clicked.connect(on_detect)
        install_btn.clicked.connect(on_install)
        install_deps_btn.clicked.connect(on_install_deps)
        model_btn.clicked.connect(on_download_model)
        host._ext_poll_timer = poll_timer
        refresh_status()

    def _ext_status_text(self, entry, e) -> str:
        python = e.get("python") or ""
        if not python:
            return "Not configured — no Python environment."
        parts = [f"Python: {python}"]
        missing = e.get("missing")
        if e.get("verified"):
            parts.append(f"✓ {entry['name']} detected: {e.get('version') or 'ok'}")
        elif missing:
            parts.append("✗ Missing packages: " + ", ".join(missing))
        else:
            parts.append("Not checked yet — click 'Detect library'.")
        if e.get("model_ready"):
            parts.append(f"Model: {entry['model']} ready")
        else:
            parts.append("Model: not downloaded")
        if entry.get("device") == "gpu" and "gpu_ok" in e:
            if e["gpu_ok"]:
                parts.append("✓ GPU: CUDA available")
            else:
                parts.append("✗ GPU: CUDA not available (CPU-only build)")
        if self._ext_ready(entry["id"]):
            parts.append("✓ Ready to use")
        return "\n".join(parts)

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
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._stage_cache = {}
        self._seg_value_cache = {}
        self._seg_mask_cache = {}
        self._seg_clear_edge_cache()

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
        menu = self._build_actions_menu(task_def, parent_task_def)
        menu.exec(QCursor.pos())

    def _build_actions_menu(self, task_def: dict, parent_task_def) -> QMenu:
        """Build the task actions menu without showing it.

        Locking only stops a module from *saving* its changes; the workflow
        structure can still be edited (delete, paste, add children).
        """
        menu = QMenu(self)

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(lambda: self._copy_task(task_def, False))
        menu.addAction(copy_action)

        copy_branch_action = QAction("Copy branch", self)
        copy_branch_action.triggered.connect(lambda: self._copy_task(task_def, True))
        menu.addAction(copy_branch_action)

        menu.addSeparator()

        paste_action = QAction("Paste", self)
        paste_action.triggered.connect(lambda: self._paste_task(task_def, False))
        menu.addAction(paste_action)

        paste_branch_action = QAction("Paste branch", self)
        paste_branch_action.triggered.connect(lambda: self._paste_task(task_def, True))
        menu.addAction(paste_branch_action)

        menu.addSeparator()

        rename_action = QAction("Rename", self)
        rename_action.triggered.connect(lambda: self._rename_task(task_def))
        menu.addAction(rename_action)

        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(
            lambda: self._delete_task(task_def, parent_task_def, False)
        )
        menu.addAction(delete_action)

        delete_branch_action = QAction("Delete branch", self)
        delete_branch_action.triggered.connect(
            lambda: self._delete_task(task_def, parent_task_def, True)
        )
        menu.addAction(delete_branch_action)
        return menu

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

    def _collect_task_ids(self, task_def: dict) -> list:
        """Return the ids of a task and every descendant (pre-order)."""
        ids = []
        stack = [task_def]
        while stack:
            node = stack.pop()
            if node.get("id"):
                ids.append(node.get("id"))
            stack.extend(node.get("children", []))
        return ids

    def _cleanup_module_data(self, task_ids) -> None:
        """Delete the per-module data folders of the given task ids."""
        if self.project is None:
            return
        import shutil

        for tid in task_ids:
            try:
                shutil.rmtree(
                    self.project.path / "Module" / str(tid), ignore_errors=True
                )
            except Exception:  # noqa: BLE001
                pass

    def _delete_task(self, task_def: dict, parent_task_def, with_branch: bool = False) -> None:
        """Delete a single task (or, with_branch, its whole branch).

        Deleting a generate module releases the lock it placed on the modules
        above it and removes its persisted data (masks); the workflow tree is
        rebuilt afterwards, so their Save buttons come back immediately.
        """
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
            removed_ids = self._collect_task_ids(task_def)
            del siblings[index]
        else:
            # Remove only this task; promote its children into its place.
            removed_ids = [task_def.get("id")]
            siblings[index:index + 1] = task_def.get("children", [])

        self._cleanup_module_data([i for i in removed_ids if i])
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()
        self._refresh_locked_pages()

    def _refresh_locked_pages(self) -> None:
        """Rebuild the open Data Import tab when its lock state changed.

        The Data Import page keeps no unsaved edits, so rebuilding is safe and
        makes a released lock (e.g. after deleting a Segmentation module)
        take effect immediately.
        """
        task = self._import_task()
        if task is None or self.project is None:
            return
        task_id = task.get("id")
        for index in range(1, self.tabs.count()):
            page = self.tabs.widget(index)
            if page.property("task_id") != task_id:
                continue
            if getattr(page, "_import_locked_state", None) == self._import_locked():
                return
            fresh = self._build_data_import_page()
            fresh.setProperty("task_id", task_id)
            title = self.tabs.tabText(index)
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, fresh, title)
            self.tabs.setCurrentIndex(index)
            return

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
            btn.setFixedSize(112, 72)
            btn.setToolTip(name)
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
        hint = QLabel(f"{name} is a placeholder module — not implemented yet.")
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
        page._import_locked_state = locked
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
        self.sample_combo = _NoWheelComboBox()
        self.sample_combo.addItem("Full sampling", "full")
        self.sample_combo.addItem("Partial random sampling", "partial")
        row.addWidget(self.sample_combo)

        self.sample_percent_spin = _NoWheelSpinBox()
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
                # Build the image LOD sample index now, so Figure views open
                # image files instantly (no first-open build).
                if Path(entry["path"]).suffix.lower() in (".tif", ".tiff"):
                    name = Path(entry["path"]).name
                    try:
                        def _idx_progress(f, _name=name):
                            progress.setLabelText(
                                f"Importing {_name}: building image index… {int(f * 100)}%"
                            )
                            QApplication.processEvents()

                        _build_image_index_files(str(out_path), _idx_progress)
                    except Exception:
                        pass  # Figure will fall back to a lazy build
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
            data_dir = self.project.data_dir if self.project is not None else None
            if data_dir is not None:
                return Path(parquet_path).relative_to(data_dir).as_posix()
        except (ValueError, AttributeError):
            pass
        return Path(parquet_path).name

    def _columns_for(self, parquet_path, task_id=None) -> list:
        """Return the column names for a parquet file from the column index.

        When `task_id` is given, the parameters produced by upstream modules
        for this file are appended: Unmix output names, then any mask column
        exposed by an upstream Segmentation module.
        """
        if not parquet_path:
            return []
        columns = list(self._column_index.get(self._rel_data_key(parquet_path), []))
        if task_id:
            for name in self._unmix_output_names(task_id, parquet_path):
                if name not in columns:
                    columns.append(name)
            for name in self._seg_applied_columns(task_id, parquet_path):
                if name not in columns:
                    columns.append(name)
        return columns

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

    # ----- Unmix data flow (computed on the fly) ------------------------
    def _upstream_unmix_configs(self, task_id, parquet_path) -> list:
        """Return the Unmix matrix configs upstream of `task_id` that apply to
        this file, in chain order (root first)."""
        configs = []
        if not task_id or not parquet_path or self.project is None:
            return configs
        for task in self._task_chain(task_id):
            if task.get("id") == task_id:
                continue  # exclude the module itself
            if task.get("type") != "unmix":
                continue
            params = task.get("params") or {}
            if parquet_path not in (params.get("samples") or []):
                continue
            config = self._unmix_flow_config(task)
            if config is not None:
                configs.append(config)
        return configs

    def _unmix_output_names(self, task_id, parquet_path) -> list:
        """Names of the parameters an upstream Unmix module adds to this file."""
        names = []
        for config in self._upstream_unmix_configs(task_id, parquet_path):
            for name in config["names"]:
                if name not in names:
                    names.append(name)
        return names

    def _unmix_flow_config(self, task_def) -> dict | None:
        """Build (and cache) the unmixing matrix of a saved Unmix task.

        Returns ``{"task_id", "names", "columns", "matrix", "method"}`` with the
        matrix at full precision, or None when there is nothing to apply.
        """
        import numpy as np

        task_id = task_def.get("id")
        cached = self._unmix_flow_cache.get(task_id)
        if cached is not None:
            return cached or None
        # Downstream modules only see the saved configuration: when the module
        # is open, use its last-saved snapshot rather than live edits.
        reg = self._module_registry.get(task_id)
        if reg is not None and reg.get("task_type") == "unmix":
            params = getattr(reg["page"], "_unmix_snapshot", None)
            if not isinstance(params, dict):
                params = task_def.get("params") or {}
        else:
            params = task_def.get("params") or {}
        settings = params.get("spectrum_settings") or {}
        enabled = set(settings.get("enabled") or [])
        columns = [p for p in settings.get("params", []) if p in enabled]
        stat = settings.get("stat", "median")
        spectra = {s["id"]: s for s in params.get("spectra", [])}
        names = []
        rows = []
        for row in params.get("matrix", []):
            positive = spectra.get(row.get("positive"))
            if positive is None:
                continue
            pos = self._unmix_params_vector(task_id, params, positive, columns, stat)
            if pos is None:
                continue
            values = []
            negative = spectra.get(row.get("negative"))
            neg = {}
            if negative is not None:
                neg = self._unmix_params_vector(
                    task_id, params, negative, columns, stat
                ) or {}
            for column in columns:
                p = pos.get(column, float("nan"))
                n = neg.get(column, 0.0) if negative is not None else 0.0
                values.append(p - n if np.isfinite(p) else float("nan"))
            if row.get("normalize", True):
                finite = [v for v in values if np.isfinite(v)]
                peak = max(finite) if finite else None
                if peak is not None and peak > 0:
                    values = [
                        (v / peak) if np.isfinite(v) else v for v in values
                    ]
            if not all(np.isfinite(v) for v in values):
                continue  # a signature with missing data cannot unmix
            names.append(row.get("name", f"Signature {len(names) + 1}"))
            rows.append(values)
        if not rows or not columns:
            self._unmix_flow_cache[task_id] = False
            return None
        config = {
            "task_id": task_id,
            "names": names,
            "columns": columns,
            "matrix": np.asarray(rows, dtype="float64"),
            "method": params.get("method", "ols"),
            "samples": set(params.get("samples") or []),
        }
        self._unmix_flow_cache[task_id] = config
        return config

    def _unmix_params_vector(self, task_id, params, spectrum, columns, stat):
        """Statistic per parameter for a spectrum stored in task params."""
        import numpy as np

        names, arrays, error = self._unmix_spectrum_arrays_for(
            task_id, params, spectrum, columns
        )
        if error:
            return None
        values = {}
        for name, arr in zip(names, arrays):
            if arr.size == 0:
                values[name] = float("nan")
            elif stat == "mean":
                values[name] = float(arr.mean())
            else:
                values[name] = float(np.median(arr))
        return values

    def _map_stages(self, task_id) -> list:
        """Ordered Scale / Compensation / Unmix stages upstream of a task.

        Each module only declares its own input→output rule; this list is the
        workflow order the data flows through.
        """
        import numpy as np

        stages = []
        if not task_id:
            return stages
        for task in self._task_chain(task_id):
            if task.get("id") == task_id:
                continue
            task_type = task.get("type")
            params = task.get("params") or {}
            if task_type == "scale":
                columns = {
                    c["name"]: c
                    for c in params.get("columns", [])
                    if c.get("name")
                }
                if columns:
                    stages.append(("scale", {"columns": columns}))
            elif task_type == "compensation":
                names = list(params.get("params") or [])
                matrix = params.get("matrix") or []
                files = set(params.get("files") or [])
                if names and files and len(matrix) == len(names):
                    stages.append(("compensation", {
                        "id": task.get("id"),
                        "files": files,
                        "names": names,
                        "matrix": np.asarray(matrix, dtype="float64") / 100.0,
                    }))
            elif task_type == "unmix":
                config = self._unmix_flow_config(task)
                if config is not None:
                    stages.append(("unmix", config))
            elif task_type == "illumination":
                submodules = [
                    item for item in (
                        _illumination_stage_item(sub)
                        for sub in params.get("submodules", [])
                    ) if item is not None
                ]
                if submodules:
                    stages.append(("illumination", {
                        "task_id": task.get("id"),
                        "submodules": submodules,
                    }))
        return stages

    def _column_data(self, task_id, parquet_path, names, extra_stages=None) -> dict:
        """Column values as seen at `task_id`, with upstream map stages applied."""
        import pandas as pd

        if not parquet_path or not names:
            return {}
        names = list(names)
        stages = self._map_stages(task_id) if task_id else []
        if extra_stages:
            stages = stages + list(extra_stages)
        base = set(self._column_index.get(self._rel_data_key(parquet_path), []))
        if not base:
            try:
                import pyarrow.parquet as pq
                base = set(pq.ParquetFile(parquet_path).schema.names)
            except Exception:  # noqa: BLE001
                base = set()
        needed = _pipeline_dependencies(stages, names) if stages else set(names)
        base_cols = [c for c in needed if c in base]
        values = {}
        if base_cols:
            try:
                df = pd.read_parquet(parquet_path, columns=base_cols)
            except Exception:  # noqa: BLE001
                df = None
            if df is not None:
                for column in base_cols:
                    values[column] = pd.to_numeric(
                        df[column], errors="coerce"
                    ).to_numpy(dtype="float64")
        if task_id:
            # Mask columns exposed by an upstream Segmentation module are not
            # stored in the data file; they are resolved from the mask .npy.
            for name, mask_path in self._seg_applied_columns(
                task_id, parquet_path
            ).items():
                if name in needed and name not in values:
                    arr = self._seg_mask_values(parquet_path, mask_path)
                    if arr is not None:
                        values[name] = arr
        if not stages:
            return {n: values[n] for n in names if n in values}
        evaluated = _evaluate_stages(
            stages, values, needed, parquet_path=parquet_path,
            cache=self._stage_cache,
        )
        return {n: evaluated[n] for n in names if n in evaluated}

    def _read_columns(self, parquet_path, columns, task_id=None, extra_stages=None):
        """Read columns from a parquet with upstream map stages applied."""
        import pandas as pd

        values = self._column_data(
            task_id, parquet_path, list(columns), extra_stages=extra_stages
        )
        if not values:
            return pd.DataFrame()
        return pd.DataFrame({
            name: values[name] for name in columns if name in values
        })

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
        add_image = QPushButton("Add Image")
        add_scatter.setCursor(Qt.CursorShape.PointingHandCursor)
        add_hist.setCursor(Qt.CursorShape.PointingHandCursor)
        add_image.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(add_scatter)
        header.addWidget(add_hist)
        header.addWidget(add_image)
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
        page._fig_column_transforms = {}  # upstream axes are applied by _column_data
        page._fig_plots = task_def.setdefault("params", {}).setdefault("plots", [])
        # Backfill last-used axis parameters for older projects.
        for pd_ in page._fig_plots:
            pd_.setdefault("last_x", pd_.get("x"))
            pd_.setdefault("last_y", pd_.get("y"))
            pd_.setdefault("last_color_param", pd_.get("color_param"))
        page._fig_cards = []
        page._fig_scene = scene
        page._fig_view = canvas_view
        page._fig_selected = []
        page._fig_settings_layout = settings_layout
        page._fig_locked = locked
        page._fig_focused = None
        page._fig_pan_scene = None
        page._fig_pan_range = None
        page._fig_save_btn = save_btn
        page._fig_dirty = False
        save_btn.clicked.connect(lambda _=False: self._figure_save_now(page))
        self._figure_update_save_btn(page)
        page._fig_filter_timer = QTimer(self)
        page._fig_filter_timer.setInterval(100)
        page._fig_filter_timer.timeout.connect(
            lambda: self._figure_image_filter_on_timer(page)
        )
        page._fig_filter_timer.start()
        canvas_view.enter_callback = lambda: self._figure_toggle_break(page, page._fig_selected)
        canvas_view.delete_callback = lambda: self._figure_delete_selected(page)
        canvas_view.unfocus_callback = lambda: self._figure_unfocus(page)
        canvas_view.plot_wheel_cb = lambda item, d: self._figure_plot_wheel(page, item, d)
        canvas_view.plot_pan_start_cb = lambda item, sp: self._figure_plot_pan_start(page, item, sp)
        canvas_view.plot_pan_move_cb = lambda sp: self._figure_plot_pan_move(page, sp)
        canvas_view.plot_pan_end_cb = lambda: self._figure_plot_pan_end(page)
        canvas_view.rubber_select_cb = lambda rect, ctrl: self._figure_box_select(page, rect, ctrl)
        canvas_view.rubber_clear_cb = lambda: self._figure_clear_selection(page)

        for plot_dict in page._fig_plots:
            self._figure_new_card(page, plot_dict)

        add_scatter.clicked.connect(lambda _=False: self._figure_add_plot(page, "scatter"))
        add_hist.clicked.connect(lambda _=False: self._figure_add_plot(page, "histogram"))
        add_image.clicked.connect(lambda _=False: self._figure_add_plot(page, "image"))

        if locked:
            add_scatter.setEnabled(False)
            add_hist.setEnabled(False)
            add_image.setEnabled(False)

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

    def _figure_attach_legend(self, plot_widget):
        """Create and attach the side legend widget for an image plot."""
        widget = _FigureLegendWidget()
        widget.setVisible(False)
        plot_widget._legend_widget = widget
        return widget

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
            side_widget=(
                self._figure_attach_legend(plot_widget)
                if plot_dict.get("type") == "image" else None
            ),
        )
        proxy = _CardProxy(
            on_select=lambda ctrl: self._figure_card_clicked(page, card.index(), ctrl),
            on_drop=lambda pos: self._figure_reorder(page, card.index(), pos),
            on_drag=lambda pos: self._figure_drag_preview(page, card.index(), pos),
            on_drag_end=lambda: self._figure_clear_drop_marker(page),
        )
        proxy.setWidget(card)
        proxy.resize(w, h)
        page._fig_scene.addItem(proxy)
        card._proxy = proxy

        # Clicking the plot (not just the title bar) also selects the card.
        click_filter = _ClickFilter(
            lambda ctrl: self._figure_card_clicked(page, card.index(), ctrl)
        )
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

        # Image plots re-sample (LOD) when the user zooms/pans their data.
        if plot_dict.get("type") == "image":
            vb = plot_widget.getPlotItem().getViewBox()
            rt = QTimer(plot_widget)
            rt.setSingleShot(True)
            rt.setInterval(30)
            rt.timeout.connect(
                lambda: self._figure_image_refresh(page, plot_dict, plot_widget)
            )
            vb.sigRangeChanged.connect(lambda *_: rt.start())
            plot_widget._img_refresh_timer = rt

        page._fig_cards.append(card)
        self._figure_render(plot_dict, plot_widget, column_transforms=page._fig_column_transforms,
                task_id=page._fig_task_id)
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
        self._figure_clear_drop_marker(page)
        if not (0 <= index < len(plots)):
            return
        new_index = self._figure_drop_index(page, index, scene_pos)
        if new_index == index:
            self._figure_relayout(page)
            return
        # A dragged card joins the row it is dropped into: a manual line break
        # it carried must not follow it, or it would stay on a row of its own.
        # Press Enter afterwards to start a new row at that card.
        plots[index]["break_before"] = False
        plots.insert(new_index, plots.pop(index))
        cards.insert(new_index, cards.pop(index))
        for i, card in enumerate(cards):
            card.set_index(i)
        page._fig_selected = [new_index]
        self._figure_relayout(page)
        self._figure_mark_dirty(page)

    def _figure_drop_marker_pos(self, page, index: int, scene_pos):
        """Return (x, y, w, h) of the slot the dragged card would land in."""
        if not page._fig_cards:
            return None
        new_index = self._figure_drop_index(page, index, scene_pos)
        # Simulate the drop (the dragged card loses its line break, exactly as
        # _figure_reorder does) and read back its final flow position.
        plots = [dict(p) for p in page._fig_plots]
        cards = list(page._fig_cards)
        if not (0 <= index < len(plots)) or not (0 <= index < len(cards)):
            return None
        dragged_plot = plots.pop(index)
        dragged_card = cards.pop(index)
        dragged_plot["break_before"] = False
        new_index = max(0, min(new_index, len(plots)))
        plots.insert(new_index, dragged_plot)
        cards.insert(new_index, dragged_card)
        positions = self._figure_flow_positions(cards, plots)
        if not (0 <= new_index < len(positions)):
            return None
        return positions[new_index]

    def _figure_drag_preview(self, page, index: int, scene_pos) -> None:
        """Show the insertion slot while a card is being dragged."""
        marker = getattr(page, "_fig_drop_marker", None)
        where = self._figure_drop_marker_pos(page, index, scene_pos)
        if where is None:
            return
        x, y, w, h = where
        if marker is None:
            marker = QGraphicsRectItem()
            pen = QPen(QColor("#4285f4"), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            marker.setPen(pen)
            marker.setBrush(QColor(66, 133, 244, 40))
            marker.setZValue(2)
            page._fig_scene.addItem(marker)
            page._fig_drop_marker = marker
        marker.setRect(QRectF(x, y, w, h))
        marker.show()

    def _figure_clear_drop_marker(self, page) -> None:
        marker = getattr(page, "_fig_drop_marker", None)
        if marker is not None:
            page._fig_scene.removeItem(marker)
            page._fig_drop_marker = None

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

    def _figure_toggle_break(self, page, indices) -> None:
        """Toggle a line break before each selected plot (like pressing Enter)."""
        changed = False
        for index in indices:
            if 0 <= index < len(page._fig_plots):
                p = page._fig_plots[index]
                p["break_before"] = not p.get("break_before", False)
                changed = True
        if changed:
            self._figure_relayout(page)
            self._figure_mark_dirty(page)

    def _figure_delete_selected(self, page) -> None:
        """Delete every currently selected plot."""
        indices = sorted(
            set(i for i in page._fig_selected if 0 <= i < len(page._fig_plots)),
            reverse=True,
        )
        if not indices:
            return
        for index in indices:
            page._fig_plots.pop(index)
            card = page._fig_cards.pop(index)
            proxy = card.proxy()
            page._fig_scene.removeItem(proxy)
            proxy.deleteLater()
        for i, c in enumerate(page._fig_cards):
            c.set_index(i)
        if page._fig_cards:
            page._fig_selected = [min(indices[-1], len(page._fig_cards) - 1)]
            self._figure_relayout(page)
            self._figure_set_selection(page, page._fig_selected)
        else:
            page._fig_selected = []
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
        columns = self._columns_for(
            rec.get("parquet"), page._fig_task_id
        ) if rec else []
        x = columns[0] if columns else None
        y = columns[1] if len(columns) > 1 else x
        count = sum(1 for p in page._fig_plots if p.get("type") == ptype)
        if ptype == "image":
            # Image plots default to coloring by the first non-coordinate channel.
            color_mode = "parameter"
            color_param = next((c for c in columns if c not in ("x", "y")), None)
        else:
            color_mode = "density"
            color_param = x
        plot_dict = {
            "type": ptype,
            "title": f"{ptype.title()} {count + 1}",
            "file": rec.get("parquet") if rec else None,
            "file_name": rec.get("name") if rec else None,
            "x": x,
            "y": y if _is_scatter_plot(ptype) else None,
            "last_x": x,
            "last_y": y if _is_scatter_plot(ptype) else None,
            "color_mode": color_mode,
            "color_param": color_param,
            "last_color_param": color_param,
            "palette": "viridis",
            "layers": (
                [{"parameter": color_param, "color": (255, 255, 255), "name": str(color_param), "visible": True}]
                if ptype == "image" and color_param else []
            ),
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
            "filter": None,
        }
        page._fig_plots.append(plot_dict)
        index = self._figure_new_card(page, plot_dict)
        self._figure_mark_dirty(page)
        self._figure_select(page, index)

    def _figure_filter_options(self, figure_task_id):
        """Return [(display_name, {"task_id":..,"gate_id":..}), ...] for gates
        in gating modules strictly upstream of the figure module."""
        options = []
        for task in self._task_chain(figure_task_id):
            if task.get("id") == figure_task_id:
                continue
            if task.get("type") != "gating":
                continue
            module_name = task.get("name", "Gating")
            for gate in task.get("params", {}).get("gates", []):
                options.append((
                    f"{module_name} → {gate.get('name', gate.get('id'))}",
                    {"task_id": task.get("id"), "gate_id": gate.get("id")},
                ))
        return options

    def _figure_filter_mask(self, plot_dict):
        """Return (mask, error) for the plot's filter, or (None, None) when
        there is no filter (or it is trivially empty)."""
        f = plot_dict.get("filter")
        if not f:
            return None, None
        gating_task = self._find_task_by_id(f.get("task_id"))
        if gating_task is None:
            return None, "Filter module not found"
        gates = gating_task.get("params", {}).get("gates", [])
        by_id = {g["id"]: g for g in gates}
        chain = []
        cur = by_id.get(f.get("gate_id"))
        while cur is not None:
            chain.append(cur)
            cur = by_id.get(cur.get("parent"))
        if not chain:
            return None, "Filter gate not found"
        needed = set()
        for g in chain:
            needed |= _gate_needed_columns(g, by_id)
        rec = self._figure_record(plot_dict.get("file"))
        if rec is None:
            return None, "No data file"
        filter_task = f.get("task_id")
        values = self._column_data(
            filter_task, rec["parquet"], list(needed)
        )
        missing = needed - set(values)
        if missing:
            return None, "Filter parameter not in this file: " + ", ".join(
                sorted(str(m) for m in missing)
            )
        try:
            mask = _compute_gate_filter_mask(
                gates, f.get("gate_id"), rec["parquet"], {},
                existing=set(values), extra=values,
            )
        except Exception:
            return None, "Filter could not be applied"
        if mask is None:
            return None, "Filter could not be applied"
        return mask, None

    def _image_load_manifest(self, parquet_path):
        import json
        mf = _image_index_dir(parquet_path) / "manifest.json"
        if not mf.exists():
            return None
        try:
            with open(mf, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _image_index_valid(self, parquet_path, manifest):
        try:
            st = Path(parquet_path).stat()
            return (
                manifest.get("version") == 2
                and manifest.get("file_size") == int(st.st_size)
                and abs(manifest.get("file_mtime", 0.0) - st.st_mtime) < 1e-6
            )
        except Exception:
            return False

    def _ensure_image_index(self, parquet_path):
        """Return the cached index entry for a file, or None when it has none.

        Image indexes are built at import time. A file without one is left
        alone (the caller prompts the user); it may be a flow/table file, or
        an image that needs re-importing.
        """
        cache = getattr(self, "_image_index_cache", None)
        if cache is None:
            cache = {}
            self._image_index_cache = cache
        ent = cache.get(parquet_path)
        if ent is not None and ent.get("manifest") is not None:
            return ent
        manifest = self._image_load_manifest(parquet_path)
        if manifest is not None and self._image_index_valid(parquet_path, manifest):
            ent = {"manifest": manifest, "levels": {}}
            cache[parquet_path] = ent
            return ent
        return None

    def _image_level_2d(self, parquet_path, block):
        ent = getattr(self, "_image_index_cache", {}).get(parquet_path)
        if ent is None:
            return None
        lv = ent.get("levels", {}).get(block)
        if lv is None:
            manifest = ent.get("manifest") or {}
            for info in manifest.get("levels", []):
                if info["block"] == block:
                    lv = _image_load_level(parquet_path, info)
                    ent.setdefault("levels", {})[block] = lv
                    break
        return lv

    def _image_raster_raw(self, parquet_path, manifest, bbox, channel,
                          mask_ent=None, stages=None):
        """Rasterise raw pixels inside a bbox (deep zoom beyond the finest level).

        `stages` evaluates the ordered map pipeline (Scale / Compensation /
        Unmix) per pixel instead of reading one channel.
        """
        import math
        import numpy as np
        import pyarrow.parquet as pq
        color_cols = manifest.get("color_cols", [])
        if stages is not None:
            needed = _pipeline_dependencies(stages, [channel])
            wanted = [c for c in needed if c not in ("x", "y")]
        else:
            needed = {channel}
            if channel is None or channel not in color_cols:
                channel = color_cols[0] if color_cols else None
            wanted = [channel] if channel else []
        x0, x1, y0, y1 = bbox
        cols = list(dict.fromkeys(["x", "y"] + wanted))
        filters = [("y", ">=", y0), ("y", "<=", y1), ("x", ">=", x0), ("x", "<=", x1)]
        try:
            df = pq.read_table(parquet_path, columns=cols, filters=filters).to_pandas()
        except Exception:
            return None, None, None
        if len(df) == 0:
            return None, None, None
        x = df["x"].to_numpy(dtype="float64")
        y = df["y"].to_numpy(dtype="float64")
        if stages is not None:
            values = {}
            if "x" in needed:
                values["x"] = x
            if "y" in needed:
                values["y"] = y
            for name in wanted:
                if name in df.columns:
                    values[name] = df[name].to_numpy(dtype="float64")
                else:
                    return None, None, None
            evaluated = _evaluate_stages(
                stages, values, [channel], parquet_path,
                origin=(float(manifest["xmin"]), float(manifest["ymin"])),
            )
            if channel not in evaluated:
                return None, None, None
            v = evaluated[channel]
        elif channel == "x":
            v = x
        elif channel == "y":
            v = y
        elif channel:
            v = df[channel].to_numpy(dtype="float64")
        else:
            v = np.ones(len(df))
        nbx = max(1, int(math.ceil(x1 - x0)))
        nby = max(1, int(math.ceil(y1 - y0)))
        bi = np.clip((x - x0).astype(np.int64), 0, nbx - 1)
        bj = np.clip((y - y0).astype(np.int64), 0, nby - 1)
        bidx = bj * nbx + bi
        cnt = np.bincount(bidx, minlength=nbx * nby).reshape(nby, nbx)
        frac = None
        if mask_ent is not None and mask_ent.get("mask_2d") is not None:
            mask2d = mask_ent["mask_2d"]
            xmin = int(manifest["xmin"])
            ymin = int(manifest["ymin"])
            mi = np.clip((x - xmin).astype(np.int64), 0, mask2d.shape[1] - 1)
            mj = np.clip((y - ymin).astype(np.int64), 0, mask2d.shape[0] - 1)
            pix_mask = mask2d[mj, mi].astype(np.float64)
            ssum = np.bincount(bidx, weights=v * pix_mask, minlength=nbx * nby).reshape(nby, nbx)
            cnt_pass = np.bincount(bidx, weights=pix_mask, minlength=nbx * nby).reshape(nby, nbx)
            chan = np.divide(ssum, cnt_pass, out=np.zeros_like(ssum), where=cnt_pass > 0)
            frac = np.divide(cnt_pass, cnt, out=np.zeros_like(cnt_pass), where=cnt > 0)
        else:
            ssum = np.bincount(bidx, weights=v, minlength=nbx * nby).reshape(nby, nbx)
            chan = np.divide(ssum, cnt, out=np.zeros_like(ssum), where=cnt > 0)
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        return chan, rect, frac

    def _image_raster(self, parquet_path, manifest, bbox, spacing, channel,
                      mask_ent=None, stages=None):
        """Return (channel_2d_array, data_rect, mask_fraction) for the visible bbox.

        `stages` evaluates the ordered map pipeline (Scale / Compensation /
        Unmix). Linear stages are exact on the block means; Scale stages are
        applied to the aggregated values.
        """
        import math
        import numpy as np
        color_cols = manifest.get("color_cols", [])
        if not color_cols:
            return None, None, None
        if stages is not None:
            needed = _pipeline_dependencies(stages, [channel])
            if not all(c in color_cols for c in needed):
                # Not all inputs are indexed; fall back to raw pixels.
                return self._image_raster_raw(
                    parquet_path, manifest, bbox, channel, mask_ent, stages
                )
            levels = manifest.get("levels", [])
            if not levels or (
                spacing < levels[0]["block"] and levels[0]["block"] > 1
            ):
                return self._image_raster_raw(
                    parquet_path, manifest, bbox, channel, mask_ent, stages
                )
            chosen = levels[-1]
            for info in levels:
                if info["block"] >= spacing:
                    chosen = info
                    break
            arr = self._image_level_2d(parquet_path, chosen["block"])
            if arr is None:
                return None, None, None
            x0, x1, y0, y1 = bbox
            xmin = manifest["xmin"]
            ymin = manifest["ymin"]
            b = chosen["block"]
            i0 = max(0, int(math.floor((x0 - xmin) / b)))
            i1 = min(arr.shape[1], int(math.ceil((x1 - xmin) / b)))
            j0 = max(0, int(math.floor((y0 - ymin) / b)))
            j1 = min(arr.shape[0], int(math.ceil((y1 - ymin) / b)))
            if i1 <= i0 or j1 <= j0:
                return None, None, None
            values = {
                name: arr[j0:j1, i0:i1, color_cols.index(name)]
                for name in needed if name in color_cols
            }
            evaluated = _evaluate_stages(
                stages, values, [channel], parquet_path,
                origin=(float(manifest["xmin"]), float(manifest["ymin"])),
            )
            if channel not in evaluated:
                return None, None, None
            rect = QRectF(
                xmin + i0 * b, ymin + j0 * b, (i1 - i0) * b, (j1 - j0) * b
            )
            frac = None
            if mask_ent is not None:
                frac_level = mask_ent.get("levels", {}).get(b)
                if frac_level is not None:
                    frac = frac_level[j0:j1, i0:i1]
            return evaluated[channel], rect, frac
        if channel is None or channel not in color_cols:
            channel = color_cols[0] if color_cols else None
        cidx = color_cols.index(channel) if channel else 0
        x0, x1, y0, y1 = bbox
        xmin = manifest["xmin"]
        ymin = manifest["ymin"]
        levels = manifest.get("levels", [])
        if not levels:
            return None, None, None
        if spacing < levels[0]["block"] and levels[0]["block"] > 1:
            return self._image_raster_raw(
                parquet_path, manifest, bbox, channel, mask_ent, None
            )
        chosen = levels[-1]
        for info in levels:
            if info["block"] >= spacing:
                chosen = info
                break
        arr = self._image_level_2d(parquet_path, chosen["block"])
        if arr is None:
            return None, None, None
        b = chosen["block"]
        i0 = max(0, int(math.floor((x0 - xmin) / b)))
        i1 = min(arr.shape[1], int(math.ceil((x1 - xmin) / b)))
        j0 = max(0, int(math.floor((y0 - ymin) / b)))
        j1 = min(arr.shape[0], int(math.ceil((y1 - ymin) / b)))
        if i1 <= i0 or j1 <= j0:
            return None, None, None
        crop = arr[j0:j1, i0:i1, cidx]
        rect = QRectF(xmin + i0 * b, ymin + j0 * b, (i1 - i0) * b, (j1 - j0) * b)
        frac = None
        if mask_ent is not None:
            frac_level = mask_ent.get("levels", {}).get(b)
            if frac_level is not None:
                frac = frac_level[j0:j1, i0:i1]
        return crop, rect, frac

    def _image_mask_crop(self, manifest, bbox, spacing, mask_ent):
        """Return (mask_fraction_2d, data_rect) for the visible bbox (level path)."""
        import math
        if mask_ent is None:
            return None, None
        levels = manifest.get("levels", [])
        if not levels:
            return None, None
        x0, x1, y0, y1 = bbox
        xmin = manifest["xmin"]
        ymin = manifest["ymin"]
        chosen = levels[-1]
        for info in levels:
            if info["block"] >= spacing:
                chosen = info
                break
        frac_level = mask_ent.get("levels", {}).get(chosen["block"])
        if frac_level is None:
            return None, None
        b = chosen["block"]
        i0 = max(0, int(math.floor((x0 - xmin) / b)))
        i1 = min(frac_level.shape[1], int(math.ceil((x1 - xmin) / b)))
        j0 = max(0, int(math.floor((y0 - ymin) / b)))
        j1 = min(frac_level.shape[0], int(math.ceil((y1 - ymin) / b)))
        if i1 <= i0 or j1 <= j0:
            return None, None
        frac = frac_level[j0:j1, i0:i1]
        rect = QRectF(xmin + i0 * b, ymin + j0 * b, (i1 - i0) * b, (j1 - j0) * b)
        return frac, rect

    def _image_filter_worker(self):
        worker = getattr(self, "_image_filter_worker_obj", None)
        if worker is None:
            worker = _ImageFilterWorker()
            self._image_filter_worker_obj = worker
            self._image_filter_cache = {}
            self._image_filter_pending = {}
        return worker

    def _figure_image_filter_error(self, f, parquet_path):
        """Return an error string for an invalid filter, or None if it is valid."""
        gating_task = self._find_task_by_id(f.get("task_id"))
        if gating_task is None:
            return "Filter module not found"
        gates = gating_task.get("params", {}).get("gates", [])
        by_id = {g["id"]: g for g in gates}
        chain = []
        cur = by_id.get(f.get("gate_id"))
        while cur is not None:
            chain.append(cur)
            cur = by_id.get(cur.get("parent"))
        if not chain:
            return "Filter gate not found"
        needed = set()
        for g in chain:
            needed |= _gate_needed_columns(g, by_id)
        values = self._column_data(f.get("task_id"), parquet_path, list(needed))
        missing = needed - set(values)
        if missing:
            return "Filter parameter not in this file: " + ", ".join(
                sorted(str(m) for m in missing)
            )
        return None

    def _figure_render_image(self, plot_dict: dict, plot_widget,
                             column_transforms=None, task_id=None,
                             extra_stages=None) -> None:
        """Render an Image plot as a raster (LOD pyramid), ImageJ-style."""
        import numpy as np
        self._figure_update_image_legend(plot_widget, [])
        rec = self._figure_record(plot_dict.get("file"))
        if not rec:
            plot_widget.clear()
            plot_widget._img_fitted = False
            plot_widget.setTitle("No data file", color="#c0392b", size="10pt")
            return
        parquet_path = rec.get("parquet")
        ent = self._ensure_image_index(parquet_path)
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

        # Resolve the filter mask (computed in a background worker, cached).
        mask_ent = None
        f = plot_dict.get("filter")
        if f:
            sig = (f.get("task_id"), f.get("gate_id"))
            key = (parquet_path, sig)
            cache = getattr(self, "_image_filter_cache", {})
            if key not in cache:
                err = self._figure_image_filter_error(f, parquet_path)
                if err:
                    plot_widget.setTitle(err, color="#c0392b", size="10pt")
                    return
                worker = self._image_filter_worker()
                pending = getattr(self, "_image_filter_pending", {})
                if key not in pending:
                    pending[key] = True
                    gating_task = self._find_task_by_id(f.get("task_id"))
                    gates = copy.deepcopy(gating_task.get("params", {}).get("gates", []))
                    stages = self._map_stages(f.get("task_id"))
                    base_columns = set(
                        self._column_index.get(
                            self._rel_data_key(parquet_path), []
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

        stages = self._map_stages(task_id) if task_id else []
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
                self._figure_update_image_legend(plot_widget, [])
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
        seg_cols = self._seg_applied_columns(task_id, parquet_path) if task_id else {}
        for layer in layers:
            if layer.get("visible") is False:
                continue
            channel = layer.get("parameter")
            if not channel:
                continue
            if channel in seg_cols:
                chan, rect = self._seg_mask_raster(
                    manifest, (x0, x1, y0, y1), seg_cols[channel]
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
            else:
                if channel not in color_cols:
                    # Only parameters produced by an Unmix module can be rendered.
                    produced = any(
                        kind == "unmix" and channel in stage["names"]
                        for kind, stage in stages
                    )
                    if not produced:
                        unavailable.append(channel)
                        continue
                raster_stages = stages if _pipeline_touches(stages, channel) else None
                chan, rect, frac = self._image_raster(
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
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = 0.0, 1.0
            if hi > lo:
                norm = (chan.astype("float64") - lo) / (hi - lo)
            else:
                norm = np.zeros(chan.shape, dtype="float64")
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
            self._figure_update_image_legend(plot_widget, [])
            if unavailable:
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
        if has_mask and mask_accum is not None and mask_accum.shape == ref_shape:
            rgba[..., 3] = (mask_accum * 255.0).astype(np.uint8)
        else:
            rgba[..., 3] = 255
        img.setImage(rgba, autoLevels=False)
        img.setRect(ref_rect)
        img.setVisible(True)
        self._figure_apply_display_options(plot_dict, plot_widget)
        self._figure_update_image_legend(
            plot_widget, rendered_layers,
            show=plot_dict.get("show_legend", True),
        )

    def _figure_update_image_legend(self, plot_widget, layers, show=True) -> None:
        """Update the colour legend shown beside an image plot."""
        widget = getattr(plot_widget, "_legend_widget", None)
        if widget is None:
            return
        if not show or not layers:
            widget.setVisible(False)
            return
        widget.set_layers(layers)
        widget.setVisible(True)

    def _figure_image_refresh(self, page, plot_dict, plot_widget) -> None:
        self._figure_render_image(
            plot_dict, plot_widget,
            column_transforms=page._fig_column_transforms,
            task_id=page._fig_task_id,
        )

    def _figure_image_filter_on_timer(self, page) -> None:
        """Poll the filter worker: cache finished masks and re-render matching plots."""
        worker = getattr(self, "_image_filter_worker_obj", None)
        if worker is None or not hasattr(page, "_fig_plots"):
            return
        completed = []
        for key, result in worker.poll():
            getattr(self, "_image_filter_cache", {})[key] = result
            getattr(self, "_image_filter_pending", {}).pop(key, None)
            completed.append(key)
        if not completed:
            return
        for i, p in enumerate(page._fig_plots):
            if p.get("type") != "image" or i >= len(page._fig_cards):
                continue
            rec = self._figure_record(p.get("file"))
            path = rec.get("parquet") if rec else None
            f = p.get("filter")
            if not f or not path:
                continue
            key = (path, (f.get("task_id"), f.get("gate_id")))
            if key in completed:
                self._figure_render_image(
                    p, page._fig_cards[i].plot_widget(),
                    column_transforms=page._fig_column_transforms,
                    task_id=page._fig_task_id,
                )

    def _figure_render(self, plot_dict: dict, plot_widget, column_transforms=None,
                       task_id=None, extra_stages=None) -> None:
        """(Re)draw a plot widget from its plot_dict."""
        if plot_dict.get("type") == "image":
            self._figure_render_image(
                plot_dict, plot_widget, column_transforms, task_id, extra_stages
            )
            return
        mask, error = self._figure_filter_mask(plot_dict)
        if error:
            plot_widget.clear()
            plot_widget.setTitle(error, color="#c0392b", size="10pt")
            return
        if plot_dict.get("type") == "histogram":
            self._figure_render_histogram(
                plot_dict, plot_widget, column_transforms, mask, task_id,
                extra_stages,
            )
        else:
            self._figure_render_scatter(
                plot_dict, plot_widget, column_transforms, mask, task_id,
                extra_stages,
            )

    def _figure_render_scatter(self, plot_dict: dict, plot_widget, column_transforms=None,
                               mask=None, task_id=None, extra_stages=None) -> None:
        import numpy as np
        import pandas as pd

        plot_widget.clear()
        rec = self._figure_record(plot_dict.get("file"))
        x_name = plot_dict.get("x")
        y_name = plot_dict.get("y")
        if not rec:
            plot_widget.setTitle("No data file", color="#c0392b", size="10pt")
            return
        if not x_name or not y_name:
            plot_widget.setTitle("Parameter not in this file", color="#c0392b", size="10pt")
            return

        transforms = column_transforms or {}

        columns = [x_name, y_name]
        mode = plot_dict.get("color_mode", "density")
        color_param = plot_dict.get("color_param") if mode == "parameter" else None
        if color_param and color_param not in columns:
            columns.append(color_param)

        try:
            df = self._read_columns(
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

        scatter = _NoClickScatter(
            x=x, y=y, symbol="o", size=size, pen=None, brush=brushes,
            pxMode=px_mode,
        )
        # Scatter points must never swallow clicks: gating tools (quadrant,
        # split, range, polygon) start from a click on the plot, which would
        # otherwise be consumed by the point under the cursor.
        scatter.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        scatter.setAcceptHoverEvents(False)
        plot_widget.addItem(scatter)

    def _figure_render_histogram(self, plot_dict: dict, plot_widget, column_transforms=None,
                                 mask=None, task_id=None, extra_stages=None) -> None:
        import numpy as np
        import pandas as pd
        import pyqtgraph as pg

        plot_widget.clear()
        rec = self._figure_record(plot_dict.get("file"))
        x_name = plot_dict.get("x")
        if not rec:
            plot_widget.setTitle("No data file", color="#c0392b", size="10pt")
            return
        if not x_name:
            plot_widget.setTitle("Parameter not in this file", color="#c0392b", size="10pt")
            return
        try:
            df = self._read_columns(
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
        x = _apply_scale_transforms(x, (column_transforms or {}).get(x_name))
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

    def _figure_apply_display_options(self, plot_dict: dict, plot_widget) -> None:
        """Apply axis labels, visibility, tick notation, and sample-name title."""
        plot_item = plot_widget.getPlotItem()
        x_name = plot_dict.get("x")
        y_name = plot_dict.get("y") if _is_scatter_plot(plot_dict.get("type")) else "Count"

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
                task_id=page._fig_task_id,
            )
        self._figure_mark_dirty(page)

    def _figure_apply_change_multi(self, page) -> None:
        """Re-render every selected plot after a batch edit (marks the page dirty)."""
        for i in page._fig_selected:
            if 0 <= i < len(page._fig_cards):
                self._figure_render(
                    page._fig_plots[i], page._fig_cards[i].plot_widget(),
                    column_transforms=page._fig_column_transforms,
                    task_id=page._fig_task_id,
                )
        self._figure_mark_dirty(page)

    def _figure_refresh_all(self, page) -> None:
        """Re-render every plot card (used when an upstream module saved)."""
        page._fig_column_transforms = {}  # upstream axes are applied by _column_data
        # Gate definitions may have changed upstream; drop cached image filters.
        self._image_filter_cache = {}
        self._image_filter_pending = {}
        for i, card in enumerate(page._fig_cards):
            if i < len(page._fig_plots):
                plot_widget = card.plot_widget()
                vb = plot_widget.getPlotItem().getViewBox()
                xr, yr = vb.viewRange()
                self._figure_render(
                    page._fig_plots[i], plot_widget,
                    column_transforms=page._fig_column_transforms,
                    task_id=page._fig_task_id,
                )
                # Keep the view range; only the data (and gate) changed.
                vb.disableAutoRange()
                vb.setRange(xRange=(xr[0], xr[1]), yRange=(yr[0], yr[1]), padding=0)

    def _figure_set_selection(self, page, indices) -> None:
        """Set the selected plot set (a list of indices) and refresh settings."""
        indices = sorted(set(i for i in indices if 0 <= i < len(page._fig_cards)))
        page._fig_selected = indices
        sel = set(indices)
        for i, card in enumerate(page._fig_cards):
            card.set_selected(i in sel)
        self._figure_rebuild_settings(page)
        page._fig_view.setFocus()

    def _figure_select(self, page, index: int) -> None:
        """Select a single plot (clicking a card with no modifier)."""
        self._figure_set_selection(page, [index])
        if page._fig_focused != index:
            self._figure_unfocus(page)

    def _figure_card_clicked(self, page, index: int, ctrl: bool) -> None:
        """Handle a click on a card: Ctrl toggles it in the selection."""
        if ctrl:
            sel = list(page._fig_selected)
            if index in sel:
                sel.remove(index)
            else:
                sel.append(index)
            self._figure_unfocus(page)
            self._figure_set_selection(page, sel)
        else:
            self._figure_select(page, index)

    def _figure_box_select(self, page, scene_rect, ctrl: bool) -> None:
        """Select every plot whose card intersects the rubber-band rectangle."""
        hits = []
        for i, card in enumerate(page._fig_cards):
            if scene_rect.intersects(card.proxy().sceneBoundingRect()):
                hits.append(i)
        if ctrl:
            sel = set(page._fig_selected)
            sel.update(hits)
            hits = sorted(sel)
        self._figure_unfocus(page)
        self._figure_set_selection(page, hits)

    def _figure_clear_selection(self, page) -> None:
        """Deselect every plot (a plain click on empty canvas)."""
        self._figure_unfocus(page)
        self._figure_set_selection(page, [])

    def _figure_focus(self, page, index: int) -> None:
        """Focus a plot: wheel/drag act on its data instead of the canvas."""
        if not (0 <= index < len(page._fig_cards)):
            return
        page._fig_focused = index
        page._fig_selected = [index]
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
        dx = -d.x() * x_scale
        dy = d.y() * y_scale
        vb.setRange(
            xRange=(xr[0] + dx, xr[1] + dx),
            yRange=(yr[0] + dy, yr[1] + dy),
            padding=0,
        )
        return True

    def _figure_plot_pan_end(self, page) -> bool:
        was_panning = page._fig_pan_scene is not None
        page._fig_pan_scene = None
        page._fig_pan_range = None
        return was_panning

    def _figure_rebuild_settings(self, page) -> None:
        """Rebuild the right-hand settings panel for the current selection."""
        if len(page._fig_selected) > 1:
            self._figure_rebuild_settings_multi(page)
        else:
            self._figure_rebuild_settings_single(page)

    def _figure_rebuild_settings_single(self, page) -> None:
        """Rebuild the right-hand settings panel for a single selected plot."""
        layout = page._fig_settings_layout
        self._clear_layout(layout)
        index = page._fig_selected[0] if page._fig_selected else -1
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
        columns = self._columns_for(
            rec.get("parquet"), page._fig_task_id
        ) if rec else []

        # File.
        layout.addWidget(QLabel("File"))
        file_combo = _NoWheelComboBox()
        for r in records:
            file_combo.addItem(r.get("name", ""), r.get("parquet", ""))
        if plot_dict.get("file") is not None:
            idx = file_combo.findData(plot_dict.get("file"))
            if idx >= 0:
                file_combo.setCurrentIndex(idx)
        file_combo.setEnabled(enabled)
        layout.addWidget(self._file_nav_row(file_combo, enabled))

        # X parameter (image plots have no x parameter: their axes are the
        # image's own spatial coordinates, fixed by the image itself).
        x_combo = None
        if plot_dict.get("type") != "image":
            layout.addWidget(QLabel("X parameter"))
            x_combo = self._figure_param_combo(columns, plot_dict.get("x"), enabled)
            layout.addWidget(x_combo)

        # Filter: apply an upstream gate (gate + ancestors) to the data.
        layout.addWidget(QLabel("Filter"))
        filter_combo = _NoWheelComboBox()
        filter_combo.addItem("None", None)
        filter_options = self._figure_filter_options(page._fig_task_id)
        selected_idx = 0
        current_filter = plot_dict.get("filter")
        filter_found = False
        for i, (label, val) in enumerate(filter_options):
            filter_combo.addItem(label, val)
            if (
                current_filter
                and val.get("task_id") == current_filter.get("task_id")
                and val.get("gate_id") == current_filter.get("gate_id")
            ):
                selected_idx = i + 1
                filter_found = True
        if current_filter and not filter_found:
            # The selected gate was removed upstream; clear the stale filter.
            plot_dict["filter"] = None
        filter_combo.setCurrentIndex(selected_idx)
        filter_combo.setEnabled(enabled)
        layout.addWidget(filter_combo)

        y_combo = None
        bins_spin = None
        hist_mode_combo = None

        if _is_scatter_plot(plot_dict.get("type")):
            # Image plots have no x/y parameters to pick — their axes are the
            # image's own spatial coordinates; channel choice is the Channels
            # table built below.
            if plot_dict.get("type") != "image":
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
            bins_spin = _NoWheelSpinBox()
            bins_spin.setRange(2, 2000)
            bins_spin.setValue(int(plot_dict.get("bins", 20)))
            bins_spin.setKeyboardTracking(False)
            bins_spin.setEnabled(enabled)
            layout.addWidget(bins_spin)

            # Display mode: histogram bars, fitted curve, or both overlaid.
            layout.addWidget(QLabel("Display mode"))
            hist_mode_combo = _NoWheelComboBox()
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

        # Y range (scatter/histogram/image: histograms show the Count axis).
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
        width_field = _SizeField(size[0], lambda v: on_width_changed())
        height_field = _SizeField(size[1], lambda v: on_height_changed())
        width_field.set_enabled(enabled)
        height_field.set_enabled(enabled)
        sh.addWidget(QLabel("W"))
        sh.addWidget(width_field)
        sh.addWidget(QLabel("H"))
        sh.addWidget(height_field)
        sh.addStretch()
        layout.addWidget(size_row)

        # Resolution = W / 10; editing it rescales W (and H) keeping the ratio.
        layout.addWidget(QLabel("Resolution"))
        res_row = QWidget()
        rh = QHBoxLayout(res_row)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(4)
        res_value = int(round(size[0] / 10.0)) if size[0] else 20
        resolution_field = _SizeField(
            res_value, lambda v: on_resolution_changed(), minimum=20, maximum=400
        )
        resolution_field.set_enabled(enabled)
        rh.addWidget(resolution_field)
        if plot_dict.get("type") == "image":
            origin_btn = QPushButton("Origin")
            origin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            origin_btn.setEnabled(enabled)
            origin_btn.setToolTip("Match the original image's aspect ratio")
            origin_btn.clicked.connect(lambda _=False: on_origin())
            rh.addWidget(origin_btn)
        square_btn = QPushButton("Square")
        square_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        square_btn.setEnabled(enabled)
        square_btn.setToolTip("Make width equal to height")
        square_btn.clicked.connect(lambda _=False: on_square())
        rh.addWidget(square_btn)
        rh.addStretch()
        layout.addWidget(res_row)

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
            new_cols = self._columns_for(
                new_rec.get("parquet"), page._fig_task_id
            ) if new_rec else []
            # Keep the last-used x/y; blank them if this file lacks them.
            plot_dict["x"] = (
                plot_dict.get("last_x") if plot_dict.get("last_x") in new_cols else None
            )
            if _is_scatter_plot(plot_dict.get("type")):
                plot_dict["y"] = (
                    plot_dict.get("last_y") if plot_dict.get("last_y") in new_cols else None
                )
                plot_dict["color_param"] = (
                    plot_dict.get("last_color_param")
                    if plot_dict.get("last_color_param") in new_cols
                    else None
                )
            self._figure_rebuild_settings(page)
            self._figure_apply_change(page, index, plot_dict)

        file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())

        def on_filter_changed():
            plot_dict["filter"] = filter_combo.currentData()
            # Keep the current view range; only the data subset changes.
            vb = plot_widget.getPlotItem().getViewBox()
            xr, yr = vb.viewRange()
            self._figure_apply_change(page, index, plot_dict)
            vb.disableAutoRange()
            vb.setRange(xRange=(xr[0], xr[1]), yRange=(yr[0], yr[1]), padding=0)

        filter_combo.currentIndexChanged.connect(lambda _=0: on_filter_changed())

        def on_axis_param(attr):
            def handler(value):
                value = value or None
                plot_dict[attr] = value
                # Remember this as the last-used parameter.
                plot_dict["last_" + attr] = value
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

        if x_combo is not None:
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

        def on_width_changed():
            w = width_field.value()
            if w is None:
                return
            h = height_field.value()
            h = h if h is not None else w
            plot_dict["size"] = [int(w), int(h)]
            resolution_field.set_value(int(round(w / 10.0)))
            self._figure_resize_card(page, index, w, h)
            self._figure_mark_dirty(page)

        def on_height_changed():
            w = width_field.value()
            h = height_field.value()
            if w is None or h is None:
                return
            plot_dict["size"] = [int(w), int(h)]
            self._figure_resize_card(page, index, w, h)
            self._figure_mark_dirty(page)

        def on_resolution_changed():
            r = resolution_field.value()
            if r is None:
                return
            w_old = width_field.value()
            h_old = height_field.value()
            w_new = r * 10
            if w_old and w_old > 0:
                h_new = int(round(h_old * w_new / w_old)) if h_old else int(round(w_new * 0.77))
            else:
                h_new = int(round(w_new * 0.77))
            plot_dict["size"] = [int(w_new), int(h_new)]
            width_field.set_value(w_new)
            height_field.set_value(h_new)
            self._figure_resize_card(page, index, w_new, h_new)
            self._figure_mark_dirty(page)

        def _legend_extra():
            # Extra card width consumed by the side legend (and its spacing).
            lg = getattr(plot_widget, "_legend_widget", None)
            if lg is not None and lg.isVisible():
                return lg.width() + 4
            return 0

        def on_origin():
            # Match the original image's aspect ratio, keeping H unchanged.
            extent = getattr(plot_widget, "_img_extent", None)
            if not extent:
                return
            img_w = extent[1] - extent[0] + 1
            img_h = extent[3] - extent[2] + 1
            h = height_field.value()
            if not h or img_w <= 0 or img_h <= 0:
                return
            w_new = int(round(h * img_h / img_w)) + _legend_extra()
            w_new = max(200, min(4000, w_new))
            plot_dict["size"] = [w_new, int(h)]
            width_field.set_value(w_new)
            resolution_field.set_value(int(round(w_new / 10.0)))
            self._figure_resize_card(page, index, w_new, h)
            self._figure_mark_dirty(page)

        def on_square():
            h = height_field.value()
            if h is None:
                return
            w_new = int(h) + _legend_extra()
            plot_dict["size"] = [w_new, int(h)]
            width_field.set_value(w_new)
            resolution_field.set_value(int(round(w_new / 10.0)))
            self._figure_resize_card(page, index, w_new, h)
            self._figure_mark_dirty(page)

    def _figure_rebuild_settings_multi(self, page) -> None:
        """Rebuild the settings panel for a multi-selection of plots.

        Each field shows the value when all selected plots agree, and blank
        when they differ; setting a field applies it to every selected plot.
        """
        layout = page._fig_settings_layout
        self._clear_layout(layout)
        indices = page._fig_selected
        plots = [page._fig_plots[i] for i in indices if 0 <= i < len(page._fig_plots)]
        if not plots:
            self._figure_rebuild_settings_single(page)
            return
        locked = page._fig_locked
        enabled = not locked

        layout.addWidget(QLabel(f"<b>{len(plots)} plots selected</b>"))
        hint = QLabel(
            "A value is shown only when all selected plots agree; otherwise it "
            "is blank. Setting a value applies it to all selected plots."
        )
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if locked:
            note = QLabel("Draft (locked) — settings are read-only.")
            note.setStyleSheet("color: #b58900;")
            note.setWordWrap(True)
            layout.addWidget(note)

        scatter_plots = [p for p in plots if p.get("type") == "scatter"]
        hist_plots = [p for p in plots if p.get("type") == "histogram"]

        def common(ps, key, default=None):
            vals = [p.get(key, default) for p in ps]
            if not vals:
                return default
            first = vals[0]
            for v in vals[1:]:
                if v != first:
                    return _MIXED
            return first

        def apply(ps, key, value):
            for p in ps:
                p[key] = value

        def make_value_combo(items, current):
            combo = _NoWheelComboBox()
            combo.addItem("", _MIXED)
            for text, val in items:
                combo.addItem(text, val)
            if current is _MIXED:
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.setEnabled(enabled)
            return combo

        def make_color_button(ps, key, default):
            cur = common(ps, key, default)
            btn = QPushButton()
            btn.setFixedSize(36, 20)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(enabled)
            if cur is _MIXED:
                btn.setStyleSheet(
                    "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "stop:0 #cccccc, stop:1 #999999); border: 1px solid #555;"
                )
                btn.setToolTip("Colors differ")
            else:
                btn.setStyleSheet(
                    f"background-color: rgb({cur[0]},{cur[1]},{cur[2]}); "
                    "border: 1px solid #555;"
                )

            def on_pick(_=False):
                chosen = QColorDialog.getColor(QColor(0, 0, 200), self, "Choose color")
                if chosen.isValid():
                    rgb = (chosen.red(), chosen.green(), chosen.blue())
                    apply(ps, key, rgb)
                    btn.setStyleSheet(
                        f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); "
                        "border: 1px solid #555;"
                    )
                    self._figure_apply_change_multi(page)

            btn.clicked.connect(on_pick)
            return btn

        def make_range_row(ps, axis):
            min_key = f"{axis}min"
            max_key = f"{axis}max"
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            auto = QCheckBox("Auto")
            amin = common(ps, min_key)
            amax = common(ps, max_key)
            if amin is _MIXED or amax is _MIXED:
                auto.setTristate(True)
                auto.setCheckState(Qt.CheckState.PartiallyChecked)
            else:
                auto.setChecked(amin is None and amax is None)
            auto.setEnabled(enabled)

            min_spin = _LogStepSpinBox(
                None if amin is _MIXED else amin, lambda v: commit()
            )
            max_spin = _LogStepSpinBox(
                None if amax is _MIXED else amax, lambda v: commit()
            )

            def commit():
                st = auto.checkState()
                if st == Qt.CheckState.Checked:
                    apply(ps, min_key, None)
                    apply(ps, max_key, None)
                    min_spin.set_value(None)
                    max_spin.set_value(None)
                elif st == Qt.CheckState.Unchecked:
                    mn = min_spin.value()
                    mx = max_spin.value()
                    if mn is not None and mx is not None:
                        apply(ps, min_key, mn)
                        apply(ps, max_key, mx)
                min_spin.set_enabled(enabled and auto.checkState() != Qt.CheckState.Checked)
                max_spin.set_enabled(enabled and auto.checkState() != Qt.CheckState.Checked)
                self._figure_apply_change_multi(page)

            auto.stateChanged.connect(lambda _=0: commit())
            min_spin.set_enabled(enabled and auto.checkState() != Qt.CheckState.Checked)
            max_spin.set_enabled(enabled and auto.checkState() != Qt.CheckState.Checked)

            h.addWidget(auto)
            h.addWidget(QLabel("Min"))
            h.addWidget(min_spin)
            h.addWidget(QLabel("Max"))
            h.addWidget(max_spin)
            h.addStretch()
            return row

        records = self._imported_files()

        # Union of columns across every selected plot's file.
        col_set = []
        seen = set()
        for p in plots:
            rec = self._figure_record(p.get("file"))
            for c in (
                self._columns_for(rec.get("parquet"), page._fig_task_id)
                if rec else []
            ):
                if c not in seen:
                    seen.add(c)
                    col_set.append(c)

        # File.
        layout.addWidget(QLabel("File"))
        file_combo = make_value_combo(
            [(r.get("name", ""), r.get("parquet", "")) for r in records],
            common(plots, "file"),
        )
        layout.addWidget(file_combo)

        def on_file_changed(_=0):
            f = file_combo.currentData()
            if f is None or f is _MIXED:
                return
            for p in plots:
                p["file"] = f
                p["file_name"] = file_combo.currentText()
                rec = self._figure_record(f)
                cols = self._columns_for(
                    rec.get("parquet"), page._fig_task_id
                ) if rec else []
                p["x"] = p.get("last_x") if p.get("last_x") in cols else None
                if _is_scatter_plot(p.get("type")):
                    p["y"] = p.get("last_y") if p.get("last_y") in cols else None
                    p["color_param"] = (
                        p.get("last_color_param")
                        if p.get("last_color_param") in cols
                        else None
                    )
            self._figure_apply_change_multi(page)
            self._figure_rebuild_settings_multi(page)

        file_combo.currentIndexChanged.connect(on_file_changed)

        # X parameter (omit when every selected plot is an image — image axes
        # are fixed to the image's own spatial coordinates).
        x_combo = None
        if any(p.get("type") != "image" for p in plots):
            layout.addWidget(QLabel("X parameter"))
            xv = common(plots, "x")
            x_combo = self._figure_param_combo(col_set, None if xv is _MIXED else xv, enabled)
            layout.addWidget(x_combo)

        def on_x(v):
            v = v or None
            for p in plots:
                p["x"] = v
                p["last_x"] = v
                p["xmin"] = None
                p["xmax"] = None
            self._figure_apply_change_multi(page)
            self._figure_rebuild_settings_multi(page)

        if x_combo is not None:
            x_combo.currentTextChanged.connect(on_x)

        # Filter: apply an upstream gate (gate + ancestors) to the data.
        layout.addWidget(QLabel("Filter"))
        filter_combo = _NoWheelComboBox()
        filter_combo.addItem("", _MIXED)
        filter_combo.addItem("None", None)
        filter_options = self._figure_filter_options(page._fig_task_id)
        for label, val in filter_options:
            filter_combo.addItem(label, val)
        fv = common(plots, "filter")
        if fv is _MIXED:
            filter_combo.setCurrentIndex(0)
        elif fv is None:
            filter_combo.setCurrentIndex(1)
        else:
            idx = 0
            for k, (label, val) in enumerate(filter_options):
                if (
                    val.get("task_id") == fv.get("task_id")
                    and val.get("gate_id") == fv.get("gate_id")
                ):
                    idx = k + 2
                    break
            filter_combo.setCurrentIndex(idx)
        filter_combo.setEnabled(enabled)
        layout.addWidget(filter_combo)

        def on_filter_changed(_=0):
            data = filter_combo.currentData()
            if data is _MIXED:
                return
            apply(plots, "filter", data)
            # Keep the current view range; only the data subset changes.
            for i in indices:
                if 0 <= i < len(page._fig_cards):
                    vb = page._fig_cards[i].plot_widget().getPlotItem().getViewBox()
                    xr, yr = vb.viewRange()
                    self._figure_render(
                        page._fig_plots[i], page._fig_cards[i].plot_widget(),
                        column_transforms=page._fig_column_transforms,
                        task_id=page._fig_task_id,
                    )
                    vb.disableAutoRange()
                    vb.setRange(xRange=(xr[0], xr[1]), yRange=(yr[0], yr[1]), padding=0)
            self._figure_mark_dirty(page)

        filter_combo.currentIndexChanged.connect(on_filter_changed)

        # Y parameter (scatter only).
        y_combo = None
        if scatter_plots:
            layout.addWidget(QLabel("Y parameter"))
            yv = common(scatter_plots, "y")
            y_combo = self._figure_param_combo(col_set, None if yv is _MIXED else yv, enabled)
            layout.addWidget(y_combo)

            def on_y(v):
                v = v or None
                for p in scatter_plots:
                    p["y"] = v
                    p["last_y"] = v
                    p["ymin"] = None
                    p["ymax"] = None
                self._figure_apply_change_multi(page)
                self._figure_rebuild_settings_multi(page)

            y_combo.currentTextChanged.connect(on_y)

        # Color settings (scatter only).
        if scatter_plots:
            layout.addWidget(QLabel("Color mode"))
            mode_combo = make_value_combo(
                FIGURE_COLOR_MODES, common(scatter_plots, "color_mode", "density")
            )
            layout.addWidget(mode_combo)

            def on_mode_changed(_=0):
                data = mode_combo.currentData()
                if data is _MIXED:
                    return
                apply(scatter_plots, "color_mode", data)
                self._figure_apply_change_multi(page)
                self._figure_rebuild_settings_multi(page)

            mode_combo.currentIndexChanged.connect(on_mode_changed)

            mode_val = common(scatter_plots, "color_mode", "density")
            if mode_val is not _MIXED:
                if mode_val == "parameter":
                    layout.addWidget(QLabel("Color parameter"))
                    cpv = common(scatter_plots, "color_param")
                    cp_combo = self._figure_param_combo(
                        col_set, None if cpv is _MIXED else cpv, enabled
                    )
                    layout.addWidget(cp_combo)

                    def on_cp(v):
                        apply(scatter_plots, "color_param", v or None)
                        apply(scatter_plots, "last_color_param", v or None)
                        self._figure_apply_change_multi(page)

                    cp_combo.currentTextChanged.connect(on_cp)
                elif mode_val == "solid":
                    layout.addWidget(QLabel("Solid color"))
                    layout.addWidget(make_color_button(
                        scatter_plots, "solid_color", (0, 0, 200)
                    ))
                else:
                    layout.addWidget(QLabel("Palette"))
                    palette_combo = make_value_combo(
                        [(p, p) for p in FIGURE_PALETTES],
                        common(scatter_plots, "palette", "viridis"),
                    )
                    layout.addWidget(palette_combo)

                    def on_palette(_=0):
                        data = palette_combo.currentData()
                        if data is _MIXED:
                            return
                        apply(scatter_plots, "palette", data)
                        self._figure_apply_change_multi(page)

                    palette_combo.currentIndexChanged.connect(on_palette)

            # Point size.
            layout.addWidget(QLabel("Point size"))
            ps_row = QWidget()
            ph = QHBoxLayout(ps_row)
            ph.setContentsMargins(0, 0, 0, 0)
            ph.setSpacing(4)
            point_fixed = QCheckBox("Fixed")
            fixed_val = common(scatter_plots, "fixed_point_size", False)
            if fixed_val is _MIXED:
                point_fixed.setTristate(True)
                point_fixed.setCheckState(Qt.CheckState.PartiallyChecked)
            else:
                point_fixed.setChecked(bool(fixed_val))
            point_fixed.setEnabled(enabled)

            ps_val = common(scatter_plots, "point_size", 4)

            def on_point_size(v):
                apply(scatter_plots, "point_size", float(v))
                self._figure_apply_change_multi(page)

            point_spin = _LogStepSpinBox(None if ps_val is _MIXED else ps_val, on_point_size)
            point_spin.set_enabled(enabled and point_fixed.isChecked())

            def on_point_fixed(_=0):
                st = point_fixed.checkState()
                if st == Qt.CheckState.PartiallyChecked:
                    return
                apply(scatter_plots, "fixed_point_size", st == Qt.CheckState.Checked)
                point_spin.set_enabled(enabled and st == Qt.CheckState.Checked)
                self._figure_apply_change_multi(page)

            point_fixed.stateChanged.connect(on_point_fixed)

            ph.addWidget(point_fixed)
            ph.addWidget(point_spin)
            ph.addStretch()
            layout.addWidget(ps_row)

        # Histogram settings.
        if hist_plots:
            layout.addWidget(QLabel("Bins"))
            bv = common(hist_plots, "bins", 20)

            def on_bins(v):
                apply(hist_plots, "bins", int(float(v)))
                self._figure_apply_change_multi(page)

            bins_spin = _LogStepSpinBox(None if bv is _MIXED else bv, on_bins)
            bins_spin.set_enabled(enabled)
            layout.addWidget(bins_spin)

            layout.addWidget(QLabel("Display mode"))
            hist_mode_combo = make_value_combo(
                FIGURE_HISTOGRAM_MODES, common(hist_plots, "hist_mode", "histogram")
            )
            layout.addWidget(hist_mode_combo)

            def on_hist_mode(_=0):
                data = hist_mode_combo.currentData()
                if data is _MIXED:
                    return
                apply(hist_plots, "hist_mode", data)
                self._figure_apply_change_multi(page)

            hist_mode_combo.currentIndexChanged.connect(on_hist_mode)

            layout.addWidget(QLabel("Histogram color"))
            layout.addWidget(make_color_button(hist_plots, "hist_color", (0, 0, 200)))
            layout.addWidget(QLabel("Curve color"))
            layout.addWidget(make_color_button(hist_plots, "curve_color", (200, 40, 40)))

        # X range.
        layout.addWidget(QLabel("X range"))
        layout.addWidget(make_range_row(plots, "x"))

        if scatter_plots:
            layout.addWidget(QLabel("Y range"))
            layout.addWidget(make_range_row(scatter_plots, "y"))

        # Size (width/height in canvas units).
        layout.addWidget(QLabel("Size (px)"))
        size_row = QWidget()
        sh = QHBoxLayout(size_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(4)
        size_val = common(plots, "size", [440, 340])
        if size_val is _MIXED:
            w_val = h_val = _MIXED
        else:
            w_val, h_val = int(size_val[0]), int(size_val[1])

        def on_size_changed(v=None):
            w = width_field.value()
            h = height_field.value()
            if w is None or h is None:
                return
            for i in indices:
                self._figure_resize_card(page, i, int(w), int(h))
            self._figure_mark_dirty(page)

        width_field = _SizeField(None if w_val is _MIXED else w_val, on_size_changed)
        height_field = _SizeField(None if h_val is _MIXED else h_val, on_size_changed)
        width_field.set_enabled(enabled)
        height_field.set_enabled(enabled)
        sh.addWidget(QLabel("W"))
        sh.addWidget(width_field)
        sh.addWidget(QLabel("H"))
        sh.addWidget(height_field)
        sh.addStretch()
        layout.addWidget(size_row)

        # Display options.
        layout.addWidget(QLabel("Display"))
        for label, key in (
            ("Show axes", "show_axes"),
            ("Show axis names", "show_axis_names"),
            ("Show indices", "show_indices"),
            ("Show sample name", "show_sample_name"),
        ):
            cb = QCheckBox(label)
            v = common(plots, key, True)
            if v is _MIXED:
                cb.setTristate(True)
                cb.setCheckState(Qt.CheckState.PartiallyChecked)
            else:
                cb.setChecked(bool(v))
            cb.setEnabled(enabled)

            def make_toggle(k, c):
                def h(_=0):
                    st = c.checkState()
                    if st == Qt.CheckState.PartiallyChecked:
                        return
                    apply(plots, k, st == Qt.CheckState.Checked)
                    self._figure_apply_change_multi(page)

                return h

            cb.stateChanged.connect(make_toggle(key, cb))
            layout.addWidget(cb)

        layout.addStretch()

    def _file_nav_row(self, combo, enabled: bool) -> QWidget:
        """Wrap a file combo with prev/next buttons that cycle through files."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        h.addWidget(combo, 1)
        prev_btn = QPushButton()
        prev_btn.setIcon(_triangle_icon("up"))
        next_btn = QPushButton()
        next_btn.setIcon(_triangle_icon("down"))
        for btn in (prev_btn, next_btn):
            btn.setFixedSize(24, 24)
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(enabled)
        prev_btn.setToolTip("Previous file")
        next_btn.setToolTip("Next file")

        def prev():
            n = combo.count()
            if n > 0:
                combo.setCurrentIndex((combo.currentIndex() - 1) % n)

        def nxt():
            n = combo.count()
            if n > 0:
                combo.setCurrentIndex((combo.currentIndex() + 1) % n)

        prev_btn.clicked.connect(prev)
        next_btn.clicked.connect(nxt)
        h.addWidget(prev_btn)
        h.addWidget(next_btn)
        return row

    def _figure_param_combo(self, columns, current, enabled: bool) -> QComboBox:
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

    def _figure_scatter_color_settings(self, layout, plot_dict, plot_widget, columns,
                                       enabled, on_change, on_rebuild) -> None:
        """Build + wire color mode/param, palette/solid, and point-size rows.

        Shared by the Figure and Scaling scatter settings panels.
        """
        if plot_dict.get("type") == "image":
            self._figure_image_layer_settings(
                layout, plot_dict, columns, enabled, on_change, on_rebuild
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
            palette_combo = _NoWheelComboBox()
            palette_combo.addItems(FIGURE_PALETTES)
            idx = palette_combo.findText(plot_dict.get("palette", "viridis"))
            if idx >= 0:
                palette_combo.setCurrentIndex(idx)
            palette_combo.setEnabled(enabled)
            layout.addWidget(palette_combo)

        # Point size (scatter only; an Image renders as a raster, so this row
        # is hidden for image plots).
        if plot_dict.get("type") != "image":
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
                plot_dict["last_color_param"] = v
                on_change()
            cp_combo.currentTextChanged.connect(on_cp)

        if palette_combo is not None:
            def on_palette(_=0):
                plot_dict["palette"] = palette_combo.currentText()
                on_change()
            palette_combo.currentIndexChanged.connect(on_palette)

    def _figure_image_layer_settings(self, layout, plot_dict, columns, enabled,
                                     on_change, on_rebuild) -> None:
        """Build the image channel table (show / parameter / colour / name)."""
        layout.addWidget(QLabel("Channels"))
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Show", "Parameter", "Color", "Name"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 44)
        table.setColumnWidth(2, 48)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setMinimumHeight(120)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: none; }"
            "QTableWidget::item:selected { background: #dbeafa; }"
        )
        layout.addWidget(table, 1)

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
                QColor(cur[0], cur[1], cur[2]), self, "Choose color"
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
            elif col == 3:
                layers[row]["name"] = item.text()
            else:
                return
            plot_dict["layers"] = layers
            on_change()

        def on_add(_=False):
            existing = {l.get("parameter") for l in _figure_image_layers(plot_dict)}
            available = [c for c in columns if c not in existing]
            chosen = self._pick_items_dialog(
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

                name_item = QTableWidgetItem(str(layer.get("name") or param or ""))
                name_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEditable
                )
                table.setItem(i, 3, name_item)
            table.blockSignals(False)
            table.resizeRowsToContents()

        table.itemChanged.connect(on_item_changed)
        add_btn.clicked.connect(on_add)
        remove_btn.clicked.connect(on_remove)
        show_legend.toggled.connect(on_show_legend)

        populate()

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
    def _scaling_column_set(self, task_id=None) -> list:
        """Return the sorted, de-duplicated set of input column names."""
        cols = set()
        for rec in self._imported_files():
            for c in self._columns_for(rec.get("parquet"), task_id):
                cols.add(c)
        return sorted(cols)

    def _scaling_load_config(self, task_def: dict) -> list:
        """Return the per-column scaling config, merged with the current columns."""
        cols = self._scaling_column_set(task_def.get("id"))
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
        cols = self._columns_for(
            rec.get("parquet"), task_def.get("id")
        ) if rec else []
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

            method_combo = _NoWheelComboBox()
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
        cols = self._columns_for(
            rec.get("parquet"), page._scaling_task_id
        ) if rec else []
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
        # Upstream stages are applied by the data pipeline; this module's own
        # current (possibly unsaved) config goes on top for the preview.
        transforms = {}
        for c in page._scaling_columns:
            if c.get("name"):
                transforms.setdefault(c["name"], []).append(c)
        self._figure_render_scatter(
            preview, page._scaling_plot_widget,
            column_transforms=transforms, task_id=page._scaling_task_id,
        )

    def _scaling_rebuild_settings(self, page) -> None:
        layout = page._scaling_settings_layout
        self._clear_layout(layout)
        preview = page._scaling_preview
        enabled = not page._scaling_locked
        plot_widget = page._scaling_plot_widget

        records = self._imported_files()
        rec = self._figure_record(preview.get("file"))
        columns = self._columns_for(
            rec.get("parquet"), page._scaling_task_id
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
            new_cols = self._columns_for(
                new_rec.get("parquet"), page._scaling_task_id
            ) if new_rec else []
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
        # Any saved change can alter the data flowing downstream.
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._stage_cache = {}
        self._seg_value_cache = {}
        self._module_set_state(page, MODULE_STATE_SAVED)
        # Saved output can lock (or unlock) modules, so refresh the lock badges
        # and button states in the workflow tree.
        self._refresh_workflow_tree()
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
            self._figure_rebuild_settings(page)
        elif task_type == "scale":
            self._scaling_render_preview(page)
        elif task_type == "gating":
            self._gating_render(page)
            self._gating_rebuild_settings(page)
        # Other module types have no live visualization to refresh yet.

    # ----- unmix module (map) -----------------------------------------
    def _build_unmix_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
        """Build the Unmix page: two fixed sub-pages (extraction / matrix)."""
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
        close_btn.clicked.connect(lambda _=False: self._unmix_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        page._unmix_task_def = task_def
        page._unmix_task_id = task_def.get("id")
        page._unmix_params = task_def.setdefault("params", {})
        page._unmix_params.setdefault("spectra", [])
        page._unmix_params.setdefault("matrix", [])
        page._unmix_params.setdefault("method", "ols")
        page._unmix_params.setdefault("trees", [])
        page._unmix_params.setdefault("next_spectrum_no", 1)
        spectrum_cfg = page._unmix_params.setdefault("spectrum_settings", {})
        spectrum_cfg.setdefault("params", [])
        spectrum_cfg.setdefault("enabled", None)
        spectrum_cfg.setdefault("stat", "median")
        spectrum_cfg.setdefault("colormap", "viridis")
        spectrum_cfg.setdefault("line_color", "#2b6cb0")
        spectrum_cfg.setdefault("decimals", 2)
        page._unmix_spectrum_cfg = spectrum_cfg
        page._unmix_snapshot = copy.deepcopy(page._unmix_params)
        page._unmix_locked = locked
        page._unmix_save_btn = save_btn
        page._unmix_dirty = False
        page._unmix_spectra = page._unmix_params["spectra"]
        page._unmix_active_spectrum = None
        # One embedded gate tree per spectrum (each is a full Gating pane).
        page._unmix_trees = []
        page._unmix_active_tree = None
        # Debounced recompute of the displayed spectrum after gate edits.
        page._unmix_spectrum_timer = QTimer(self)
        page._unmix_spectrum_timer.setSingleShot(True)
        page._unmix_spectrum_timer.timeout.connect(
            lambda: self._unmix_refresh_outputs(page)
        )
        # Debounced apply of a signature-table row drag.
        page._unmix_sig_order_timer = QTimer(self)
        page._unmix_sig_order_timer.setSingleShot(True)
        page._unmix_sig_order_timer.setInterval(200)
        page._unmix_sig_order_timer.timeout.connect(
            lambda: self._unmix_signature_reordered(page)
        )
        page._unmix_matrix = page._unmix_params["matrix"]
        if "samples" not in page._unmix_params:
            page._unmix_params["samples"] = [
                r.get("parquet") for r in self._imported_files() if r.get("parquet")
            ]
        # Per-spectrum value cache, cleared whenever anything changes.
        page._unmix_spectrum_cache = {}

        tabs = QTabWidget()
        tabs.addTab(self._unmix_extraction_subpage(page), "Spectrum Extraction")
        tabs.addTab(self._unmix_matrix_subpage(page), "Matrix & Unmixing")
        layout.addWidget(tabs, 1)

        save_btn.clicked.connect(lambda _=False: self._unmix_save_now(page))
        self._unmix_restore_trees(page)
        self._unmix_rebuild_spectrum_list(page)
        self._unmix_rebuild_spectrum_settings(page)
        self._unmix_rebuild_signature_table(page)
        self._unmix_rebuild_sample_list(page)
        self._unmix_render_matrix(page)
        active_spectrum = page._unmix_params.get("active_spectrum")
        if any(s["id"] == active_spectrum for s in page._unmix_spectra):
            self._unmix_select_spectrum(page, active_spectrum)
        self._unmix_update_save_btn(page)
        return page

    # ----- unmix: gate trees (reusing the Gating module) ----------------
    def _unmix_extraction_subpage(self, page) -> QWidget:
        """Sub-page 1: spectrum area (1) above the embedded gate tree (2)."""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self._unmix_spectrum_area(page))
        page._unmix_tree_stack = QStackedWidget()
        page._unmix_tree_stack.addWidget(self._unmix_tree_placeholder())
        split.addWidget(page._unmix_tree_stack)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 380])
        outer.addWidget(split, 1)
        return w

    def _unmix_spectrum_area(self, page) -> QWidget:
        """Spectrum settings (left) | spectra list (middle) | plot (right)."""
        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        row = QHBoxLayout(content)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)

        # Left: settings shared by every spectrum in this module.
        settings_panel = self._build_panel_frame()
        settings_lay = QVBoxLayout(settings_panel)
        settings_lay.setContentsMargins(10, 10, 10, 10)
        settings_lay.setSpacing(6)
        settings_lay.addWidget(QLabel("<b>Spectrum settings</b>"))
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
        page._unmix_spectrum_settings_layout = settings_layout

        # Middle: extracted spectra + the "New gate tree" entry point.
        list_panel = self._build_panel_frame()
        list_lay = QVBoxLayout(list_panel)
        list_lay.setContentsMargins(10, 10, 10, 10)
        list_lay.setSpacing(6)
        list_lay.addWidget(QLabel("<b>Spectra</b>"))
        spectra_list = QListWidget()
        spectra_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: none; }"
            "QListWidget::item:selected { background: #dbeafa; }"
        )
        list_lay.addWidget(spectra_list, 1)
        spectra_list.itemClicked.connect(
            lambda item: self._unmix_select_spectrum(
                page, item.data(Qt.ItemDataRole.UserRole)
            )
        )
        spectra_list.itemDoubleClicked.connect(
            lambda item: self._unmix_select_spectrum(
                page, item.data(Qt.ItemDataRole.UserRole), open_tree=True
            )
        )
        spectra_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        spectra_list.customContextMenuRequested.connect(
            lambda pos: self._unmix_spectrum_context_menu(page, pos)
        )
        new_tree_btn = QPushButton("New gate tree")
        new_tree_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_tree_btn.setEnabled(not page._unmix_locked)
        new_tree_btn.clicked.connect(lambda _=False: self._unmix_new_gate_tree(page))
        list_lay.addWidget(new_tree_btn)
        page._unmix_spectra_list = spectra_list
        page._unmix_new_tree_btn = new_tree_btn

        # Middle: spectrum curve (empty until a spectrum is extracted).
        plot_panel = self._build_panel_frame()
        plot_lay = QVBoxLayout(plot_panel)
        plot_lay.setContentsMargins(10, 10, 10, 10)
        plot_lay.setSpacing(6)
        plot_lay.addWidget(QLabel("<b>Spectrum</b>"))
        spectrum_plot = pg.PlotWidget()
        spectrum_plot.setBackground("w")
        spectrum_plot.showGrid(x=True, y=True, alpha=0.2)
        spectrum_plot.getPlotItem().setAxisItems({
            "bottom": _make_rotated_axis("bottom"),
            "left": _make_sci_axis("left"),
        })
        plot_lay.addWidget(spectrum_plot, 1)
        page._unmix_spectrum_plot = spectrum_plot

        row.addWidget(settings_panel, 1)
        row.addWidget(list_panel, 1)
        row.addWidget(plot_panel, 2)
        return content

    def _unmix_tree_placeholder(self) -> QWidget:
        """Shown in the gate-tree area until the first tree is created."""
        w = QWidget()
        l = QVBoxLayout(w)
        hint = QLabel('Click "New gate tree" to start drawing gates.')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888888;")
        l.addStretch()
        l.addWidget(hint)
        l.addStretch()
        return w

    def _unmix_make_tree(self, page, store: dict, spectrum_id=None) -> dict:
        """Register one gate tree; its widgets are built lazily when opened."""
        tree = {
            "id": store["id"],
            "store": store,
            "holder": None,
            "content": None,
            "spectrum_id": spectrum_id,
        }
        page._unmix_trees.append(tree)
        return tree

    def _unmix_build_tree_widgets(self, page, tree: dict) -> None:
        """Create the embedded Gating pane for a tree (once)."""
        if tree.get("holder") is not None:
            return
        holder = _GatingState()
        holder._module_page = page
        holder._gating_on_dirty = lambda: self._unmix_on_gating_dirty(page, holder)
        holder._gating_spectrum_menu = (
            lambda menu, h, idx: self._unmix_spectrum_menu(page, h, idx, menu)
        )
        self._gating_state_init(holder, tree["store"], page._unmix_locked)
        holder._gating_task_id = page._unmix_task_id
        holder._gating_save_btn = None
        spectrum = self._unmix_spectrum_for_tree(page, tree)
        holder._gating_protected_gate_id = spectrum["gate_id"] if spectrum else None
        content = self._gating_build_content(holder, page._unmix_locked)
        tree["holder"] = holder
        tree["content"] = content
        page._unmix_tree_stack.addWidget(content)

    def _unmix_activate_tree(self, page, tree: dict) -> None:
        """Show a gate tree and refresh its scatter (it may have been hidden)."""
        self._unmix_build_tree_widgets(page, tree)
        page._unmix_active_tree = tree
        page._unmix_tree_stack.setCurrentWidget(tree["content"])
        self._gating_render(tree["holder"])
        self._gating_rebuild_settings(tree["holder"])

    def _unmix_destroy_tree(self, page, tree: dict) -> None:
        """Tear down a gate tree (worker, timer and plot items)."""
        if tree.get("holder") is not None:
            self._gating_cleanup(tree["holder"])
        if tree.get("content") is not None:
            page._unmix_tree_stack.removeWidget(tree["content"])
            tree["content"].deleteLater()
        if tree in page._unmix_trees:
            page._unmix_trees.remove(tree)
        if page._unmix_active_tree is tree:
            page._unmix_active_tree = None
        if not page._unmix_trees:
            page._unmix_tree_stack.setCurrentIndex(0)

    def _unmix_new_gate_tree(self, page) -> None:
        """Start a fresh gate tree (discarding an unused scratch tree)."""
        active = page._unmix_active_tree
        if active is not None and active["spectrum_id"] is None:
            gates = active["store"].get("params", {}).get("gates", [])
            if gates:
                reply = QMessageBox.question(
                    self,
                    "New gate tree",
                    "The current gate tree is not used by any spectrum.\n"
                    "Discard it and start a new one?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self._unmix_destroy_tree(page, active)
        tree = self._unmix_make_tree(page, {"id": uuid4().hex[:8], "params": {}})
        self._unmix_activate_tree(page, tree)
        self._unmix_clear_spectrum_selection(page)
        self._unmix_mark_dirty(page)

    def _unmix_restore_trees(self, page) -> None:
        """Recreate the gate trees stored in the project file."""
        for rec in page._unmix_params.get("trees", []):
            store = {
                "id": rec.get("id") or uuid4().hex[:8],
                "params": rec.get("params") or {},
            }
            self._unmix_make_tree(page, store, rec.get("spectrum_id"))
        active_id = page._unmix_params.get("active_tree")
        tree = next((t for t in page._unmix_trees if t["id"] == active_id), None)
        if tree is None and page._unmix_trees:
            tree = page._unmix_trees[0]
        if tree is not None:
            self._unmix_activate_tree(page, tree)

    def _unmix_on_gating_dirty(self, page, holder) -> None:
        """A gate tree changed: mark the module dirty and refresh its spectrum."""
        if page._unmix_locked:
            return
        self._unmix_mark_dirty(page)
        # If the displayed spectrum comes from this tree, recompute it soon.
        spectrum = page._unmix_active_spectrum
        if spectrum is None:
            return
        tree = self._unmix_tree_for_holder(page, holder)
        if tree is not None and tree["id"] == spectrum.get("tree_id"):
            page._unmix_spectrum_timer.start(250)

    def _unmix_cleanup(self, page) -> None:
        """Stop every embedded gate tree (workers and timers)."""
        for name in ("_unmix_spectrum_timer", "_unmix_sig_order_timer"):
            timer = getattr(page, name, None)
            if timer is not None:
                timer.stop()
        for tree in list(page._unmix_trees):
            if tree.get("holder") is not None:
                self._gating_cleanup(tree["holder"])
        page._unmix_trees = []
        page._unmix_active_tree = None

    # ----- unmix: spectra ----------------------------------------------
    def _unmix_tree_by_id(self, page, tree_id):
        return next((t for t in page._unmix_trees if t["id"] == tree_id), None)

    def _unmix_tree_for_holder(self, page, holder):
        return next((t for t in page._unmix_trees if t.get("holder") is holder), None)

    def _unmix_spectrum_for_tree(self, page, tree):
        if tree is None or not tree.get("spectrum_id"):
            return None
        return next(
            (s for s in page._unmix_spectra if s["id"] == tree["spectrum_id"]),
            None,
        )

    def _unmix_spectrum_parameters(self, page) -> list:
        """Every parameter available in the module (union of imported files).

        The settings are set up before any spectrum exists, so they cannot be
        tied to one gate tree's file.
        """
        columns = []
        for rec in self._imported_files():
            for name in self._columns_for(rec.get("parquet")):
                if name not in columns:
                    columns.append(name)
        return columns

    def _unmix_selected_parameters(self, page) -> list:
        """The parameters a spectrum contains, in left-to-right plot order."""
        cfg = page._unmix_spectrum_cfg
        enabled = set(cfg.get("enabled") or [])
        return [p for p in cfg.get("params", []) if p in enabled]

    def _unmix_apply_parameter_order(self, page, new_order) -> None:
        """Apply a drag-reordered selection without moving unselected items."""
        cfg = page._unmix_spectrum_cfg
        current = list(cfg.get("params") or [])
        chosen = set(new_order)
        slots = [i for i, name in enumerate(current) if name in chosen]
        if len(slots) != len(new_order):
            return
        for slot, name in zip(slots, new_order):
            current[slot] = name
        cfg["params"] = current
        self._unmix_render_spectrum_plot(page)
        self._unmix_mark_dirty(page)

    def _unmix_sync_spectrum_params(self, page) -> list:
        """Keep the module-wide parameter order and selection in sync."""
        cfg = page._unmix_spectrum_cfg
        columns = self._unmix_spectrum_parameters(page)
        stored = [p for p in cfg.get("params", []) if p in columns]
        for col in columns:
            if col not in stored:
                stored.append(col)
        cfg["params"] = stored
        enabled = cfg.get("enabled")
        if not isinstance(enabled, list):
            # Unset: default to every parameter except the image coordinates.
            enabled = [c for c in columns if c not in ("x", "y")]
        cfg["enabled"] = [c for c in enabled if c in stored]
        return stored

    def _unmix_next_spectrum_name(self, page) -> str:
        """Spectrum N — numbering keeps increasing and never reuses gaps."""
        while True:
            n = int(page._unmix_params.get("next_spectrum_no", 1))
            page._unmix_params["next_spectrum_no"] = n + 1
            name = f"Spectrum {n}"
            if not any(s["name"] == name for s in page._unmix_spectra):
                return name

    def _unmix_rebuild_spectrum_list(self, page) -> None:
        lst = page._unmix_spectra_list
        active = page._unmix_active_spectrum
        lst.blockSignals(True)
        lst.clear()
        for spectrum in page._unmix_spectra:
            item = QListWidgetItem(spectrum["name"])
            item.setData(Qt.ItemDataRole.UserRole, spectrum["id"])
            lst.addItem(item)
        lst.blockSignals(False)
        if active is not None:
            self._unmix_highlight_spectrum(page, active["id"])

    def _unmix_highlight_spectrum(self, page, spectrum_id) -> None:
        lst = page._unmix_spectra_list
        lst.blockSignals(True)
        lst.clearSelection()
        for i in range(lst.count()):
            item = lst.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == spectrum_id:
                lst.setCurrentItem(item)
                break
        lst.blockSignals(False)

    def _unmix_select_spectrum(self, page, spectrum_id, open_tree=False) -> None:
        spectrum = next(
            (s for s in page._unmix_spectra if s["id"] == spectrum_id), None
        )
        page._unmix_active_spectrum = spectrum
        if spectrum is None:
            lst = page._unmix_spectra_list
            lst.blockSignals(True)
            lst.clearSelection()
            lst.setCurrentItem(None)
            lst.blockSignals(False)
        else:
            self._unmix_highlight_spectrum(page, spectrum["id"])
        self._unmix_render_spectrum_plot(page)
        if open_tree and spectrum is not None:
            tree = self._unmix_tree_by_id(page, spectrum.get("tree_id"))
            if tree is not None:
                self._unmix_activate_tree(page, tree)

    def _unmix_clear_spectrum_selection(self, page) -> None:
        self._unmix_select_spectrum(page, None, open_tree=False)

    def _unmix_spectrum_context_menu(self, page, pos) -> None:
        lst = page._unmix_spectra_list
        item = lst.itemAt(pos)
        if item is None:
            return
        spectrum = next(
            (s for s in page._unmix_spectra
             if s["id"] == item.data(Qt.ItemDataRole.UserRole)),
            None,
        )
        if spectrum is None:
            return
        enabled = not page._unmix_locked
        menu = QMenu(lst)
        ren_act = QAction("Rename", menu)
        ren_act.setEnabled(enabled)
        ren_act.triggered.connect(lambda: self._unmix_rename_spectrum(page, spectrum))
        menu.addAction(ren_act)
        copy_act = QAction("Duplicate", menu)
        copy_act.setEnabled(enabled)
        copy_act.triggered.connect(lambda: self._unmix_copy_spectrum(page, spectrum))
        menu.addAction(copy_act)
        menu.addSeparator()
        del_act = QAction("Delete", menu)
        del_act.setEnabled(enabled)
        del_act.triggered.connect(lambda: self._unmix_delete_spectrum(page, spectrum))
        menu.addAction(del_act)
        menu.exec(lst.viewport().mapToGlobal(pos))

    def _unmix_set_spectrum(self, page, tree, gate_id) -> None:
        """Bind a gate tree to a new spectrum (a tree can only be set once)."""
        if tree.get("spectrum_id"):
            return
        cfg = page._unmix_spectrum_cfg
        enabled = list(cfg.get("enabled") or [])
        if not enabled:
            QMessageBox.information(
                self,
                "Set as spectrum",
                "No spectrum parameters are selected.\n"
                'Choose them with "Set" in Spectrum settings first.',
            )
            return
        preview = tree["store"].get("params", {}).get("preview") or {}
        if not preview.get("file"):
            QMessageBox.warning(
                self,
                "Set as spectrum",
                "This gate tree has no data file selected.",
            )
            return
        columns = set(self._columns_for(preview.get("file")))
        missing = [p for p in enabled if p not in columns]
        if missing:
            shown = ", ".join(str(m) for m in missing[:12])
            if len(missing) > 12:
                shown += ", ..."
            QMessageBox.warning(
                self,
                "Set as spectrum",
                f"The data file of this gate tree is missing "
                f"{len(missing)} selected spectrum parameter(s):\n\n{shown}"
                "\n\nChoose a data file that contains these parameters.",
            )
            return
        spectrum = {
            "id": uuid4().hex[:8],
            "name": self._unmix_next_spectrum_name(page),
            "tree_id": tree["id"],
            "gate_id": gate_id,
        }
        page._unmix_spectra.append(spectrum)
        tree["spectrum_id"] = spectrum["id"]
        if tree.get("holder") is not None:
            tree["holder"]._gating_protected_gate_id = gate_id
        self._unmix_rebuild_spectrum_list(page)
        self._unmix_select_spectrum(page, spectrum["id"], open_tree=False)
        self._unmix_rebuild_signature_table(page)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_reset_spectrum_source(self, page, tree) -> None:
        """Move a spectrum's source to another gate of its bound tree."""
        holder = tree.get("holder")
        spectrum = self._unmix_spectrum_for_tree(page, tree)
        if holder is None or spectrum is None:
            return
        others = [
            g for g in holder._gating_gates if g["id"] != spectrum.get("gate_id")
        ]
        if not others:
            QMessageBox.information(
                self,
                "Re-set spectrum source",
                "This gate tree has no other gates.",
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Re-set spectrum source")
        dlg = QVBoxLayout(dialog)
        dlg.addWidget(QLabel("Use another gate as the source of this spectrum:"))
        combo = QComboBox()
        for gate in others:
            combo.addItem(gate.get("name", gate["id"]), gate["id"])
        dlg.addWidget(combo)
        row = QHBoxLayout()
        row.addStretch()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)
        dlg.addLayout(row)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        gate_id = combo.currentData()
        if not gate_id:
            return
        spectrum["gate_id"] = gate_id
        holder._gating_protected_gate_id = gate_id
        self._unmix_select_spectrum(page, spectrum["id"], open_tree=False)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_spectrum_menu(self, page, holder, idx, menu) -> None:
        """Add Unmix actions to a gate tree's context menu."""
        if idx is None or idx < 0 or idx >= len(holder._gating_gates):
            return
        tree = self._unmix_tree_for_holder(page, holder)
        if tree is None:
            return
        gate_id = holder._gating_gates[idx]["id"]
        spectrum = self._unmix_spectrum_for_tree(page, tree)
        if spectrum is None and not page._unmix_locked:
            act = QAction("Set as spectrum", menu)
            act.triggered.connect(
                lambda _=False: self._unmix_set_spectrum(page, tree, gate_id)
            )
            menu.addAction(act)
        elif spectrum is not None and spectrum.get("gate_id") == gate_id:
            act = QAction("Re-set as spectrum...", menu)
            act.setEnabled(not page._unmix_locked)
            act.triggered.connect(
                lambda _=False: self._unmix_reset_spectrum_source(page, tree)
            )
            menu.addAction(act)

    def _unmix_rename_spectrum(self, page, spectrum) -> None:
        text, ok = QInputDialog.getText(
            self, "Rename spectrum", "Name:", QLineEdit.EchoMode.Normal,
            spectrum.get("name", ""),
        )
        if not ok:
            return
        name = text.strip()
        if not name:
            return
        if any(s is not spectrum and s["name"] == name for s in page._unmix_spectra):
            QMessageBox.information(
                self, "Rename spectrum", "A spectrum with this name already exists."
            )
            return
        spectrum["name"] = name
        self._unmix_rebuild_spectrum_list(page)
        self._unmix_render_spectrum_plot(page)
        self._unmix_rebuild_signature_table(page)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_copy_spectrum(self, page, spectrum) -> None:
        """Duplicate a spectrum, including an independent copy of its gate tree."""
        name = spectrum["name"] + " copy"
        while any(s["name"] == name for s in page._unmix_spectra):
            name += " copy"
        new_spectrum = copy.deepcopy(spectrum)
        new_spectrum["id"] = uuid4().hex[:8]
        new_spectrum["name"] = name
        source_tree = self._unmix_tree_by_id(page, spectrum.get("tree_id"))
        if source_tree is not None:
            store = {
                "id": uuid4().hex[:8],
                "params": copy.deepcopy(source_tree["store"].get("params") or {}),
            }
            new_tree = self._unmix_make_tree(page, store, new_spectrum["id"])
            new_spectrum["tree_id"] = new_tree["id"]
        page._unmix_spectra.append(new_spectrum)
        self._unmix_rebuild_spectrum_list(page)
        self._unmix_select_spectrum(page, new_spectrum["id"], open_tree=False)
        self._unmix_rebuild_signature_table(page)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_delete_spectrum(self, page, spectrum) -> None:
        reply = QMessageBox.question(
            self,
            "Delete spectrum",
            f'Delete "{spectrum["name"]}"?\n'
            "Its bound gate tree will be destroyed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        tree = self._unmix_tree_by_id(page, spectrum.get("tree_id"))
        if tree is not None:
            self._unmix_destroy_tree(page, tree)
        if spectrum in page._unmix_spectra:
            page._unmix_spectra.remove(spectrum)
        if page._unmix_active_spectrum is spectrum:
            page._unmix_active_spectrum = None
        self._unmix_rebuild_spectrum_list(page)
        self._unmix_select_spectrum(
            page,
            page._unmix_active_spectrum["id"] if page._unmix_active_spectrum else None,
            open_tree=False,
        )
        self._unmix_rebuild_signature_table(page)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_spectrum_arrays(self, page, spectrum):
        """Return (names, arrays, error) for a spectrum's gated subset."""
        cfg = page._unmix_spectrum_cfg
        ordered = self._unmix_sync_spectrum_params(page)
        enabled = set(cfg.get("enabled") or [])
        columns = [p for p in ordered if p in enabled]
        live_trees = [
            {"id": t["id"], "params": t["store"].get("params", {})}
            for t in page._unmix_trees
        ]
        return self._unmix_spectrum_arrays_for(
            page._unmix_task_id, page._unmix_params, spectrum, columns,
            trees=live_trees,
        )

    def _unmix_spectrum_arrays_for(self, task_id, params, spectrum, columns,
                                   trees=None):
        """Return (names, arrays, error) for a spectrum from task parameters.

        Each array holds the transformed, finite values of one parameter inside
        the source gate, in `columns` order (parameters missing from the gate
        tree's file are skipped). `trees` overrides the saved tree list when
        the module is open and has unsaved edits.
        """
        import numpy as np

        if trees is None:
            trees = params.get("trees") or []
        tree = next((t for t in trees if t.get("id") == spectrum.get("tree_id")), None)
        if tree is None:
            return None, None, "The gate tree for this spectrum no longer exists."
        tree_params = tree.get("params") or {}
        gates = tree_params.get("gates") or []
        preview = tree_params.get("preview") or {}
        gate = next((g for g in gates if g.get("id") == spectrum.get("gate_id")), None)
        if gate is None:
            return None, None, "The source gate no longer exists."
        parquet = preview.get("file")
        if not parquet:
            return None, None, "The gate tree has no data file."
        # Gate columns and spectrum columns are evaluated through the same
        # ordered pipeline, so upstream Scale/Compensation modules apply.
        by_id = {g["id"]: g for g in gates}
        needed = set()
        for g in gates:
            needed |= _gate_needed_columns(g, by_id)
        gate_values = self._column_data(task_id, parquet, list(needed))
        try:
            mask = _compute_gate_filter_mask(
                gates, gate["id"], parquet, {},
                existing=set(gate_values), extra=gate_values,
            )
        except Exception as exc:  # noqa: BLE001
            return None, None, f"Could not compute the gate: {exc}"
        if mask is None:
            return None, None, "Could not compute the gate (missing parameters)."

        data = self._column_data(task_id, parquet, columns)
        names = [p for p in columns if p in data]
        if not names:
            return None, None, "No parameters selected."
        mask = np.asarray(mask, dtype=bool)
        arrays = []
        for name in names:
            arr = np.asarray(data[name], dtype="float64")
            if arr.size != mask.size:
                return None, None, "Gate mask size mismatch."
            arr = arr[mask]
            arr = arr[np.isfinite(arr)]
            arrays.append(arr)
        return names, arrays, None

    def _unmix_spectrum_values(self, page, spectrum):
        """Compute a spectrum from its source gate.

        Returns ``(names, values, hist, y_range, error)`` where `values` is the
        median/mean per parameter, `hist` is a (bins, parameters) density
        heatmap of the gated subset and `y_range` is the raw value range the
        heatmap spans.
        """
        import numpy as np

        names, arrays, error = self._unmix_spectrum_arrays(page, spectrum)
        if error:
            return None, None, None, None, error
        stat = page._unmix_spectrum_cfg.get("stat", "median")
        values = []
        for arr in arrays:
            if arr.size == 0:
                values.append(float("nan"))
            elif stat == "mean":
                values.append(float(arr.mean()))
            else:
                values.append(float(np.median(arr)))

        # Distribution heatmap: one column per parameter, shared raw value axis.
        bins = 128
        pooled = (
            np.concatenate([a for a in arrays if a.size])
            if any(a.size for a in arrays) else np.array([])
        )
        if pooled.size:
            lo, hi = np.percentile(pooled, [0.5, 99.5])
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = float(pooled.min()), float(pooled.max())
            if hi <= lo:
                hi = lo + 1.0
        else:
            lo, hi = 0.0, 1.0
        hist = np.zeros((bins, len(names)), dtype="float32")
        for i, arr in enumerate(arrays):
            if arr.size == 0:
                continue
            counts, _ = np.histogram(
                np.clip(arr, lo, hi), bins=bins, range=(lo, hi)
            )
            peak = counts.max()
            if peak > 0:
                hist[:, i] = (counts / float(peak)).astype("float32")
        return names, values, hist, (lo, hi), None

    def _unmix_render_spectrum_plot(self, page) -> None:
        import numpy as np

        plot_widget = page._unmix_spectrum_plot
        plot_item = plot_widget.getPlotItem()
        plot_item.clear()
        axis = plot_item.getAxis("bottom")
        spectrum = page._unmix_active_spectrum
        if spectrum is None:
            axis.setTicks(None)
            axis.setHeight(None)
            plot_item.setTitle("")
            return
        names, values, hist, y_range, error = self._unmix_spectrum_values(
            page, spectrum
        )
        if error:
            axis.setTicks(None)
            axis.setHeight(None)
            plot_item.setTitle(f"<span style='color:#b00000'>{error}</span>")
            return
        cfg = page._unmix_spectrum_cfg
        plot_item.setTitle(spectrum.get("name", ""))
        xs = list(range(len(names)))

        lo, hi = y_range
        finite = [v for v in values if np.isfinite(v)]
        y0 = min([lo] + finite)
        y1 = max([hi] + finite)
        if y1 <= y0:
            y1 = y0 + 1.0

        # Distribution heatmap drawn behind the spectrum line. Empty bins stay
        # transparent so the plot background shows through.
        cmap = _figure_colormap(cfg.get("colormap", "viridis"))
        rgb = np.array(cmap.getLookupTable(0.0, 1.0, 256), dtype="ubyte")
        lut = np.zeros((256, 4), dtype="ubyte")
        lut[:, :3] = rgb[:, :3]
        ramp = np.linspace(0.0, 1.0, 256) ** 0.45
        lut[:, 3] = np.clip(ramp * 255.0, 0, 255).astype("ubyte")
        lut[0, 3] = 0
        img = pg.ImageItem(axisOrder="row-major")
        img.setImage(hist, autoLevels=False, levels=(0.0, 1.0), lut=lut)
        img.setRect(QRectF(-0.5, lo, max(1, len(names)), hi - lo))
        img.setZValue(-10)
        plot_item.addItem(img)

        line_color = cfg.get("line_color", "#2b6cb0")
        plot_item.plot(
            xs, values,
            pen=pg.mkPen(line_color, width=2),
            symbol="o", symbolSize=6,
            symbolBrush=line_color, symbolPen=None,
        )
        axis.setTicks([[(i, name) for i, name in enumerate(names)]])
        # Rotated labels need as much height as the widest parameter name.
        font = axis.style.get("tickFont") or axis.font()
        metrics = QFontMetrics(font)
        widest = max((metrics.horizontalAdvance(str(n)) for n in names), default=0)
        axis.setHeight(int(widest) + 26)
        plot_item.setXRange(-0.5, max(0.5, len(names) - 0.5), padding=0)
        plot_item.setYRange(y0, y1, padding=0.02)

    def _unmix_rebuild_spectrum_settings(self, page) -> None:
        """Build the module-wide spectrum settings (one set for all spectra)."""
        layout = page._unmix_spectrum_settings_layout
        self._clear_layout(layout)
        enabled = not page._unmix_locked
        cfg = page._unmix_spectrum_cfg
        self._unmix_sync_spectrum_params(page)

        layout.addWidget(QLabel("Statistic"))
        stat_combo = QComboBox()
        stat_combo.addItem("Median", "median")
        stat_combo.addItem("Mean", "mean")
        stat_index = stat_combo.findData(cfg.get("stat", "median"))
        stat_combo.setCurrentIndex(stat_index if stat_index >= 0 else 0)
        stat_combo.setEnabled(enabled)
        layout.addWidget(stat_combo)

        layout.addWidget(QLabel("Distribution colormap"))
        cmap_combo = _NoWheelComboBox()
        for name in ("viridis", "plasma", "inferno", "magma", "cividis", "turbo"):
            cmap_combo.addItem(name, name)
        cmap_index = cmap_combo.findData(cfg.get("colormap", "viridis"))
        cmap_combo.setCurrentIndex(cmap_index if cmap_index >= 0 else 0)
        cmap_combo.setEnabled(enabled)
        layout.addWidget(cmap_combo)

        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("Parameters"))
        params_row.addStretch()
        selected_count = len(self._unmix_selected_parameters(page))
        count_label = QLabel(f"{selected_count} selected")
        count_label.setStyleSheet("color: #5b6b82;")
        params_row.addWidget(count_label)
        layout.addLayout(params_row)
        params_btn = QPushButton("Set")
        params_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        params_btn.setEnabled(enabled)
        layout.addWidget(params_btn)

        def update_params_button():
            params_btn.setToolTip(
                f"{len(cfg.get('enabled') or [])}/{len(cfg.get('params') or [])}"
            )

        update_params_button()
        page._unmix_params_btn = params_btn
        params_btn.clicked.connect(
            lambda _=False: self._unmix_edit_spectrum_parameters(page)
        )

        # The selected parameters, in plotting order; drag a row to reorder.
        selected_names = self._unmix_selected_parameters(page)
        if selected_names:
            order_list = QListWidget()
            order_list.setDragDropMode(
                QAbstractItemView.DragDropMode.InternalMove
            )
            order_list.setDefaultDropAction(Qt.DropAction.MoveAction)
            order_list.setDropIndicatorShown(True)
            order_list.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
            )
            order_list.setStyleSheet(
                "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
            )
            handle_icon = QIcon(_drag_handle_pixmap())
            for name in selected_names:
                item = QListWidgetItem(handle_icon, name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip("Drag to reorder")
                order_list.addItem(item)
            order_list.setEnabled(enabled)

            def on_rows_moved(*_args):
                order = [
                    order_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(order_list.count())
                ]
                self._unmix_apply_parameter_order(page, order)

            order_list.model().rowsMoved.connect(on_rows_moved)
            layout.addWidget(order_list, 1)
        else:
            hint = QLabel("No parameters selected. Click Set to choose.")
            hint.setStyleSheet("color: gray;")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            layout.addStretch(1)

        layout.addWidget(QLabel("Spectrum line color"))
        color_btn = QPushButton()
        color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        color_btn.setEnabled(enabled)
        color_btn.setFixedHeight(26)
        layout.addWidget(color_btn)

        layout.addWidget(QLabel("Matrix decimal places"))
        decimals_spin = _NoWheelSpinBox()
        decimals_spin.setRange(0, 6)
        decimals_spin.setValue(int(cfg.get("decimals", 2)))
        decimals_spin.setEnabled(enabled)
        layout.addWidget(decimals_spin)

        def refresh():
            self._unmix_refresh_outputs(page)
            self._unmix_mark_dirty(page)

        def update_color_button():
            color = QColor(cfg.get("line_color", "#2b6cb0"))
            text_color = "#ffffff" if color.lightness() < 128 else "#000000"
            color_btn.setText(color.name())
            color_btn.setStyleSheet(
                f"QPushButton {{ background-color: {color.name()}; "
                f"color: {text_color}; border: 1px solid #999; }}"
            )

        def pick_line_color():
            color = QColorDialog.getColor(
                QColor(cfg.get("line_color", "#2b6cb0")), self,
                "Spectrum line color",
            )
            if color.isValid():
                cfg["line_color"] = color.name()
                update_color_button()
                refresh()

        def on_stat_changed():
            cfg["stat"] = stat_combo.currentData()
            refresh()

        def on_colormap_changed():
            cfg["colormap"] = cmap_combo.currentData()
            refresh()

        def on_decimals_changed(value):
            cfg["decimals"] = int(value)
            self._unmix_render_matrix(page)
            self._unmix_mark_dirty(page)

        update_color_button()
        stat_combo.currentIndexChanged.connect(lambda _=0: on_stat_changed())
        cmap_combo.currentIndexChanged.connect(lambda _=0: on_colormap_changed())
        color_btn.clicked.connect(lambda _=False: pick_line_color())
        decimals_spin.valueChanged.connect(on_decimals_changed)

    def _pick_items_dialog(self, title, items, selected,
                           placeholder="Filter...") -> list | None:
        """Reusable multi-select dialog.

        Filter box, tri-state "Select all" over the currently visible rows,
        checkable rows with Shift-range toggling and a
        "<selected>/<available>" counter. Returns the checked labels in `items`
        order, or None when the dialog is cancelled.
        """
        selected = set(selected or [])
        result = {"value": None}

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(340, 420)
        dialog.resize(380, 560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        filter_edit = QLineEdit()
        filter_edit.setPlaceholderText(placeholder)
        layout.addWidget(filter_edit)

        select_all = QCheckBox("Select all")
        select_all.setTristate(True)
        layout.addWidget(select_all)

        item_list = QListWidget()
        item_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        item_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        for name in items:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
            )
            item_list.addItem(item)
        layout.addWidget(item_list, 1)

        count_label = QLabel("")
        layout.addWidget(count_label)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("OK")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        footer.addWidget(cancel_btn)
        footer.addWidget(ok_btn)
        layout.addLayout(footer)

        anchor = {"row": None}

        def visible_items():
            return [
                item_list.item(i)
                for i in range(item_list.count())
                if not item_list.item(i).isHidden()
            ]

        def update_counts() -> None:
            visible = visible_items()
            checked = sum(
                1 for item in visible
                if item.checkState() == Qt.CheckState.Checked
            )
            count_label.setText(f"{checked}/{len(visible)}")
            if not visible or checked == 0:
                state = Qt.CheckState.Unchecked
            elif checked == len(visible):
                state = Qt.CheckState.Checked
            else:
                state = Qt.CheckState.PartiallyChecked
            select_all.blockSignals(True)
            select_all.setCheckState(state)
            select_all.blockSignals(False)

        def set_visible_checked(checked: bool) -> None:
            target = (
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            item_list.blockSignals(True)
            for item in visible_items():
                item.setCheckState(target)
            item_list.blockSignals(False)
            update_counts()

        def on_select_all(_checked=False) -> None:
            visible = visible_items()
            if not visible:
                return
            all_checked = all(
                item.checkState() == Qt.CheckState.Checked for item in visible
            )
            set_visible_checked(not all_checked)

        def apply_filter(text: str) -> None:
            needle = text.strip().lower()
            item_list.blockSignals(True)
            for i in range(item_list.count()):
                item = item_list.item(i)
                item.setHidden(
                    bool(needle) and needle not in item.text().lower()
                )
            item_list.blockSignals(False)
            update_counts()

        def on_item_clicked(item) -> None:
            row = item_list.row(item)
            modifiers = QApplication.keyboardModifiers()
            if (
                modifiers & Qt.KeyboardModifier.ShiftModifier
                and anchor["row"] is not None
            ):
                lo, hi = sorted((anchor["row"], row))
                state = item.checkState()
                item_list.blockSignals(True)
                for r in range(lo, hi + 1):
                    other = item_list.item(r)
                    if not other.isHidden():
                        other.setCheckState(state)
                item_list.blockSignals(False)
            anchor["row"] = row
            update_counts()

        def on_accept() -> None:
            result["value"] = [
                item_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(item_list.count())
                if item_list.item(i).checkState() == Qt.CheckState.Checked
            ]
            dialog.accept()

        filter_edit.textChanged.connect(apply_filter)
        select_all.clicked.connect(on_select_all)
        item_list.itemClicked.connect(on_item_clicked)
        item_list.itemChanged.connect(lambda _item: update_counts())
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn.clicked.connect(on_accept)

        update_counts()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return result["value"]

    def _unmix_edit_spectrum_parameters(self, page) -> None:
        """Pick which parameters a spectrum contains (shared picker dialog)."""
        cfg = page._unmix_spectrum_cfg
        ordered = self._unmix_sync_spectrum_params(page)
        chosen = self._pick_items_dialog(
            "Spectrum parameters", ordered, set(cfg.get("enabled") or []),
            "Filter parameters...",
        )
        if chosen is None:
            return
        chosen_set = set(chosen)
        cfg["enabled"] = [p for p in cfg.get("params", []) if p in chosen_set]
        self._unmix_rebuild_spectrum_settings(page)
        self._unmix_render_spectrum_plot(page)
        self._unmix_mark_dirty(page)

    def _unmix_matrix_subpage(self, page) -> QWidget:
        """Sub-page 2: signatures + samples (left) | matrix image + settings (right)."""
        w = QWidget()
        w.setStyleSheet("background: #f5f8fb;")
        row = QHBoxLayout(w)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)

        left = QSplitter(Qt.Orientation.Vertical)
        left.setChildrenCollapsible(False)
        left.addWidget(self._unmix_signature_panel(page))
        left.addWidget(self._unmix_sample_panel(page))
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)
        left.setSizes([420, 240])

        right = QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(self._unmix_matrix_panel(page))
        right.addWidget(self._unmix_matrix_settings_panel(page))
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([560, 220])

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([400, 800])
        row.addWidget(splitter, 1)
        return w

    def _unmix_matrix_panel(self, page) -> QWidget:
        """The matrix heatmap itself (rendered to an image)."""
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Spectral matrix</b>"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setStyleSheet("background: #ffffff; border: none;")
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("background: #ffffff; color: #888888;")
        label.setText("Add a signature to build the matrix.")
        scroll.setWidget(label)
        lay.addWidget(scroll, 1)
        page._unmix_matrix_label = label
        page._unmix_matrix_image = None
        return panel

    def _unmix_matrix_settings_panel(self, page) -> QWidget:
        """Cell geometry, colours, number display and clipboard export."""
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Matrix settings</b>"))

        cfg = self._unmix_matrix_cfg(page)
        editable = not page._unmix_locked
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("Cell width"), 0, 0)
        width_spin = _NoWheelSpinBox()
        width_spin.setRange(12, 400)
        width_spin.setValue(int(cfg["cell_width"]))
        width_spin.setEnabled(editable)
        grid.addWidget(width_spin, 0, 1)

        grid.addWidget(QLabel("Cell height"), 0, 2)
        height_spin = _NoWheelSpinBox()
        height_spin.setRange(12, 200)
        height_spin.setValue(int(cfg["cell_height"]))
        height_spin.setEnabled(editable)
        grid.addWidget(height_spin, 0, 3)

        grid.addWidget(QLabel("Colormap"), 1, 0)
        cmap_combo = _NoWheelComboBox()
        for name in ("viridis", "plasma", "inferno", "magma", "cividis", "turbo"):
            cmap_combo.addItem(name, name)
        cmap_index = cmap_combo.findData(cfg["colormap"])
        cmap_combo.setCurrentIndex(cmap_index if cmap_index >= 0 else 0)
        cmap_combo.setEnabled(editable)
        grid.addWidget(cmap_combo, 1, 1, 1, 3)

        show_numbers = QCheckBox("Show numbers")
        show_numbers.setChecked(bool(cfg["show_numbers"]))
        show_numbers.setEnabled(editable)
        grid.addWidget(show_numbers, 2, 0, 1, 2)

        auto_color = QCheckBox("Auto number color")
        auto_color.setChecked(bool(cfg["auto_text_color"]))
        auto_color.setEnabled(editable)
        grid.addWidget(auto_color, 2, 2, 1, 2)

        grid.addWidget(QLabel("Number color"), 3, 0)
        color_btn = QPushButton()
        color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        color_btn.setFixedHeight(26)
        color_btn.setEnabled(editable and not auto_color.isChecked())
        grid.addWidget(color_btn, 3, 1)

        copy_btn = QPushButton("Copy matrix to clipboard")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        grid.addWidget(copy_btn, 3, 2, 1, 2)
        lay.addLayout(grid)
        lay.addStretch(1)

        def update_color_button():
            color = QColor(cfg["text_color"])
            text_color = "#ffffff" if color.lightness() < 128 else "#000000"
            color_btn.setText(color.name())
            color_btn.setStyleSheet(
                f"QPushButton {{ background-color: {color.name()}; "
                f"color: {text_color}; border: 1px solid #999; }}"
            )

        def refresh():
            self._unmix_render_matrix(page)
            self._unmix_mark_dirty(page)

        def on_width(value):
            cfg["cell_width"] = int(value)
            refresh()

        def on_height(value):
            cfg["cell_height"] = int(value)
            refresh()

        def on_colormap():
            cfg["colormap"] = cmap_combo.currentData()
            refresh()

        def on_show_numbers(checked):
            cfg["show_numbers"] = bool(checked)
            refresh()

        def on_auto_color(checked):
            cfg["auto_text_color"] = bool(checked)
            color_btn.setEnabled(editable and not checked)
            refresh()

        def pick_color():
            color = QColorDialog.getColor(
                QColor(cfg["text_color"]), self, "Matrix number color"
            )
            if color.isValid():
                cfg["text_color"] = color.name()
                update_color_button()
                refresh()

        width_spin.valueChanged.connect(on_width)
        height_spin.valueChanged.connect(on_height)
        cmap_combo.currentIndexChanged.connect(lambda _=0: on_colormap())
        show_numbers.toggled.connect(on_show_numbers)
        auto_color.toggled.connect(on_auto_color)
        color_btn.clicked.connect(lambda _=False: pick_color())
        copy_btn.clicked.connect(lambda _=False: self._unmix_copy_matrix(page))

        update_color_button()
        return panel

    def _unmix_matrix_cfg(self, page) -> dict:
        cfg = page._unmix_params.setdefault("matrix_settings", {})
        cfg.setdefault("cell_width", 35)
        cfg.setdefault("cell_height", 28)
        cfg.setdefault("colormap", "viridis")
        cfg.setdefault("show_numbers", True)
        cfg.setdefault("auto_text_color", True)
        cfg.setdefault("text_color", "#5b6b82")
        return cfg

    def _unmix_copy_matrix(self, page) -> None:
        """Copy the matrix as tab-separated text (pasteable into Excel)."""
        import numpy as np

        columns, rows = self._unmix_matrix_table_values(page)
        if not rows or not columns:
            QMessageBox.information(
                self, "Copy matrix", "There is no matrix to copy."
            )
            return
        lines = ["\t".join(["Signature"] + list(columns))]
        for row, values in rows:
            cells = [str(row.get("name", ""))]
            cells += [
                f"{v:.4f}" if np.isfinite(v) else "" for v in values
            ]
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage("Matrix copied to clipboard")

    def _unmix_signature_panel(self, page) -> QWidget:
        """Positive/negative/normalize/name rows that define the matrix."""
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Signatures</b>"))

        add_btn = QPushButton("Add signature")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setEnabled(not page._unmix_locked)
        add_btn.clicked.connect(lambda _=False: self._unmix_add_signature(page))
        add_row = QHBoxLayout()
        add_row.addWidget(add_btn)
        add_row.addStretch()
        lay.addLayout(add_row)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(
            ["Positive signature", "Negative signature", "Normalize", "Name"]
        )
        table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: none; }"
        )
        table.verticalHeader().setDefaultSectionSize(30)
        table.verticalHeader().setSectionsMovable(True)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos: self._unmix_signature_menu(page, pos)
        )
        header = table.verticalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos: self._unmix_signature_header_menu(page, pos)
        )
        header.sectionMoved.connect(
            lambda *_: page._unmix_sig_order_timer.start()
        )
        page._unmix_signature_table = table
        lay.addWidget(table, 1)
        return panel

    def _unmix_sample_panel(self, page) -> QWidget:
        """Which imported samples this matrix is applied to."""
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Samples</b>"))
        head.addStretch()
        all_btn = QPushButton("All")
        none_btn = QPushButton("None")
        for btn in (all_btn, none_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(56)
            btn.setEnabled(not page._unmix_locked)
        all_btn.clicked.connect(lambda _=False: self._unmix_set_all_samples(page, True))
        none_btn.clicked.connect(
            lambda _=False: self._unmix_set_all_samples(page, False)
        )
        head.addWidget(all_btn)
        head.addWidget(none_btn)
        lay.addLayout(head)

        samples_list = QListWidget()
        samples_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: none; }"
        )
        samples_list.setEnabled(not page._unmix_locked)
        samples_list.itemChanged.connect(
            lambda _item: self._unmix_on_samples_changed(page)
        )
        page._unmix_samples_list = samples_list
        lay.addWidget(samples_list, 1)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Unmixing method"))
        method_combo = _NoWheelComboBox()
        method_combo.addItem("OLS (ordinary least squares)", "ols")
        method_index = method_combo.findData(
            page._unmix_params.get("method", "ols")
        )
        method_combo.setCurrentIndex(method_index if method_index >= 0 else 0)
        method_combo.setEnabled(not page._unmix_locked)
        method_combo.currentIndexChanged.connect(
            lambda _=0: self._unmix_on_method_changed(page, method_combo)
        )
        method_row.addWidget(method_combo, 1)
        lay.addLayout(method_row)
        return panel

    # ----- unmix: signature table --------------------------------------
    def _unmix_rebuild_signature_table(self, page) -> None:
        """(Re)build the four-column signature editor from the data model."""
        table = page._unmix_signature_table
        spectra = page._unmix_spectra
        rows = page._unmix_matrix
        editable = not page._unmix_locked
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            pos_combo = _NoWheelComboBox()
            for spectrum in spectra:
                pos_combo.addItem(spectrum["name"], spectrum["id"])
            index = pos_combo.findData(row.get("positive"))
            pos_combo.setCurrentIndex(index if index >= 0 else 0)
            pos_combo.setEnabled(editable)

            neg_combo = _NoWheelComboBox()
            neg_combo.addItem("NA", None)
            for spectrum in spectra:
                neg_combo.addItem(spectrum["name"], spectrum["id"])
            index = neg_combo.findData(row.get("negative"))
            neg_combo.setCurrentIndex(index if index >= 0 else 0)
            neg_combo.setEnabled(editable)

            norm_box = QCheckBox()
            norm_box.setChecked(bool(row.get("normalize", True)))
            norm_box.setEnabled(editable)
            norm_host = QWidget()
            norm_lay = QHBoxLayout(norm_host)
            norm_lay.setContentsMargins(0, 0, 0, 0)
            norm_lay.addStretch()
            norm_lay.addWidget(norm_box)
            norm_lay.addStretch()

            name_edit = QLineEdit(row.get("name", ""))
            name_edit.setEnabled(editable)

            pos_combo.currentIndexChanged.connect(
                lambda _=0, r=row, c=pos_combo: self._unmix_on_signature_field(
                    page, r, "positive", c.currentData()
                )
            )
            neg_combo.currentIndexChanged.connect(
                lambda _=0, r=row, c=neg_combo: self._unmix_on_signature_field(
                    page, r, "negative", c.currentData()
                )
            )
            norm_box.toggled.connect(
                lambda checked, r=row: self._unmix_on_signature_field(
                    page, r, "normalize", bool(checked)
                )
            )
            name_edit.editingFinished.connect(
                lambda r=row, e=name_edit: self._unmix_set_signature_name(
                    page, r, e
                )
            )
            # Cell widgets swallow the table's context menu, so each one gets
            # its own "Remove signature" entry.
            for widget in (pos_combo, neg_combo, norm_box, norm_host, name_edit):
                widget.setContextMenuPolicy(
                    Qt.ContextMenuPolicy.CustomContextMenu
                )
                widget.customContextMenuRequested.connect(
                    lambda pos, w=widget, r=row: self._unmix_signature_widget_menu(
                        page, r, w, pos
                    )
                )

            table.setCellWidget(i, 0, pos_combo)
            table.setCellWidget(i, 1, neg_combo)
            table.setCellWidget(i, 2, norm_host)
            table.setCellWidget(i, 3, name_edit)
        table.setVerticalHeaderLabels([r.get("name", "") for r in rows])
        table.resizeRowsToContents()

    def _unmix_signature_reordered(self, page) -> None:
        """Apply the visual row order (from dragging the header) to the model."""
        table = page._unmix_signature_table
        header = table.verticalHeader()
        count = len(page._unmix_matrix)
        order = sorted(range(count), key=lambda i: header.visualIndex(i))
        if order == list(range(count)):
            return
        page._unmix_matrix[:] = [page._unmix_matrix[i] for i in order]
        self._unmix_rebuild_signature_table(page)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_signature_menu(self, page, pos) -> None:
        table = page._unmix_signature_table
        index = table.rowAt(pos.y())
        if index < 0 or index >= len(page._unmix_matrix):
            return
        menu = QMenu(table)
        act = QAction("Remove signature", menu)
        act.setEnabled(not page._unmix_locked)
        act.triggered.connect(
            lambda _=False: self._unmix_remove_signature(page, index)
        )
        menu.addAction(act)
        menu.exec(table.viewport().mapToGlobal(pos))

    def _unmix_signature_header_menu(self, page, pos) -> None:
        """Right-click on a row header: remove that signature."""
        header = page._unmix_signature_table.verticalHeader()
        index = header.logicalIndexAt(pos)
        if index < 0 or index >= len(page._unmix_matrix):
            return
        menu = QMenu(header)
        act = QAction("Remove signature", menu)
        act.setEnabled(not page._unmix_locked)
        act.triggered.connect(
            lambda _=False: self._unmix_remove_signature(page, index)
        )
        menu.addAction(act)
        menu.exec(header.mapToGlobal(pos))

    def _unmix_signature_widget_menu(self, page, row, widget, pos) -> None:
        """Right-click inside a signature row's cell widgets."""
        index = next(
            (
                i
                for i, other in enumerate(page._unmix_matrix)
                if other is row
            ),
            -1,
        )
        if index < 0:
            return
        if isinstance(widget, QLineEdit):
            menu = widget.createStandardContextMenu()
            menu.addSeparator()
        else:
            menu = QMenu(widget)
        act = QAction("Remove signature", menu)
        act.setEnabled(not page._unmix_locked)
        act.triggered.connect(
            lambda _=False: self._unmix_remove_signature(page, index)
        )
        menu.addAction(act)
        menu.exec(widget.mapToGlobal(pos))
        menu.deleteLater()

    def _unmix_add_signature(self, page) -> None:
        if not page._unmix_spectra:
            QMessageBox.information(
                self,
                "Add signature",
                "Create a spectrum first on the Spectrum Extraction tab.",
            )
            return
        spectrum = page._unmix_spectra[0]
        row = {
            "id": uuid4().hex[:8],
            "positive": spectrum["id"],
            "negative": None,
            "normalize": True,
            "name": self._unmix_unique_signature_name(page, spectrum["name"]),
        }
        page._unmix_matrix.append(row)
        self._unmix_rebuild_signature_table(page)
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_remove_signature(self, page, index: int) -> None:
        if 0 <= index < len(page._unmix_matrix):
            del page._unmix_matrix[index]
            self._unmix_rebuild_signature_table(page)
            self._unmix_render_matrix(page)
            self._unmix_mark_dirty(page)

    def _unmix_unique_signature_name(self, page, base: str) -> str:
        """A name that avoids other signatures and every data parameter name."""
        taken = {r.get("name") for r in page._unmix_matrix}
        taken |= set(self._unmix_spectrum_parameters(page))
        if base not in taken:
            return base
        n = 2
        while f"{base} {n}" in taken:
            n += 1
        return f"{base} {n}"

    def _unmix_on_signature_field(self, page, row, key: str, value) -> None:
        if row.get(key) == value:
            return
        row[key] = value
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    def _unmix_set_signature_name(self, page, row, edit) -> None:
        name = edit.text().strip()
        current = row.get("name", "")
        if name == current:
            return
        if not name:
            QMessageBox.information(
                self, "Signature name", "The name cannot be empty."
            )
            edit.setText(current)
            return
        if any(
            other is not row and other.get("name") == name
            for other in page._unmix_matrix
        ):
            QMessageBox.information(
                self,
                "Signature name",
                "Another signature already uses this name.",
            )
            edit.setText(current)
            return
        if name in set(self._unmix_spectrum_parameters(page)):
            QMessageBox.information(
                self,
                "Signature name",
                "The name cannot match a parameter name of the sample data.",
            )
            edit.setText(current)
            return
        row["name"] = name
        try:
            index = page._unmix_matrix.index(row)
            page._unmix_signature_table.setVerticalHeaderItem(
                index, QTableWidgetItem(name)
            )
        except ValueError:
            pass
        self._unmix_render_matrix(page)
        self._unmix_mark_dirty(page)

    # ----- unmix: samples ----------------------------------------------
    def _unmix_rebuild_sample_list(self, page) -> None:
        samples_list = page._unmix_samples_list
        selected = set(page._unmix_params.get("samples") or [])
        samples_list.blockSignals(True)
        samples_list.clear()
        for rec in self._imported_files():
            path = rec.get("parquet")
            item = QListWidgetItem(rec.get("name", ""))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if path in selected
                else Qt.CheckState.Unchecked
            )
            samples_list.addItem(item)
        samples_list.blockSignals(False)

    def _unmix_on_samples_changed(self, page) -> None:
        samples_list = page._unmix_samples_list
        page._unmix_params["samples"] = [
            samples_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(samples_list.count())
            if samples_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        self._unmix_mark_dirty(page)

    def _unmix_set_all_samples(self, page, checked: bool) -> None:
        samples_list = page._unmix_samples_list
        samples_list.blockSignals(True)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(samples_list.count()):
            samples_list.item(i).setCheckState(state)
        samples_list.blockSignals(False)
        self._unmix_on_samples_changed(page)

    def _unmix_on_method_changed(self, page, combo) -> None:
        page._unmix_params["method"] = combo.currentData()
        self._unmix_mark_dirty(page)

    # ----- unmix: matrix heatmap ---------------------------------------
    def _unmix_matrix_table_values(self, page):
        """Return (columns, [(row, values)]) for the signature matrix."""
        import numpy as np

        columns = self._unmix_selected_parameters(page)
        by_id = {s["id"]: s for s in page._unmix_spectra}
        result = []
        for row in page._unmix_matrix:
            positive = by_id.get(row.get("positive"))
            negative = by_id.get(row.get("negative"))
            if positive is None:
                result.append((row, [float("nan")] * len(columns)))
                continue
            pos_values, _err = self._unmix_spectrum_vector(page, positive)
            if pos_values is None:
                result.append((row, [float("nan")] * len(columns)))
                continue
            neg_values = {}
            if negative is not None:
                neg_values = self._unmix_spectrum_vector(page, negative)[0] or {}
            values = []
            for column in columns:
                p = pos_values.get(column, float("nan"))
                n = neg_values.get(column, 0.0) if negative is not None else 0.0
                values.append(p - n if np.isfinite(p) else float("nan"))
            if row.get("normalize", True):
                finite = [v for v in values if np.isfinite(v)]
                peak = max(finite) if finite else None
                if peak is not None and peak > 0:
                    values = [
                        (v / peak) if np.isfinite(v) else v for v in values
                    ]
            result.append((row, values))
        return columns, result

    def _unmix_render_matrix(self, page) -> None:
        """Paint the matrix as an image with rotated column names."""
        import numpy as np

        label = page._unmix_matrix_label
        columns, rows = self._unmix_matrix_table_values(page)
        if not rows or not columns:
            page._unmix_matrix_image = None
            label.clear()
            label.setText(
                "Add a signature to build the matrix."
                if not rows
                else "No parameters selected."
            )
            return

        cfg = self._unmix_matrix_cfg(page)
        cell_w = max(12, int(cfg["cell_width"]))
        cell_h = max(12, int(cfg["cell_height"]))
        font = QFont(label.font())
        font.setPixelSize(max(9, min(13, cell_h // 2 + 2)))
        metrics = QFontMetrics(font)

        row_names = [row.get("name", "") for row, _ in rows]
        left = max((metrics.horizontalAdvance(n) for n in row_names), default=0)
        bottom = max((metrics.horizontalAdvance(c) for c in columns), default=0)
        left += 12
        bottom += 12
        top = 8
        right = 8
        width = left + cell_w * len(columns) + right
        height = top + cell_h * len(rows) + bottom

        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)

        finite = [v for _r, values in rows for v in values if np.isfinite(v)]
        lo = min(finite) if finite else 0.0
        hi = max(finite) if finite else 0.0
        lut = _figure_colormap(cfg["colormap"]).getLookupTable(0.0, 1.0, 256)
        show_numbers = bool(cfg["show_numbers"])
        decimals = int(page._unmix_spectrum_cfg.get("decimals", 2))
        auto_color = bool(cfg["auto_text_color"])
        fixed_text = QColor(cfg["text_color"])
        grid_pen = QPen(QColor(255, 255, 255, 140))
        grid_pen.setWidth(1)

        for i, (_row, values) in enumerate(rows):
            y = top + i * cell_h
            for j, value in enumerate(values):
                x = left + j * cell_w
                if np.isfinite(value):
                    frac = 0.5 if hi <= lo else (value - lo) / (hi - lo)
                    frac = min(1.0, max(0.0, frac))
                    r, g, b = (int(c) for c in lut[int(frac * 255)])
                    painter.fillRect(x, y, cell_w, cell_h, QColor(r, g, b))
                    luma = 0.299 * r + 0.587 * g + 0.114 * b
                    text_color = (
                        QColor("#ffffff") if luma < 140 else QColor("#111111")
                    ) if auto_color else fixed_text
                else:
                    painter.fillRect(x, y, cell_w, cell_h, QColor("#f2f4f7"))
                    text_color = QColor("#9aa4b2")
                painter.setPen(grid_pen)
                painter.drawRect(x, y, cell_w, cell_h)
                if show_numbers:
                    painter.setPen(text_color)
                    painter.drawText(
                        QRect(x, y, cell_w, cell_h),
                        Qt.AlignmentFlag.AlignCenter,
                        f"{value:.{decimals}f}" if np.isfinite(value) else "—",
                    )
            painter.setPen(QColor("#333333"))
            painter.drawText(
                QRect(0, y, left - 10, cell_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                row_names[i],
            )

        # Column names rotated -90°, ending just below the grid.
        grid_bottom = top + cell_h * len(rows)
        shift = (metrics.ascent() - metrics.descent()) / 2.0
        for j, name in enumerate(columns):
            text_width = metrics.horizontalAdvance(name)
            cx = left + j * cell_w + cell_w / 2.0
            painter.save()
            painter.translate(cx + shift, grid_bottom + 6 + text_width)
            painter.rotate(-90)
            painter.setPen(QColor("#333333"))
            painter.drawText(0, 0, name)
            painter.restore()
        painter.end()

        page._unmix_matrix_image = image
        label.setText("")
        label.setPixmap(QPixmap.fromImage(image))
        label.resize(image.size())

    def _unmix_refresh_outputs(self, page) -> None:
        """Recompute everything derived from the spectra."""
        self._unmix_render_spectrum_plot(page)
        self._unmix_render_matrix(page)

    def _unmix_spectrum_vector(self, page, spectrum):
        """Return ({parameter: statistic}, error) for one spectrum, cached."""
        import numpy as np

        cache = getattr(page, "_unmix_spectrum_cache", None)
        if cache is None:
            cache = {}
            page._unmix_spectrum_cache = cache
        cached = cache.get(spectrum["id"])
        if cached is not None:
            return cached, None
        names, arrays, error = self._unmix_spectrum_arrays(page, spectrum)
        if error:
            return None, error
        stat = page._unmix_spectrum_cfg.get("stat", "median")
        values = {}
        for name, arr in zip(names, arrays):
            if arr.size == 0:
                values[name] = float("nan")
            elif stat == "mean":
                values[name] = float(arr.mean())
            else:
                values[name] = float(np.median(arr))
        cache[spectrum["id"]] = values
        return values, None

    def _unmix_mark_dirty(self, page) -> None:
        page._unmix_dirty = True
        page._unmix_spectrum_cache = {}
        self._unmix_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _unmix_save_now(self, page) -> None:
        page._unmix_params["trees"] = [
            {
                "id": tree["id"],
                "params": tree["store"].get("params", {}),
                "spectrum_id": tree["spectrum_id"],
            }
            for tree in page._unmix_trees
        ]
        active = page._unmix_active_tree
        page._unmix_params["active_tree"] = active["id"] if active else None
        active_spectrum = page._unmix_active_spectrum
        page._unmix_params["active_spectrum"] = (
            active_spectrum["id"] if active_spectrum else None
        )
        page._unmix_task_def["params"] = page._unmix_params
        if self.project is not None:
            self.project.save()
        page._unmix_snapshot = copy.deepcopy(page._unmix_params)
        page._unmix_dirty = False
        self._unmix_update_save_btn(page)
        self.statusBar().showMessage("Unmix saved")
        self._module_saved(page)

    def _unmix_update_save_btn(self, page) -> None:
        page._unmix_save_btn.setEnabled(page._unmix_dirty and not page._unmix_locked)

    def _unmix_close(self, page) -> None:
        """Close the page, prompting first if there are unsaved changes."""
        if page._unmix_dirty:
            reply = QMessageBox.question(
                self,
                "Close Unmix",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._unmix_task_def["params"] = page._unmix_snapshot
        self._unmix_cleanup(page)
        self._close_module_page(page)

    # ----- compensation module (map) -----------------------------------
    def _build_compensation_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
        """Build the Compensation page: files (1) | matrix (5)."""
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
        close_btn.clicked.connect(lambda _=False: self._comp_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        params = task_def.setdefault("params", {})
        params.setdefault("files", [])
        params.setdefault("params", [])
        params.setdefault("matrix", [])
        page._comp_task_def = task_def
        page._comp_task_id = task_def.get("id")
        page._comp_params = params
        page._comp_files = params["files"]
        page._comp_names = params["params"]
        page._comp_matrix = params["matrix"]
        page._comp_locked = locked
        page._comp_save_btn = save_btn
        page._comp_dirty = False
        page._comp_snapshot = copy.deepcopy(params)
        page._comp_order_timer = QTimer(self)
        page._comp_order_timer.setSingleShot(True)
        page._comp_order_timer.setInterval(400)
        page._comp_order_timer.timeout.connect(
            lambda: self._comp_reordered(page)
        )
        save_btn.clicked.connect(lambda _=False: self._comp_save_now(page))

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        row = QHBoxLayout(content)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._comp_files_panel(page))
        splitter.addWidget(self._comp_matrix_panel(page))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([220, 1100])
        row.addWidget(splitter, 1)
        layout.addWidget(content, 1)

        self._comp_refresh(page)
        self._comp_update_save_btn(page)
        return page

    def _comp_files_panel(self, page) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        set_btn = QPushButton("Set Files")
        set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_btn.setEnabled(not page._comp_locked and bool(page._comp_names))
        set_btn.clicked.connect(lambda _=False: self._comp_set_files(page))
        lay.addWidget(set_btn)
        files_list = QListWidget()
        files_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: none; }"
        )
        lay.addWidget(files_list, 1)
        page._comp_files_list = files_list
        page._comp_setfiles_btn = set_btn
        return panel

    def _comp_matrix_panel(self, page) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        top = QHBoxLayout()
        set_btn = QPushButton("Set Compensation Matrix")
        set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_btn.setEnabled(not page._comp_locked)
        set_btn.clicked.connect(lambda _=False: self._comp_set_params(page))
        top.addWidget(set_btn)
        top.addStretch()
        copy_btn = QPushButton("Copy Matrix")
        paste_btn = QPushButton("Paste Matrix")
        for btn in (copy_btn, paste_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(lambda _=False: self._comp_copy_matrix(page))
        paste_btn.clicked.connect(lambda _=False: self._comp_paste_matrix(page))
        top.addWidget(copy_btn)
        top.addWidget(paste_btn)
        lay.addLayout(top)

        table = QTableWidget()
        table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: none; }"
            "QTableWidget::item:selected { background: #dbeafa; }"
        )
        table.verticalHeader().setDefaultSectionSize(26)
        table.verticalHeader().setSectionsMovable(True)
        table.verticalHeader().sectionMoved.connect(
            lambda *_: page._comp_order_timer.start()
        )
        # Apply the new order as soon as the drag is released (the timer above
        # is only a fallback), so a mid-drag rebuild cannot misplace rows.
        release_filter = _ReleaseFilter(lambda: self._comp_reordered(page))
        table.verticalHeader().viewport().installEventFilter(release_filter)
        page._comp_release_filter = release_filter
        table.horizontalHeader().setDefaultSectionSize(66)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        table.itemChanged.connect(lambda item: self._comp_item_changed(page, item))
        lay.addWidget(table, 1)

        hint = QLabel(
            "No parameters selected.\n"
            'Click "Set Compensation Matrix" to choose the parameters.'
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888888;")
        hint.setWordWrap(True)
        lay.addWidget(hint, 1)

        page._comp_table = table
        page._comp_hint = hint
        page._comp_setparams_btn = set_btn
        page._comp_copy_btn = copy_btn
        page._comp_paste_btn = paste_btn
        return panel

    # --- compensation helpers
    def _comp_file_name(self, path) -> str:
        for rec in self._imported_files():
            if rec.get("parquet") == path:
                return rec.get("name", "")
        return Path(str(path)).name

    def _comp_columns_for(self, page, path) -> list:
        return self._columns_for(path, page._comp_task_id)

    def _comp_all_columns(self, page) -> list:
        columns = []
        for rec in self._imported_files():
            for name in self._comp_columns_for(page, rec.get("parquet")):
                if name not in columns:
                    columns.append(name)
        return columns

    def _comp_refresh(self, page) -> None:
        files_list = page._comp_files_list
        files_list.blockSignals(True)
        files_list.clear()
        for path in page._comp_files:
            item = QListWidgetItem(self._comp_file_name(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            files_list.addItem(item)
        files_list.blockSignals(False)
        editable = not page._comp_locked
        page._comp_setfiles_btn.setEnabled(editable and bool(page._comp_names))
        page._comp_setfiles_btn.setToolTip(
            "" if page._comp_names
            else "Set the compensation matrix parameters first"
        )
        page._comp_copy_btn.setEnabled(bool(page._comp_names))
        page._comp_paste_btn.setEnabled(editable and bool(page._comp_names))
        self._comp_rebuild_table(page)

    def _comp_rebuild_table(self, page) -> None:
        table = page._comp_table
        names = page._comp_names
        matrix = page._comp_matrix
        table.blockSignals(True)
        table.clear()
        # Resetting the count clears the header's visual section order too;
        # otherwise the moved rows stay offset after a reorder.
        table.setRowCount(0)
        table.setColumnCount(0)
        table.setRowCount(len(names))
        table.setColumnCount(len(names))
        table.setHorizontalHeaderLabels(names)
        grip = QIcon(_drag_handle_pixmap())
        for i, name in enumerate(names):
            header_item = QTableWidgetItem(name)
            header_item.setIcon(grip)
            header_item.setToolTip("Drag to reorder rows (columns follow)")
            table.setVerticalHeaderItem(i, header_item)
        for i in range(len(names)):
            for j in range(len(names)):
                if i == j:
                    item = QTableWidgetItem("100")
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    item.setBackground(QColor("#e4e7ec"))
                    item.setForeground(QColor("#5b6b82"))
                else:
                    value = (
                        matrix[i][j]
                        if i < len(matrix) and j < len(matrix[i]) else 0
                    )
                    item = QTableWidgetItem(str(int(value)))
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsEditable
                    )
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if page._comp_locked
            else (
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
                | QAbstractItemView.EditTrigger.AnyKeyPressed
            )
        )
        self._comp_refresh_colors(page)
        has_names = bool(names)
        table.setVisible(has_names)
        page._comp_hint.setVisible(not has_names)
        table.resizeColumnsToContents()
        for j in range(table.columnCount()):
            if table.columnWidth(j) < 56:
                table.setColumnWidth(j, 56)

    def _comp_refresh_colors(self, page) -> None:
        table = page._comp_table
        matrix = page._comp_matrix
        positives = [
            v for i, row in enumerate(matrix)
            for j, v in enumerate(row) if i != j and v > 0
        ]
        negatives = [
            v for i, row in enumerate(matrix)
            for j, v in enumerate(row) if i != j and v < 0
        ]
        max_pos = max(positives) if positives else 0
        min_neg = min(negatives) if negatives else 0
        table.blockSignals(True)
        for i in range(min(table.rowCount(), len(matrix))):
            for j in range(min(table.columnCount(), len(matrix[i]))):
                if i == j:
                    continue
                item = table.item(i, j)
                if item is None:
                    continue
                item.setBackground(_comp_cell_color(matrix[i][j], max_pos, min_neg))
        table.blockSignals(False)

    def _comp_item_changed(self, page, item) -> None:
        table = page._comp_table
        i, j = table.row(item), table.column(item)
        if page._comp_locked or i < 0 or j < 0 or i == j:
            return
        if i >= len(page._comp_matrix) or j >= len(page._comp_matrix[i]):
            return
        text = item.text().strip()
        try:
            value = int(float(text))
        except (TypeError, ValueError):
            table.blockSignals(True)
            item.setText(str(int(page._comp_matrix[i][j])))
            table.blockSignals(False)
            return
        if value == page._comp_matrix[i][j]:
            return
        page._comp_matrix[i][j] = value
        self._comp_refresh_colors(page)
        self._comp_mark_dirty(page)

    def _comp_set_files(self, page) -> None:
        if not page._comp_names:
            QMessageBox.information(
                self,
                "Set Files",
                "Set the compensation matrix parameters first.",
            )
            return
        records = self._imported_files()
        names = [rec.get("name", "") for rec in records]
        current = [self._comp_file_name(path) for path in page._comp_files]
        chosen = self._pick_items_dialog(
            "Select files", names, current, "Filter files..."
        )
        if chosen is None:
            return
        chosen_set = set(chosen)
        paths = [
            rec.get("parquet") for rec in records
            if rec.get("name", "") in chosen_set
        ]
        problems = []
        for path in paths:
            columns = set(self._comp_columns_for(page, path))
            missing = [name for name in page._comp_names if name not in columns]
            if missing:
                problems.append((self._comp_file_name(path), missing))
        if problems:
            self._comp_show_missing(problems)
            return
        page._comp_files[:] = paths
        self._comp_refresh(page)
        self._comp_mark_dirty(page)

    def _comp_set_params(self, page) -> None:
        all_columns = self._comp_all_columns(page)
        current = list(page._comp_names)
        chosen = self._pick_items_dialog(
            "Compensation parameters", all_columns, current,
            "Filter parameters...",
        )
        if chosen is None:
            return
        chosen_set = set(chosen)
        new_names = [name for name in all_columns if name in chosen_set]
        added = [name for name in new_names if name not in current]
        problems = []
        for path in page._comp_files:
            columns = set(self._comp_columns_for(page, path))
            missing = [name for name in added if name not in columns]
            if missing:
                problems.append((self._comp_file_name(path), missing))
        if problems:
            self._comp_show_missing(problems)
            return
        self._comp_apply_params(page, new_names)
        self._comp_mark_dirty(page)

    def _comp_show_missing(self, problems) -> None:
        lines = ["These parameters are missing from the selected files:", ""]
        for name, missing in problems:
            lines.append(f"{name}: {', '.join(missing)}")
        QMessageBox.warning(self, "Compensation", "\n".join(lines))

    def _comp_apply_params(self, page, new_names) -> None:
        """Add/remove/reorder parameters, keeping the numbers by name."""
        old_names = list(page._comp_names)
        old_matrix = page._comp_matrix
        by_name = {}
        for i, row_name in enumerate(old_names):
            for j, col_name in enumerate(old_names):
                if i < len(old_matrix) and j < len(old_matrix[i]):
                    by_name[(row_name, col_name)] = old_matrix[i][j]
        matrix = []
        for i, row_name in enumerate(new_names):
            row = []
            for j, col_name in enumerate(new_names):
                if i == j:
                    row.append(100)
                else:
                    row.append(by_name.get((row_name, col_name), 0))
            matrix.append(row)
        page._comp_names[:] = list(new_names)
        page._comp_matrix[:] = matrix
        self._comp_refresh(page)

    def _comp_reordered(self, page) -> None:
        table = page._comp_table
        header = table.verticalHeader()
        count = len(page._comp_names)
        order = sorted(range(count), key=lambda i: header.visualIndex(i))
        if order == list(range(count)):
            return
        page._comp_names[:] = [page._comp_names[i] for i in order]
        page._comp_matrix[:] = [
            [page._comp_matrix[i][j] for j in order] for i in order
        ]
        self._comp_rebuild_table(page)
        self._comp_mark_dirty(page)

    def _comp_copy_matrix(self, page) -> None:
        names = list(page._comp_names)
        if not names:
            return
        lines = ["\t".join([""] + names)]
        for i, name in enumerate(names):
            values = [
                str(int(page._comp_matrix[i][j]))
                for j in range(len(names))
            ]
            lines.append("\t".join([name] + values))
        QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage("Compensation matrix copied")

    def _comp_paste_matrix(self, page) -> None:
        names = list(page._comp_names)
        if not names:
            return
        text = QApplication.clipboard().text()
        raw_lines = [
            line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if line.strip()
        ]
        if not raw_lines:
            QMessageBox.warning(
                self, "Paste Matrix", "The clipboard does not contain a matrix."
            )
            return
        header = [cell.strip() for cell in raw_lines[0].split("\t")]
        if header and not header[0]:
            header = header[1:]
        values = {}
        row_names = []
        for line in raw_lines[1:]:
            cells = [cell.strip() for cell in line.split("\t")]
            if not cells or not cells[0]:
                continue
            row_name = cells[0]
            row_names.append(row_name)
            numbers = cells[1:]
            if len(numbers) != len(header):
                QMessageBox.warning(
                    self,
                    "Paste Matrix",
                    "The pasted matrix has the wrong number of columns.",
                )
                return
            for col_name, raw in zip(header, numbers):
                try:
                    values[(row_name, col_name)] = int(float(raw))
                except (TypeError, ValueError):
                    QMessageBox.warning(
                        self,
                        "Paste Matrix",
                        f'"{raw}" is not an integer percentage.',
                    )
                    return
        if set(header) != set(names) or set(row_names) != set(names):
            QMessageBox.warning(
                self,
                "Paste Matrix",
                "The pasted matrix uses different parameter names.\n"
                "It was not applied.",
            )
            return
        matrix = []
        for i, row_name in enumerate(names):
            row = []
            for j, col_name in enumerate(names):
                if i == j:
                    row.append(100)
                else:
                    row.append(values.get((row_name, col_name), 0))
            matrix.append(row)
        page._comp_matrix[:] = matrix
        self._comp_rebuild_table(page)
        self._comp_mark_dirty(page)

    def _comp_mark_dirty(self, page) -> None:
        page._comp_dirty = True
        self._comp_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _comp_save_now(self, page) -> None:
        page._comp_task_def["params"] = page._comp_params
        if self.project is not None:
            self.project.save()
        page._comp_snapshot = copy.deepcopy(page._comp_params)
        page._comp_dirty = False
        self._comp_update_save_btn(page)
        self.statusBar().showMessage("Compensation saved")
        self._module_saved(page)

    def _comp_update_save_btn(self, page) -> None:
        page._comp_save_btn.setEnabled(page._comp_dirty and not page._comp_locked)

    def _comp_close(self, page) -> None:
        if page._comp_dirty:
            reply = QMessageBox.question(
                self,
                "Close Compensation",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._comp_task_def["params"] = page._comp_snapshot
        timer = getattr(page, "_comp_order_timer", None)
        if timer is not None:
            timer.stop()
        self._close_module_page(page)

    # ----- illumination correction module (map) -------------------------
    def _build_illumination_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
        """Build the Illumination Correction page."""
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
        close_btn.clicked.connect(lambda _=False: self._illum_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        params = task_def.setdefault("params", {})
        params.setdefault("submodules", [])
        page._illum_task_def = task_def
        page._illum_task_id = task_def.get("id")
        page._illum_params = params
        page._illum_submodules = params["submodules"]
        page._illum_selected = None
        page._illum_plot = None
        page._illum_locked = locked
        page._illum_save_btn = save_btn
        page._illum_dirty = False
        page._illum_snapshot = copy.deepcopy(params)
        page._illum_worker = _IlluminationWorker()
        page._illum_request_key = None
        page._illum_poll_timer = QTimer(self)
        page._illum_poll_timer.setInterval(120)
        page._illum_poll_timer.timeout.connect(
            lambda: self._illum_on_poll(page)
        )
        page._illum_poll_timer.start()
        page._illum_order_timer = QTimer(self)
        page._illum_order_timer.setSingleShot(True)
        page._illum_order_timer.setInterval(200)
        page._illum_order_timer.timeout.connect(
            lambda: self._illum_reordered(page)
        )
        save_btn.clicked.connect(lambda _=False: self._illum_save_now(page))

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        row = QHBoxLayout(content)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._illum_list_panel(page))
        right_host = QWidget()
        right_lay = QVBoxLayout(right_host)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        splitter.addWidget(right_host)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([220, 1100])
        row.addWidget(splitter, 1)
        layout.addWidget(content, 1)
        page._illum_right_host = right_host

        self._illum_refresh_list(page)
        self._illum_rebuild_right(page)
        self._illum_update_save_btn(page)
        return page

    def _illum_list_panel(self, page) -> QWidget:
        panel = self._build_panel_frame()
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(6, 10, 10, 10)
        outer.setSpacing(4)
        outer.addWidget(_FlowArrowBand())
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)
        add_btn = QPushButton("Add Submodule")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setEnabled(not page._illum_locked)
        add_btn.clicked.connect(lambda _=False: self._illum_add_submodule(page))
        inner.addWidget(add_btn)
        sub_list = QListWidget()
        sub_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: none; }"
            "QListWidget::item:selected { background: #dbeafa; }"
        )
        sub_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        sub_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        sub_list.itemDoubleClicked.connect(
            lambda item: self._illum_select_submodule(
                page, item.data(Qt.ItemDataRole.UserRole)
            )
        )
        sub_list.itemClicked.connect(
            lambda item: self._illum_select_submodule(
                page, item.data(Qt.ItemDataRole.UserRole)
            )
        )
        sub_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        sub_list.customContextMenuRequested.connect(
            lambda pos: self._illum_list_menu(page, pos)
        )
        sub_list.model().rowsMoved.connect(
            lambda *_: page._illum_order_timer.start()
        )
        inner.addWidget(sub_list, 1)
        note = QLabel(
            "Data passes through the submodules above, top to bottom, "
            "and leaves the module after the last one."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7785;")
        inner.addWidget(note)
        outer.addLayout(inner, 1)
        page._illum_list = sub_list
        page._illum_add_btn = add_btn
        return panel

    def _illum_refresh_list(self, page) -> None:
        sub_list = page._illum_list
        sub_list.blockSignals(True)
        sub_list.clear()
        for sub in page._illum_submodules:
            item = QListWidgetItem(sub.get("name", ""))
            item.setData(Qt.ItemDataRole.UserRole, sub.get("id"))
            if sub.get("mask") is None:
                item.setToolTip("Mask not calculated yet")
            sub_list.addItem(item)
        sub_list.blockSignals(False)
        if page._illum_selected:
            for i in range(sub_list.count()):
                if sub_list.item(i).data(Qt.ItemDataRole.UserRole) == page._illum_selected:
                    sub_list.setCurrentItem(sub_list.item(i))
                    break

    def _illum_selected_sub(self, page):
        for sub in page._illum_submodules:
            if sub.get("id") == page._illum_selected:
                return sub
        return None

    def _illum_next_name(self, page) -> str:
        taken = {sub.get("name") for sub in page._illum_submodules}
        n = 1
        while f"Channel{n}" in taken:
            n += 1
        return f"Channel{n}"

    def _illum_unique_name(self, page, base: str) -> str:
        taken = {sub.get("name") for sub in page._illum_submodules}
        if base not in taken:
            return base
        n = 2
        while f"{base} {n}" in taken:
            n += 1
        return f"{base} {n}"

    def _illum_default_axes(self):
        columns = []
        for rec in self._imported_files():
            for name in self._columns_for(rec.get("parquet")):
                if name not in columns:
                    columns.append(name)
        return (
            "x" if "x" in columns else None,
            "y" if "y" in columns else None,
        )

    def _illum_add_submodule(self, page) -> None:
        x_default, y_default = self._illum_default_axes()
        sub = {
            "id": uuid4().hex[:8],
            "name": self._illum_next_name(page),
            "files": [],
            "channel": None,
            "x": x_default,
            "y": y_default,
            "tile_w": 256,
            "tile_h": 256,
            "bin": 5,
            "resolution": 10,
            "method": "median",
            "calc_files": [],
            "mask": None,
            "mask_source": None,
        }
        page._illum_submodules.append(sub)
        page._illum_selected = sub["id"]
        self._illum_refresh_list(page)
        self._illum_rebuild_right(page)
        self._illum_mark_dirty(page)

    def _illum_list_menu(self, page, pos) -> None:
        sub_list = page._illum_list
        item = sub_list.itemAt(pos)
        if item is None:
            return
        sub_id = item.data(Qt.ItemDataRole.UserRole)
        sub = next((s for s in page._illum_submodules if s["id"] == sub_id), None)
        if sub is None:
            return
        menu = QMenu(sub_list)
        ren = QAction("Rename", menu)
        ren.setEnabled(not page._illum_locked)
        ren.triggered.connect(lambda: self._illum_rename_submodule(page, sub))
        menu.addAction(ren)
        dup = QAction("Duplicate", menu)
        dup.setEnabled(not page._illum_locked)
        dup.triggered.connect(lambda: self._illum_duplicate_submodule(page, sub))
        menu.addAction(dup)
        menu.addSeparator()
        delete = QAction("Delete", menu)
        delete.setEnabled(not page._illum_locked)
        delete.triggered.connect(lambda: self._illum_delete_submodule(page, sub))
        menu.addAction(delete)
        menu.exec(sub_list.viewport().mapToGlobal(pos))

    def _illum_rename_submodule(self, page, sub) -> None:
        text, ok = QInputDialog.getText(
            self, "Rename submodule", "Name:", QLineEdit.EchoMode.Normal,
            sub.get("name", ""),
        )
        if not ok:
            return
        name = text.strip()
        if not name:
            return
        if any(
            other is not sub and other.get("name") == name
            for other in page._illum_submodules
        ):
            QMessageBox.information(
                self, "Rename submodule", "A submodule with this name already exists."
            )
            return
        sub["name"] = name
        self._illum_refresh_list(page)
        self._illum_mark_dirty(page)

    def _illum_duplicate_submodule(self, page, sub) -> None:
        name = sub.get("name", "Channel") + " copy"
        while any(s.get("name") == name for s in page._illum_submodules):
            name += " copy"
        new_sub = copy.deepcopy(sub)
        new_sub["id"] = uuid4().hex[:8]
        new_sub["name"] = name
        new_sub["mask"] = None
        new_sub["mask_source"] = None
        page._illum_submodules.append(new_sub)
        page._illum_selected = new_sub["id"]
        self._illum_refresh_list(page)
        self._illum_rebuild_right(page)
        self._illum_mark_dirty(page)

    def _illum_drop_mask_file(self, task_id, sub) -> None:
        """Delete a submodule's illumination mask file, if any."""
        if self.project is None:
            return
        try:
            self.project._mask_path(str(task_id), str(sub["id"])).unlink(missing_ok=True)
        except OSError:
            pass

    def _illum_delete_submodule(self, page, sub) -> None:
        reply = QMessageBox.question(
            self,
            "Delete submodule",
            f'Delete "{sub.get("name", "")}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._illum_submodules.remove(sub)
        self._illum_drop_mask_file(page._illum_task_id, sub)
        if page._illum_selected == sub["id"]:
            page._illum_selected = (
                page._illum_submodules[0]["id"]
                if page._illum_submodules else None
            )
        self._illum_refresh_list(page)
        self._illum_rebuild_right(page)
        self._illum_mark_dirty(page)

    def _illum_select_submodule(self, page, sub_id) -> None:
        if page._illum_selected == sub_id:
            return
        page._illum_selected = sub_id
        self._illum_rebuild_right(page)

    def _illum_reordered(self, page) -> None:
        sub_list = page._illum_list
        order = [
            sub_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(sub_list.count())
        ]
        by_id = {sub["id"]: sub for sub in page._illum_submodules}
        reordered = [by_id[sid] for sid in order if sid in by_id]
        if len(reordered) != len(page._illum_submodules):
            return
        if [s["id"] for s in reordered] == [s["id"] for s in page._illum_submodules]:
            return
        page._illum_submodules[:] = reordered
        self._illum_refresh_list(page)
        self._illum_render_preview(page)
        self._illum_mark_dirty(page)

    # --- right side
    def _illum_rebuild_right(self, page) -> None:
        host = page._illum_right_host
        self._clear_layout(host.layout())
        sub = self._illum_selected_sub(page)
        page._illum_plot = None
        if sub is None:
            hint = QLabel(
                "Add a submodule on the left, then double-click it to edit."
            )
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet("color: #888888;")
            host.layout().addWidget(hint)
            return
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        top = QWidget()
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(12)
        top_lay.addWidget(self._illum_channel_panel(page, sub), 1)
        top_lay.addWidget(self._illum_params_panel(page, sub), 2)
        top_lay.addWidget(self._illum_mask_panel(page, sub), 2)
        bottom = QWidget()
        bottom_lay = QHBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)
        bottom_lay.setSpacing(12)
        bottom_lay.addWidget(self._illum_plot_panel(page, sub, "Before"), 2)
        bottom_lay.addWidget(self._illum_plot_panel(page, sub, "After"), 2)
        bottom_lay.addWidget(self._illum_plot_settings(page, sub), 1)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([320, 520])
        host.layout().addWidget(splitter, 1)
        self._illum_render_preview(page)
        self._illum_render_mask(page, sub)

    def _illum_channel_panel(self, page, sub) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        editable = not page._illum_locked
        columns = self._illum_all_columns()

        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(4)
        top_lay.addWidget(QLabel("<b>Channel &amp; axes</b>"))
        channel_combo = _NoWheelComboBox()
        channel_combo.addItem("", None)
        for name in columns:
            channel_combo.addItem(name, name)
        index = channel_combo.findData(sub.get("channel"))
        channel_combo.setCurrentIndex(index if index >= 0 else 0)
        channel_combo.setEnabled(editable)
        top_lay.addWidget(QLabel("Channel"))
        top_lay.addWidget(channel_combo)
        axes_row = QHBoxLayout()
        x_combo = _NoWheelComboBox()
        y_combo = _NoWheelComboBox()
        for combo, value in ((x_combo, sub.get("x")), (y_combo, sub.get("y"))):
            combo.addItem("", None)
            for name in columns:
                combo.addItem(name, name)
            idx = combo.findData(value)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setEnabled(editable)
        axes_row.addWidget(QLabel("X"))
        axes_row.addWidget(x_combo, 1)
        axes_row.addWidget(QLabel("Y"))
        axes_row.addWidget(y_combo, 1)
        top_lay.addLayout(axes_row)

        files_host = QWidget()
        files_lay = QVBoxLayout(files_host)
        files_lay.setContentsMargins(0, 0, 0, 0)
        files_lay.setSpacing(4)
        set_btn = QPushButton("Set Files")
        set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_btn.setEnabled(editable and bool(sub.get("channel")))
        set_btn.setToolTip(
            "" if sub.get("channel") else "Select the channel first"
        )
        set_btn.clicked.connect(lambda _=False: self._illum_set_files(page, sub))
        files_lay.addWidget(set_btn)
        files_list = QListWidget()
        files_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: none; }"
        )
        for path in sub.get("files") or []:
            files_list.addItem(QListWidgetItem(self._comp_file_name(path)))
        page._illum_files_list = files_list
        files_lay.addWidget(files_list, 1)

        lay.addWidget(top, 1)
        lay.addWidget(files_host, 3)

        def on_channel():
            new_channel = channel_combo.currentData()
            if new_channel == sub.get("channel"):
                return
            files = sub.get("files") or []
            missing = [
                self._comp_file_name(path) for path in files
                if new_channel and new_channel not in set(self._columns_for(path))
            ]
            if missing:
                self._comp_show_missing(
                    [(name, [new_channel]) for name in missing]
                )
                channel_combo.blockSignals(True)
                idx = channel_combo.findData(sub.get("channel"))
                channel_combo.setCurrentIndex(idx if idx >= 0 else 0)
                channel_combo.blockSignals(False)
                return
            sub["channel"] = new_channel
            set_btn.setEnabled(editable and bool(new_channel))
            set_btn.setToolTip("" if new_channel else "Select the channel first")
            self._illum_reset_plot_defaults(page, sub)
            self._illum_render_preview(page)
            self._illum_mark_dirty(page)

        def on_axis(key, combo):
            def handler():
                sub[key] = combo.currentData()
                self._illum_reset_plot_defaults(page, sub)
                self._illum_render_preview(page)
                self._illum_mark_dirty(page)
            return handler

        channel_combo.currentIndexChanged.connect(lambda _=0: on_channel())
        x_combo.currentIndexChanged.connect(lambda _=0: on_axis("x", x_combo)())
        y_combo.currentIndexChanged.connect(lambda _=0: on_axis("y", y_combo)())
        return panel

    def _illum_all_columns(self) -> list:
        columns = []
        for rec in self._imported_files():
            for name in self._columns_for(rec.get("parquet")):
                if name not in columns:
                    columns.append(name)
        return columns

    def _illum_set_files(self, page, sub) -> None:
        channel = sub.get("channel")
        if not channel:
            QMessageBox.information(
                self, "Set Files", "Select the channel first."
            )
            return
        records = self._imported_files()
        names = [rec.get("name", "") for rec in records]
        current = [
            self._comp_file_name(path) for path in (sub.get("files") or [])
        ]
        chosen = self._pick_items_dialog(
            "Select files", names, current, "Filter files..."
        )
        if chosen is None:
            return
        chosen_set = set(chosen)
        paths = [
            rec.get("parquet") for rec in records
            if rec.get("name", "") in chosen_set
        ]
        problems = []
        for path in paths:
            columns = set(self._columns_for(path))
            if channel not in columns:
                problems.append((self._comp_file_name(path), [channel]))
        if problems:
            self._comp_show_missing(problems)
            return
        sub["files"] = paths
        sub["calc_files"] = [
            path for path in (sub.get("calc_files") or []) if path in paths
        ]
        self._illum_rebuild_right(page)
        self._illum_mark_dirty(page)

    def _illum_params_panel(self, page, sub) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Correction parameters</b>"))
        editable = not page._illum_locked

        sizes = QHBoxLayout()
        sizes.addWidget(QLabel("Tile W"))
        tile_w = _NoWheelSpinBox()
        tile_w.setRange(1, 1_000_000)
        tile_w.setValue(int(sub.get("tile_w", 256)))
        tile_w.setEnabled(editable)
        sizes.addWidget(tile_w)
        sizes.addWidget(QLabel("Tile H"))
        tile_h = _NoWheelSpinBox()
        tile_h.setRange(1, 1_000_000)
        tile_h.setValue(int(sub.get("tile_h", 256)))
        tile_h.setEnabled(editable)
        sizes.addWidget(tile_h)
        sizes.addStretch()
        lay.addLayout(sizes)

        hint = QLabel(self._illum_divisibility_hint(sub))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b58900;")
        lay.addWidget(hint)

        bins_row = QHBoxLayout()
        bins_row.addWidget(QLabel("Bin"))
        bin_spin = _NoWheelSpinBox()
        bin_spin.setRange(3, 10)
        bin_spin.setValue(int(sub.get("bin", 5)))
        bin_spin.setEnabled(editable)
        bins_row.addWidget(bin_spin)
        bins_row.addWidget(QLabel("Resolution"))
        res_spin = _NoWheelSpinBox()
        res_spin.setRange(max(3, int(sub.get("bin", 5))), 20)
        res_spin.setValue(max(int(sub.get("bin", 5)), int(sub.get("resolution", 10))))
        res_spin.setEnabled(editable)
        bins_row.addWidget(res_spin)
        bins_row.addStretch()
        lay.addLayout(bins_row)

        lay.addWidget(QLabel("Correction method"))
        method_combo = _NoWheelComboBox()
        method_combo.addItem("Down (darkest unchanged)", "down")
        method_combo.addItem("Up (brightest unchanged)", "up")
        method_combo.addItem("Median (default)", "median")
        idx = method_combo.findData(sub.get("method", "median"))
        method_combo.setCurrentIndex(idx if idx >= 0 else 2)
        method_combo.setEnabled(editable)
        lay.addWidget(method_combo)

        lay.addWidget(QLabel("Files for the calculation"))
        calc_list = QListWidget()
        calc_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: none; }"
        )
        calc_list.setEnabled(editable)
        selected = set(sub.get("calc_files") or [])
        files = sub.get("files") or []
        calc_list.blockSignals(True)
        for path in files:
            item = QListWidgetItem(self._comp_file_name(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if path in selected
                else Qt.CheckState.Unchecked
            )
            calc_list.addItem(item)
        calc_list.blockSignals(False)
        lay.addWidget(calc_list, 1)
        page._illum_calc_list = calc_list

        calc_row = QHBoxLayout()
        calc_btn = QPushButton("Calculate Illumination")
        calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        calc_btn.setEnabled(editable and bool(sub.get("channel")))
        calc_btn.clicked.connect(
            lambda _=False: self._illum_calculate(page, sub, calc_btn)
        )
        calc_row.addWidget(calc_btn)
        status = QLabel("")
        status.setStyleSheet("color: #6b7785;")
        calc_row.addWidget(status, 1)
        lay.addLayout(calc_row)
        page._illum_calc_btn = calc_btn
        page._illum_calc_status = status

        def on_sizes():
            sub["tile_w"] = int(tile_w.value())
            sub["tile_h"] = int(tile_h.value())
            hint.setText(self._illum_divisibility_hint(sub))
            if sub.get("mask"):
                sub["mask"] = None
                sub["mask_source"] = None
                self._illum_drop_mask_file(page._illum_task_id, sub)
                status.setText("Parameters changed — recalculate the mask.")
            self._illum_mark_dirty(page)

        def on_bin(value):
            sub["bin"] = int(value)
            res_spin.setMinimum(int(value))
            if res_spin.value() < int(value):
                res_spin.setValue(int(value))
            sub["resolution"] = int(res_spin.value())
            if sub.get("mask"):
                sub["mask"] = None
                sub["mask_source"] = None
                self._illum_drop_mask_file(page._illum_task_id, sub)
                status.setText("Parameters changed — recalculate the mask.")
            self._illum_mark_dirty(page)

        def on_resolution(value):
            sub["resolution"] = int(value)
            if sub.get("mask"):
                sub["mask"] = None
                sub["mask_source"] = None
                self._illum_drop_mask_file(page._illum_task_id, sub)
                status.setText("Parameters changed — recalculate the mask.")
            self._illum_mark_dirty(page)

        def on_method():
            sub["method"] = method_combo.currentData()
            self._illum_mark_dirty(page)
            self._illum_render_mask(page, sub)
            self._illum_render_preview(page)

        def on_calc_files(_item=None):
            sub["calc_files"] = [
                calc_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(calc_list.count())
                if calc_list.item(i).checkState() == Qt.CheckState.Checked
            ]
            self._illum_mark_dirty(page)

        tile_w.valueChanged.connect(lambda _=0: on_sizes())
        tile_h.valueChanged.connect(lambda _=0: on_sizes())
        bin_spin.valueChanged.connect(on_bin)
        res_spin.valueChanged.connect(on_resolution)
        method_combo.currentIndexChanged.connect(lambda _=0: on_method())
        calc_list.itemChanged.connect(on_calc_files)
        return panel

    def _illum_divisibility_hint(self, sub) -> str:
        files = sub.get("files") or []
        tile_w = int(sub.get("tile_w", 256))
        tile_h = int(sub.get("tile_h", 256))
        if not files or tile_w <= 0 or tile_h <= 0:
            return ""
        try:
            geom = _image_geometry(files[0])
        except Exception:  # noqa: BLE001
            return ""
        width = int(round(geom[1] - geom[0] + 1))
        height = int(round(geom[3] - geom[2] + 1))
        messages = []
        if width % tile_w:
            messages.append(
                f"Width {width} is not divisible by tile width {tile_w}: "
                "the edge strip is excluded from the mask calculation."
            )
        if height % tile_h:
            messages.append(
                f"Height {height} is not divisible by tile height {tile_h}: "
                "the edge strip is excluded from the mask calculation."
            )
        return "\n".join(messages)

    def _illum_mask_panel(self, page, sub) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Illumination mask</b>"))
        plot = pg.PlotWidget(background="w")
        plot.getPlotItem().hideAxis("bottom")
        plot.getPlotItem().hideAxis("left")
        plot.getPlotItem().getViewBox().setAspectLocked(True)
        image_item = pg.ImageItem(axisOrder="row-major")
        plot.addItem(image_item)
        lay.addWidget(plot, 1)
        source = QLabel("")
        source.setWordWrap(True)
        source.setStyleSheet("color: #6b7785;")
        lay.addWidget(source)
        page._illum_mask_plot = plot
        page._illum_mask_image = image_item
        page._illum_mask_source = source
        return panel

    def _illum_render_mask(self, page, sub) -> None:
        import numpy as np

        image_item = getattr(page, "_illum_mask_image", None)
        if image_item is None:
            return
        source = getattr(page, "_illum_mask_source", None)
        mask_data = sub.get("mask")
        if not mask_data:
            image_item.clear()
            if source is not None:
                source.setText("No mask yet. Click Calculate Illumination.")
            return
        mask = np.asarray(mask_data, dtype="float64")
        finite = mask[np.isfinite(mask)]
        if finite.size == 0:
            image_item.clear()
            return
        lo = float(finite.min())
        hi = float(finite.max())
        if hi <= lo:
            hi = lo + 1.0
        lut = _figure_colormap("viridis").getLookupTable(0.0, 1.0, 256)
        image_item.setImage(mask, autoLevels=False, levels=(lo, hi), lut=lut)
        image_item.setRect(QRectF(0, 0, mask.shape[1], mask.shape[0]))
        source_params = sub.get("mask_source") or {}
        if source is not None:
            source.setText(
                f"tile {source_params.get('tile_w', sub.get('tile_w'))}"
                f" × {source_params.get('tile_h', sub.get('tile_h'))}"
                f" · bin {source_params.get('bin', sub.get('bin'))}"
                f" · resolution {source_params.get('resolution', sub.get('resolution'))}\n"
                f"channel {sub.get('channel')} · method {sub.get('method')}\n"
                f"mask range {lo:.4g} – {hi:.4g}"
            )

    def _illum_plot_panel(self, page, sub, title: str) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        plot = pg.PlotWidget(background="w")
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.getPlotItem().setAxisItems({
            "bottom": _make_sci_axis("bottom"),
            "left": _make_sci_axis("left"),
        })
        lay.addWidget(plot, 1)
        if title == "Before":
            page._illum_before_plot = plot
        else:
            page._illum_after_plot = plot
        return panel

    def _illum_plot_settings(self, page, sub) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Plot settings</b>"))
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
        lay.addWidget(scroll, 1)
        page._illum_settings_layout = settings

        plot_dict = {
            "type": "scatter",
            "file": (sub.get("files") or [None])[0],
            "file_name": None,
            "x": sub.get("x"),
            "y": sub.get("y"),
            "color_mode": "parameter",
            "color_param": sub.get("channel"),
            "palette": "viridis",
            "show_axes": True,
            "show_axis_names": True,
            "show_indices": True,
            "show_sample_name": True,
            "show_tile_grid": True,
        }
        page._illum_plot = plot_dict
        page._illum_plot_settings_built = True

        records = self._imported_files()
        enabled = not page._illum_locked

        def refresh():
            self._illum_render_preview(page)
            self._illum_mark_dirty(page)

        settings.addWidget(QLabel("Plot type"))
        type_combo = _NoWheelComboBox()
        type_combo.addItem("Scatter", "scatter")
        type_combo.addItem("Image", "image")
        idx = type_combo.findData(plot_dict.get("type"))
        type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        type_combo.setEnabled(enabled)
        settings.addWidget(type_combo)

        settings.addWidget(QLabel("File"))
        file_combo = _NoWheelComboBox()
        files = sub.get("files") or []
        for path in files:
            file_combo.addItem(self._comp_file_name(path), path)
        idx = file_combo.findData(plot_dict.get("file"))
        file_combo.setCurrentIndex(idx if idx >= 0 else 0)
        file_combo.setEnabled(enabled)
        settings.addWidget(file_combo)
        plot_dict["file_name"] = file_combo.currentText()

        columns = []
        rec = self._figure_record(plot_dict.get("file"))
        if rec is not None:
            columns = self._columns_for(rec.get("parquet"), page._illum_task_id)

        settings.addWidget(QLabel("X parameter"))
        x_combo = self._gating_param_combo(columns, plot_dict.get("x"), enabled)
        settings.addWidget(x_combo)
        settings.addWidget(QLabel("Y parameter"))
        y_combo = self._gating_param_combo(columns, plot_dict.get("y"), enabled)
        settings.addWidget(y_combo)

        settings.addWidget(QLabel("Color parameter (corrected channel)"))
        color_label = QLabel(str(plot_dict.get("color_param") or "—"))
        color_label.setStyleSheet("color: #37474f; font-weight: bold;")
        settings.addWidget(color_label)
        page._illum_color_label = color_label

        settings.addWidget(QLabel("Palette"))
        palette_combo = _NoWheelComboBox()
        for name in ("viridis", "plasma", "inferno", "magma", "cividis", "turbo"):
            palette_combo.addItem(name, name)
        idx = palette_combo.findData(plot_dict.get("palette"))
        palette_combo.setCurrentIndex(idx if idx >= 0 else 0)
        palette_combo.setEnabled(enabled)
        settings.addWidget(palette_combo)

        grid_box = QCheckBox("Show tile grid")
        grid_box.setChecked(bool(plot_dict.get("show_tile_grid")))
        grid_box.setEnabled(enabled)
        settings.addWidget(grid_box)
        settings.addStretch()

        def on_type():
            plot_dict["type"] = type_combo.currentData()
            refresh()

        def on_file():
            plot_dict["file"] = file_combo.currentData()
            plot_dict["file_name"] = file_combo.currentText()
            refresh()

        def on_param(key, combo):
            def handler(value):
                plot_dict[key] = value or None
                refresh()
            return handler

        def on_palette():
            plot_dict["palette"] = palette_combo.currentData()
            refresh()

        def on_grid(checked):
            plot_dict["show_tile_grid"] = bool(checked)
            # Only toggle the overlay; re-rendering the data would re-run the
            # whole correction pipeline and make the checkbox feel sluggish.
            self._illum_update_tile_grid(page)
            self._illum_mark_dirty(page)

        type_combo.currentIndexChanged.connect(lambda _=0: on_type())
        file_combo.currentIndexChanged.connect(lambda _=0: on_file())
        x_combo.currentTextChanged.connect(on_param("x", x_combo))
        y_combo.currentTextChanged.connect(on_param("y", y_combo))
        palette_combo.currentIndexChanged.connect(lambda _=0: on_palette())
        grid_box.toggled.connect(on_grid)
        return panel

    def _illum_sub_stage(self, sub):
        return _illumination_stage_item(sub)

    def _illum_preview_stages(self, page, subs):
        items = [
            item for item in (self._illum_sub_stage(s) for s in subs)
            if item is not None
        ]
        if not items:
            return None
        return [("illumination", {
            "task_id": page._illum_task_id,
            "submodules": items,
        })]

    def _illum_render_preview(self, page) -> None:
        sub = self._illum_selected_sub(page)
        plot_dict = getattr(page, "_illum_plot", None)
        before = getattr(page, "_illum_before_plot", None)
        after = getattr(page, "_illum_after_plot", None)
        if sub is None or plot_dict is None or before is None or after is None:
            return
        try:
            index = page._illum_submodules.index(sub)
        except ValueError:
            return
        earlier = page._illum_submodules[:index]
        before_stages = self._illum_preview_stages(page, earlier)
        after_stages = self._illum_preview_stages(page, earlier + [sub])
        for plot_widget in (before, after):
            self._illum_clear_tile_grid(plot_widget)
        self._figure_render(
            plot_dict, before, task_id=page._illum_task_id,
            extra_stages=before_stages,
        )
        self._figure_render(
            plot_dict, after, task_id=page._illum_task_id,
            extra_stages=after_stages,
        )
        if plot_dict.get("show_tile_grid"):
            before._illum_grid_item = self._illum_draw_tile_grid(
                before, plot_dict, sub
            )
            after._illum_grid_item = self._illum_draw_tile_grid(
                after, plot_dict, sub
            )

    def _illum_clear_tile_grid(self, plot_widget) -> None:
        item = getattr(plot_widget, "_illum_grid_item", None)
        if item is None:
            return
        try:
            plot_widget.removeItem(item)
        except Exception:  # noqa: BLE001
            pass
        plot_widget._illum_grid_item = None

    def _illum_draw_tile_grid(self, plot_widget, plot_dict, sub):
        import numpy as np

        tile_w = int(sub.get("tile_w", 0) or 0)
        tile_h = int(sub.get("tile_h", 0) or 0)
        if tile_w <= 0 or tile_h <= 0:
            return None
        if plot_dict.get("type") == "image":
            extent = getattr(plot_widget, "_img_extent", None)
            if not extent:
                return None
            xmin, xmax, ymin, ymax = extent
        else:
            xmin = getattr(plot_widget, "_data_xmin", None)
            xmax = getattr(plot_widget, "_data_xmax", None)
            ymin = getattr(plot_widget, "_data_ymin", None)
            ymax = getattr(plot_widget, "_data_ymax", None)
            if None in (xmin, xmax, ymin, ymax):
                return None
        xs = []
        ys = []
        x = xmin
        while x <= xmax:
            xs.extend([x, x, np.nan])
            ys.extend([ymin, ymax, np.nan])
            x += tile_w
            if len(xs) > 20000:
                break
        y = ymin
        while y <= ymax:
            xs.extend([xmin, xmax, np.nan])
            ys.extend([y, y, np.nan])
            y += tile_h
            if len(xs) > 20000:
                break
        if not xs:
            return None
        item = pg.PlotDataItem(
            np.asarray(xs), np.asarray(ys),
            pen=pg.mkPen((120, 140, 170, 140), width=1),
            connect="finite",
        )
        plot_widget.addItem(item)
        return item

    def _illum_update_tile_grid(self, page) -> None:
        """Add/remove the tile-grid overlay without re-rendering the data."""
        sub = self._illum_selected_sub(page)
        plot_dict = getattr(page, "_illum_plot", None)
        before = getattr(page, "_illum_before_plot", None)
        after = getattr(page, "_illum_after_plot", None)
        if sub is None or plot_dict is None or before is None or after is None:
            return
        for plot_widget in (before, after):
            self._illum_clear_tile_grid(plot_widget)
        if plot_dict.get("show_tile_grid"):
            before._illum_grid_item = self._illum_draw_tile_grid(
                before, plot_dict, sub
            )
            after._illum_grid_item = self._illum_draw_tile_grid(
                after, plot_dict, sub
            )

    def _illum_reset_plot_defaults(self, page, sub) -> None:
        plot_dict = getattr(page, "_illum_plot", None)
        if plot_dict is None:
            return
        plot_dict["x"] = sub.get("x")
        plot_dict["y"] = sub.get("y")
        plot_dict["color_param"] = sub.get("channel")
        label = getattr(page, "_illum_color_label", None)
        if label is not None:
            label.setText(str(sub.get("channel") or "—"))

    def _illum_calculate(self, page, sub, button) -> None:
        channel = sub.get("channel")
        x_name = sub.get("x")
        y_name = sub.get("y")
        if not channel or not x_name or not y_name:
            QMessageBox.information(
                self,
                "Calculate Illumination",
                "Select the channel and the X/Y parameters first.",
            )
            return
        files = list(sub.get("calc_files") or [])
        if not files:
            QMessageBox.information(
                self,
                "Calculate Illumination",
                "Select at least one file to calculate the mask from.",
            )
            return
        if int(sub.get("resolution", 10)) < int(sub.get("bin", 5)):
            QMessageBox.information(
                self,
                "Calculate Illumination",
                "Resolution must be greater than or equal to bin.",
            )
            return
        try:
            index = page._illum_submodules.index(sub)
        except ValueError:
            return
        earlier = page._illum_submodules[:index]
        stages = self._map_stages(page._illum_task_id) or []
        stages = list(stages)
        earlier_items = [
            item for item in (self._illum_sub_stage(s) for s in earlier)
            if item is not None
        ]
        if earlier_items:
            stages.append(("illumination", {
                "task_id": page._illum_task_id,
                "submodules": earlier_items,
            }))
        base_columns = {}
        for path in files:
            base_columns[path] = set(
                self._column_index.get(self._rel_data_key(path), [])
            )
        page._illum_request_key = sub["id"]
        button.setEnabled(False)
        status = getattr(page, "_illum_calc_status", None)
        if status is not None:
            status.setText("Calculating…")
        page._illum_worker.request(
            sub["id"], stages, base_columns, files, channel, x_name, y_name,
            int(sub.get("tile_w", 256)), int(sub.get("tile_h", 256)),
            int(sub.get("bin", 5)), int(sub.get("resolution", 10)),
        )

    def _illum_on_poll(self, page) -> None:
        worker = getattr(page, "_illum_worker", None)
        if worker is None:
            return
        for key, result, error in worker.poll():
            if key != page._illum_request_key:
                continue
            page._illum_request_key = None
            button = getattr(page, "_illum_calc_btn", None)
            if button is not None:
                button.setEnabled(not page._illum_locked)
            status = getattr(page, "_illum_calc_status", None)
            if error:
                if status is not None:
                    status.setText(error)
                QMessageBox.warning(self, "Calculate Illumination", error)
                continue
            sub = next(
                (s for s in page._illum_submodules if s["id"] == key), None
            )
            if sub is None:
                continue
            sub["mask"] = result["mask"]
            sub["mask_source"] = {
                "tile_w": result["tile_w"],
                "tile_h": result["tile_h"],
                "bin": result["bin"],
                "resolution": result["resolution"],
                "files": result["files"],
                "channel": sub.get("channel"),
                "x": sub.get("x"),
                "y": sub.get("y"),
            }
            if status is not None:
                status.setText("Mask calculated.")
            self._illum_render_mask(page, sub)
            if sub.get("id") == page._illum_selected:
                self._illum_render_preview(page)
            self._illum_refresh_list(page)
            self._illum_mark_dirty(page)

    def _illum_mark_dirty(self, page) -> None:
        page._illum_dirty = True
        self._illum_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _illum_save_now(self, page) -> None:
        page._illum_task_def["params"] = page._illum_params
        if self.project is not None:
            self.project.save()
        page._illum_snapshot = copy.deepcopy(page._illum_params)
        page._illum_dirty = False
        self._illum_update_save_btn(page)
        self.statusBar().showMessage("Illumination correction saved")
        self._module_saved(page)

    def _illum_update_save_btn(self, page) -> None:
        page._illum_save_btn.setEnabled(page._illum_dirty and not page._illum_locked)

    def _illum_close(self, page) -> None:
        if page._illum_dirty:
            reply = QMessageBox.question(
                self,
                "Close Illumination Correction",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._illum_task_def["params"] = page._illum_snapshot
        for name in ("_illum_poll_timer", "_illum_order_timer"):
            timer = getattr(page, name, None)
            if timer is not None:
                timer.stop()
        worker = getattr(page, "_illum_worker", None)
        if worker is not None:
            worker.stop()
        self._close_module_page(page)

    # ----- segmentation module (generate) -------------------------------
    def _seg_params_for(self, task_def) -> dict:
        """Saved params of a segmentation task (its snapshot while open)."""
        task_id = task_def.get("id")
        reg = self._module_registry.get(task_id)
        if reg is not None and reg.get("task_type") == "segmentation":
            snap = getattr(reg["page"], "_seg_snapshot", None)
            if isinstance(snap, dict):
                return snap
        return task_def.get("params") or {}

    def _seg_mask_record(self, params, parquet_path):
        """The mask record of one file in a segmentation task's params."""
        masks = params.get("masks") or {}
        rec = masks.get(parquet_path)
        return rec if isinstance(rec, dict) else None

    def _seg_mask_file(self, rec):
        """Absolute path of a mask record's .npy file, or None if missing."""
        if not rec or self.project is None:
            return None
        rel = rec.get("path")
        if not rel:
            return None
        path = self.project.path / rel
        return path if path.is_file() else None

    def _seg_signature(self, path) -> str:
        """A cheap change signature (size + nanosecond mtime) for a mask file."""
        try:
            st = Path(path).stat()
        except OSError:
            return ""
        return f"{int(st.st_size)}-{int(st.st_mtime_ns)}"

    def _seg_applied_columns(self, task_id, parquet_path) -> dict:
        """{column_name: mask_path} exposed by upstream Segmentation modules.

        Only masks whose saved signature still matches the file on disk are
        exposed, so downstream modules always see the saved state.
        """
        if not task_id or not parquet_path or self.project is None:
            return {}
        out = {}
        for task in self._task_chain(task_id):
            if task.get("id") == task_id:
                continue
            if task.get("type") != "segmentation":
                continue
            params = self._seg_params_for(task)
            applied = params.get("applied") or {}
            sign = applied.get(parquet_path)
            if not sign:
                continue
            path = self._seg_mask_file(self._seg_mask_record(params, parquet_path))
            if path is None or self._seg_signature(path) != sign:
                continue
            name = params.get("column") or "Mask"
            out.setdefault(name, path)
        return out

    def _seg_load_mask(self, mask_path):
        """Load a mask .npy (cached by path + signature)."""
        import numpy as np

        cache = getattr(self, "_seg_mask_cache", None)
        if cache is None:
            cache = {}
            self._seg_mask_cache = cache
        key = str(mask_path)
        sign = self._seg_signature(mask_path)
        hit = cache.get(key)
        if hit is not None and hit[0] == sign:
            return hit[1]
        try:
            mask = np.load(mask_path)
        except (OSError, ValueError):
            return None
        if mask.ndim != 2:
            return None
        if len(cache) > 4:
            cache.clear()
        cache[key] = (sign, mask)
        return mask

    def _seg_mask_values(self, parquet_path, mask_path):
        """Mask labels aligned to a parquet's rows (float64, 0 where unknown)."""
        import numpy as np
        import pandas as pd

        ent = self._ensure_image_index(parquet_path)
        if ent is None or ent.get("manifest") is None:
            return None
        man = ent["manifest"]
        mask = self._seg_load_mask(mask_path)
        if mask is None:
            return None
        cache = getattr(self, "_seg_value_cache", None)
        if cache is None:
            cache = {}
            self._seg_value_cache = cache
        key = (str(parquet_path), str(mask_path))
        hit = cache.get(key)
        if hit is not None:
            return hit
        try:
            df = pd.read_parquet(parquet_path, columns=["x", "y"])
        except Exception:  # noqa: BLE001
            return None
        xi = np.rint(
            df["x"].to_numpy(dtype="float64") - float(man["xmin"])
        ).astype(np.int64)
        yi = np.rint(
            df["y"].to_numpy(dtype="float64") - float(man["ymin"])
        ).astype(np.int64)
        ok = (
            (xi >= 0) & (xi < mask.shape[1])
            & (yi >= 0) & (yi < mask.shape[0])
        )
        out = np.zeros(len(df), dtype="float64")
        out[ok] = mask[yi[ok], xi[ok]].astype("float64")
        if len(cache) > 4:
            cache.clear()
        cache[key] = out
        return out

    def _seg_mask_raster(self, manifest, bbox, mask_path):
        """Return (label_2d, data_rect) for a mask over the visible bbox."""
        import numpy as np

        mask = self._seg_load_mask(mask_path)
        if mask is None:
            return None, None
        x0, x1, y0, y1 = bbox
        xmin = float(manifest["xmin"])
        ymin = float(manifest["ymin"])
        i0 = max(0, int(np.floor(x0 - xmin)))
        i1 = min(mask.shape[1], int(np.ceil(x1 - xmin)))
        j0 = max(0, int(np.floor(y0 - ymin)))
        j1 = min(mask.shape[0], int(np.ceil(y1 - ymin)))
        if i1 <= i0 or j1 <= j0:
            return None, None
        crop = np.asarray(mask[j0:j1, i0:i1], dtype="float32")
        rect = QRectF(xmin + i0, ymin + j0, i1 - i0, j1 - j0)
        return crop, rect

    def _seg_temp_dir(self):
        """Shared temp folder for this project's segmentation caches."""
        import hashlib
        import tempfile

        key = "no-project"
        if self.project is not None:
            key = hashlib.md5(
                str(self.project.path.resolve()).encode("utf-8")
            ).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / "smage" / "segmentation" / key

    def _seg_clear_edge_cache(self) -> None:
        """Drop this project's cached mask-edge images (temp data only)."""
        import shutil

        cache = getattr(self, "_seg_edge_cache", None)
        if cache is not None:
            cache.clear()
        try:
            shutil.rmtree(self._seg_temp_dir(), ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def _seg_edge_map(self, mask_path):
        """Return (and disk-cache) the boolean edge map of a mask."""
        import numpy as np

        mask = self._seg_load_mask(mask_path)
        if mask is None:
            return None
        sign = self._seg_signature(mask_path)
        cache = getattr(self, "_seg_edge_cache", None)
        if cache is None:
            cache = {}
            self._seg_edge_cache = cache
        key = str(mask_path)
        hit = cache.get(key)
        if hit is not None and hit[0] == sign:
            return hit[1]
        path = self._seg_temp_dir() / f"{Path(str(mask_path)).stem}.{sign}.npy"
        edge = None
        if path.is_file():
            try:
                edge = np.load(path)
            except (OSError, ValueError):
                edge = None
        if edge is None or edge.shape != mask.shape:
            edge = np.zeros(mask.shape, dtype=bool)
            if mask.size:
                edge[1:, :] |= mask[1:, :] != mask[:-1, :]
                edge[:, 1:] |= mask[:, 1:] != mask[:, :-1]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, edge)
            except OSError:
                pass
        if len(cache) > 4:
            cache.clear()
        cache[key] = (sign, edge)
        return edge

    # --- segmentation page
    def _build_segmentation_page(self, name: str, task_def: dict, locked: bool) -> QWidget:
        """Build the Segmentation page (files | method | preview | plot settings)."""
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
        close_btn.clicked.connect(lambda _=False: self._seg_close(page))
        tl.addWidget(save_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        params = task_def.setdefault("params", {})
        params.setdefault("method", "nuclear_expansion")
        # Migrate legacy method ids (Cellpose/StarDist) to their -cpu names.
        if params.get("method") == "cellpose":
            params["method"] = "cellpose-cpu"
        elif params.get("method") == "stardist":
            params["method"] = "stardist-cpu"
        params.setdefault("files", [])
        params.setdefault("expose", {})
        params.setdefault("column", "Mask")
        params.setdefault("method_params", {})
        params.setdefault("masks", {})
        params.setdefault("applied", {})
        params.setdefault("plot", {})
        page._seg_task_def = task_def
        page._seg_task_id = task_def.get("id")
        page._seg_params = params
        page._seg_locked = locked
        page._seg_save_btn = save_btn
        page._seg_dirty = False
        page._seg_snapshot = copy.deepcopy(params)
        page._seg_applied_snapshot = dict(params.get("applied") or {})
        page._seg_overlay_item = None
        page._seg_mask_cache = {}
        page._seg_ext_running = False
        page._seg_log_lines = []
        save_btn.clicked.connect(lambda _=False: self._seg_save_now(page))

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        row = QHBoxLayout(content)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._seg_files_panel(page))
        splitter.addWidget(self._seg_method_panel(page))
        page._seg_preview_panel = self._seg_preview_panel(page)
        page._seg_settings_panel = self._seg_plot_settings_panel(page)
        splitter.addWidget(page._seg_preview_panel)
        splitter.addWidget(page._seg_settings_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)
        splitter.setStretchFactor(3, 1)
        splitter.setSizes([260, 260, 520, 260])
        row.addWidget(splitter, 1)
        layout.addWidget(content, 1)
        page._seg_splitter = splitter

        self._seg_refresh_files(page)
        self._seg_update_preview_visibility(page)
        self._seg_update_save_btn(page)
        return page

    def _seg_default_plot(self, parquet_path) -> dict:
        """A fresh image plot config for the preview panel."""
        columns = []
        rec = self._figure_record(parquet_path)
        if rec is not None:
            columns = self._columns_for(rec.get("parquet"), None)
        channel = next((c for c in columns if c not in ("x", "y")), None)
        return {
            "type": "image",
            "file": parquet_path,
            "file_name": rec.get("name") if rec else None,
            "x": "x",
            "y": "y",
            "color_mode": "parameter",
            "color_param": channel,
            "last_color_param": channel,
            "palette": "viridis",
            "layers": (
                [{
                    "parameter": channel,
                    "color": (255, 255, 255),
                    "name": str(channel),
                    "visible": True,
                }] if channel else []
            ),
            "show_legend": True,
            "show_axes": True,
            "show_axis_names": True,
            "show_indices": True,
            "show_sample_name": True,
            "mask_mode": "shape",
            "mask_color": "random",
            "mask_alpha": 45,
        }

    def _seg_file_rows(self, page) -> list:
        return list(page._seg_params.get("files") or [])

    def _seg_file_status(self, page, parquet_path) -> str:
        """no mask / pending / applied for one file."""
        params = page._seg_params
        rec = self._seg_mask_record(params, parquet_path)
        path = self._seg_mask_file(rec)
        if path is None:
            return SEG_STATUS_NO_MASK
        saved = page._seg_applied_snapshot.get(parquet_path) if hasattr(
            page, "_seg_applied_snapshot"
        ) else None
        if saved and saved == self._seg_signature(path):
            return SEG_STATUS_APPLIED
        return SEG_STATUS_PENDING

    def _seg_any_mask(self, page) -> bool:
        for path in self._seg_file_rows(page):
            rec = self._seg_mask_record(page._seg_params, path)
            if self._seg_mask_file(rec) is not None:
                return True
        return False

    def _seg_files_panel(self, page) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        # Top: method chooser (small), bottom: files (large) — ratio 1:6.
        top = QWidget()
        tlay = QVBoxLayout(top)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.setSpacing(4)
        tlay.addWidget(QLabel("Method"))
        method_combo = _NoWheelComboBox()
        for text, value in SEG_METHODS:
            method_combo.addItem(text, value)
        idx = method_combo.findData(page._seg_params.get("method"))
        method_combo.setCurrentIndex(idx if idx >= 0 else 0)
        method_combo.setEnabled(not page._seg_locked)
        method_combo.currentIndexChanged.connect(
            lambda _=0: self._seg_on_method_changed(page)
        )
        tlay.addWidget(method_combo)
        page._seg_method_combo = method_combo

        bottom = QWidget()
        blay = QVBoxLayout(bottom)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(6)
        set_btn = QPushButton("Set Files")
        set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_btn.setEnabled(not page._seg_locked)
        set_btn.clicked.connect(lambda _=False: self._seg_set_files(page))
        blay.addWidget(set_btn)
        page._seg_setfiles_btn = set_btn

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["File", "Expose", "Status"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: none; }"
            "QTableWidget::item:selected { background: #dbeafa; }"
        )
        table.itemChanged.connect(lambda item: self._seg_expose_changed(page, item))
        blay.addWidget(table, 1)
        page._seg_files_table = table

        blay.addWidget(QLabel("Mask column"))
        name_edit = QLineEdit(str(page._seg_params.get("column") or "Mask"))
        name_edit.setEnabled(not page._seg_locked)
        name_edit.editingFinished.connect(
            lambda: self._seg_column_edited(page)
        )
        blay.addWidget(name_edit)
        page._seg_column_edit = name_edit
        hint = QLabel(
            "Checked files expose their mask as a parameter to downstream modules "
            "when this module is saved."
        )
        hint.setStyleSheet("color: #6b7785;")
        hint.setWordWrap(True)
        blay.addWidget(hint)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 6)
        split.setSizes([90, 540])
        lay.addWidget(split, 1)
        return panel

    def _seg_refresh_files(self, page) -> None:
        """Rebuild the file table and the mask-column status snapshot."""
        table = page._seg_files_table
        table.blockSignals(True)
        rows = self._seg_file_rows(page)
        table.setRowCount(len(rows))
        expose = page._seg_params.get("expose") or {}
        editable = not page._seg_locked
        for i, path in enumerate(rows):
            name_item = QTableWidgetItem(self._comp_file_name(path))
            name_item.setData(Qt.ItemDataRole.UserRole, path)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(i, 0, name_item)

            has_mask = self._seg_mask_file(
                self._seg_mask_record(page._seg_params, path)
            ) is not None
            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            chk.setCheckState(
                Qt.CheckState.Checked if expose.get(path) else Qt.CheckState.Unchecked
            )
            chk.setSelected(False)
            if not has_mask or not editable:
                chk.setFlags(Qt.ItemFlag.ItemIsSelectable)
            table.setItem(i, 1, chk)

            status = self._seg_file_status(page, path)
            sitem = QTableWidgetItem(status)
            sitem.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if status == SEG_STATUS_APPLIED:
                sitem.setForeground(QColor("#1a7f37"))
            elif status == SEG_STATUS_PENDING:
                sitem.setForeground(QColor("#b58900"))
            else:
                sitem.setForeground(QColor("#9aa0a6"))
            table.setItem(i, 2, sitem)
        table.blockSignals(False)
        self._seg_rebuild_method_panel(page)
        self._seg_refresh_plot_settings(page)
        self._seg_update_preview_visibility(page)

    def _seg_expose_changed(self, page, item) -> None:
        if page._seg_locked or item.column() != 1:
            return
        table = page._seg_files_table
        row = table.row(item)
        rows = self._seg_file_rows(page)
        if not (0 <= row < len(rows)):
            return
        path = rows[row]
        expose = page._seg_params.setdefault("expose", {})
        expose[path] = item.checkState() == Qt.CheckState.Checked
        self._seg_refresh_status(page)
        self._seg_mark_dirty(page)

    def _seg_refresh_status(self, page) -> None:
        """Update only the status cells (no widget rebuild)."""
        table = page._seg_files_table
        rows = self._seg_file_rows(page)
        table.blockSignals(True)
        for i, path in enumerate(rows):
            if i >= table.rowCount():
                break
            item = table.item(i, 2)
            if item is None:
                continue
            status = self._seg_file_status(page, path)
            item.setText(status)
            if status == SEG_STATUS_APPLIED:
                item.setForeground(QColor("#1a7f37"))
            elif status == SEG_STATUS_PENDING:
                item.setForeground(QColor("#b58900"))
            else:
                item.setForeground(QColor("#9aa0a6"))
        table.blockSignals(False)

    def _seg_is_image_file(self, rec) -> bool:
        """Whether an imported (processed) file holds pixel data.

        Processed records do not carry the original dimensions, so this checks
        the source file's extension, then any recorded image dimensions, then
        the presence of an image index (which the importer builds for TIFFs).
        """
        dims = rec.get("dimensions") or {}
        if dims.get("kind") == "image":
            return True
        source = str(rec.get("source") or "")
        if Path(source).suffix.lower() in (".tif", ".tiff"):
            return True
        parquet = rec.get("parquet")
        if parquet and self._image_load_manifest(parquet) is not None:
            return True
        return False

    def _seg_set_files(self, page) -> None:
        records = self._imported_files()
        names = [rec.get("name", "") for rec in records]
        current = [self._comp_file_name(p) for p in self._seg_file_rows(page)]
        chosen = self._pick_items_dialog(
            "Select data files", names, current, "Filter files..."
        )
        if chosen is None:
            return
        chosen_set = set(chosen)
        picks = [rec for rec in records if rec.get("name", "") in chosen_set]
        bad = [rec.get("name", "") for rec in picks if not self._seg_is_image_file(rec)]
        if bad:
            QMessageBox.warning(
                self,
                "Segmentation",
                "Only image files can be used: segmentation works on pixels and "
                "the mask must line up row by row with the image.\n\n"
                "Not image files:\n" + "\n".join(f"· {n}" for n in bad),
            )
            return
        page._seg_params["files"] = [rec.get("parquet") for rec in picks]
        self._seg_refresh_files(page)
        self._seg_mark_dirty(page)

    def _seg_column_edited(self, page) -> None:
        name = page._seg_column_edit.text().strip() or "Mask"
        page._seg_params["column"] = name
        self._seg_refresh_status(page)
        self._seg_mark_dirty(page)

    def _seg_on_method_changed(self, page) -> None:
        page._seg_params["method"] = page._seg_method_combo.currentData()
        self._seg_rebuild_method_panel(page)
        self._seg_mark_dirty(page)

    def _seg_rebuild_method_panel(self, page) -> None:
        """Swap the second panel between method params and the mask-input table."""
        host = page._seg_method_host
        self._clear_layout(host.layout())
        method = page._seg_params.get("method")
        if method == "mask_input":
            self._seg_mask_input_panel(page, host)
        elif _extension_def(method) is not None:
            self._seg_external_method_panel(page, host)
        else:
            self._seg_method_params_panel(page, host)

    def _seg_method_panel(self, page) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        host = QWidget()
        host.setLayout(QVBoxLayout())
        host.layout().setContentsMargins(0, 0, 0, 0)
        host.layout().setSpacing(6)
        lay.addWidget(host, 1)
        page._seg_method_host = host
        return panel

    def _seg_map_params(self, page) -> dict:
        method = page._seg_params.get("method") or "nuclear_expansion"
        store = page._seg_params.setdefault("method_params", {})
        params = store.setdefault(method, {})
        for key, value in SEG_DEFAULT_PARAMS.items():
            params.setdefault(key, value)
        return params

    def _seg_otsu_value(self, page, parquet_path, channel):
        """Otsu threshold of one channel (cached per file + channel)."""
        import pandas as pd

        cache = getattr(page, "_seg_otsu_cache", None)
        if cache is None:
            cache = {}
            page._seg_otsu_cache = cache
        key = (str(parquet_path), str(channel))
        if key in cache:
            return cache[key]
        try:
            df = pd.read_parquet(parquet_path, columns=[channel])
        except Exception:  # noqa: BLE001
            return None
        values = pd.to_numeric(
            df[channel], errors="coerce"
        ).to_numpy(dtype="float64")
        value = _otsu_threshold(values)
        cache[key] = value
        return value

    def _seg_method_params_panel(self, page, host) -> None:
        """Nuclear Expansion parameters + Calculate."""
        lay = host.layout()
        params = self._seg_map_params(page)
        enabled = not page._seg_locked

        lay.addWidget(QLabel("<b>Nuclear Expansion</b>"))
        lay.addWidget(QLabel("Nucleus channel"))
        columns = []
        for path in self._seg_file_rows(page):
            for name in self._columns_for(path, None):
                if name not in columns:
                    columns.append(name)
        channel_combo = _NoWheelComboBox()
        channel_combo.addItem("", None)
        for name in columns:
            if name in ("x", "y"):
                continue
            channel_combo.addItem(name, name)
        idx = channel_combo.findData(params.get("channel"))
        if idx >= 0:
            channel_combo.setCurrentIndex(idx)
        channel_combo.setEnabled(enabled)
        lay.addWidget(channel_combo)

        lay.addWidget(QLabel("Threshold"))
        mode_combo = _NoWheelComboBox()
        mode_combo.addItem("Otsu (automatic)", "otsu")
        mode_combo.addItem("Manual value", "manual")
        idx = mode_combo.findData(params.get("threshold_mode", "otsu"))
        mode_combo.setCurrentIndex(max(0, idx))
        mode_combo.setEnabled(enabled)
        lay.addWidget(mode_combo)

        def otsu_default():
            """The Otsu value of the chosen channel, used as the default."""
            channel = params.get("channel")
            rows = self._seg_file_rows(page)
            if not channel or not rows:
                return None
            return self._seg_otsu_value(page, rows[0], channel)

        initial = float(params.get("threshold", 0.0))
        if params.get("threshold_mode", "otsu") != "manual":
            otsu = otsu_default()
            if otsu is not None:
                initial = otsu
        thr_spin = _LogStepSpinBox(initial, lambda v: on_threshold(v))
        thr_spin.set_enabled(enabled)
        thr_spin.setToolTip(
            "Prefilled with the Otsu value of the chosen channel. Editing it "
            "switches to a manual threshold."
        )
        lay.addWidget(thr_spin)

        lay.addWidget(QLabel("Minimum nucleus area (px)"))
        area_spin = _NoWheelSpinBox()
        area_spin.setRange(1, 1000000)
        area_spin.setValue(int(params.get("min_area", 30)))
        area_spin.setEnabled(enabled)
        lay.addWidget(area_spin)

        lay.addWidget(QLabel("Expansion radius (px)"))
        exp_spin = _NoWheelSpinBox()
        exp_spin.setRange(0, 500)
        exp_spin.setValue(int(params.get("expand", 5)))
        exp_spin.setEnabled(enabled)
        lay.addWidget(exp_spin)

        calc_btn = QPushButton("Calculate")
        calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        calc_btn.setEnabled(enabled)
        lay.addWidget(calc_btn)
        status = QLabel("")
        status.setStyleSheet("color: #6b7785;")
        status.setWordWrap(True)
        lay.addWidget(status)
        lay.addStretch(1)
        page._seg_calc_btn = calc_btn
        page._seg_calc_status = status

        def refresh_otsu():
            if params.get("threshold_mode") == "manual":
                return
            value = otsu_default()
            if value is not None:
                thr_spin.set_value(value)

        def on_threshold(value):
            params["threshold"] = float(value)
            if params.get("threshold_mode") != "manual":
                params["threshold_mode"] = "manual"
                mode_combo.setCurrentIndex(mode_combo.findData("manual"))
            self._seg_mark_dirty(page)

        def on_channel(_=0):
            params["channel"] = channel_combo.currentData()
            refresh_otsu()
            self._seg_mark_dirty(page)

        def on_mode(_=0):
            params["threshold_mode"] = mode_combo.currentData()
            if params["threshold_mode"] == "otsu":
                refresh_otsu()
            self._seg_mark_dirty(page)

        def on_area(value):
            params["min_area"] = int(value)
            self._seg_mark_dirty(page)

        def on_expand(value):
            params["expand"] = int(value)
            self._seg_mark_dirty(page)

        channel_combo.currentIndexChanged.connect(on_channel)
        mode_combo.currentIndexChanged.connect(on_mode)
        area_spin.valueChanged.connect(on_area)
        exp_spin.valueChanged.connect(on_expand)
        calc_btn.clicked.connect(lambda _=False: self._seg_calculate(page))

    def _seg_channel_columns(self, page) -> list:
        """Union of non-coordinate columns across the selected files."""
        columns = []
        for path in self._seg_file_rows(page):
            for name in self._columns_for(path, None):
                if name in ("x", "y"):
                    continue
                if name not in columns:
                    columns.append(name)
        return columns

    def _seg_external_method_panel(self, page, host) -> None:
        """Cellpose / StarDist params: extension status, channel, Calculate."""
        method = page._seg_params.get("method")
        entry = _extension_def(method)
        lay = host.layout()
        params = self._seg_map_params(page)
        enabled = not page._seg_locked
        ready = self._ext_ready(method)

        lay.addWidget(QLabel(f"<b>{entry['name']}</b>"))
        lay.addWidget(QLabel(entry["desc"]))
        lay.addWidget(QLabel("Runs in an external Python environment."))

        banner = QLabel()
        banner.setWordWrap(True)
        if ready:
            banner.setText("Extension ready ✓")
            banner.setStyleSheet("color: #1a7f37; font-weight: bold;")
        else:
            banner.setText(
                f"{entry['name']} extension is not configured.\n"
                "Open Extension → Manage Extension to set it up first."
            )
            banner.setStyleSheet("color: #b58900; font-weight: bold;")
        lay.addWidget(banner)

        def param_label(title, desc=""):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addWidget(QLabel(title))
            if desc:
                d = QLabel(desc)
                d.setStyleSheet("color: #6b7785;")
                h.addWidget(d)
            h.addStretch()
            return row

        lay.addWidget(QLabel("Files to calculate"))
        files_list = QListWidget()
        files_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        files_list.setMaximumHeight(110)
        for path in self._seg_file_rows(page):
            item = QListWidgetItem(self._comp_file_name(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            has_mask = (
                self._seg_mask_file(self._seg_mask_record(page._seg_params, path))
                is not None
            )
            item.setCheckState(
                Qt.CheckState.Unchecked if has_mask else Qt.CheckState.Checked
            )
            files_list.addItem(item)
        lay.addWidget(files_list)
        page._seg_calc_files_list = files_list

        lay.addWidget(param_label("Nucleus channel", "nuclear stain channel"))
        columns = self._seg_channel_columns(page)
        channel_combo = _NoWheelComboBox()
        channel_combo.addItem("", None)
        for name in columns:
            channel_combo.addItem(name, name)
        idx = channel_combo.findData(params.get("channel"))
        if idx >= 0:
            channel_combo.setCurrentIndex(idx)
        channel_combo.setEnabled(enabled)
        lay.addWidget(channel_combo)

        cyto_combo = None
        if entry["family"] == "cellpose":
            lay.addWidget(param_label(
                "Cytoplasm channel (optional)", "optional second channel"
            ))
            cyto_combo = _NoWheelComboBox()
            cyto_combo.addItem("(none)", None)
            for name in columns:
                cyto_combo.addItem(name, name)
            idx = cyto_combo.findData(params.get("cyto_channel"))
            if idx >= 0:
                cyto_combo.setCurrentIndex(idx)
            cyto_combo.setEnabled(enabled)
            lay.addWidget(cyto_combo)

        prob_spin = None
        nms_spin = None
        tile_size_spin = None
        if entry["family"] == "stardist":
            params.setdefault("model", entry["model"])
            params.setdefault("prob", 0.5)
            params.setdefault("nms", 0.3)
            params.setdefault("tile_size", 0)
            lay.addWidget(param_label("Probability threshold", "lower detects more cells"))
            prob_spin = _NoWheelDoubleSpinBox()
            prob_spin.setRange(0.01, 1.0)
            prob_spin.setSingleStep(0.05)
            prob_spin.setDecimals(2)
            prob_spin.setValue(float(params.get("prob", 0.5)))
            prob_spin.setEnabled(enabled)
            lay.addWidget(prob_spin)

            lay.addWidget(param_label("NMS threshold", "overlap suppression"))
            nms_spin = _NoWheelDoubleSpinBox()
            nms_spin.setRange(0.0, 1.0)
            nms_spin.setSingleStep(0.05)
            nms_spin.setDecimals(2)
            nms_spin.setValue(float(params.get("nms", 0.3)))
            nms_spin.setEnabled(enabled)
            lay.addWidget(nms_spin)

            lay.addWidget(param_label(
                "Tile size (px)", "approximate tile edge; 0 = auto"
            ))
            tile_size_spin = _NoWheelSpinBox()
            tile_size_spin.setRange(0, 20000)
            tile_size_spin.setSingleStep(64)
            tile_size_spin.setValue(int(params.get("tile_size", 0)))
            tile_size_spin.setEnabled(enabled)
            tile_size_spin.setToolTip(
                "Approximate edge length of each tile in pixels. Smage splits "
                "each image into roughly this-sized tiles (n × m), so the "
                "actual counts adapt to the image dimensions. 0 lets StarDist "
                "pick a good size automatically."
            )
            lay.addWidget(tile_size_spin)

        calc_btn = QPushButton("Calculate")
        calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        calc_btn.setEnabled(enabled and ready)
        lay.addWidget(calc_btn)
        status = QLabel("")
        status.setStyleSheet("color: #6b7785;")
        status.setWordWrap(True)
        lay.addWidget(status)
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setPlaceholderText("Calculation output appears here…")
        log.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; "
            "font-family: Consolas, monospace; font-size: 9pt;"
        )
        for line in getattr(page, "_seg_log_lines", []):
            log.appendPlainText(line)
        lay.addWidget(log, 1)
        page._seg_calc_btn = calc_btn
        page._seg_calc_status = status
        page._seg_calc_log = log

        def on_channel(_=0):
            params["channel"] = channel_combo.currentData()
            self._seg_mark_dirty(page)

        def on_cyto(_=0):
            params["cyto_channel"] = cyto_combo.currentData()
            self._seg_mark_dirty(page)

        def on_prob(value):
            params["prob"] = float(value)
            self._seg_mark_dirty(page)

        def on_nms(value):
            params["nms"] = float(value)
            self._seg_mark_dirty(page)

        def on_tile_size(value):
            params["tile_size"] = int(value)
            self._seg_mark_dirty(page)

        channel_combo.currentIndexChanged.connect(on_channel)
        if cyto_combo is not None:
            cyto_combo.currentIndexChanged.connect(on_cyto)
        if prob_spin is not None:
            prob_spin.valueChanged.connect(on_prob)
        if nms_spin is not None:
            nms_spin.valueChanged.connect(on_nms)
        if tile_size_spin is not None:
            tile_size_spin.valueChanged.connect(on_tile_size)
        calc_btn.clicked.connect(lambda _=False: self._seg_calculate(page))

    def _seg_mask_input_panel(self, page, host) -> None:
        """Mask Input: one row per file with a mask-file picker + Import."""
        lay = host.layout()
        lay.addWidget(QLabel("<b>Mask Input</b>"))
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["File", "Mask file", "Status"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: none; }"
        )
        lay.addWidget(table, 1)
        page._seg_input_table = table

        import_btn = QPushButton("Import")
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_btn.setEnabled(not page._seg_locked)
        lay.addWidget(import_btn)
        hint = QLabel(
            "Masks must be TIFF images whose width and height match the data file."
        )
        hint.setStyleSheet("color: #6b7785;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        status = QLabel("")
        status.setStyleSheet("color: #6b7785;")
        status.setWordWrap(True)
        lay.addWidget(status)
        page._seg_import_status = status
        import_btn.clicked.connect(lambda _=False: self._seg_import_masks(page))

        self._seg_refresh_input_table(page)

    def _seg_mask_record_live(self, page, parquet_path, create=False) -> dict:
        masks = page._seg_params.setdefault("masks", {})
        rec = masks.get(parquet_path)
        if not isinstance(rec, dict):
            if not create:
                return {}
            rec = {"path": None, "source": None, "input": None,
                   "state": SEG_IMPORT_SELECT}
            masks[parquet_path] = rec
        return rec

    def _seg_refresh_input_table(self, page) -> None:
        table = getattr(page, "_seg_input_table", None)
        if table is None:
            return
        rows = self._seg_file_rows(page)
        table.blockSignals(True)
        table.setRowCount(len(rows))
        enabled = not page._seg_locked
        for i, path in enumerate(rows):
            name_item = QTableWidgetItem(self._comp_file_name(path))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(i, 0, name_item)

            rec = self._seg_mask_record(page._seg_params, path) or {}
            btn = QPushButton(Path(str(rec.get("input") or "")).name or "…")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(enabled)
            btn.setToolTip(rec.get("input") or "Choose a mask file")
            btn.clicked.connect(
                lambda _=False, p=path: self._seg_pick_mask_file(page, p)
            )
            table.setCellWidget(i, 1, btn)

            state = rec.get("state") or (
                SEG_IMPORT_DONE if self._seg_mask_file(rec) else SEG_IMPORT_SELECT
            )
            sitem = QTableWidgetItem(state)
            sitem.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if state == SEG_IMPORT_DONE:
                sitem.setForeground(QColor("#1a7f37"))
            elif state in (SEG_IMPORT_FORMAT, SEG_IMPORT_SIZE, SEG_IMPORT_UNKNOWN):
                sitem.setForeground(QColor("#c0392b"))
            else:
                sitem.setForeground(QColor("#9aa0a6"))
            table.setItem(i, 2, sitem)
        table.blockSignals(False)

    def _seg_pick_mask_file(self, page, parquet_path) -> None:
        rec = self._seg_mask_record_live(page, parquet_path, create=True)
        already = self._seg_mask_file(rec) is not None
        if already:
            reply = QMessageBox.question(
                self,
                "Choose mask file",
                "This file's mask has already been imported.\n"
                "Choosing another one will delete the imported mask. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        start_dir = getattr(self, "_seg_last_mask_dir", None) or str(Path.home())
        if rec.get("input"):
            start_dir = str(Path(str(rec["input"])).parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select mask file", start_dir, "All files (*)"
        )
        if not path:
            return
        self._seg_last_mask_dir = str(Path(path).parent)
        if already:
            self._seg_delete_mask(page, parquet_path)
        rec["input"] = path
        rec["state"] = SEG_IMPORT_WAITING
        rec["path"] = rec.get("path") if rec.get("source") == "builtin" else None
        self._seg_refresh_input_table(page)
        self._seg_refresh_files(page)
        self._seg_mark_dirty(page)

    def _seg_delete_mask(self, page, parquet_path) -> None:
        """Remove a file's mask .npy and its record."""
        rec = self._seg_mask_record(page._seg_params, parquet_path) or {}
        rel = rec.get("path")
        if rel and self.project is not None:
            try:
                (self.project.path / rel).unlink(missing_ok=True)
            except OSError:
                pass
        page._seg_mask_cache = {}
        self._seg_value_cache = {}

    def _seg_import_masks(self, page) -> None:
        import numpy as np
        import tifffile

        rows = self._seg_file_rows(page)
        if not rows:
            QMessageBox.information(
                self, "Import", "Select data files first."
            )
            return
        pending = []
        for path in rows:
            rec = self._seg_mask_record_live(page, path, create=True)
            if rec.get("state") == SEG_IMPORT_DONE and self._seg_mask_file(rec):
                continue
            if not rec.get("input"):
                continue
            pending.append((path, rec))
        if not pending:
            QMessageBox.information(
                self, "Import",
                "All files have already been imported — nothing new to import.",
            )
            return

        done = 0
        errors = []
        for parquet_path, rec in pending:
            ent = self._ensure_image_index(parquet_path)
            if ent is None or ent.get("manifest") is None:
                rec["state"] = SEG_IMPORT_UNKNOWN
                errors.append(f"{self._comp_file_name(parquet_path)}: no image index")
                continue
            man = ent["manifest"]
            height = int(round(man["ymax"] - man["ymin"])) + 1
            width = int(round(man["xmax"] - man["xmin"])) + 1
            src = str(rec.get("input") or "")
            suffix = Path(src).suffix.lower()
            if suffix not in (".tif", ".tiff", ".ome.tif", ".ome.tiff"):
                rec["state"] = SEG_IMPORT_FORMAT
                continue
            try:
                arr = tifffile.imread(src)
            except Exception:  # noqa: BLE001
                rec["state"] = SEG_IMPORT_FORMAT
                continue
            arr = np.asarray(arr)
            while arr.ndim > 2:
                arr = arr[0]
            if arr.ndim != 2:
                rec["state"] = SEG_IMPORT_FORMAT
                continue
            if arr.shape[0] != height or arr.shape[1] != width:
                rec["state"] = SEG_IMPORT_SIZE
                continue
            try:
                rel = self._seg_write_mask(page._seg_task_id, parquet_path, arr)
            except Exception:  # noqa: BLE001
                rec["state"] = SEG_IMPORT_UNKNOWN
                continue
            rec["path"] = rel
            rec["source"] = "import"
            rec["state"] = SEG_IMPORT_DONE
            done += 1

        self._seg_after_masks_changed(page)
        self._seg_refresh_input_table(page)
        self._seg_refresh_files(page)
        msg = f"Imported {done} mask(s)."
        if errors:
            msg += "\n" + "\n".join(errors)
        page._seg_import_status.setText(msg)
        self.statusBar().showMessage(msg.splitlines()[0])

    def _seg_write_mask(self, task_id, parquet_path, array) -> str:
        """Save a mask array under <project>/Module/<task-id>/; return rel path."""
        import numpy as np

        if self.project is None:
            raise RuntimeError("no project")
        stem = Path(str(parquet_path)).stem
        rel = f"Module/{task_id}/{stem}.npy"
        path = self.project.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, np.asarray(array, dtype=np.int32))
        self._seg_mask_cache = {}
        self._seg_value_cache = {}
        return rel

    def _seg_after_masks_changed(self, page) -> None:
        """Persist mask records: masks are products, so save right away."""
        self._seg_mask_cache = {}
        self._seg_value_cache = {}
        page._seg_task_def["output"] = {"saved": True}
        page._seg_task_def["params"] = page._seg_params
        if self.project is not None:
            self.project.save()
        page._seg_snapshot = copy.deepcopy(page._seg_params)
        page._seg_dirty = False
        self._seg_update_save_btn(page)
        self._refresh_workflow_tree()
        self._module_saved(page)

    def _seg_calculate(self, page) -> None:
        """Run the selected method on every selected file."""
        method = page._seg_params.get("method")
        if _extension_def(method) is not None:
            self._seg_calculate_external(page)
            return
        self._seg_calculate_nuclear(page)

    def _seg_calculate_nuclear(self, page) -> None:
        """Run Nuclear Expansion on every selected file."""
        rows = self._seg_file_rows(page)
        params = self._seg_map_params(page)
        status = getattr(page, "_seg_calc_status", None)
        if not rows:
            QMessageBox.information(self, "Calculate", "Select data files first.")
            return
        if not params.get("channel"):
            QMessageBox.information(
                self, "Calculate", "Choose the nucleus channel first."
            )
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        done = 0
        cells = 0
        errors = []
        try:
            for i, parquet_path in enumerate(rows):
                if status is not None:
                    status.setText(
                        f"Calculating {i + 1}/{len(rows)}: "
                        f"{self._comp_file_name(parquet_path)}…"
                    )
                    QApplication.processEvents()
                mask, err = self._seg_nuclear_expansion(parquet_path, params)
                if mask is None:
                    errors.append(f"{self._comp_file_name(parquet_path)}: {err}")
                    continue
                try:
                    rel = self._seg_write_mask(page._seg_task_id, parquet_path, mask)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{self._comp_file_name(parquet_path)}: {exc}")
                    continue
                rec = self._seg_mask_record_live(page, parquet_path, create=True)
                rec["path"] = rel
                rec["source"] = "builtin"
                rec["state"] = SEG_IMPORT_DONE
                done += 1
                cells += int(mask.max())
        finally:
            QApplication.restoreOverrideCursor()

        self._seg_after_masks_changed(page)
        self._seg_refresh_files(page)
        # The refresh rebuilds the parameter panel, so re-read its status label.
        status = getattr(page, "_seg_calc_status", status)
        if status is not None:
            msg = f"Calculated {done} mask(s) · {cells} cell(s)."
            if errors:
                msg += "\n" + "\n".join(errors)
            status.setText(msg)

    def _seg_raster_stack(self, parquet_path, channels):
        """Reconstruct a (C, H, W) float stack of the given channels."""
        import numpy as np

        images = []
        shape = None
        for channel in channels:
            image, err = self._seg_raster_channel(parquet_path, channel)
            if image is None:
                return None, err
            if shape is None:
                shape = image.shape
            elif image.shape != shape:
                return None, "channel shapes differ"
            images.append(image)
        return np.stack(images, axis=0).astype("float32"), None

    def _seg_calculate_external(self, page) -> None:
        """Run Cellpose / StarDist on the selected files in one subprocess."""
        import json
        import tempfile

        import numpy as np

        method = page._seg_params.get("method")
        entry = _extension_def(method)
        params = self._seg_map_params(page)
        files_list = getattr(page, "_seg_calc_files_list", None)
        if files_list is not None:
            rows = []
            for i in range(files_list.count()):
                item = files_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    rows.append(item.data(Qt.ItemDataRole.UserRole))
        else:
            rows = self._seg_file_rows(page)
        status = getattr(page, "_seg_calc_status", None)

        if not rows:
            QMessageBox.information(
                self, "Calculate",
                "Select at least one file in the list to calculate.",
            )
            return
        if not params.get("channel"):
            QMessageBox.information(
                self, "Calculate", "Choose the nucleus channel first."
            )
            return
        if not self._ext_ready(method):
            QMessageBox.information(
                self,
                "Calculate",
                f"{entry['name']} extension is not configured.\n"
                "Open Extension → Manage Extension to set it up first.",
            )
            return

        python = self._ext_python(method)
        runner = str(self._ext_runner_path(method))
        tmpdir = Path(tempfile.mkdtemp(prefix="smage-seg-"))
        jobs = []
        manifest = []
        nucleus = params.get("channel")
        cyto = params.get("cyto_channel") if entry["family"] == "cellpose" else None
        # Reconstructing the channel image happens in the main app, before the
        # external subprocess starts, so log it (this is the silent phase).
        self._seg_log(page, f"Rasterizing {len(rows)} file(s)…")
        for i, parquet_path in enumerate(rows):
            self._seg_log(
                page, f"  rasterizing {self._comp_file_name(parquet_path)}…"
            )
            if cyto:
                stack, err = self._seg_raster_stack(parquet_path, [cyto, nucleus])
                nucleus_idx, cyto_idx = 1, 0
            else:
                stack, err = self._seg_raster_stack(parquet_path, [nucleus])
                nucleus_idx, cyto_idx = 0, None
            if stack is None:
                continue
            image_npy = tmpdir / f"in_{i}.npy"
            output_npy = tmpdir / f"out_{i}.npy"
            np.save(image_npy, stack)
            manifest.append({"image": str(image_npy), "output": str(output_npy)})
            jobs.append({"parquet": parquet_path, "output": str(output_npy)})

        if not jobs:
            return

        manifest_path = tmpdir / "jobs.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        argv = [
            python, runner, "segment",
            "--manifest", str(manifest_path),
            "--nucleus", str(nucleus_idx),
            "--model", entry["model"],
            "--device", entry["device"],
        ]
        if cyto_idx is not None:
            argv += ["--cyto", str(cyto_idx)]
        if entry["family"] == "stardist":
            argv += [
                "--prob", str(params.get("prob", 0.5)),
                "--nms", str(params.get("nms", 0.3)),
                "--tile-size", str(params.get("tile_size", 0)),
            ]

        page._seg_ext_jobs = jobs
        page._seg_ext_tmpdir = tmpdir
        page._seg_ext_done = set()
        page._seg_ext_errors = []
        page._seg_ext_cells = 0
        page._seg_ext_report = []
        page._seg_ext_tile = None
        page._seg_ext_activity = ""
        page._seg_ext_finished = False
        page._seg_ext_running = True
        page._seg_ext_start_time = time.monotonic()
        # Keep appending to the existing log so each run's history is preserved.
        self._seg_log(
            page,
            f"===== {entry['name']} run — {len(jobs)} file(s) =====",
        )
        worker = self._ext_worker()
        worker.clear()  # drop any stale results from earlier operations
        # Stop any previous run's poll timer so it can't steal this run's events.
        old_timer = getattr(page, "_seg_ext_timer", None)
        if old_timer is not None:
            old_timer.stop()
        worker.run(("seg-batch",), argv)
        if status is not None:
            status.setText(f"Running {entry['name']} on {len(jobs)} file(s)…")

        timer = QTimer(self)
        timer.setInterval(150)
        timer.timeout.connect(lambda: self._seg_ext_poll(page))
        timer.start()
        page._seg_ext_timer = timer

    def _seg_log(self, page, text) -> None:
        """Append a timestamped line to the segmentation calculation log."""
        ts = datetime.now().strftime("%H:%M:%S")
        lines = getattr(page, "_seg_log_lines", None)
        if lines is None:
            lines = []
            page._seg_log_lines = lines
        for line in text.splitlines():
            if line.strip():
                lines.append(f"[{ts}] {line}")
        box = getattr(page, "_seg_calc_log", None)
        if box is not None:
            for line in text.splitlines():
                if line.strip():
                    box.appendPlainText(f"[{ts}] {line}")
        page._seg_log_last_is_tile = False

    def _seg_log_tile(self, page, i, total) -> None:
        """Update the in-progress tile line in the log (replace in place)."""
        ts = datetime.now().strftime("%H:%M:%S")
        text = f"[{ts}] tile {i}/{total}"
        lines = getattr(page, "_seg_log_lines", None)
        if lines is None:
            lines = []
            page._seg_log_lines = lines
        if getattr(page, "_seg_log_last_is_tile", False) and lines:
            lines[-1] = text
        else:
            lines.append(text)
        box = getattr(page, "_seg_calc_log", None)
        if box is not None:
            if getattr(page, "_seg_log_last_is_tile", False):
                cursor = box.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.insertText(text)
            else:
                box.appendPlainText(text)
        page._seg_log_last_is_tile = True

    def _seg_ext_poll(self, page) -> None:
        """Collect finished external segmentations and finalize the masks."""
        import shutil

        import numpy as np

        worker = self._ext_worker()
        status = getattr(page, "_seg_calc_status", None)
        jobs = getattr(page, "_seg_ext_jobs", None) or []
        done = getattr(page, "_seg_ext_done", None) or set()
        errors = getattr(page, "_seg_ext_errors", [])
        cells = getattr(page, "_seg_ext_cells", 0)
        report = getattr(page, "_seg_ext_report", None) or []
        tile = getattr(page, "_seg_ext_tile", None)
        activity = getattr(page, "_seg_ext_activity", "") or ""
        finished = getattr(page, "_seg_ext_finished", False)
        out_to_job = {job["output"]: job for job in jobs}

        for item in worker.poll():
            if item[1] == "line":
                line = item[2]
                if line.startswith("tile: "):
                    info = line[len("tile: "):].strip()
                    if "/" in info:
                        try:
                            cur, tot = info.split("/", 1)
                            tile = (int(cur), int(tot))
                            self._seg_log_tile(page, int(cur), int(tot))
                        except ValueError:
                            pass
                elif line.startswith("done:"):
                    body = line[len("done:"):].strip()
                    out_path, sep, meta = body.partition("|")
                    out_path = out_path.strip()
                    n_cells = 0
                    seconds = 0.0
                    for part in meta.split("|"):
                        if part.startswith("cells="):
                            try:
                                n_cells = int(part[len("cells="):])
                            except ValueError:
                                pass
                        elif part.startswith("time="):
                            try:
                                seconds = float(part[len("time="):])
                            except ValueError:
                                pass
                    job = out_to_job.get(out_path)
                    if job is None or out_path in done:
                        continue
                    parquet_path = job["parquet"]
                    try:
                        arr = np.load(out_path)
                        if arr.ndim != 2:
                            raise ValueError("bad mask shape")
                        rel = self._seg_write_mask(
                            page._seg_task_id, parquet_path, arr
                        )
                        rec = self._seg_mask_record_live(
                            page, parquet_path, create=True
                        )
                        rec["path"] = rel
                        rec["source"] = "builtin"
                        rec["state"] = SEG_IMPORT_DONE
                        cells += int(arr.max())
                        report.append(
                            f"{self._comp_file_name(parquet_path)}: "
                            f"{n_cells} cells, {seconds:.1f}s"
                        )
                        self._seg_log(
                            page,
                            f"✓ {self._comp_file_name(parquet_path)}: "
                            f"{n_cells} cells, {seconds:.1f}s",
                        )
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{self._comp_file_name(parquet_path)}: {exc}")
                        self._seg_log(page, f"✗ {self._comp_file_name(parquet_path)}: {exc}")
                    done.add(out_path)
                else:
                    # Non-progress output (e.g. TensorFlow/model loading lines):
                    # remember the latest for the status, and record it in the
                    # scrollable log so the user can see and copy the details.
                    if line.strip():
                        activity = line.strip()[-120:]
                        self._seg_log(page, line)
                continue
            if len(item) != 4 or item[1] != "done":
                continue
            key, _tag, code, text = item[0], item[1], item[2], item[3]
            if not (isinstance(key, tuple) and key[0] == "seg-batch"):
                continue
            finished = True
            for job in jobs:
                if job["output"] in done:
                    continue
                if code != 0:
                    errors.append(
                        f"{self._comp_file_name(job['parquet'])}: "
                        f"{text.strip()[-200:]}"
                    )
                else:
                    errors.append(
                        f"{self._comp_file_name(job['parquet'])}: no output produced"
                    )
                done.add(job["output"])
            if text and code != 0:
                self._seg_log(page, text.rstrip())

        page._seg_ext_done = done
        page._seg_ext_errors = errors
        page._seg_ext_cells = cells
        page._seg_ext_report = report
        page._seg_ext_tile = tile
        page._seg_ext_activity = activity
        page._seg_ext_finished = finished
        if finished:
            page._seg_ext_running = False

        if not finished:
            if status is not None:
                start = getattr(page, "_seg_ext_start_time", None)
                elapsed = int(time.monotonic() - start) if start else 0
                minutes, seconds = divmod(elapsed, 60)
                elapsed_str = (
                    f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
                )
                stamp = f"[{datetime.now().strftime('%H:%M:%S')} · {elapsed_str}]"
                msg = f"{stamp} Running… {len(done)}/{len(jobs)} file(s) done"
                if tile is not None:
                    msg += f" — tile {tile[0]}/{tile[1]}"
                elif activity:
                    msg += f" — {activity}"
                status.setText(msg)
            return

        timer = getattr(page, "_seg_ext_timer", None)
        if timer is not None:
            timer.stop()
        shutil.rmtree(page._seg_ext_tmpdir, ignore_errors=True)
        ok_count = len(jobs) - len(errors)
        start = getattr(page, "_seg_ext_start_time", None)
        elapsed = int(time.monotonic() - start) if start else 0
        minutes, seconds = divmod(elapsed, 60)
        elapsed_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        self._seg_log(
            page,
            f"Calculated {ok_count} mask(s) · {cells} cell(s) in {elapsed_str}.",
        )
        for err in errors:
            self._seg_log(page, f"✗ {err}")
        self._seg_after_masks_changed(page)
        self._seg_refresh_files(page)
        status = getattr(page, "_seg_calc_status", None)
        if status is not None:
            msg = f"Calculated {ok_count} mask(s) · {cells} cell(s) in {elapsed_str}."
            if report:
                msg += "\n" + "\n".join(report)
            if errors:
                msg += "\n" + "\n".join(errors)
            status.setText(msg)

    def _seg_raster_channel(self, parquet_path, channel):
        """Reconstruct one channel as a 2-D float image (or (None, error))."""
        import numpy as np
        import pandas as pd

        ent = self._ensure_image_index(parquet_path)
        if ent is None or ent.get("manifest") is None:
            return None, "no image index — re-import this file as an image"
        man = ent["manifest"]
        try:
            df = pd.read_parquet(parquet_path, columns=["x", "y", channel])
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
        xmin = float(man["xmin"])
        ymin = float(man["ymin"])
        width = int(round(float(man["xmax"]) - xmin)) + 1
        height = int(round(float(man["ymax"]) - ymin)) + 1
        xi = np.rint(df["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        yi = np.rint(df["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        values = pd.to_numeric(df[channel], errors="coerce").to_numpy(dtype="float64")
        image = np.zeros((height, width), dtype="float64")
        ok = (
            (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
            & np.isfinite(values)
        )
        image[yi[ok], xi[ok]] = values[ok]
        if values[ok].size == 0:
            return None, "no finite pixel values"
        return image, None

    def _seg_nuclear_expansion(self, parquet_path, params):
        """Threshold → label → filter → expand. Returns (mask, error)."""
        import numpy as np
        from scipy import ndimage

        channel = params.get("channel")
        if not channel:
            return None, "no nucleus channel"
        image, err = self._seg_raster_channel(parquet_path, channel)
        if image is None:
            return None, err

        if params.get("threshold_mode") == "manual":
            threshold = float(params.get("threshold", 0.0))
        else:
            threshold = _otsu_threshold(image)
        binary = image > threshold
        if not binary.any():
            return None, "threshold produced an empty mask"
        binary = ndimage.binary_opening(binary, structure=np.ones((3, 3)))
        labels, _count = ndimage.label(binary)
        labels = _relabel_sequential(labels, int(params.get("min_area", 1)))
        expand = int(params.get("expand", 0))
        if expand > 0 and labels.any():
            distance, (iy, ix) = ndimage.distance_transform_edt(
                labels == 0, return_indices=True
            )
            grown = distance <= expand
            labels = np.where(grown, labels[iy, ix], labels)
        return labels.astype(np.int32), None

    def _seg_mark_dirty(self, page) -> None:
        page._seg_dirty = True
        self._seg_update_save_btn(page)
        self._module_set_state(page, MODULE_STATE_MODIFIED)

    def _seg_update_save_btn(self, page) -> None:
        page._seg_save_btn.setEnabled(page._seg_dirty and not page._seg_locked)

    def _seg_save_now(self, page) -> None:
        params = page._seg_params
        column = (params.get("column") or "Mask").strip() or "Mask"
        expose = params.get("expose") or {}
        # The mask column must not collide with a real column name of a file
        # that is exposed (checked) in this module — unchecked files don't
        # publish a mask column, so their names don't matter here.
        taken = set()
        for path in self._seg_file_rows(page):
            if not expose.get(path):
                continue
            for name in self._column_index.get(self._rel_data_key(path), []):
                taken.add(name)
        if column in taken:
            QMessageBox.warning(
                self,
                "Mask column",
                f'"{column}" is already a column of the selected files.\n'
                "Choose another name.",
            )
            return
        params["column"] = column
        applied = {}
        for path in self._seg_file_rows(page):
            if not expose.get(path):
                continue
            rec = self._seg_mask_record(params, path)
            mask_path = self._seg_mask_file(rec)
            if mask_path is None:
                continue
            applied[path] = self._seg_signature(mask_path)
        params["applied"] = applied
        page._seg_applied_snapshot = dict(applied)
        page._seg_task_def["params"] = params
        if self.project is not None:
            self.project.save()
        page._seg_snapshot = copy.deepcopy(params)
        page._seg_dirty = False
        self._seg_update_save_btn(page)
        self._seg_refresh_status(page)
        self._seg_value_cache = {}
        self.statusBar().showMessage("Segmentation saved")
        self._module_saved(page)

    def _seg_close(self, page) -> None:
        if getattr(page, "_seg_ext_running", False):
            reply = QMessageBox.question(
                self,
                "Close Segmentation",
                "A calculation is still running.\n"
                "Closing the module now will interrupt it. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._ext_worker().kill()
        if page._seg_dirty:
            reply = QMessageBox.question(
                self,
                "Close Segmentation",
                "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            page._seg_task_def["params"] = page._seg_snapshot
        for name in ("_seg_overlay_timer", "_seg_ext_timer"):
            timer = getattr(page, name, None)
            if timer is not None:
                timer.stop()
        self._close_module_page(page)

    # --- segmentation preview (part 3) and plot settings (part 4)
    def _seg_preview_panel(self, page) -> QWidget:
        import pyqtgraph as pg

        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Preview</b>"))
        body = QWidget()
        bh = QHBoxLayout(body)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(4)
        plot = pg.PlotWidget(background="w")
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.getPlotItem().setAxisItems({
            "bottom": _make_sci_axis("bottom"),
            "left": _make_sci_axis("left"),
        })
        bh.addWidget(plot, 1)
        legend = self._figure_attach_legend(plot)
        bh.addWidget(legend, 0)
        lay.addWidget(body, 1)
        page._seg_preview_plot = plot
        # Re-render both the image (LOD raster for the new view) and the mask
        # overlay when the user zooms or pans.
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(30)
        timer.timeout.connect(lambda: self._seg_render_preview(page))
        page._seg_overlay_timer = timer
        plot.getPlotItem().getViewBox().sigRangeChanged.connect(
            lambda *_: timer.start()
        )
        return panel

    def _seg_plot_settings_panel(self, page) -> QWidget:
        panel = self._build_panel_frame()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Plot settings</b>"))
        host = QWidget()
        host.setLayout(QVBoxLayout())
        host.layout().setContentsMargins(0, 0, 0, 0)
        host.layout().setSpacing(6)
        lay.addWidget(host, 1)
        page._seg_settings_host = host
        return panel

    def _seg_plot_dict(self, page) -> dict:
        """The preview's plot config, defaulted for the current files."""
        plot_dict = page._seg_params.setdefault("plot", {})
        rows = self._seg_file_rows(page)
        if not rows:
            return plot_dict
        if plot_dict.get("file") not in rows:
            fresh = self._seg_default_plot(rows[0])
            fresh.update({k: v for k, v in plot_dict.items() if k != "file"})
            plot_dict.clear()
            plot_dict.update(fresh)
        if not plot_dict.get("type"):
            plot_dict["type"] = "image"
        if not plot_dict.get("layers") and plot_dict.get("color_param"):
            channel = plot_dict["color_param"]
            plot_dict["layers"] = [{
                "parameter": channel,
                "color": (255, 255, 255),
                "name": str(channel),
                "visible": True,
            }]
        plot_dict.setdefault("show_legend", True)
        return plot_dict

    def _seg_refresh_plot_settings(self, page) -> None:
        """Rebuild part 4 (file/channel/mask options) from the current files."""
        host = getattr(page, "_seg_settings_host", None)
        if host is None:
            return
        self._clear_layout(host.layout())
        lay = host.layout()
        enabled = not page._seg_locked
        plot_dict = self._seg_plot_dict(page)
        rows = [p for p in self._seg_file_rows(page)
                if self._seg_mask_file(self._seg_mask_record(page._seg_params, p))]

        lay.addWidget(QLabel("File"))
        file_combo = _NoWheelComboBox()
        for path in rows:
            file_combo.addItem(self._comp_file_name(path), path)
        idx = file_combo.findData(plot_dict.get("file"))
        if idx >= 0:
            file_combo.setCurrentIndex(idx)
        file_combo.setEnabled(enabled and bool(rows))
        lay.addWidget(file_combo)

        columns = []
        rec = self._figure_record(plot_dict.get("file"))
        if rec is not None:
            columns = self._columns_for(rec.get("parquet"), None)

        self._figure_image_layer_settings(
            lay, plot_dict, columns, enabled,
            on_change=lambda: self._seg_render_preview(page),
            on_rebuild=lambda: (
                self._seg_refresh_plot_settings(page),
                self._seg_render_preview(page),
            ),
        )

        lay.addWidget(QLabel("Mask display"))
        mode_combo = _NoWheelComboBox()
        mode_combo.addItem("Don't show mask", "none")
        mode_combo.addItem("Show mask as shape", "shape")
        mode_combo.addItem("Show mask edges", "edges")
        idx = mode_combo.findData(plot_dict.get("mask_mode", "shape"))
        mode_combo.setCurrentIndex(max(0, idx))
        mode_combo.setEnabled(enabled)
        lay.addWidget(mode_combo)

        color_mode_combo = None
        alpha_spin = None
        if plot_dict.get("mask_mode", "shape") != "none":
            lay.addWidget(QLabel("Mask colors"))
            color_mode_combo = _NoWheelComboBox()
            color_mode_combo.addItem("Random colors", "random")
            color_mode_combo.addItem("Grayscale", "gray")
            idx = color_mode_combo.findData(plot_dict.get("mask_color", "random"))
            color_mode_combo.setCurrentIndex(max(0, idx))
            color_mode_combo.setEnabled(enabled)
            lay.addWidget(color_mode_combo)

            lay.addWidget(QLabel("Opacity (%)"))
            alpha_spin = _NoWheelSpinBox()
            alpha_spin.setRange(5, 100)
            alpha_spin.setValue(int(plot_dict.get("mask_alpha", 45)))
            alpha_spin.setEnabled(enabled)
            lay.addWidget(alpha_spin)

        self._figure_display_toggles(
            lay, plot_dict, enabled,
            on_change=lambda: self._seg_render_preview(page),
        )
        lay.addStretch(1)

        def on_file(_=0):
            path = file_combo.currentData()
            if not path:
                return
            plot_dict["file"] = path
            plot_dict["file_name"] = file_combo.currentText()
            self._seg_refresh_plot_settings(page)
            self._seg_render_preview(page)
            self._seg_mark_dirty(page)

        def on_mode(_=0):
            plot_dict["mask_mode"] = mode_combo.currentData()
            self._seg_refresh_plot_settings(page)
            self._seg_render_preview(page)
            self._seg_mark_dirty(page)

        def on_color_mode(_=0):
            plot_dict["mask_color"] = color_mode_combo.currentData()
            self._seg_render_overlay(page)
            self._seg_mark_dirty(page)

        def on_alpha(value):
            plot_dict["mask_alpha"] = int(value)
            self._seg_render_overlay(page)
            self._seg_mark_dirty(page)

        file_combo.currentIndexChanged.connect(on_file)
        mode_combo.currentIndexChanged.connect(on_mode)
        if color_mode_combo is not None:
            color_mode_combo.currentIndexChanged.connect(on_color_mode)
        if alpha_spin is not None:
            alpha_spin.valueChanged.connect(on_alpha)

    def _seg_update_preview_visibility(self, page) -> None:
        """Parts 3 and 4 appear once at least one file has a mask."""
        has_mask = self._seg_any_mask(page)
        panel3 = getattr(page, "_seg_preview_panel", None)
        panel4 = getattr(page, "_seg_settings_panel", None)
        for panel in (panel3, panel4):
            if panel is not None:
                panel.setVisible(has_mask)
        if has_mask:
            self._seg_render_preview(page)

    def _seg_render_preview(self, page) -> None:
        plot = getattr(page, "_seg_preview_plot", None)
        if plot is None:
            return
        plot_dict = self._seg_plot_dict(page)
        rec = self._figure_record(plot_dict.get("file"))
        if rec is None:
            return
        self._figure_render(plot_dict, plot, task_id=page._seg_task_id)
        self._seg_render_overlay(page)

    def _seg_render_overlay(self, page) -> None:
        """Draw the mask overlay on top of the preview image."""
        import numpy as np
        import pyqtgraph as pg

        plot = getattr(page, "_seg_preview_plot", None)
        if plot is None:
            return
        plot_dict = self._seg_plot_dict(page)
        old = getattr(page, "_seg_overlay_item", None)
        if old is not None:
            try:
                plot.removeItem(old)
            except Exception:  # noqa: BLE001
                pass
            page._seg_overlay_item = None
        mode = plot_dict.get("mask_mode", "shape")
        if mode == "none":
            return
        parquet_path = plot_dict.get("file")
        rec = self._seg_mask_record(page._seg_params, parquet_path)
        mask_path = self._seg_mask_file(rec)
        if mask_path is None:
            return
        ent = self._ensure_image_index(parquet_path)
        if ent is None or ent.get("manifest") is None:
            return
        man = ent["manifest"]
        vb = plot.getPlotItem().getViewBox()
        xr = vb.viewRange()[0]
        yr = vb.viewRange()[1]
        x0, x1 = min(xr), max(xr)
        y0, y1 = min(yr), max(yr)
        if x1 <= x0 or y1 <= y0:
            return
        xmin, ymin = float(man["xmin"]), float(man["ymin"])
        i0 = max(0, int(np.floor(x0 - xmin)))
        i1 = min(int(round(float(man["xmax"]) - xmin)) + 1, int(np.ceil(x1 - xmin)))
        j0 = max(0, int(np.floor(y0 - ymin)))
        j1 = min(int(round(float(man["ymax"]) - ymin)) + 1, int(np.ceil(y1 - ymin)))
        if i1 <= i0 or j1 <= j0:
            return
        mask = self._seg_load_mask(mask_path)
        if mask is None:
            return
        i1 = min(i1, mask.shape[1])
        j1 = min(j1, mask.shape[0])
        step = max(1, int(round((x1 - x0) / max(1, plot.width()))))
        crop = mask[j0:j1:step, i0:i1:step].astype(np.int64)
        if crop.size == 0:
            return
        max_label = int(crop.max())
        if max_label <= 0:
            return
        # Per-label colors: grayscale or a stable random palette.
        if plot_dict.get("mask_color") == "random":
            rng = np.random.default_rng(20240521)
            lut = rng.integers(60, 256, size=(max_label + 1, 3))
            lut[0] = 0
        else:
            ramp = np.linspace(0.35, 1.0, max(1, max_label + 1))
            lut = np.stack(
                [ramp * 255.0, ramp * 255.0, ramp * 255.0], axis=1
            ).astype(np.int64)
        if mode == "edges":
            edges_full = self._seg_edge_map(mask_path)
            if edges_full is None:
                return
            edge = edges_full[j0:j1:step, i0:i1:step]
            neighbor = np.zeros_like(crop)
            neighbor[:-1, :] = np.maximum(neighbor[:-1, :], crop[1:, :])
            neighbor[1:, :] = np.maximum(neighbor[1:, :], crop[:-1, :])
            neighbor[:, :-1] = np.maximum(neighbor[:, :-1], crop[:, 1:])
            neighbor[:, 1:] = np.maximum(neighbor[:, 1:], crop[:, :-1])
            label_for_color = np.where(crop > 0, crop, neighbor)
            alpha = np.where(edge, 255, 0)
        else:
            label_for_color = crop
            alpha = np.where(crop > 0, 255, 0)
        opacity = max(0, min(100, int(plot_dict.get("mask_alpha", 45))))
        rgba = np.zeros((crop.shape[0], crop.shape[1], 4), dtype=np.uint8)
        clipped = np.clip(label_for_color, 0, max_label)
        rgba[..., 0:3] = lut[clipped]
        rgba[..., 3] = (alpha * opacity / 100.0).astype(np.uint8)
        item = pg.ImageItem(axisOrder="row-major")
        item.setImage(rgba, autoLevels=False)
        item.setRect(QRectF(
            xmin + i0 * step, ymin + j0 * step,
            crop.shape[1] * step, crop.shape[0] * step,
        ))
        item.setZValue(10)
        plot.addItem(item)
        page._seg_overlay_item = item

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
        self._gating_state_init(page, task_def, locked)
        page._gating_save_btn = save_btn
        save_btn.clicked.connect(lambda _=False: self._gating_save_now(page))
        self._gating_update_save_btn(page)

        layout.addWidget(self._gating_build_content(page, locked), 1)
        return page

    def _gating_state_init(self, page, task_def: dict, locked: bool) -> None:
        """Initialize the ``_gating_*`` state attributes on `page`.

        `page` is either a module page (a QWidget) or a `_GatingState` bag, so
        the same methods can drive several gate trees in the Unmix module.
        """
        records = self._imported_files()
        rec = records[0] if records else None
        cols = self._columns_for(
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
        page._gating_preview = preview
        page._gating_task_def = task_def
        page._gating_task_id = task_def.get("id")
        page._gating_gates = params.setdefault("gates", [])
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
        page._gating_stat_timer = QTimer(self)
        page._gating_stat_timer.setInterval(100)
        page._gating_stat_timer.timeout.connect(
            lambda: self._gating_on_stats_timer(page)
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
        page._gating_snapshot = copy.deepcopy(params)

    def _gating_build_content(self, page, locked: bool) -> QWidget:
        """Build the gate tree | scatter | plot settings pane for `page`."""
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
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tree.setStyleSheet(
            "QTreeWidget { background: #ffffff; border: none; }"
            "QTreeWidget::item:selected { background: #dbeafa; }"
        )
        tree_lay.addWidget(tree, 1)
        page._gating_tree = tree
        tree.itemCollapsed.connect(
            lambda item: self._gating_on_collapse(page, item)
        )
        tree.itemExpanded.connect(
            lambda item: self._gating_on_expand(page, item)
        )

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
        cl.addWidget(mid_panel, 2)
        cl.addWidget(settings_panel, 1)

        tree.itemClicked.connect(lambda item, col=0: self._gating_on_tree_selection(page, item, col))
        tree.itemDoubleClicked.connect(lambda item, col=0: self._gating_on_tree_double_clicked(page, item, col))
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self._gating_tree_context_menu(page, pos))
        self._gating_load_rois(page)
        self._gating_rebuild_tree(page)
        self._gating_render(page)
        self._gating_rebuild_settings(page)
        return content

    def _gating_make_plot(self, page):
        """Create the gating scatter plot (sci axes + A/F buttons + gate ViewBox)."""
        viewbox = _GateViewBox(
            on_gate_created=lambda tool, roi: self._gating_add_gate(page, tool, roi),
            on_empty_click=lambda: self._gating_deselect(page),
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

    def _gating_on_roi_drag(self, page, gate, roi) -> None:
        self._gating_sync_gate(gate, roi)
        self._gating_refresh_tree_stats(page, affected={gate["id"]})
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

    def _gating_quadrant_sync(self, page, crosshair_id, key, line) -> None:
        affected = set()
        for g in page._gating_gates:
            if g.get("crosshair_id") == crosshair_id:
                g[key] = line.value()
                affected.add(g["id"])
        self._gating_refresh_tree_stats(page, affected=affected)
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

    def _gating_split_sync(self, page, split_id, line) -> None:
        affected = set()
        for g in page._gating_gates:
            if g.get("split_id") == split_id:
                g["vx"] = line.value()
                affected.add(g["id"])
        self._gating_refresh_tree_stats(page, affected=affected)
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

    def _gating_range_sync(self, page, gate, key, line) -> None:
        gate[key] = line.value()
        self._gating_refresh_tree_stats(page, affected={gate["id"]})
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

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
        affected = set()
        for g in page._gating_gates:
            if g.get("skewed_id") == skewed_id:
                g["center"] = [center.x(), center.y()]
                for i, key in enumerate(keys):
                    sp = pts[i + 1]
                    g[key] = [sp.x(), sp.y()]
                affected.add(g["id"])
        self._gating_refresh_tree_stats(page, affected=affected)
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

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
            page._gating_context_gate_id = page._gating_gates[-1]["id"]
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
            self._gating_setup_line(page, vline1, gate["id"])
            self._gating_setup_line(page, vline2, gate["id"])
            vline1.sigPositionChangeFinished.connect(
                lambda l=vline1, g=gate: self._gating_range_sync(page, g, "x1", l)
            )
            vline2.sigPositionChangeFinished.connect(
                lambda l=vline2, g=gate: self._gating_range_sync(page, g, "x2", l)
            )
            page._gating_gates.append(gate)
            page._gating_rois.append([vline1, vline2])
            self._gating_set_items_handles_visible([vline1, vline2], False)
            page._gating_context_gate_id = page._gating_gates[-1]["id"]
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
            page._gating_context_gate_id = page._gating_gates[-1]["id"]
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
            roi.setAcceptHoverEvents(True)
            roi.setCursor(Qt.CursorShape.CrossCursor)
            roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
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
            roi.sigClicked.connect(
                lambda *a: self._gating_select_gate_by_id(
                    page, page._gating_gates[-1]["id"]
                )
            )
            self._gating_set_items_handles_visible([roi], False)
            page._gating_context_gate_id = page._gating_gates[-1]["id"]
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
        arg.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        arg.setAcceptHoverEvents(True)
        arg.setCursor(Qt.CursorShape.CrossCursor)
        arg.sigRegionChangeFinished.connect(
            lambda r=arg, g=gate: self._gating_on_roi_drag(page, g, r)
        )
        arg.sigClicked.connect(
            lambda roi, ev, g=gate: self._gating_gate_clicked(page, g["id"], ev)
        )
        page._gating_gates.append(gate)
        page._gating_rois.append([arg])
        self._gating_set_items_handles_visible([arg], False)
        page._gating_context_gate_id = gate["id"]
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
                # Lines have no separate handles; keep them movable/hoverable.
                continue
            else:
                for h in item.handles:
                    h["item"].setVisible(visible)

    def _gating_show_handles(self, page, idx) -> None:
        """Show handles for the gate at `idx`, hiding every other gate's."""
        for i, gate_items in enumerate(page._gating_rois):
            self._gating_set_items_handles_visible(gate_items, i == idx)
        selected_skewed = None
        if 0 <= idx < len(page._gating_gates):
            selected_skewed = page._gating_gates[idx].get("skewed_id")
        for sid, entry in page._gating_skewed_lines.items():
            self._gating_set_items_handles_visible(
                [entry["roi"]], sid == selected_skewed
            )

    def _gating_setup_line(self, page, line, gate_id) -> None:
        """Give an InfiniteLine a crosshair cursor and click-to-select."""
        line.setCursor(Qt.CursorShape.CrossCursor)
        line.sigClicked.connect(
            lambda *a, gid=gate_id: self._gating_select_gate_by_id(page, gid)
        )

    def _gating_select_gate_by_id(self, page, gate_id) -> None:
        """Select a gate by clicking it in the plot (full selection + tree sync)."""
        idx = -1
        for i, g in enumerate(page._gating_gates):
            if g["id"] == gate_id:
                idx = i
                break
        if idx < 0:
            return
        self._gating_apply_selection(page, idx)
        tree = page._gating_tree
        tree.blockSignals(True)
        tree.clearSelection()
        item = self._gating_tree_item_for_index(page, idx)
        if item is not None:
            item.setSelected(True)
            tree.setCurrentItem(item)
        tree.blockSignals(False)
        self._gating_render(page)
        self._gating_rebuild_settings(page)

    def _gating_deselect(self, page) -> None:
        """Deselect all gates (click on empty plot space), without changing the view."""
        self._gating_show_handles(page, -1)
        tree = page._gating_tree
        tree.blockSignals(True)
        tree.clearSelection()
        tree.blockSignals(False)

    def _gating_open_gate_subset(self, page, gate_id) -> None:
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
            if gate.get("x"):
                preview["x"] = gate["x"]
            if gate.get("y"):
                preview["y"] = gate["y"]
        self._gating_render(page)
        self._gating_rebuild_settings(page)
        tree = page._gating_tree
        tree.blockSignals(True)
        tree.clearSelection()
        item = self._gating_tree_item_for_index(page, idx)
        if item is not None:
            item.setSelected(True)
            tree.setCurrentItem(item)
        tree.blockSignals(False)

    def _gating_gate_clicked(self, page, gate_id, ev) -> None:
        """Handle a click on a gate ROI: double-click opens the subset view."""
        if ev is not None and ev.double():
            self._gating_open_gate_subset(page, gate_id)
        else:
            self._gating_select_gate_by_id(page, gate_id)

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
        """Return the set of gate ids to show for the current view mode."""
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
        # "All Events" view: no gate is bound to it, so show nothing.
        return set()

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
        self._gating_update_labels(page)

    def _gating_set_label_style(self, page, label) -> None:
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

    def _gating_apply_label_style(self, page) -> None:
        for label in page._gating_labels:
            self._gating_set_label_style(page, label)

    def _gating_new_label(self, page) -> _GateLabel:
        label = _GateLabel(
            on_drag_finished=lambda l: self._gating_on_label_dragged(page, l),
            text="", color="#000000", anchor=(0.5, 0.5),
        )
        self._gating_set_label_style(page, label)
        label.setAcceptHoverEvents(True)
        label.setCursor(Qt.CursorShape.CrossCursor)
        label.setZValue(10)
        page._gating_viewbox.addItem(label, ignoreBounds=True)
        label.setVisible(False)
        return label

    def _gating_on_label_dragged(self, page, label) -> None:
        """Store the user's label offset (relative to the gate's default spot)."""
        labels = page._gating_labels
        if label not in labels:
            return
        i = labels.index(label)
        if i >= len(page._gating_gates):
            return
        gate = page._gating_gates[i]
        dx, dy = _gate_label_position(gate)
        pos = label.pos()
        gate["label_offset"] = [pos.x() - dx, pos.y() - dy]
        self._gating_mark_dirty(page)

    def _gating_update_labels(self, page) -> None:
        """Reconcile and refresh the per-gate name labels on the scatter."""
        labels = page._gating_labels
        gates = page._gating_gates
        while len(labels) < len(gates):
            labels.append(self._gating_new_label(page))
        while len(labels) > len(gates):
            item = labels.pop()
            try:
                page._gating_viewbox.removeItem(item)
            except Exception:
                pass
        x = page._gating_preview.get("x")
        y = page._gating_preview.get("y")
        show_labels = page._gating_preview.get("show_gate_labels", True)
        visible_ids = self._gating_visible_ids(page)
        show_percent = page._gating_preview.get("label_show_percent", True)
        for i, gate in enumerate(gates):
            label = labels[i]
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
            px, py = _gate_label_position(gate)
            ox, oy = gate.get("label_offset") or [0.0, 0.0]
            label.setPos(px + ox, py + oy)
            xy_ok = gate.get("x") == x and gate.get("y") == y
            hier_ok = gate.get("id") in visible_ids
            label.setVisible(show_labels and xy_ok and hier_ok)

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
                    roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                    roi.setAcceptHoverEvents(True)
                    roi.setCursor(Qt.CursorShape.CrossCursor)
                    roi.sigRegionChangeFinished.connect(
                        lambda r=roi, g=gate: self._gating_on_roi_drag(page, g, r)
                    )
                    roi.sigClicked.connect(
                        lambda roi, ev, g=gate: self._gating_gate_clicked(page, g["id"], ev)
                    )
                    items = [roi]
                elif t == "ellipse":
                    roi = pg.EllipseROI(
                        (gate["pos"][0], gate["pos"][1]),
                        (gate["size"][0], gate["size"][1]),
                        **_gate_roi_pens(),
                    )
                    self._gating_style_handles(roi)
                    roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                    roi.setAcceptHoverEvents(True)
                    roi.setCursor(Qt.CursorShape.CrossCursor)
                    roi.sigRegionChangeFinished.connect(
                        lambda r=roi, g=gate: self._gating_on_roi_drag(page, g, r)
                    )
                    roi.sigClicked.connect(
                        lambda roi, ev, g=gate: self._gating_gate_clicked(page, g["id"], ev)
                    )
                    items = [roi]
                elif t == "polygon":
                    roi = _GatePolygonROI(
                        [tuple(p) for p in gate["points"]],
                        closed=True, **_gate_roi_pens(),
                    )
                    self._gating_style_handles(roi)
                    roi.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
                    roi.setAcceptHoverEvents(True)
                    roi.setCursor(Qt.CursorShape.CrossCursor)
                    roi.sigRegionChangeFinished.connect(
                        lambda r=roi, g=gate: self._gating_on_roi_drag(page, g, r)
                    )
                    roi.sigClicked.connect(
                        lambda roi, ev, g=gate: self._gating_gate_clicked(page, g["id"], ev)
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
                    self._gating_setup_line(page, vline1, gate["id"])
                    self._gating_setup_line(page, vline2, gate["id"])
                    vline1.sigPositionChangeFinished.connect(
                        lambda l=vline1, g=gate: self._gating_range_sync(page, g, "x1", l)
                    )
                    vline2.sigPositionChangeFinished.connect(
                        lambda l=vline2, g=gate: self._gating_range_sync(page, g, "x2", l)
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
                            lambda c=sid: self._gating_skewed_sync(page, c)
                        )
                        roi.sigClicked.connect(
                            lambda *a: self._gating_select_gate_by_id(page, gate["id"])
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
                self,
                "Delete gate",
                "This gate is the source of a spectrum.\n"
                'Use "Re-set as spectrum..." to move the source to another '
                "gate before deleting it.",
            )
            return
        if not cascade:
            # Keep children; re-parent them to this gate's parent.
            for g in page._gating_gates:
                if g.get("parent") in to_delete:
                    g["parent"] = parent_id

        indices = [i for i, g in enumerate(page._gating_gates) if g["id"] in to_delete]
        for i in reversed(indices):
            for item in page._gating_rois[i]:
                page._gating_viewbox.removeItem(item)
            if i < len(page._gating_labels):
                label = page._gating_labels.pop(i)
                try:
                    page._gating_viewbox.removeItem(label)
                except Exception:
                    pass
            del page._gating_gates[i]
            del page._gating_rois[i]
        self._gating_prune_crosshairs(page)
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
        by_id = {g["id"]: g for g in page._gating_gates}
        needed = set()
        for g in page._gating_gates:
            needed |= _gate_needed_columns(g, by_id)
        values = self._column_data(
            page._gating_task_id, parquet_path, list(needed)
        )
        try:
            return _compute_gate_filter_mask(
                page._gating_gates, gate_id, parquet_path, {},
                existing=set(values), extra=values,
            )
        except Exception:
            return None

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

        # Unmix extends this menu with "Set as spectrum" (and re-set).
        extra_menu = getattr(page, "_gating_spectrum_menu", None)
        if extra_menu is not None:
            extra_menu(menu, page, idx)

        # Boolean gating.
        bool_menu = QMenu("Boolean gating", menu)
        not_act = QAction("NOT", bool_menu)
        not_act.triggered.connect(lambda _=False: self._gating_add_not_gate(page, idx))
        bool_menu.addAction(not_act)
        and_act = QAction("AND", bool_menu)
        and_act.triggered.connect(
            lambda _=False: self._gating_add_boolean_gate(page, idx, "and")
        )
        bool_menu.addAction(and_act)
        or_act = QAction("OR", bool_menu)
        or_act.triggered.connect(
            lambda _=False: self._gating_add_boolean_gate(page, idx, "or")
        )
        bool_menu.addAction(or_act)
        menu.addMenu(bool_menu)

        # A gate that is a spectrum's source (Unmix) cannot be deleted.
        protected = getattr(page, "_gating_protected_gate_id", None)
        is_protected = bool(protected) and page._gating_gates[idx]["id"] == protected

        del_one = QAction("Delete this gate", menu)
        del_one.setEnabled(not is_protected)
        del_one.triggered.connect(lambda: self._gating_delete_gate(page, idx, cascade=False))
        menu.addAction(del_one)
        del_all = QAction("Delete gate and all children", menu)
        del_all.setEnabled(not is_protected)
        del_all.triggered.connect(lambda: self._gating_delete_gate(page, idx, cascade=True))
        menu.addAction(del_all)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _gating_apply_selection(self, page, idx) -> None:
        """Apply a selection: `idx` is a gate index, or -1 for 'All Events'."""
        preview = page._gating_preview
        page._gating_active_gate_id = None
        if isinstance(idx, int) and 0 <= idx < len(page._gating_gates):
            gate = page._gating_gates[idx]
            page._gating_context_gate_id = gate["id"]
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
            preview["x"] = page._gating_settings_x
            preview["y"] = page._gating_settings_y
            preview["xmin"] = None
            preview["xmax"] = None
            preview["ymin"] = None
            preview["ymax"] = None
        self._gating_show_handles(page, idx if isinstance(idx, int) and idx >= 0 else -1)

    def _gating_tree_item_for_index(self, page, idx):
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

    def _gating_on_tree_selection(self, page, item, column) -> None:
        """Single-click: open the gate's drawing context (axes + range + parent points)."""
        idx = item.data(0, Qt.ItemDataRole.UserRole) if item else -1
        idx = idx if isinstance(idx, int) and idx >= 0 else -1
        self._gating_apply_selection(page, idx)
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
            self._gating_update_labels(page)
            self._gating_mark_dirty(page)

    def _gating_add_not_gate(self, page, idx) -> None:
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
        self._gating_rebuild_tree(page)
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

    def _gating_add_boolean_gate(self, page, idx, op) -> None:
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
                self, f"{op.upper()} gate",
                "This gate has no sibling gates at the same level.",
            )
            return

        dialog = QDialog(self)
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
        self._gating_rebuild_tree(page)
        self._gating_update_labels(page)
        self._gating_mark_dirty(page)

    def _gating_request_stats(self, page, affected=None) -> None:
        """Submit a background statistics recomputation (incremental if affected)."""
        worker = getattr(page, "_gating_stats_worker", None)
        if worker is None:
            return
        parquet_path = page._gating_preview.get("file")
        if not parquet_path or not page._gating_gates:
            page._gating_last_stats = {}
            return
        gates = copy.deepcopy(page._gating_gates)
        stages = self._map_stages(page._gating_task_id)
        base_columns = set(
            self._column_index.get(self._rel_data_key(parquet_path), [])
        )
        page._gating_stat_generation += 1
        # Mask columns exposed by an upstream Segmentation module are not in
        # the data file; resolve them here and hand them to the worker.
        virtual = {}
        for name, mask_path in self._seg_applied_columns(
            page._gating_task_id, parquet_path
        ).items():
            arr = self._seg_mask_values(parquet_path, mask_path)
            if arr is not None:
                virtual[name] = arr
        worker.request(
            page._gating_stat_generation,
            parquet_path, gates, stages, base_columns, affected, virtual,
        )

    def _gating_apply_stats(self, page, stats) -> None:
        """Update the gate tree labels with a fresh stats dict."""
        page._gating_last_stats = stats
        tree = page._gating_tree
        tree.blockSignals(True)

        def update(item):
            for i in range(item.childCount()):
                child = item.child(i)
                idx = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(idx, int) and 0 <= idx < len(page._gating_gates):
                    gate = page._gating_gates[idx]
                    child.setText(
                        0, self._gating_gate_display_name(gate, idx, stats)
                    )
                update(child)

        for i in range(tree.topLevelItemCount()):
            update(tree.topLevelItem(i))
        tree.blockSignals(False)
        tree.resizeColumnToContents(0)
        self._gating_update_labels(page)

    def _gating_on_stats_timer(self, page) -> None:
        """Poll the worker and apply the latest non-stale result."""
        if not hasattr(page, "_gating_tree"):
            return
        worker = getattr(page, "_gating_stats_worker", None)
        if worker is None:
            return
        for generation, stats in worker.poll():
            if generation == page._gating_stat_generation:
                self._gating_apply_stats(page, stats)

    @staticmethod
    def _gating_gate_display_name(gate, idx, stats) -> str:
        name = gate.get("name", f"Gate {idx + 1}")
        st = stats.get(gate["id"])
        if st is None:
            return name
        count, pct = st
        return f"{name} ({pct:.2f}%, {count})"

    def _gating_refresh_tree_stats(self, page, affected=None) -> None:
        """Refresh gate count/percent labels (background, incremental if affected)."""
        self._gating_request_stats(page, affected=affected)

    def _gating_on_collapse(self, page, item) -> None:
        gid = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if gid:
            page._gating_collapsed.add(gid)

    def _gating_on_expand(self, page, item) -> None:
        gid = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if gid:
            page._gating_collapsed.discard(gid)

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

        # Show last-known stats immediately; a background recompute follows.
        stats = page._gating_last_stats

        def add_children(parent_item, parent_id):
            for idx, gate in by_parent.get(parent_id, []):
                item = QTreeWidgetItem(
                    [self._gating_gate_display_name(gate, idx, stats)]
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
            self._gating_set_items_handles_visible(gate_items, False)
        for entry in page._gating_skewed_lines.values():
            self._gating_set_items_handles_visible([entry["roi"]], False)
        self._gating_refresh_tree_stats(page)

    def _gating_render(self, page) -> None:
        """Render the gating scatter for the current view mode."""
        transforms = {}
        rec = self._figure_record(page._gating_preview.get("file"))
        preview = page._gating_preview

        # Show a gray overlay when the current file lacks x/y parameters.
        cols = self._columns_for(
            rec.get("parquet"), page._gating_task_id
        ) if rec else []
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
            task_id=page._gating_task_id,
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
        columns = self._columns_for(
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
        layout.addWidget(self._file_nav_row(file_combo, enabled))

        layout.addWidget(QLabel("X parameter"))
        x_combo = self._gating_param_combo(columns, preview.get("x"), enabled)
        layout.addWidget(x_combo)

        layout.addWidget(QLabel("Y parameter"))
        y_combo = self._gating_param_combo(columns, preview.get("y"), enabled)
        layout.addWidget(y_combo)

        self._figure_scatter_color_settings(
            layout, preview, plot_widget, columns, enabled,
            on_change=lambda: self._gating_on_preview_change(page),
            on_rebuild=lambda: (
                self._gating_rebuild_settings(page),
                self._gating_on_preview_change(page),
            ),
        )

        layout.addWidget(QLabel("X range"))
        layout.addWidget(self._figure_range_row(
            preview, "x", plot_widget, enabled,
            on_commit=lambda: self._gating_mark_dirty(page),
        ))
        layout.addWidget(QLabel("Y range"))
        layout.addWidget(self._figure_range_row(
            preview, "y", plot_widget, enabled,
            on_commit=lambda: self._gating_mark_dirty(page),
        ))

        self._figure_display_toggles(
            layout, preview, enabled,
            on_change=lambda: self._gating_on_preview_change(page),
        )

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
            self._gating_update_labels(page)
            self._gating_apply_label_style(page)
            self._gating_mark_dirty(page)

        show_labels_cb.toggled.connect(lambda _=0: on_label_style_change())
        show_percent_cb.toggled.connect(lambda _=0: on_label_style_change())
        font_spin.valueChanged.connect(lambda _=0: on_label_style_change())
        bold_cb.toggled.connect(lambda _=0: on_label_style_change())
        opacity_spin.valueChanged.connect(lambda _=0: on_label_style_change())

        layout.addStretch()

        def on_file_changed():
            preview["file"] = file_combo.currentData()
            preview["file_name"] = file_combo.currentText()
            page._gating_active_gate_id = None
            page._gating_context_gate_id = None
            page._gating_parent_id = None
            # Keep the last-used x/y; blank them if this file lacks them.
            new_rec = self._figure_record(preview["file"])
            new_cols = self._columns_for(
                new_rec.get("parquet"), page._gating_task_id
            ) if new_rec else []
            preview["x"] = (
                preview.get("last_x") if preview.get("last_x") in new_cols else None
            )
            preview["y"] = (
                preview.get("last_y") if preview.get("last_y") in new_cols else None
            )
            # Keep the visible range when switching files.
            vb = page._gating_viewbox
            xr, yr = vb.viewRange()
            self._gating_render(page)
            vb.setXRange(xr[0], xr[1], padding=0)
            vb.setYRange(yr[0], yr[1], padding=0)
            self._gating_refresh_tree_stats(page)
            self._gating_mark_dirty(page)
            self._gating_rebuild_settings(page)
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
                # Keep the active subset / context; only the display axes change.
                self._gating_render(page)
                self._gating_mark_dirty(page)
            return handler
        x_combo.currentTextChanged.connect(on_param("x"))
        y_combo.currentTextChanged.connect(on_param("y"))

    def _gating_on_preview_change(self, page) -> None:
        """Re-render and mark dirty after a scatter setting change."""
        self._gating_render(page)
        self._gating_mark_dirty(page)

    def _gating_mark_dirty(self, page) -> None:
        page._gating_dirty = True
        self._gating_update_save_btn(page)
        # `page` may be a _GatingState embedded in another module (Unmix).
        self._module_set_state(getattr(page, "_module_page", page), MODULE_STATE_MODIFIED)

    def _gating_save_now(self, page) -> None:
        params = page._gating_task_def.setdefault("params", {})
        params["gates"] = page._gating_gates
        params["preview"] = page._gating_preview
        if self.project is not None:
            self.project.save()
        page._gating_snapshot = copy.deepcopy(page._gating_task_def.get("params", {}))
        page._gating_dirty = False
        self._gating_update_save_btn(page)
        self.statusBar().showMessage("Gating saved")
        self._module_saved(page)

    def _gating_update_save_btn(self, page) -> None:
        btn = getattr(page, "_gating_save_btn", None)
        if btn is not None:
            btn.setEnabled(page._gating_dirty and not page._gating_locked)
        hook = getattr(page, "_gating_on_dirty", None)
        if hook is not None:
            hook()

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
        page._gating_last_stats = {}

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
        elif task_type == "unmix":
            page = self._build_unmix_page(name, task_def, locked)
        elif task_type == "compensation":
            page = self._build_compensation_page(name, task_def, locked)
        elif task_type == "illumination":
            page = self._build_illumination_page(name, task_def, locked)
        elif task_type == "segmentation":
            page = self._build_segmentation_page(name, task_def, locked)
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
