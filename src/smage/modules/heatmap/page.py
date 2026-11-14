"""Heatmap module: aggregated matrix with dendrograms and export."""
from __future__ import annotations

from PySide6.QtCore import (
    QPointF,
    QRect,
    QSize,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from pathlib import Path
from smage.core.constants import (
    MODULE_STATE_MODIFIED,
    _FEATURE_LOG_LIMIT,
)
from smage.core.formatting import (
    _heatmap_cell_text,
    _heatmap_export_text,
)
from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_chain,
    _gate_needed_columns,
)
from smage.core.heatmap import (
    _heatmap_fill_nan,
    _heatmap_linkage,
    _heatmap_normalize,
    _heatmap_sample_counts,
)
from smage.core.sampling import _stable_sample_selection
from smage.core.settings import _default_palette
from smage.runlog import runlog
from smage.widgets.colormap import _figure_colormap
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelSpinBox,
)
import copy
import hashlib
import json
import queue
import threading
from functools import partial
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.data import sampling as _data_sampling
from smage.data import stores as _data_stores
from smage.widgets import colormap as _widgets_colormap
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


_HEATMAP_STORE_NAME = "heatmap.json"

class _HeatmapCanvas(QWidget):
    """Paints the heatmap matrix with optional dendrograms and axis labels."""

    ROW_DENDRO_WIDTH = 90
    COL_DENDRO_HEIGHT = 70
    COL_LABEL_HEIGHT = 130
    LABEL_GAP = 6
    COLORBAR_WIDTH = 14
    COLORBAR_GAP = 24
    COLORBAR_LABEL_WIDTH = 66
    MARGIN = 10
    NA_COLOR = "#d9d9d9"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows = []
        self._cols = []
        self._values = None
        self._row_dendro = None
        self._col_dendro = None
        self._cfg = {}
        self._zoom = 1.0
        self.setMouseTracking(True)
        self.setMinimumSize(240, 160)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

    # -- content -----------------------------------------------------------
    def set_content(self, rows, cols, values, row_dendro, col_dendro, cfg) -> None:
        self._rows = list(rows or [])
        self._cols = list(cols or [])
        self._values = values
        self._row_dendro = row_dendro
        self._col_dendro = col_dendro
        self._cfg = dict(cfg or {})
        self.updateGeometry()
        self.update()

    def clear(self, message: str = "") -> None:
        self._rows = []
        self._cols = []
        self._values = None
        self._row_dendro = None
        self._col_dendro = None
        self._cfg = {"message": message}
        self.updateGeometry()
        self.update()

    # -- geometry ----------------------------------------------------------
    def _layout(self):
        import numpy as np

        values = self._values
        if values is None or not self._rows or not self._cols:
            return None
        cfg = self._cfg
        zoom = min(6.0, max(0.2, float(getattr(self, "_zoom", 1.0) or 1.0)))
        cell_w = max(4, int(round(int(cfg.get("cell_width", 42)) * zoom)))
        cell_h = max(4, int(round(int(cfg.get("cell_height", 28)) * zoom)))
        metrics = QFontMetrics(self.font())
        row_label_w = min(
            260,
            max(
                (metrics.horizontalAdvance(str(r)) for r in self._rows),
                default=0,
            ) + 10,
        )
        try:
            row_dendro_size = max(20, int(cfg.get("dendro_width", self.ROW_DENDRO_WIDTH)))
        except (TypeError, ValueError):
            row_dendro_size = self.ROW_DENDRO_WIDTH
        try:
            col_dendro_size = max(20, int(cfg.get("dendro_height", self.COL_DENDRO_HEIGHT)))
        except (TypeError, ValueError):
            col_dendro_size = self.COL_DENDRO_HEIGHT
        row_dendro_w = row_dendro_size if self._row_dendro else 0
        col_dendro_h = col_dendro_size if self._col_dendro else 0
        # Row labels stay left of the grid; the row dendrogram sits right of it.
        grid_x = self.MARGIN + row_label_w
        grid_y = self.MARGIN + col_dendro_h
        grid_w = len(self._cols) * cell_w
        grid_h = len(self._rows) * cell_h
        dendro_x = grid_x + grid_w
        colorbar_x = dendro_x + row_dendro_w + self.COLORBAR_GAP
        width = (
            colorbar_x + self.COLORBAR_WIDTH + self.COLORBAR_LABEL_WIDTH
            + self.MARGIN
        )
        height = (
            grid_y + grid_h + self.COL_LABEL_HEIGHT + self.MARGIN
        )
        vmin, vmax = self._range(values)
        return {
            "cell_w": cell_w, "cell_h": cell_h,
            "row_label_w": row_label_w, "row_dendro_w": row_dendro_w,
            "col_dendro_h": col_dendro_h,
            "grid_x": grid_x, "grid_y": grid_y,
            "grid_w": grid_w, "grid_h": grid_h,
            "dendro_x": dendro_x, "colorbar_x": colorbar_x,
            "width": width, "height": height,
            "vmin": vmin, "vmax": vmax,
        }

    def _range(self, values):
        import numpy as np

        cfg = self._cfg
        if not cfg.get("auto_range", True):
            try:
                lo = float(cfg.get("vmin"))
                hi = float(cfg.get("vmax"))
            except (TypeError, ValueError):
                lo = hi = None
            if lo is not None and hi is not None and hi > lo:
                return lo, hi
        finite = np.asarray(values, dtype="float64")
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            return 0.0, 1.0
        lo = float(finite.min())
        hi = float(finite.max())
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def sizeHint(self):
        layout = self._layout()
        if layout is None:
            return QSize(280, 200)
        return QSize(layout["width"], layout["height"])

    def minimumSizeHint(self):
        layout = self._layout()
        if layout is None:
            return QSize(240, 160)
        return QSize(min(layout["width"], 240), min(layout["height"], 160))

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        import numpy as np

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        layout = self._layout()
        if layout is None:
            painter.setPen(QColor("#8a94a6"))
            message = self._cfg.get("message") or (
                "Press Calculate to build the heatmap."
            )
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, message
            )
            return

        values = np.asarray(self._values, dtype="float64")
        cfg = self._cfg
        cell_w = layout["cell_w"]
        cell_h = layout["cell_h"]
        grid_x = layout["grid_x"]
        grid_y = layout["grid_y"]
        vmin = layout["vmin"]
        vmax = layout["vmax"]
        show_numbers = bool(cfg.get("show_numbers", True))
        auto_color = bool(cfg.get("auto_text_color", True))
        fixed_color = QColor(cfg.get("text_color") or "#000000")
        try:
            decimals = max(0, min(6, int(cfg.get("decimals", 1))))
        except (TypeError, ValueError):
            decimals = 1

        cmap = _figure_colormap(cfg.get("colormap") or _default_palette())
        lut = [
            tuple(int(c) for c in row[:3])
            for row in cmap.getLookupTable(0.0, 1.0, 256)
        ]
        span = (vmax - vmin) or 1.0
        metrics = QFontMetrics(self.font())
        number_font = self.font()
        number_font.setPointSizeF(max(6.0, min(10.0, cell_h / 3.2)))
        painter.setFont(number_font)
        number_metrics = QFontMetrics(number_font)

        for r in range(len(self._rows)):
            for c in range(len(self._cols)):
                rect = QRect(
                    grid_x + c * cell_w, grid_y + r * cell_h, cell_w, cell_h
                )
                value = float(values[r, c])
                if not np.isfinite(value):
                    color = QColor(self.NA_COLOR)
                    rgb = (217, 217, 217)
                else:
                    pos = (value - vmin) / span
                    idx = int(round(min(1.0, max(0.0, pos)) * 255))
                    rgb = lut[idx]
                    color = QColor(*rgb)
                painter.fillRect(rect, color)
                if show_numbers:
                    if auto_color:
                        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
                        painter.setPen(QColor("#ffffff" if lum < 140 else "#000000"))
                    else:
                        painter.setPen(fixed_color)
                    text = _heatmap_cell_text(value, decimals)
                    if number_metrics.horizontalAdvance(text) <= cell_w - 4:
                        painter.drawText(
                            rect, Qt.AlignmentFlag.AlignCenter, text
                        )

        # Cell grid lines (subtle, keeps large matrices readable).
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for c in range(1, len(self._cols)):
            x = grid_x + c * cell_w
            painter.drawLine(x, grid_y, x, grid_y + layout["grid_h"])
        for r in range(1, len(self._rows)):
            y = grid_y + r * cell_h
            painter.drawLine(grid_x, y, grid_x + layout["grid_w"], y)

        painter.setFont(self.font())
        self._paint_row_labels(painter, layout, metrics)
        self._paint_col_labels(painter, layout, metrics)
        if self._row_dendro:
            self._paint_dendrogram(painter, self._row_dendro, layout, horizontal=True)
        if self._col_dendro:
            self._paint_dendrogram(painter, self._col_dendro, layout, horizontal=False)
        self._paint_colorbar(painter, layout, lut, vmin, vmax, decimals)

    def _paint_row_labels(self, painter, layout, metrics) -> None:
        painter.setPen(QColor("#2c3a48"))
        label_x = layout["grid_x"] - self.LABEL_GAP
        for r, name in enumerate(self._rows):
            rect = QRect(
                0, layout["grid_y"] + r * layout["cell_h"],
                label_x, layout["cell_h"],
            )
            text = metrics.elidedText(
                str(name), Qt.TextElideMode.ElideRight, max(10, label_x - 4)
            )
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

    def _paint_col_labels(self, painter, layout, metrics) -> None:
        # Rotated labels are pre-rendered into small pixmaps: their box then
        # starts exactly at the heatmap's lower edge (text reads downwards).
        top = layout["grid_y"] + layout["grid_h"] + self.LABEL_GAP
        font = self.font()
        color = QColor("#2c3a48")
        cache = getattr(self, "_col_label_cache", None)
        if cache is None:
            cache = {}
            self._col_label_cache = cache
        base_key = (
            font.family(), round(font.pointSizeF(), 2), int(font.weight()),
            color.name(),
        )
        dpr = float(self.devicePixelRatioF() or 1.0)
        for c, name in enumerate(self._cols):
            center_x = layout["grid_x"] + c * layout["cell_w"] + layout["cell_w"] / 2
            text = metrics.elidedText(
                str(name), Qt.TextElideMode.ElideRight,
                self.COL_LABEL_HEIGHT - 10,
            )
            key = (base_key, round(dpr, 2), text)
            pixmap = cache.get(key)
            if pixmap is None:
                width = max(1, metrics.horizontalAdvance(text) + 2)
                height = metrics.height() + 2
                # Render at the screen's pixel ratio, otherwise Qt upscales the
                # bitmap and the rotated names look blurry on scaled displays.
                source = QPixmap(
                    max(1, int(round(width * dpr))),
                    max(1, int(round(height * dpr))),
                )
                source.fill(Qt.GlobalColor.transparent)
                pp = QPainter(source)
                pp.scale(dpr, dpr)
                pp.setFont(font)
                pp.setPen(color)
                pp.drawText(1, metrics.ascent() + 1, text)
                pp.end()
                pixmap = source.transformed(
                    QTransform().rotate(-90), Qt.TransformationMode.FastTransformation
                )
                pixmap.setDevicePixelRatio(dpr)
                cache[key] = pixmap
                if len(cache) > 500:
                    cache.clear()
            logical_w = pixmap.width() / max(1.0, pixmap.devicePixelRatio())
            painter.drawPixmap(
                QPointF(center_x - logical_w / 2.0, float(top)), pixmap
            )

    def _paint_dendrogram(self, painter, info, layout, horizontal: bool) -> None:
        dcoords = info.get("dcoord") or []
        icoords = info.get("icoord") or []
        if not dcoords or not icoords:
            return
        dmax = max((max(item) for item in dcoords), default=0.0) or 1.0
        painter.setPen(QPen(QColor("#5f6b7a"), 1))
        if horizontal:
            x0 = layout["dendro_x"]
            thickness = layout["row_dendro_w"]
            y0 = layout["grid_y"]
            cell = layout["cell_h"]
        else:
            x0 = layout["grid_x"]
            thickness = layout["col_dendro_h"]
            y0 = layout["grid_y"]
            cell = layout["cell_w"]
        for ico, dco in zip(icoords, dcoords):
            points = []
            for leaf, dist in zip(ico, dco):
                pos = (leaf / 10.0) * cell
                if horizontal:
                    # Leaves touch the grid; the root sits further right.
                    points.append(QPointF(x0 + thickness * (dist / dmax),
                                          y0 + pos))
                else:
                    # Leaves touch the grid; the root sits above it.
                    points.append(QPointF(x0 + pos,
                                          y0 - thickness * (dist / dmax)))
            painter.drawPolyline(QPolygonF(points))

    def _paint_colorbar(self, painter, layout, lut, vmin, vmax,
                        decimals: int = 1) -> None:
        bar_x = layout["colorbar_x"]
        bar_y = layout["grid_y"]
        bar_h = layout["grid_h"]
        bar_w = self.COLORBAR_WIDTH
        steps = 64
        for i in range(steps):
            pos = 1.0 - i / (steps - 1)
            idx = int(round(pos * 255))
            painter.fillRect(
                QRect(bar_x, bar_y + i * bar_h / steps,
                      bar_w, max(1, int(bar_h / steps) + 1)),
                QColor(*lut[idx]),
            )
        painter.setPen(QColor(150, 160, 172))
        painter.drawRect(bar_x, bar_y, bar_w, bar_h)
        painter.setPen(QColor("#2c3a48"))
        painter.drawText(
            QRect(bar_x + bar_w + 4, bar_y - 8, 60, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _heatmap_cell_text(vmax),
        )
        painter.drawText(
            QRect(bar_x + bar_w + 4, bar_y + bar_h - 8, 60, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _heatmap_cell_text(vmin),
        )

    # -- zoom --------------------------------------------------------------
    MIN_ZOOM = 0.25
    MAX_ZOOM = 5.0

    def _scroll_area(self):
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QScrollArea):
                return widget
            widget = widget.parentWidget()
        return None

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        old = self._zoom
        new = min(self.MAX_ZOOM, max(self.MIN_ZOOM, old * factor))
        if abs(new - old) < 1e-6:
            event.accept()
            return
        scroll = self._scroll_area()
        h_frac = v_frac = None
        if scroll is not None:
            hb = scroll.horizontalScrollBar()
            vb = scroll.verticalScrollBar()
            h_frac = hb.value() / max(1, hb.maximum())
            v_frac = vb.value() / max(1, vb.maximum())
        self._zoom = new
        self.updateGeometry()
        self.adjustSize()
        self.update()
        if scroll is not None:
            def restore() -> None:
                hb.setValue(int(round(h_frac * hb.maximum())))
                vb.setValue(int(round(v_frac * vb.maximum())))
            QTimer.singleShot(0, restore)
        event.accept()

    # -- tooltips ----------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        import numpy as np

        layout = self._layout()
        if layout is None:
            self.setToolTip("")
            return
        pos = event.position().toPoint()
        dx = pos.x() - layout["grid_x"]
        dy = pos.y() - layout["grid_y"]
        if 0 <= dx < layout["grid_w"] and 0 <= dy < layout["grid_h"]:
            col = int(dx // layout["cell_w"])
            row = int(dy // layout["cell_h"])
            value = float(np.asarray(self._values)[row, col])
            try:
                dec = max(0, min(6, int(self._cfg.get("decimals", 1))))
            except (TypeError, ValueError):
                dec = 1
            self.setToolTip(
                f"{self._rows[row]} · {self._cols[col]}"
                + chr(10)
                + _heatmap_cell_text(value, dec)
            )
        else:
            self.setToolTip("")


def _heatmap_store_path(ctx, task_id) -> Path | None:
    folder = _data_files._module_dir(ctx, task_id)
    if folder is None:
        return None
    return folder / _HEATMAP_STORE_NAME


def _heatmap_store_load(ctx, task_id) -> dict | None:
    path = _heatmap_store_path(ctx, task_id)
    if path is None or not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _heatmap_store_save(ctx, task_id, payload: dict) -> None:
    path = _heatmap_store_path(ctx, task_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError:
        pass


def _heatmap_signature(ctx, page) -> str:
    """Hash of everything a recalculation depends on."""
    params = page._heatmap_params
    task_id = page._heatmap_task_id
    files = []
    for path in params.get("files") or []:
        try:
            st = Path(path).stat()
            entry = [str(path), int(st.st_size), int(st.st_mtime_ns)]
        except OSError:
            entry = [str(path), 0, 0]
        sig = _data_sampling._subsample_sig(ctx, task_id, path)
        if sig:
            entry.append(sig)
        files.append(entry)
    try:
        stages = [
            [kind, stage]
            for kind, stage in _data_columns._map_stages(ctx, task_id)
        ]
        stages_text = json.dumps(stages, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        stages_text = ""
    payload = {
        "task": task_id,
        "files": files,
        "sampling": params.get("sampling") or {},
        "gates": [
            (g.get("task"), g.get("id"))
            for g in params.get("gates") or []
        ],
        "variables": list(params.get("variables") or []),
        "stages": hashlib.sha1(stages_text.encode("utf-8")).hexdigest(),
    }
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _heatmap_gate_options(ctx, page) -> list:
    """Gates (from upstream Gating modules) present for every selected file."""
    files = list(page._heatmap_params.get("files") or [])
    if not files:
        return []
    task_id = page._heatmap_task_id
    gating_tasks = [
        task for task in _data_files._task_chain(ctx, task_id)
        if task.get("id") != task_id
        and task.get("type") in ("gating", "region")
    ]
    multi = len(gating_tasks) > 1
    options = []
    used_labels = set()
    for task in gating_tasks:
        per_file = [
            {g["id"]: g for g in _data_stores._gating_gates_for(ctx, task, path)}
            for path in files
        ]
        if not per_file or any(not ids for ids in per_file):
            continue
        common = set(per_file[0])
        for ids in per_file[1:]:
            common &= set(ids)
        if not common:
            continue
        gates = _data_stores._gating_gates_for(ctx, task)
        for gate in gates:
            if gate["id"] not in common:
                continue
            label = str(gate.get("name", gate["id"]))
            if multi:
                # Only distinguish the same gate name across modules.
                label += f" ({task.get('name', '')})"
            if label in used_labels:
                label = f"{label} [{gate['id']}]"
            used_labels.add(label)
            options.append({
                "task": task["id"],
                "id": gate["id"],
                "label": label,
                "name": gate.get("name", gate["id"]),
            })
    return options


def _heatmap_common_columns(ctx, page) -> list:
    files = list(page._heatmap_params.get("files") or [])
    if not files:
        return []
    lists = [
        _data_columns._columns_for(ctx, path, page._heatmap_task_id) for path in files
    ]
    if not lists:
        return []
    common = set(lists[0])
    for columns in lists[1:]:
        common &= set(columns)
    return [name for name in lists[0] if name in common]


def _heatmap_file_sizes(ctx, page) -> dict:
    sizes = {}
    for path in page._heatmap_params.get("files") or []:
        try:
            index = _data_sampling._subsample_row_index(ctx, page._heatmap_task_id, path)
            sizes[path] = (
                int(len(index)) if index is not None
                else int(_data_columns._data_row_count(ctx, path) or 0)
            )
        except Exception:  # noqa: BLE001
            sizes[path] = 0
    return sizes


def _heatmap_sampled_counts(ctx, page) -> dict:
    params = page._heatmap_params
    sampling = params.get("sampling") or {}
    method = sampling.get("method") or "uniform"
    try:
        value = int(sampling.get(method) or 0)
    except (TypeError, ValueError):
        value = 0
    return _heatmap_sample_counts(
        _heatmap_file_sizes(ctx, page),
        sampling.get("mode") or "full", method, value,
    )


def _build_heatmap_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    bar = QFrame()
    bar.setObjectName("moduleBar")
    tl = QHBoxLayout(bar)
    tl.setContentsMargins(12, 6, 12, 6)
    tl.setSpacing(4)
    tl.addWidget(QLabel(
        f"<b>{_data_files._module_display_title(ctx, task_def.get('id'), name)}</b>"
    ))
    if locked:
        draft = QLabel("Draft (locked)")
        draft.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft)
    tl.addStretch()
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setEnabled(not locked)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(lambda _=False: _heatmap_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("files", [])
    params.setdefault("sampling", {
        "mode": "full", "method": "uniform",
        "uniform": 10000, "total": 100000,
    })
    params.setdefault("gates", [])
    params.setdefault("variables", [])
    params.setdefault("log", [])
    plot_cfg = params.setdefault("plot", {})
    plot_cfg.setdefault("cell_width", 42)
    plot_cfg.setdefault("cell_height", 28)
    plot_cfg.setdefault("colormap", _default_palette())
    plot_cfg.setdefault("show_numbers", True)
    plot_cfg.setdefault("auto_text_color", True)
    plot_cfg.setdefault("text_color", "#000000")
    plot_cfg.setdefault("decimals", 1)
    plot_cfg.setdefault("normalize", "raw")
    plot_cfg.setdefault("cluster_rows", False)
    plot_cfg.setdefault("cluster_cols", False)
    plot_cfg.setdefault("dendro_width", 90)
    plot_cfg.setdefault("dendro_height", 70)
    plot_cfg.setdefault("metric", "euclidean")
    plot_cfg.setdefault("linkage", "average")
    plot_cfg.setdefault("auto_range", True)
    plot_cfg.setdefault("vmin", None)
    plot_cfg.setdefault("vmax", None)

    if not params["files"]:
        params["files"] = [
            rec.get("parquet")
            for rec in _data_files._available_files(ctx, task_def.get("id"))
        ]

    page._heatmap_task_def = task_def
    page._heatmap_task_id = task_def.get("id")
    page._heatmap_params = params
    page._heatmap_locked = locked
    page._heatmap_save_btn = save_btn
    page._heatmap_dirty = False
    page._heatmap_snapshot = copy.deepcopy(params)
    page._heatmap_log_lines = list(params.get("log") or [])
    page._heatmap_running = False
    page._heatmap_cancel = False
    page._heatmap_queue = None
    page._heatmap_timer = None
    page._heatmap_thread = None
    page._heatmap_result = _heatmap_store_load(ctx, task_def.get("id"))
    page._heatmap_dendro_cache = {}
    page._heatmap_syncing = False
    save_btn.clicked.connect(lambda _=False: _heatmap_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    outer = QHBoxLayout(content)
    outer.setContentsMargins(12, 12, 12, 12)
    outer.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_heatmap_left_panel(ctx, page))
    splitter.addWidget(_heatmap_middle_panel(ctx, page))
    splitter.addWidget(_heatmap_right_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 3)
    splitter.setSizes([260, 260, 900])
    page._heatmap_splitter = splitter
    outer.addWidget(splitter, 1)
    layout.addWidget(content, 1)
    # Apply the requested proportions once the page has a real size.
    QTimer.singleShot(0, lambda sp=splitter: sp.setSizes([280, 280, 840]))

    _heatmap_refresh(ctx, page)
    _heatmap_refresh_plot(ctx, page)
    _heatmap_update_save_btn(ctx, page)
    return page


def _heatmap_left_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    # Data files and Sampling share the column height 1:1.
    split = QSplitter(Qt.Orientation.Vertical)
    split.setChildrenCollapsible(False)
    split.addWidget(_heatmap_files_section(ctx, page))
    split.addWidget(_heatmap_sampling_section(ctx, page))
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 1)

    def equalize():
        total = max(2, split.height())
        split.setSizes([total // 2, total - total // 2])

    QTimer.singleShot(0, equalize)
    lay.addWidget(split, 1)
    page._heatmap_left_splitter = split
    return panel


def _heatmap_files_section(ctx, page) -> QWidget:
    section = QWidget()
    lay = QVBoxLayout(section)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Data files</b>"))
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(not page._heatmap_locked)
    files_btn.clicked.connect(lambda _=False: _heatmap_select_files(ctx, page))
    lay.addWidget(files_btn)
    files_box = QListWidget()
    files_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(files_box, 1)
    files_count = QLabel("")
    files_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(files_count)
    page._heatmap_files_box = files_box
    page._heatmap_files_count = files_count
    page._heatmap_files_btn = files_btn
    return section


def _heatmap_sampling_section(ctx, page) -> QWidget:
    section = QWidget()
    lay = QVBoxLayout(section)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Sampling</b>"))
    sampling = _NoWheelComboBox()
    sampling.addItem("Full data", "full")
    sampling.addItem("Sample", "sample")
    sampling.setEnabled(not page._heatmap_locked)
    lay.addWidget(sampling)
    page._heatmap_sampling = sampling

    method = _NoWheelComboBox()
    method.addItem("Uniform per file", "uniform")
    method.addItem("Proportional", "proportional")
    method.setEnabled(not page._heatmap_locked)
    lay.addWidget(method)
    page._heatmap_sampling_method = method

    value_row = QWidget()
    value_lay = QHBoxLayout(value_row)
    value_lay.setContentsMargins(0, 0, 0, 0)
    value_lay.setSpacing(6)
    value_label = QLabel("Rows per file")
    value_lay.addWidget(value_label)
    value_spin = _NoWheelSpinBox()
    value_spin.setRange(1, 2_000_000_000)
    value_spin.setGroupSeparatorShown(True)
    value_spin.setSingleStep(1000)
    value_spin.setEnabled(not page._heatmap_locked)
    value_lay.addWidget(value_spin, 1)
    lay.addWidget(value_row)
    page._heatmap_sampling_value_label = value_label
    page._heatmap_sampling_value = value_spin

    hint = QLabel(
        "Files with fewer rows are used entirely; rows are never "
        "duplicated. Sampling happens before gating."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)
    lay.addStretch(1)

    summary = QLabel("")
    summary.setWordWrap(True)
    summary.setStyleSheet("color: #6b7785; font-size: 8pt;")
    lay.addWidget(summary)
    page._heatmap_sampling_summary = summary

    sampling.currentIndexChanged.connect(
        lambda _=0: _heatmap_on_sampling(ctx, page)
    )
    method.currentIndexChanged.connect(
        lambda _=0: _heatmap_on_sampling(ctx, page)
    )
    value_spin.valueChanged.connect(
        lambda _v: _heatmap_on_sampling_value(ctx, page)
    )
    return section


def _heatmap_gates_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Gates (rows)</b>"))
    hint = QLabel(
        "Gates shared by every selected file, from all upstream Gating "
        "modules. Drag rows to change their order."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)
    gates_btn = QPushButton("Select gates…")
    gates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    gates_btn.setEnabled(not page._heatmap_locked)
    gates_btn.clicked.connect(lambda _=False: _heatmap_select_gates(ctx, page))
    lay.addWidget(gates_btn)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    box.setDefaultDropAction(Qt.DropAction.MoveAction)
    box.setDragEnabled(not page._heatmap_locked)
    box.setToolTip("Drag rows to change their order")
    box.model().rowsMoved.connect(
        lambda *a: _heatmap_on_order(ctx, page, "gates", box)
    )
    lay.addWidget(box, 1)
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    page._heatmap_gates_box = box
    page._heatmap_gates_count = count
    page._heatmap_gates_btn = gates_btn
    return panel


def _heatmap_variables_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Channels</b>"))
    hint = QLabel(
        "Channels shared by every selected file. Drag channels to change "
        "their order."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)
    vars_btn = QPushButton("Select channels…")
    vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    vars_btn.setEnabled(not page._heatmap_locked)
    vars_btn.clicked.connect(
        lambda _=False: _heatmap_select_variables(ctx, page)
    )
    lay.addWidget(vars_btn)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    box.setDefaultDropAction(Qt.DropAction.MoveAction)
    box.setDragEnabled(not page._heatmap_locked)
    box.setToolTip("Drag channels to change their order")
    box.model().rowsMoved.connect(
        lambda *a: _heatmap_on_order(ctx, page, "variables", box)
    )
    lay.addWidget(box, 1)
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    page._heatmap_vars_box = box
    page._heatmap_vars_count = count
    page._heatmap_vars_btn = vars_btn
    return panel


def _heatmap_middle_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(8)

    split = QSplitter(Qt.Orientation.Vertical)
    split.setChildrenCollapsible(False)
    split.addWidget(_heatmap_gates_panel(ctx, page))
    split.addWidget(_heatmap_variables_panel(ctx, page))
    split.setSizes([280, 280])
    lay.addWidget(split, 1)

    row = QHBoxLayout()
    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.clicked.connect(lambda _=False: _heatmap_calculate(ctx, page))
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel_btn.setVisible(False)
    cancel_btn.clicked.connect(lambda _=False: _heatmap_cancel(ctx, page))
    row.addWidget(calc_btn, 1)
    row.addWidget(cancel_btn)
    lay.addLayout(row)
    page._heatmap_calc_btn = calc_btn
    page._heatmap_cancel_btn = cancel_btn

    progress = QProgressBar()
    progress.setTextVisible(False)
    progress.setFixedHeight(8)
    progress.setVisible(False)
    lay.addWidget(progress)
    page._heatmap_progress = progress

    status = QLabel("")
    status.setWordWrap(True)
    status.setStyleSheet("color: #6b7785;")
    lay.addWidget(status)
    page._heatmap_status = status
    return panel


def _heatmap_right_panel(ctx, page) -> QWidget:
    split = QSplitter(Qt.Orientation.Vertical)
    split.setChildrenCollapsible(False)
    split.addWidget(_heatmap_plot_panel(ctx, page))
    bottom = QSplitter(Qt.Orientation.Horizontal)
    bottom.setChildrenCollapsible(False)
    bottom.addWidget(_heatmap_log_panel(ctx, page))
    bottom.addWidget(_heatmap_params_panel(ctx, page))
    # Log : plot settings = 2 : 3.
    bottom.setStretchFactor(0, 2)
    bottom.setStretchFactor(1, 3)
    bottom.setSizes([320, 480])
    split.addWidget(bottom)
    # Heatmap : (log + settings) = 3 : 2.
    split.setStretchFactor(0, 3)
    split.setStretchFactor(1, 2)
    split.setSizes([600, 400])
    page._heatmap_bottom_splitter = bottom

    def apply_bottom_ratio():
        total = bottom.width()
        if total <= 0:
            QTimer.singleShot(30, apply_bottom_ratio)
            return
        first = total * 2 // 5
        bottom.setSizes([first, total - first])

    def apply_right_ratio():
        total = split.height()
        if total <= 0:
            QTimer.singleShot(30, apply_right_ratio)
            return
        first = total * 3 // 5
        split.setSizes([first, total - first])

    QTimer.singleShot(0, apply_bottom_ratio)
    QTimer.singleShot(0, apply_right_ratio)
    return split


def _heatmap_plot_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    header = QHBoxLayout()
    header.addWidget(QLabel("<b>Heatmap</b>"))
    header.addStretch()
    stale = QLabel("")
    stale.setStyleSheet("color: #b58900;")
    header.addWidget(stale)
    lay.addLayout(header)
    scroll = QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
    scroll.setStyleSheet("background: #ffffff; border: none;")
    canvas = _HeatmapCanvas()
    scroll.setWidget(canvas)
    lay.addWidget(scroll, 1)
    page._heatmap_canvas = canvas
    page._heatmap_canvas_scroll = scroll
    page._heatmap_stale_label = stale
    return panel


def _heatmap_log_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Log</b>"))
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Calculation output appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    for line in page._heatmap_log_lines:
        log.appendPlainText(line)
    lay.addWidget(log, 1)
    page._heatmap_log_box = log
    return panel


def _heatmap_params_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
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
    form = QVBoxLayout(host)
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(6)
    scroll.setWidget(host)
    lay.addWidget(scroll, 1)
    page._heatmap_params_form = form
    _heatmap_fill_params(ctx, page, form)
    return panel


def _heatmap_fill_params(ctx, page, form) -> None:
    _widgets_panels._clear_settings_layout(ctx, form)
    cfg = page._heatmap_params.setdefault("plot", {})
    editable = not page._heatmap_locked

    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(6)
    grid.addWidget(QLabel("Cell width"), 0, 0)
    width_spin = _NoWheelSpinBox()
    width_spin.setRange(12, 400)
    width_spin.setValue(int(cfg.get("cell_width", 42)))
    width_spin.setEnabled(editable)
    grid.addWidget(width_spin, 0, 1)
    grid.addWidget(QLabel("Cell height"), 0, 2)
    height_spin = _NoWheelSpinBox()
    height_spin.setRange(12, 200)
    height_spin.setValue(int(cfg.get("cell_height", 28)))
    height_spin.setEnabled(editable)
    grid.addWidget(height_spin, 0, 3)

    grid.addWidget(QLabel("Colormap"), 1, 0)
    cmap_combo = _NoWheelComboBox()
    _widgets_colormap._populate_palette_combo(ctx,
        cmap_combo, cfg.get("colormap") or _default_palette()
    )
    cmap_combo.setEnabled(editable)
    grid.addWidget(cmap_combo, 1, 1, 1, 3)

    grid.addWidget(QLabel("Normalize"), 2, 0)
    norm_combo = _NoWheelComboBox()
    norm_combo.addItem("Raw values", "raw")
    norm_combo.addItem("Column z-score", "column")
    norm_combo.addItem("Row z-score", "row")
    idx = norm_combo.findData(cfg.get("normalize", "raw"))
    norm_combo.setCurrentIndex(max(0, idx))
    norm_combo.setEnabled(editable)
    grid.addWidget(norm_combo, 2, 1, 1, 3)

    show_numbers = QCheckBox("Show numbers")
    show_numbers.setChecked(bool(cfg.get("show_numbers", True)))
    show_numbers.setEnabled(editable)
    auto_color = QCheckBox("Auto number color")
    auto_color.setChecked(bool(cfg.get("auto_text_color", True)))
    auto_color.setEnabled(editable)
    grid.addWidget(show_numbers, 3, 0, 1, 2)
    grid.addWidget(auto_color, 3, 2, 1, 2)

    grid.addWidget(QLabel("Number color"), 4, 0)
    color_btn = QPushButton()
    color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    color_btn.setFixedHeight(24)
    color_btn.setEnabled(editable and not auto_color.isChecked())
    grid.addWidget(color_btn, 4, 1)

    grid.addWidget(QLabel("Decimals"), 4, 2)
    decimals_spin = _NoWheelSpinBox()
    decimals_spin.setRange(0, 6)
    decimals_spin.setValue(int(cfg.get("decimals", 1)))
    decimals_spin.setEnabled(editable)
    decimals_spin.setToolTip("Number of decimals shown in the cells")
    grid.addWidget(decimals_spin, 4, 3)

    auto_range = QCheckBox("Auto value range")
    auto_range.setChecked(bool(cfg.get("auto_range", True)))
    auto_range.setEnabled(editable)
    grid.addWidget(auto_range, 5, 0, 1, 2)
    vmin_spin = QDoubleSpinBox()
    vmax_spin = QDoubleSpinBox()
    for spin, key in ((vmin_spin, "vmin"), (vmax_spin, "vmax")):
        spin.setRange(-1e12, 1e12)
        spin.setDecimals(4)
        spin.setEnabled(editable and not auto_range.isChecked())
        value = cfg.get(key)
        if isinstance(value, (int, float)):
            spin.setValue(float(value))
    grid.addWidget(QLabel("Min"), 6, 0)
    grid.addWidget(vmin_spin, 6, 1)
    grid.addWidget(QLabel("Max"), 6, 2)
    grid.addWidget(vmax_spin, 6, 3)

    cluster_rows = QCheckBox("Cluster rows")
    cluster_rows.setChecked(bool(cfg.get("cluster_rows")))
    cluster_rows.setEnabled(editable)
    cluster_cols = QCheckBox("Cluster columns")
    cluster_cols.setChecked(bool(cfg.get("cluster_cols")))
    cluster_cols.setEnabled(editable)
    grid.addWidget(cluster_rows, 7, 0, 1, 2)
    grid.addWidget(cluster_cols, 7, 2, 1, 2)

    grid.addWidget(QLabel("Dendro width"), 8, 0)
    dendro_width = _NoWheelSpinBox()
    dendro_width.setRange(20, 300)
    dendro_width.setValue(int(cfg.get("dendro_width", 90)))
    dendro_width.setEnabled(editable)
    dendro_width.setToolTip("Width of the row dendrogram (right side)")
    grid.addWidget(dendro_width, 8, 1)
    grid.addWidget(QLabel("Dendro height"), 8, 2)
    dendro_height = _NoWheelSpinBox()
    dendro_height.setRange(20, 300)
    dendro_height.setValue(int(cfg.get("dendro_height", 70)))
    dendro_height.setEnabled(editable)
    dendro_height.setToolTip("Height of the column dendrogram (top)")
    grid.addWidget(dendro_height, 8, 3)

    grid.addWidget(QLabel("Distance"), 9, 0)
    metric_combo = _NoWheelComboBox()
    metric_combo.addItem("Euclidean", "euclidean")
    metric_combo.addItem("Correlation", "correlation")
    metric_combo.setCurrentIndex(
        max(0, metric_combo.findData(cfg.get("metric", "euclidean")))
    )
    metric_combo.setEnabled(editable)
    grid.addWidget(metric_combo, 9, 1)
    grid.addWidget(QLabel("Linkage"), 9, 2)
    linkage_combo = _NoWheelComboBox()
    linkage_combo.addItem("Average", "average")
    linkage_combo.addItem("Complete", "complete")
    linkage_combo.addItem("Single", "single")
    linkage_combo.addItem("Ward", "ward")
    linkage_combo.setCurrentIndex(
        max(0, linkage_combo.findData(cfg.get("linkage", "average")))
    )
    linkage_combo.setEnabled(editable)
    grid.addWidget(linkage_combo, 9, 3)

    copy_btn = QPushButton("Copy matrix")
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.setToolTip("Copy the displayed matrix to the clipboard (TSV)")
    copy_btn.clicked.connect(lambda _=False: _heatmap_copy_matrix(ctx, page))
    grid.addWidget(copy_btn, 10, 0, 1, 4)
    form.addLayout(grid)

    def update_color_button():
        color = QColor(cfg.get("text_color") or "#000000")
        text_color = "#ffffff" if color.lightness() < 128 else "#000000"
        color_btn.setText(color.name())
        color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {color.name()}; "
            f"color: {text_color}; border: 1px solid #999; }}"
        )

    def changed():
        _heatmap_mark_dirty(ctx, page)
        _heatmap_refresh_plot(ctx, page)

    def on_width(value):
        cfg["cell_width"] = int(value)
        changed()

    def on_height(value):
        cfg["cell_height"] = int(value)
        changed()

    def on_colormap():
        cfg["colormap"] = cmap_combo.currentData()
        changed()

    def on_normalize():
        cfg["normalize"] = norm_combo.currentData() or "raw"
        changed()

    def on_show_numbers(checked):
        cfg["show_numbers"] = bool(checked)
        changed()

    def on_decimals(value):
        cfg["decimals"] = int(value)
        changed()

    def on_auto_color(checked):
        cfg["auto_text_color"] = bool(checked)
        color_btn.setEnabled(editable and not checked)
        changed()

    def on_auto_range(checked):
        cfg["auto_range"] = bool(checked)
        vmin_spin.setEnabled(editable and not checked)
        vmax_spin.setEnabled(editable and not checked)
        changed()

    def on_range():
        cfg["vmin"] = float(vmin_spin.value())
        cfg["vmax"] = float(vmax_spin.value())
        changed()

    def on_cluster_rows(checked):
        cfg["cluster_rows"] = bool(checked)
        changed()

    def on_dendro_width(value):
        cfg["dendro_width"] = int(value)
        changed()

    def on_dendro_height(value):
        cfg["dendro_height"] = int(value)
        changed()

    def on_cluster_cols(checked):
        cfg["cluster_cols"] = bool(checked)
        changed()

    def on_metric():
        cfg["metric"] = metric_combo.currentData() or "euclidean"
        changed()

    def on_linkage():
        cfg["linkage"] = linkage_combo.currentData() or "average"
        changed()

    def pick_color():
        color = QColorDialog.getColor(
            QColor(cfg.get("text_color") or "#000000"),
            ctx.host, "Number color",
        )
        if color.isValid():
            cfg["text_color"] = color.name()
            update_color_button()
            changed()

    width_spin.valueChanged.connect(on_width)
    height_spin.valueChanged.connect(on_height)
    cmap_combo.currentIndexChanged.connect(lambda _=0: on_colormap())
    norm_combo.currentIndexChanged.connect(lambda _=0: on_normalize())
    show_numbers.toggled.connect(on_show_numbers)
    decimals_spin.valueChanged.connect(on_decimals)
    auto_color.toggled.connect(on_auto_color)
    auto_range.toggled.connect(on_auto_range)
    vmin_spin.valueChanged.connect(lambda _v: on_range())
    vmax_spin.valueChanged.connect(lambda _v: on_range())
    cluster_rows.toggled.connect(on_cluster_rows)
    cluster_cols.toggled.connect(on_cluster_cols)
    dendro_width.valueChanged.connect(on_dendro_width)
    dendro_height.valueChanged.connect(on_dendro_height)
    metric_combo.currentIndexChanged.connect(lambda _=0: on_metric())
    linkage_combo.currentIndexChanged.connect(lambda _=0: on_linkage())
    color_btn.clicked.connect(lambda _=False: pick_color())
    update_color_button()


def _heatmap_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._heatmap_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, path, page._heatmap_task_id)
        for path in page._heatmap_params.get("files") or []
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files...",
        reorder=True,
    )
    if chosen is None:
        return
    page._heatmap_params["files"] = [
        by_name[name] for name in chosen if name in by_name
    ]
    _heatmap_mark_dirty(ctx, page)
    _heatmap_refresh(ctx, page)


def _heatmap_select_gates(ctx, page) -> None:
    options = _heatmap_gate_options(ctx, page)
    if not options:
        QMessageBox.information(
            ctx.host, "Select gates",
            "Select data files that share gates first.",
        )
        return
    labels = [opt["label"] for opt in options]
    by_label = {opt["label"]: opt for opt in options}
    current = [
        gate.get("label")
        for gate in page._heatmap_params.get("gates") or []
        if gate.get("label") in by_label
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select gates", labels, current, "Filter gates...", reorder=True,
    )
    if chosen is None:
        return
    selected = []
    for label in chosen:
        opt = by_label.get(label)
        if opt is None:
            continue
        selected.append({
            "task": opt["task"], "id": opt["id"], "label": label,
        })
    page._heatmap_params["gates"] = selected
    _heatmap_mark_dirty(ctx, page)
    _heatmap_refresh(ctx, page)


def _heatmap_select_variables(ctx, page) -> None:
    columns = _heatmap_common_columns(ctx, page)
    if not columns:
        QMessageBox.information(
            ctx.host, "Select channels",
            "Select data files with common channels first.",
        )
        return
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select channels", columns,
        page._heatmap_params.get("variables") or [],
        "Filter channels...", reorder=True,
    )
    if chosen is None:
        return
    page._heatmap_params["variables"] = list(chosen)
    _heatmap_mark_dirty(ctx, page)
    _heatmap_refresh(ctx, page)


def _heatmap_on_order(ctx, page, key, box) -> None:
    if page._heatmap_syncing:
        return
    values = []
    for i in range(box.count()):
        item = box.item(i)
        value = item.data(Qt.ItemDataRole.UserRole)
        if not value and key != "gates":
            # A drag-and-drop move can lose the custom role while the row
            # text survives; recover the channel instead of dropping it.
            value = item.text()
            if value:
                item.setData(Qt.ItemDataRole.UserRole, value)
        values.append(value)
    if key == "gates":
        by_key = {
            f"{g.get('task')}|{g.get('id')}": g
            for g in page._heatmap_params.get("gates") or []
        }
        page._heatmap_params["gates"] = [
            by_key[value] for value in values if value in by_key
        ]
    else:
        page._heatmap_params["variables"] = [
            value for value in values if value
        ]
    _heatmap_mark_dirty(ctx, page)
    _heatmap_update_buttons(ctx, page)
    _heatmap_refresh_stale(ctx, page)


def _heatmap_fill_list(ctx, box, entries) -> None:
    box.blockSignals(True)
    box.clear()
    for value, label in entries:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, value)
        flags = item.flags()
        item.setFlags(
            flags | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        box.addItem(item)
    box.blockSignals(False)


def _heatmap_refresh(ctx, page) -> None:
    params = page._heatmap_params
    records = _data_files._available_files(ctx, page._heatmap_task_id)
    by_path = {rec.get("parquet"): rec for rec in records}
    params["files"] = [
        path for path in params.get("files") or [] if path in by_path
    ]
    page._heatmap_files_box.blockSignals(True)
    page._heatmap_files_box.clear()
    for path in params["files"]:
        page._heatmap_files_box.addItem(
            _data_naming._comp_file_name(ctx, path, page._heatmap_task_id)
        )
    page._heatmap_files_box.blockSignals(False)
    page._heatmap_files_count.setText(
        f"{len(params['files'])} file(s)"
    )

    # Keep only gates/variables that still exist for the selection.
    options = _heatmap_gate_options(ctx, page)
    by_gate = {
        f"{opt['task']}|{opt['id']}": opt for opt in options
    }
    gates = []
    for gate in params.get("gates") or []:
        key = f"{gate.get('task')}|{gate.get('id')}"
        opt = by_gate.get(key)
        if opt is None:
            continue
        gates.append({
            "task": opt["task"], "id": opt["id"],
            "label": opt["label"],
        })
    if gates != (params.get("gates") or []):
        params["gates"] = gates
        _heatmap_mark_dirty(ctx, page)
    common = set(_heatmap_common_columns(ctx, page))
    variables = [
        name for name in params.get("variables") or [] if name in common
    ]
    if variables != (params.get("variables") or []):
        params["variables"] = variables
        _heatmap_mark_dirty(ctx, page)

    page._heatmap_syncing = True
    try:
        _heatmap_fill_list(ctx,
            page._heatmap_gates_box,
            [
                (f"{g['task']}|{g['id']}", g["label"])
                for g in params["gates"]
            ],
        )
        _heatmap_fill_list(ctx,
            page._heatmap_vars_box,
            [(name, name) for name in params["variables"]],
        )
    finally:
        page._heatmap_syncing = False
    page._heatmap_gates_count.setText(
        f"{len(params['gates'])} gate(s)"
    )
    page._heatmap_vars_count.setText(
        f"{len(params['variables'])} channel(s)"
    )

    _heatmap_sync_sampling_controls(ctx, page)
    _heatmap_refresh_sampling(ctx, page)
    _heatmap_update_buttons(ctx, page)
    _heatmap_refresh_stale(ctx, page)


def _heatmap_sync_sampling_controls(ctx, page) -> None:
    sampling = page._heatmap_params.setdefault("sampling", {})
    mode = sampling.get("mode") or "full"
    method = sampling.get("method") or "uniform"
    sampled = mode == "sample"
    page._heatmap_sampling.blockSignals(True)
    idx = page._heatmap_sampling.findData(mode)
    page._heatmap_sampling.setCurrentIndex(max(0, idx))
    page._heatmap_sampling.blockSignals(False)
    page._heatmap_sampling_method.blockSignals(True)
    idx = page._heatmap_sampling_method.findData(method)
    page._heatmap_sampling_method.setCurrentIndex(max(0, idx))
    page._heatmap_sampling_method.blockSignals(False)
    page._heatmap_sampling_method.setVisible(sampled)
    page._heatmap_sampling_value_label.setText(
        "Total rows" if method == "proportional" else "Rows per file"
    )
    page._heatmap_sampling_value_label.setVisible(sampled)
    page._heatmap_sampling_value.setVisible(sampled)
    page._heatmap_sampling_value.blockSignals(True)
    try:
        value = int(sampling.get(method) or 1)
    except (TypeError, ValueError):
        value = 1
    page._heatmap_sampling_value.setValue(max(1, value))
    page._heatmap_sampling_value.blockSignals(False)
    page._heatmap_sampling.setEnabled(not page._heatmap_locked)


def _heatmap_on_sampling(ctx, page) -> None:
    sampling = page._heatmap_params.setdefault("sampling", {})
    sampling["mode"] = page._heatmap_sampling.currentData() or "full"
    sampling["method"] = (
        page._heatmap_sampling_method.currentData() or "uniform"
    )
    method = sampling["method"]
    sampling[method] = int(page._heatmap_sampling_value.value())
    _heatmap_sync_sampling_controls(ctx, page)
    _heatmap_refresh_sampling(ctx, page)
    _heatmap_mark_dirty(ctx, page)
    _heatmap_update_buttons(ctx, page)
    _heatmap_refresh_stale(ctx, page)


def _heatmap_on_sampling_value(ctx, page) -> None:
    sampling = page._heatmap_params.setdefault("sampling", {})
    method = sampling.get("method") or "uniform"
    sampling[method] = int(page._heatmap_sampling_value.value())
    _heatmap_refresh_sampling(ctx, page)
    _heatmap_mark_dirty(ctx, page)
    _heatmap_refresh_stale(ctx, page)


def _heatmap_refresh_sampling(ctx, page) -> None:
    params = page._heatmap_params
    files = params.get("files") or []
    if not files:
        page._heatmap_sampling_summary.setText("Select data files first.")
        return
    sizes = _heatmap_file_sizes(ctx, page)
    sampling = params.get("sampling") or {}
    method = sampling.get("method") or "uniform"
    try:
        value = int(sampling.get(method) or 0)
    except (TypeError, ValueError):
        value = 0
    counts = _heatmap_sample_counts(
        sizes, sampling.get("mode") or "full", method, value
    )
    lines = []
    for path in files:
        name = _data_naming._comp_file_name(ctx, path, page._heatmap_task_id)
        used = counts.get(path, sizes.get(path, 0))
        lines.append(f"{name}: {sizes.get(path, 0):,} → {used:,}")
    page._heatmap_sampling_summary.setText("\n".join(lines))


def _heatmap_update_buttons(ctx, page) -> None:
    params = page._heatmap_params
    running = page._heatmap_running
    ready = bool(
        params.get("files") and params.get("gates")
        and params.get("variables")
    )
    page._heatmap_calc_btn.setEnabled(
        ready and not running and not page._heatmap_locked
    )
    page._heatmap_cancel_btn.setVisible(running)
    page._heatmap_progress.setVisible(running)
    editable = not page._heatmap_locked and not running
    for widget in (
        page._heatmap_files_btn, page._heatmap_gates_btn,
        page._heatmap_vars_btn, page._heatmap_sampling,
    ):
        widget.setEnabled(editable)
    page._heatmap_sampling_method.setEnabled(editable)
    page._heatmap_sampling_value.setEnabled(editable)
    page._heatmap_gates_box.setDragEnabled(editable)
    page._heatmap_vars_box.setDragEnabled(editable)


def _heatmap_refresh_stale(ctx, page) -> None:
    result = page._heatmap_result
    if not result:
        page._heatmap_stale_label.setText("")
        return
    stale = result.get("signature") != _heatmap_signature(ctx, page)
    page._heatmap_stale_label.setText(
        "Outdated — press Calculate" if stale else ""
    )


def _heatmap_update_save_btn(ctx, page) -> None:
    page._heatmap_save_btn.setEnabled(
        page._heatmap_dirty and not page._heatmap_locked
    )


def _heatmap_mark_dirty(ctx, page) -> None:
    page._heatmap_dirty = True
    _heatmap_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _heatmap_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = page._heatmap_log_lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    page._heatmap_params["log"] = list(lines)
    box = getattr(page, "_heatmap_log_box", None)
    if box is not None:
        for line in str(text).splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")


def _heatmap_calculate(ctx, page) -> None:
    if page._heatmap_running:
        return
    params = page._heatmap_params
    if not params.get("files"):
        QMessageBox.warning(ctx.host, "Calculate", "Select data files first.")
        return
    if not params.get("gates"):
        QMessageBox.warning(ctx.host, "Calculate", "Select at least one gate.")
        return
    if not params.get("variables"):
        QMessageBox.warning(
            ctx.host, "Calculate", "Select at least one channel."
        )
        return
    sampling = params.get("sampling") or {}
    if sampling.get("mode") == "sample":
        method = sampling.get("method") or "uniform"
        try:
            value = int(sampling.get(method) or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            QMessageBox.warning(
                ctx.host, "Calculate", "Enter a sampling row count."
            )
            return

    page._heatmap_running = True
    page._heatmap_cancel = False
    page._heatmap_status.setText("Calculating…")
    page._heatmap_progress.setRange(0, max(1, len(params["files"])))
    page._heatmap_progress.setValue(0)
    _heatmap_update_buttons(ctx, page)

    runlog.action(
        "heatmap.calculate", module=str(page._heatmap_task_id),
        files=len(params["files"]), gates=len(params["gates"]),
        variables=len(params["variables"]),
        sampling=str((params.get("sampling") or {}).get("mode", "full")),
    )
    q = queue.Queue()
    page._heatmap_queue = q
    page._heatmap_thread = threading.Thread(
        target=partial(_heatmap_run, ctx), args=(page, q), daemon=True
    )
    page._heatmap_thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _heatmap_poll(ctx, page))
    timer.start()
    page._heatmap_timer = timer


def _heatmap_cancel(ctx, page) -> None:
    page._heatmap_cancel = True
    page._heatmap_status.setText("Cancelling…")


def _heatmap_run(ctx, page, q) -> None:
    import time
    import warnings

    import numpy as np

    params = copy.deepcopy(page._heatmap_params)
    task_id = page._heatmap_task_id
    files = list(params.get("files") or [])
    gate_refs = list(params.get("gates") or [])
    variables = list(params.get("variables") or [])
    sampling = dict(params.get("sampling") or {})
    plot_cfg = dict(params.get("plot") or {})
    total = len(files)
    started = time.perf_counter()
    try:
        q.put(("log", "— Starting heatmap calculation —"))
        sizes = {}
        for path in files:
            index = _data_sampling._subsample_row_index(ctx, task_id, path)
            sizes[path] = (
                int(len(index)) if index is not None
                else int(_data_columns._data_row_count(ctx, path) or 0)
            )
        method = sampling.get("method") or "uniform"
        try:
            sample_value = int(sampling.get(method) or 0)
        except (TypeError, ValueError):
            sample_value = 0
        used_rows = _heatmap_sample_counts(
            sizes, sampling.get("mode") or "full", method, sample_value
        )
        if sampling.get("mode") == "sample":
            q.put(("log", f"Sampling: {sampling.get('method', 'uniform')}"
                          f" ({sum(used_rows.values()):,} rows used)"))
        else:
            q.put(("log", "Sampling: full data"))

        numerators = {}
        denominators = {}
        gate_counts = {}
        gate_labels = {
            f"{ref.get('task')}|{ref.get('id')}":
                str(ref.get("label") or ref.get("id"))
            for ref in gate_refs
        }
        empty_pairs = []       # (gate label, file name, reason)
        na_terms = {}          # (gate key, variable) -> [file names]
        for i, path in enumerate(files):
            if page._heatmap_cancel:
                q.put(("cancelled",))
                return
            name = _data_naming._comp_file_name(ctx, path, task_id)
            q.put(("status", f"Reading {i + 1}/{total}"))
            q.put(("log", f"Reading {i + 1}/{total}: {name} "
                          f"({sizes.get(path, 0):,} rows)"))

            gates_cache = {}
            needed = set(variables)
            for ref in gate_refs:
                task = ctx._find_task_by_id(ref.get("task"))
                if task is None:
                    continue
                gates = gates_cache.get(ref["task"])
                if gates is None:
                    gates = _data_stores._gating_gates_for(ctx, task, path)
                    gates_cache[ref["task"]] = gates
                by_id = {g["id"]: g for g in gates}
                for gate in _gate_chain(gates, ref.get("id")):
                    needed |= _gate_needed_columns(gate, by_id)
            names = sorted(needed)
            arrays = _data_columns._column_data(ctx,
                task_id, path, names, row_filter=False
            )
            missing = [name_ for name_ in variables if name_ not in arrays]
            if missing:
                raise ValueError(
                    f"{name} is missing: {', '.join(missing)}"
                )
            index = _data_sampling._subsample_row_index(ctx, task_id, path)
            if index is not None:
                arrays = {key: value[index] for key, value in arrays.items()}
            if not arrays:
                continue
            visible = len(next(iter(arrays.values())))
            keep_rows = used_rows.get(path, visible)
            if 0 < keep_rows < visible:
                selection = _stable_sample_selection(
                    np.ones(visible, dtype=bool), int(keep_rows)
                )
                picked = np.flatnonzero(selection)
                arrays = {
                    key: value[picked] for key, value in arrays.items()
                }
            q.put(("log", f"  using "
                          f"{len(next(iter(arrays.values()))):,} rows"))

            existing = set(arrays)
            for ref in gate_refs:
                key = f"{ref.get('task')}|{ref.get('id')}"
                gate_counts.setdefault(key, {})
                gates = gates_cache.get(ref.get("task"))
                if not gates or not any(
                    g["id"] == ref.get("id") for g in gates
                ):
                    empty_pairs.append((
                        gate_labels.get(key, key), name,
                        "gate does not apply to this file",
                    ))
                    continue
                try:
                    mask = _compute_gate_filter_mask(
                        gates, ref.get("id"), path, {},
                        existing=existing, extra=arrays,
                    )
                except Exception:  # noqa: BLE001
                    mask = None
                if mask is None:
                    gate_counts[key][path] = None
                    empty_pairs.append((
                        gate_labels.get(key, key), name,
                        "gate could not be evaluated",
                    ))
                    continue
                mask = np.asarray(mask, dtype=bool)
                count = int(np.count_nonzero(mask))
                gate_counts[key][path] = count
                if count == 0:
                    empty_pairs.append((
                        gate_labels.get(key, key), name, "no data points",
                    ))
                    continue
                for var in variables:
                    values = np.asarray(
                        arrays[var], dtype="float64"
                    )[mask]
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        with np.errstate(all="ignore"):
                            mean = (
                                float(np.nanmean(values))
                                if values.size else float("nan")
                            )
                    if not np.isfinite(mean):
                        # Only files with a real value take part in the
                        # weighted average; the skip is reported below.
                        na_terms.setdefault((key, var), []).append(name)
                        continue
                    slot = (key, var)
                    numerators[slot] = (
                        numerators.get(slot, 0.0) + count * mean
                    )
                    denominators[slot] = (
                        denominators.get(slot, 0.0) + count
                    )
            q.put(("progress", i + 1, total))

        if empty_pairs:
            q.put(("log",
                   f"! {len(empty_pairs)} gate/file pair(s) have no data:"
                   " the cell becomes NA and the file is skipped"))
            for gate_label, file_name, reason in empty_pairs[:20]:
                q.put(("log",
                       f"  {gate_label} · {file_name} ({reason})"))
            if len(empty_pairs) > 20:
                q.put(("log", f"  … and {len(empty_pairs) - 20} more"))
        if na_terms:
            total_terms = sum(len(names) for names in na_terms.values())
            q.put(("log",
                   f"! {total_terms} weighted term(s) were NA: "
                   "skipped, calculation continued"))
            for (key, var), names_ in list(na_terms.items())[:40]:
                label = gate_labels.get(key, key)
                shown = ", ".join(names_[:6])
                if len(names_) > 6:
                    shown += f", … (+{len(names_) - 6})"
                q.put(("log", f"  {label} × {var}: {shown}"))

        values = []
        for ref in gate_refs:
            key = f"{ref.get('task')}|{ref.get('id')}"
            row = []
            for var in variables:
                total_weight = denominators.get((key, var), 0.0)
                if total_weight > 0:
                    row.append(
                        numerators.get((key, var), 0.0) / total_weight
                    )
                else:
                    row.append(None)
            values.append(row)

        na_cells = sum(
            1 for row in values for value in row if value is None
        )
        if na_cells:
            q.put(("log",
                   f"! {na_cells} cell(s) have no data (shown as NA)."))
        q.put(("log", "Building dendrograms…"))
        clustering = {}
        for normalize in ("raw", "column", "row"):
            matrix = _heatmap_normalize(values, normalize)
            row_matrix = _heatmap_fill_nan(matrix, 1)
            col_matrix = _heatmap_fill_nan(matrix, 0)
            metric = plot_cfg.get("metric") or "euclidean"
            linkage_method = plot_cfg.get("linkage") or "average"
            clustering[f"{normalize}|{metric}|{linkage_method}"] = {
                "row": _heatmap_linkage(row_matrix, metric, linkage_method),
                "col": _heatmap_linkage(
                    col_matrix.T, metric, linkage_method
                ),
            }

        payload = {
            "version": 1,
            "signature": _heatmap_signature(ctx, page),
            "computed_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
            "file_names": {
                path: _data_naming._comp_file_name(ctx, path, task_id)
                for path in files
            },
            "gates": gate_refs,
            "variables": variables,
            "values": values,
            "counts": gate_counts,
            "clustering": clustering,
            "sampling": sampling,
            "row_counts": {path: sizes.get(path, 0) for path in files},
            "used_rows": used_rows,
            "seconds": round(time.perf_counter() - started, 3),
        }
        q.put(("done", payload))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _heatmap_poll(ctx, page) -> None:
    q = getattr(page, "_heatmap_queue", None)
    if q is None:
        return
    finished = False
    error = None
    cancelled = False
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "log":
            _heatmap_log(ctx, page, msg[1])
        elif kind == "status":
            page._heatmap_status.setText(msg[1])
        elif kind == "progress":
            page._heatmap_progress.setRange(0, max(1, msg[2]))
            page._heatmap_progress.setValue(msg[1])
        elif kind == "cancelled":
            cancelled = True
            finished = True
        elif kind == "done":
            payload = msg[1]
            _heatmap_apply_result(ctx, page, payload)
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    page._heatmap_running = False
    timer = getattr(page, "_heatmap_timer", None)
    if timer is not None:
        timer.stop()
        page._heatmap_timer = None
    page._heatmap_queue = None
    _heatmap_update_buttons(ctx, page)
    if cancelled:
        _heatmap_log(ctx, page, "Cancelled.")
        page._heatmap_status.setText("Cancelled")
        return
    if error:
        _heatmap_log(ctx, page, f"✗ {error}")
        page._heatmap_status.setText("Failed — see the log")
        QMessageBox.warning(ctx.host, "Calculate", error)
        return
    page._heatmap_status.setText("Done")
    page._heatmap_progress.setValue(page._heatmap_progress.maximum())


def _heatmap_apply_result(ctx, page, payload) -> None:
    page._heatmap_result = payload
    page._heatmap_dendro_cache = {}
    _heatmap_store_save(ctx, page._heatmap_task_id, payload)
    _heatmap_refresh_plot(ctx, page)
    _heatmap_refresh_stale(ctx, page)
    if ctx.project is not None:
        ctx.project.save()
    page._heatmap_snapshot = copy.deepcopy(page._heatmap_params)
    page._heatmap_dirty = False
    _heatmap_update_save_btn(ctx, page)
    gates = len(payload.get("gates") or [])
    variables = len(payload.get("variables") or [])
    files = len(payload.get("files") or [])
    _heatmap_log(ctx,
        page,
        f"✓ {gates} gates × {variables} channels "
        f"({files} files, {payload.get('seconds', 0):.2f} s)",
    )
    _heatmap_log(ctx, page, "Matrix saved to the module folder.")
    ctx.statusBar().showMessage("Heatmap calculated")
    ctx._module_saved(page)
    runlog.action(
        "heatmap.done", module=str(page._heatmap_task_id),
        gates=gates, variables=variables, files=files,
    )


def _heatmap_display(ctx, page):
    """(row labels, column labels, matrix, row dendro, col dendro) to draw."""
    import numpy as np

    result = page._heatmap_result
    if not result:
        return None
    values = result.get("values") or []
    gates = result.get("gates") or []
    variables = result.get("variables") or []
    if not values or not gates or not variables:
        return None
    cfg = page._heatmap_params.get("plot") or {}
    matrix = _heatmap_normalize(values, cfg.get("normalize") or "raw")
    row_labels = [gate.get("label") or gate.get("id") for gate in gates]
    col_labels = [str(name) for name in variables]
    row_order = list(range(len(row_labels)))
    col_order = list(range(len(col_labels)))
    row_dendro = col_dendro = None
    if cfg.get("cluster_rows"):
        info = _heatmap_dendro(ctx, page, "row")
        if info and len(info.get("order") or []) == len(row_labels):
            row_order = list(info["order"])
            row_dendro = info
    if cfg.get("cluster_cols"):
        info = _heatmap_dendro(ctx, page, "col")
        if info and len(info.get("order") or []) == len(col_labels):
            col_order = list(info["order"])
            col_dendro = info
    matrix = matrix[np.ix_(row_order, col_order)]
    return (
        [row_labels[i] for i in row_order],
        [col_labels[i] for i in col_order],
        matrix, row_dendro, col_dendro,
    )


def _heatmap_dendro(ctx, page, axis: str):
    result = page._heatmap_result or {}
    cfg = page._heatmap_params.get("plot") or {}
    normalize = cfg.get("normalize") or "raw"
    metric = cfg.get("metric") or "euclidean"
    linkage_method = cfg.get("linkage") or "average"
    key = f"{normalize}|{metric}|{linkage_method}"
    cache = page._heatmap_dendro_cache
    if key not in cache:
        cluster = (result.get("clustering") or {}).get(key)
        if cluster is None:
            matrix = _heatmap_normalize(
                result.get("values") or [], normalize
            )
            if getattr(matrix, "ndim", 0) == 2 and matrix.size:
                row_matrix = _heatmap_fill_nan(matrix, 1)
                col_matrix = _heatmap_fill_nan(matrix, 0)
                cluster = {
                    "row": _heatmap_linkage(
                        row_matrix, metric, linkage_method
                    ),
                    "col": _heatmap_linkage(
                        col_matrix.T, metric, linkage_method
                    ),
                }
            else:
                cluster = {}
        cache[key] = cluster
    return (cache[key] or {}).get(axis)


def _heatmap_refresh_plot(ctx, page) -> None:
    display = _heatmap_display(ctx, page)
    canvas = getattr(page, "_heatmap_canvas", None)
    if canvas is None:
        return
    if display is None:
        if page._heatmap_result:
            canvas.clear("No data in the selected gates.")
        else:
            canvas.clear("Press Calculate to build the heatmap.")
        return
    rows, cols, matrix, row_dendro, col_dendro = display
    cfg = dict(page._heatmap_params.get("plot") or {})
    cfg["normalize"] = cfg.get("normalize") or "raw"
    canvas.set_content(rows, cols, matrix, row_dendro, col_dendro, cfg)
    try:
        canvas.adjustSize()
    except RuntimeError:
        pass


def _heatmap_copy_matrix(ctx, page) -> None:
    display = _heatmap_display(ctx, page)
    if display is None:
        return
    rows, cols, matrix, _row, _col = display
    lines = ["\t".join([""] + cols)]
    for index, label in enumerate(rows):
        cells = [_heatmap_export_text(value) for value in matrix[index]]
        lines.append("\t".join([str(label)] + cells))
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Heatmap matrix copied", 3000)


def _heatmap_save_now(ctx, page) -> None:
    if page._heatmap_result is not None:
        merged = dict(page._heatmap_result.get("clustering") or {})
        merged.update(page._heatmap_dendro_cache)
        if merged:
            page._heatmap_result["clustering"] = merged
        _heatmap_store_save(ctx,
            page._heatmap_task_id, page._heatmap_result
        )
    if ctx.project is not None:
        ctx.project.save()
    page._heatmap_snapshot = copy.deepcopy(page._heatmap_params)
    page._heatmap_dirty = False
    _heatmap_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Heatmap saved")
    ctx._module_saved(page)


def _heatmap_close(ctx, page) -> None:
    if page._heatmap_dirty:
        reply = QMessageBox.question(
            ctx.host, "Close Heatmap",
            "There are unsaved changes.\n"
            "Close without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._heatmap_task_def["params"] = copy.deepcopy(
            page._heatmap_snapshot
        )
    _heatmap_cleanup(ctx, page)
    ctx._close_module_page(page)


def _heatmap_cleanup(ctx, page) -> None:
    page._heatmap_cancel = True
    timer = getattr(page, "_heatmap_timer", None)
    if timer is not None:
        timer.stop()
        page._heatmap_timer = None
