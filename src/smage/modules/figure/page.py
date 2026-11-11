"""Figure module: the workspace of scatter, image and table cards."""
from __future__ import annotations

from PySide6.QtWidgets import QTableWidget

from smage.widgets.controls import _CardProxy

from PySide6.QtCore import Signal

from PySide6.QtCore import (
    QRect,
    QRectF,
    QSize,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QRubberBand,
    QScrollArea,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from collections import Counter
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.formatting import _format_sci
from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_chain,
    _gate_needed_columns,
)
from smage.core.scale_math import (
    _apply_scale_transforms,
    _inverse_scale_value,
    _nice_ticks,
    _round_value_candidates,
)
from smage.core.settings import (
    _debug_log,
    _default_palette,
    _log_exception,
    _setting,
)
from smage.widgets.colormap import _palette_names
from smage.widgets.controls import (
    _LogStepSpinBox,
    _NoWheelComboBox,
    _NoWheelSpinBox,
    _SizeField,
)
from smage.widgets.gate_editor.canvas import (
    _ClickFilter,
    _DoubleClickFilter,
    _NoClickScatter,
)
from smage.widgets.plot.renderer import FIGURE_COLOR_MODES
from smage.widgets.plot_axes import (
    _figure_default_size,
    _is_scatter_plot,
    _scatter_brush,
)
from smage.widgets.resources import _lock_pixmap
import copy
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import sampling as _data_sampling
from smage.data import stores as _data_stores
from smage.widgets import colormap as _widgets_colormap
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import renderer as _widgets_plot_renderer


OVERLAY_LAYER_COLORS = [
    (31, 119, 180),   # blue
    (214, 39, 40),    # red
    (44, 160, 44),    # green
    (255, 127, 14),   # orange
    (148, 103, 189),  # purple
    (23, 190, 207),   # cyan
    (227, 119, 194),  # pink
    (188, 189, 34),   # olive
    (140, 86, 75),    # brown
    (127, 127, 127),  # grey
]


OVERLAY_OTHER_COLOR = (200, 200, 200)


FIGURE_HISTOGRAM_MODES = [
    ("Histogram", "histogram"),
    ("Curve fit", "curve"),
    ("Overlap", "overlap"),
]


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
        self.select_all_cb = None
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
        if (
            event.key() == Qt.Key.Key_A
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and self.select_all_cb is not None
        ):
            self.select_all_cb()
            event.accept()
            return
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

    def __init__(self, plot_widget, index: int, select_callback,
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
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)
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


class _FigurePermuteDialog(QDialog):
    """Generate copies of one plot across files, an axis or filters.

    Only one dimension per run (as in OMIQ): run it again on the results to
    build a grid of, say, parameters × files.
    """

    FILE = "file"
    X_AXIS = "x"
    Y_AXIS = "y"
    FILTER = "filter"

    def __init__(self, parent, plot_type, files, columns, filters) -> None:
        super().__init__(parent)
        self.setWindowTitle("Permute")
        self.resize(560, 560)
        self._files = list(files)
        self._columns = list(columns)
        self._filters = list(filters)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        lay.addWidget(QLabel("<b>Permute by</b>"))
        radios = QGridLayout()
        radios.setHorizontalSpacing(18)
        radios.setVerticalSpacing(4)
        # Radios sharing a parent are auto-exclusive in Qt, so the dimension
        # and layout choices need separate groups or one clears the other.
        self._dimension_group = QButtonGroup(self)
        self._layout_group = QButtonGroup(self)
        self._radios = {}
        specs = [
            (self.FILE, "File", bool(self._files)),
            (self.X_AXIS, "X axis", plot_type != "image" and bool(self._columns)),
            (self.Y_AXIS, "Y axis", _is_scatter_plot(plot_type) and bool(self._columns)),
            (self.FILTER, "Filter", bool(self._filters)),
        ]
        for i, (key, label, enabled) in enumerate(specs):
            radio = QRadioButton(label)
            radio.setEnabled(bool(enabled))
            radio.toggled.connect(lambda checked: checked and self._reload_values())
            self._dimension_group.addButton(radio)
            radios.addWidget(radio, i // 2, i % 2)
            self._radios[key] = radio
        lay.addLayout(radios)

        lay.addWidget(QLabel("<b>Values</b>"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        lay.addWidget(self.search)

        self.values_list = QListWidget()
        self.values_list.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        self.values_list.itemChanged.connect(lambda _i: self._update_preview())
        lay.addWidget(self.values_list, 1)

        pick_row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        all_btn.clicked.connect(lambda _=False: self._check_all(True))
        none_btn = QPushButton("Clear")
        none_btn.clicked.connect(lambda _=False: self._check_all(False))
        pick_row.addWidget(all_btn)
        pick_row.addWidget(none_btn)
        pick_row.addStretch()
        lay.addLayout(pick_row)

        lay.addWidget(QLabel("<b>Layout</b>"))
        self.same_row_radio = QRadioButton("Same row")
        self.new_row_radio = QRadioButton("New row")
        self._layout_group.addButton(self.same_row_radio)
        self._layout_group.addButton(self.new_row_radio)
        self.same_row_radio.setChecked(True)
        lay.addWidget(self.same_row_radio)
        lay.addWidget(self.new_row_radio)

        self.preview = QLabel("")
        self.preview.setStyleSheet("color: #6b7785;")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Permute")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_btn)
        lay.addLayout(buttons)

        for key, radio in self._radios.items():
            if radio.isEnabled():
                radio.setChecked(True)
                break
        self._reload_values()

    # -- values --------------------------------------------------------
    def _reload_values(self) -> None:
        dimension = self.dimension()
        self.values_list.blockSignals(True)
        self.values_list.clear()
        if dimension == self.FILE:
            for path, name in self._files:
                self._add_item(str(name or path), (path, name))
        elif dimension in (self.X_AXIS, self.Y_AXIS):
            for name in self._columns:
                self._add_item(str(name), name)
        elif dimension == self.FILTER:
            for label, payload in self._filters:
                self._add_item(str(label), dict(payload))
        self.values_list.blockSignals(False)
        self._apply_filter()

    def _add_item(self, text: str, payload) -> None:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, payload)
        self.values_list.addItem(item)

    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower()
        for i in range(self.values_list.count()):
            item = self.values_list.item(i)
            item.setHidden(bool(query) and query not in item.text().lower())
        self._update_preview()

    def _check_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.values_list.blockSignals(True)
        for i in range(self.values_list.count()):
            item = self.values_list.item(i)
            if not item.isHidden():
                item.setCheckState(state)
        self.values_list.blockSignals(False)
        self._update_preview()

    def _update_preview(self) -> None:
        count = len(self.values())
        ready = count > 0 and bool(self.dimension())
        self.ok_btn.setEnabled(ready)
        if count:
            self.preview.setText(
                f"{count} new plot(s) will be created; the template stays."
            )
        else:
            self.preview.setText("Select at least one value.")

    # -- results -------------------------------------------------------
    def dimension(self) -> str:
        for key, radio in self._radios.items():
            if radio.isChecked():
                return key
        return ""

    def values(self) -> list:
        out = []
        for i in range(self.values_list.count()):
            item = self.values_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def new_row(self) -> bool:
        return self.new_row_radio.isChecked()


class _OverlayLayerTable(QTableWidget):
    """Overlay-scatter layer table whose rows can be dragged into order.

    Qt's built-in internal move rebuilds the cells and drops their widgets, so
    the drag is handled here with plain mouse events: pressing a row and
    dragging it onto another row emits ``rowMoved(source, target)`` and the
    owner rebuilds the table from its own model.
    """

    rowMoved = Signal(int, int)

    def __init__(self, rows: int = 0, columns: int = 3, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._drag_row = None
        self._drag_start = None
        self._dragging = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            self._drag_row = index.row() if index.isValid() else None
            self._drag_start = event.position().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_row is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            moved = (
                event.position().toPoint() - self._drag_start
            ).manhattanLength()
            if self._dragging or moved >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and self._drag_row is not None:
            source = self._drag_row
            self.unsetCursor()
            self._drag_row = None
            self._dragging = False
            target = self.indexAt(event.position().toPoint()).row()
            if target < 0:
                target = self.rowCount()
            if target > source:
                target -= 1
            if 0 <= target != source:
                self.rowMoved.emit(source, target)
            event.accept()
            return
        self._drag_row = None
        self._dragging = False
        super().mouseReleaseEvent(event)


def _build_figure_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
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
    page._fig_progress = QLabel("")
    page._fig_progress.setStyleSheet("color: #6b7785;")
    tl.addWidget(page._fig_progress)
    page._fig_cancel_btn = QPushButton("Cancel")
    page._fig_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    page._fig_cancel_btn.setToolTip("Stop drawing the queued plots")
    page._fig_cancel_btn.setVisible(False)
    page._fig_cancel_btn.clicked.connect(
        lambda _=False: _figure_cancel_rendering(ctx, page)
    )
    tl.addWidget(page._fig_cancel_btn)
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(lambda _=False: _figure_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)

    # Left: zoomable flow canvas (Word-like, auto-wrapping rows).
    fig_panel = _widgets_panels._build_panel_frame(ctx)
    fl = QVBoxLayout(fig_panel)
    fl.setContentsMargins(10, 10, 10, 10)
    fl.setSpacing(6)
    header = QHBoxLayout()
    header.addWidget(QLabel("<b>Figures</b>"))
    header.addStretch()
    add_scatter = QPushButton("Add Scatter")
    add_overlay = QPushButton("Add Overlay")
    add_overlay.setToolTip(
        "Scatter colored by gates, painted layer over layer"
    )
    add_hist = QPushButton("Add Histogram")
    add_image = QPushButton("Add Image")
    add_nxn = QPushButton("Add N×N Plot")
    add_nxn.setToolTip(
        "Scatter matrix: one plot per variable pair below the diagonal"
    )
    for button in (add_scatter, add_overlay, add_hist, add_image, add_nxn):
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(button)
    fl.addLayout(header)

    scene = QGraphicsScene()
    canvas_view = _ZoomableGraphicsView(scene)
    fl.addWidget(canvas_view, 1)

    # Right: settings panel.
    settings_panel = _widgets_panels._build_panel_frame(ctx)
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

    # Figures canvas (left) | settings (right), resizable; default 2:1.
    fig_splitter = QSplitter(Qt.Orientation.Horizontal)
    fig_splitter.setChildrenCollapsible(False)
    fig_splitter.addWidget(fig_panel)
    fig_splitter.addWidget(settings_panel)
    fig_splitter.setStretchFactor(0, 2)
    fig_splitter.setStretchFactor(1, 1)
    fig_splitter.setSizes([800, 400])
    cl.addWidget(fig_splitter, 1)
    page._fig_splitter = fig_splitter
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
    save_btn.clicked.connect(lambda _=False: _figure_save_now(ctx, page))
    _figure_update_save_btn(ctx, page)
    page._fig_filter_timer = QTimer(ctx.host)
    page._fig_filter_timer.setInterval(100)
    page._fig_filter_timer.timeout.connect(
        lambda: _figure_image_filter_on_timer(ctx, page)
    )
    page._fig_filter_timer.start()
    canvas_view.enter_callback = lambda: _figure_toggle_break(ctx, page, page._fig_selected)
    canvas_view.delete_callback = lambda: _figure_delete_selected(ctx, page)
    canvas_view.unfocus_callback = lambda: _figure_unfocus(ctx, page)
    canvas_view.plot_wheel_cb = lambda item, d: _figure_plot_wheel(ctx, page, item, d)
    canvas_view.plot_pan_start_cb = lambda item, sp: _figure_plot_pan_start(ctx, page, item, sp)
    canvas_view.plot_pan_move_cb = lambda sp: _figure_plot_pan_move(ctx, page, sp)
    canvas_view.plot_pan_end_cb = lambda: _figure_plot_pan_end(ctx, page)
    canvas_view.rubber_select_cb = lambda rect, ctrl: _figure_box_select(ctx, page, rect, ctrl)
    canvas_view.rubber_clear_cb = lambda: _figure_clear_selection(ctx, page)
    canvas_view.select_all_cb = lambda: _figure_select_all(ctx, page)

    for plot_dict in page._fig_plots:
        _figure_new_card(ctx, page, plot_dict)

    add_scatter.clicked.connect(lambda _=False: _figure_add_plot(ctx, page, "scatter"))
    add_overlay.clicked.connect(lambda _=False: _figure_add_plot(ctx, page, "overlay"))
    add_hist.clicked.connect(lambda _=False: _figure_add_plot(ctx, page, "histogram"))
    add_image.clicked.connect(lambda _=False: _figure_add_plot(ctx, page, "image"))
    add_nxn.clicked.connect(lambda _=False: _figure_add_nxn(ctx, page))

    if locked:
        add_scatter.setEnabled(False)
        add_overlay.setEnabled(False)
        add_hist.setEnabled(False)
        add_image.setEnabled(False)
        add_nxn.setEnabled(False)

    _figure_relayout(ctx, page)
    if page._fig_plots:
        _figure_select(ctx, page, 0)
    else:
        _figure_rebuild_settings(ctx, page)
    return page


def _figure_new_card(ctx, page, plot_dict, render: bool=True) -> int:
    """Create a plot card for one plot, wrap it in a proxy and render it.

    `render=False` leaves the card empty so callers can draw big batches
    of new plots over several event-loop turns.
    """
    plot_widget = _widgets_plot_renderer._figure_make_plot_widget(ctx, plot_dict)

    index = len(page._fig_cards)
    size = plot_dict.get("size")
    if size is None:
        size = _figure_default_size(plot_dict.get("type"))
        plot_dict["size"] = list(size)
    w, h = int(size[0]), int(size[1])

    card = _FigureCard(
        plot_widget, index,
        select_callback=lambda i: _figure_select(ctx, page, i),
        side_widget=(
            _widgets_plot_renderer._figure_attach_legend(ctx, plot_widget)
            if plot_dict.get("type") == "image" else None
        ),
    )
    proxy = _CardProxy(
        on_select=lambda ctrl: _figure_card_clicked(ctx, page, card.index(), ctrl),
        on_drop=lambda pos: _figure_reorder(ctx, page, card.index(), pos),
        on_drag=lambda pos: _figure_drag_preview(ctx, page, card.index(), pos),
        on_drag_end=lambda: _figure_clear_drop_marker(ctx, page),
        on_focus=lambda: _figure_focus(ctx, page, card.index()),
        on_context=lambda pos: _figure_card_context_menu(ctx,
            page, card.index(), pos
        ),
    )
    proxy.setWidget(card)
    proxy.resize(w, h)
    page._fig_scene.addItem(proxy)
    card._proxy = proxy

    # Select / focus on clicks that reach the plot itself (the "A"/"F"
    # corner buttons and the legend); the card proxy handles the rest.
    click_filter = _ClickFilter(
        lambda ctrl: _figure_card_clicked(ctx, page, card.index(), ctrl)
    )
    card.installEventFilter(click_filter)
    plot_widget.viewport().installEventFilter(click_filter)
    page._fig_filters = getattr(page, "_fig_filters", [])
    page._fig_filters.append(click_filter)

    # Double-click focuses the plot (wheel/drag act on its data).
    dbl_click_filter = _DoubleClickFilter(lambda: _figure_focus(ctx, page, card.index()))
    card.installEventFilter(dbl_click_filter)
    plot_widget.viewport().installEventFilter(dbl_click_filter)
    page._fig_filters.append(dbl_click_filter)

    # Image plots re-sample (LOD) when the user zooms/pans their data.
    if plot_dict.get("type") == "image":
        vb = plot_widget.getPlotItem().getViewBox()
        rt = QTimer(plot_widget)
        rt.setSingleShot(True)
        rt.setInterval(30)
        rt.timeout.connect(
            lambda: _figure_image_refresh(ctx, page, plot_dict, plot_widget)
        )
        vb.sigRangeChanged.connect(lambda *_: rt.start())
        plot_widget._img_refresh_timer = rt

    page._fig_cards.append(card)
    if render:
        _widgets_plot_renderer._figure_render(ctx,
            plot_dict, plot_widget,
            column_transforms=page._fig_column_transforms,
            task_id=page._fig_task_id,
        )
    _figure_relayout(ctx, page)
    return index


def _figure_resize_card(ctx, page, index: int, w: int, h: int) -> None:
    if 0 <= index < len(page._fig_plots):
        page._fig_plots[index]["size"] = [int(w), int(h)]
        try:
            page._fig_cards[index].proxy().resize(int(w), int(h))
        except RuntimeError:
            return  # proxy already deleted (e.g. during teardown)
        _figure_relayout(ctx, page)


def _figure_mark_dirty(ctx, page) -> None:
    page._fig_dirty = True
    _figure_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _figure_save_now(ctx, page) -> None:
    if ctx.project is not None:
        ctx.project.save()
    page._fig_snapshot = copy.deepcopy(page._fig_task_def.get("params", {}))
    page._fig_dirty = False
    _figure_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Figure saved")
    ctx._module_saved(page)


def _figure_close(ctx, page) -> None:
    """Close the page, prompting first if there are unsaved changes."""
    if page._fig_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Figure",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._fig_task_def["params"] = page._fig_snapshot
    ctx._close_module_page(page)


def _figure_update_save_btn(ctx, page) -> None:
    page._fig_save_btn.setEnabled(page._fig_dirty and not page._fig_locked)


def _figure_reorder(ctx, page, index: int, scene_pos) -> None:
    plots = page._fig_plots
    cards = page._fig_cards
    _figure_clear_drop_marker(ctx, page)
    if not (0 <= index < len(plots)):
        return
    new_index = _figure_drop_index(ctx, page, index, scene_pos)
    if new_index == index:
        _figure_relayout(ctx, page)
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
    _figure_relayout(ctx, page)
    _figure_mark_dirty(ctx, page)


def _figure_drop_marker_pos(ctx, page, index: int, scene_pos):
    """Return (x, y, w, h) of the slot the dragged card would land in."""
    if not page._fig_cards:
        return None
    new_index = _figure_drop_index(ctx, page, index, scene_pos)
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
    positions = _figure_flow_positions(ctx, cards, plots)
    if not (0 <= new_index < len(positions)):
        return None
    return positions[new_index]


def _figure_drag_preview(ctx, page, index: int, scene_pos) -> None:
    """Show the insertion slot while a card is being dragged."""
    marker = getattr(page, "_fig_drop_marker", None)
    where = _figure_drop_marker_pos(ctx, page, index, scene_pos)
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


def _figure_clear_drop_marker(ctx, page) -> None:
    marker = getattr(page, "_fig_drop_marker", None)
    if marker is not None:
        page._fig_scene.removeItem(marker)
        page._fig_drop_marker = None


def _figure_flow_positions(ctx, cards, plots) -> list:
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


def _figure_drop_index(ctx, page, index: int, scene_pos) -> int:
    """Compute the insertion index for the dragged card at `scene_pos`."""
    others_cards = [c for i, c in enumerate(page._fig_cards) if i != index]
    others_plots = [p for i, p in enumerate(page._fig_plots) if i != index]
    if not others_cards:
        return 0
    pos = _figure_flow_positions(ctx, others_cards, others_plots)
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


def _figure_toggle_break(ctx, page, indices) -> None:
    """Toggle a line break before each selected plot (like pressing Enter)."""
    changed = False
    for index in indices:
        if 0 <= index < len(page._fig_plots):
            p = page._fig_plots[index]
            p["break_before"] = not p.get("break_before", False)
            changed = True
    if changed:
        _figure_relayout(ctx, page)
        _figure_mark_dirty(ctx, page)


def _figure_card_context_menu(ctx, page, index, global_pos) -> None:
    """Right-click menu for a plot card (Duplicate / Delete / Permute)."""
    if not (0 <= index < len(page._fig_cards)):
        return
    if index not in page._fig_selected:
        _figure_set_selection(ctx, page, [index])
    selected = list(page._fig_selected)
    menu = QMenu(ctx.host)
    duplicate = menu.addAction("Duplicate")
    delete = menu.addAction("Delete")
    menu.addSeparator()
    permute = menu.addAction("Permute…")
    # Permute works on one template; with several selected it is ambiguous.
    permute.setEnabled(len(selected) == 1)
    permute.setToolTip(
        "Select a single plot to permute it across files, an axis or filters"
    )
    _debug_log(f"card menu: index={index} selected={selected} plots={len(page._fig_plots)}")
    chosen = menu.exec(global_pos)
    _debug_log(f"card menu chose: {chosen.text() if chosen is not None else None}")
    if chosen is None:
        return
    if chosen is duplicate:
        _figure_duplicate_selected(ctx, page)
    elif chosen is delete:
        _figure_delete_selected(ctx, page)
    elif chosen is permute:
        _figure_permute(ctx, page, selected[0])


def _figure_scroll_to(ctx, page, index) -> None:
    """Make a card visible: new cards often land outside the viewport."""
    view = getattr(page, "_fig_view", None)
    if view is None or not (0 <= index < len(page._fig_cards)):
        return
    proxy = page._fig_cards[index].proxy()
    if proxy is None:
        return
    view.ensureVisible(proxy, 48, 48)


def _figure_insert_plot(ctx, page, plot_dict, target: int) -> int:
    """Create a card for `plot_dict` and place it at `target`."""
    page._fig_plots.append(plot_dict)
    index = _figure_new_card(ctx, page, plot_dict)
    target = max(0, min(int(target), len(page._fig_plots) - 1))
    if target != index:
        page._fig_plots.insert(target, page._fig_plots.pop(index))
        page._fig_cards.insert(target, page._fig_cards.pop(index))
        for i, card in enumerate(page._fig_cards):
            card.set_index(i)
        _figure_relayout(ctx, page)
    return target


def _figure_duplicate_selected(ctx, page) -> None:
    """Copy every selected plot, inserting the copies to their right."""
    indices = sorted(set(page._fig_selected))
    if not indices:
        return
    target = indices[-1] + 1
    for offset, index in enumerate(indices):
        if not (0 <= index < len(page._fig_plots)):
            continue
        clone = copy.deepcopy(page._fig_plots[index])
        clone["break_before"] = False
        _figure_insert_plot(ctx, page, clone, target + offset)
    _figure_mark_dirty(ctx, page)
    _figure_set_selection(ctx,
        page, list(range(target, target + len(indices)))
    )
    _figure_scroll_to(ctx, page, target)


def _figure_permute(ctx, page, index) -> None:
    """Generate copies of one plot across a file/axis/filter dimension."""
    if not (0 <= index < len(page._fig_plots)):
        return
    plot = page._fig_plots[index]
    records = _data_files._available_files(ctx, page._fig_task_id)
    files = [
        (rec.get("parquet"), rec.get("name"))
        for rec in records
        if rec.get("parquet")
    ]
    rec = _widgets_plot_renderer._figure_record(ctx, plot.get("file"))
    columns = (
        _data_columns._columns_for(ctx, rec.get("parquet"), page._fig_task_id) if rec else []
    )
    filters = _figure_filter_options(ctx, page._fig_task_id)
    _debug_log(
        f"permute open: type={plot.get('type')} files={len(files)} "
        f"columns={len(columns)} filters={len(filters)}"
    )
    dialog = ctx._figure_permute_dialog(
        plot.get("type"), files, columns, filters
    )
    result = dialog.exec()
    dimension = dialog.dimension()
    values = dialog.values()
    _debug_log(
        f"permute result={int(result)} dim={dimension!r} values={len(values)} "
        f"new_row={dialog.new_row()} accepted={result == QDialog.DialogCode.Accepted}"
    )
    if result != QDialog.DialogCode.Accepted:
        return
    if not dimension or not values:
        QMessageBox.information(
            ctx.host,
            "Permute",
            "No values were selected, so nothing was created.",
        )
        return
    new_row = dialog.new_row()
    target = index + 1
    try:
        for offset, value in enumerate(values):
            clone = copy.deepcopy(plot)
            if dimension == _FigurePermuteDialog.FILE:
                path, name = value
                clone["file"] = path
                clone["file_name"] = name
            elif dimension == _FigurePermuteDialog.X_AXIS:
                clone["x"] = value
                clone["last_x"] = value
            elif dimension == _FigurePermuteDialog.Y_AXIS:
                clone["y"] = value
                clone["last_y"] = value
            elif dimension == _FigurePermuteDialog.FILTER:
                clone["filter"] = dict(value)
            clone["break_before"] = bool(new_row)
            _figure_insert_plot(ctx, page, clone, target + offset)
    except Exception as exc:  # noqa: BLE001
        _log_exception(exc)
        QMessageBox.critical(
            ctx.host, "Permute", f"Could not create the plots:\n{exc}"
        )
        return
    _figure_mark_dirty(ctx, page)
    _figure_set_selection(ctx, page, list(range(target, target + len(values))))
    _figure_scroll_to(ctx, page, target)
    _debug_log(
        f"permute done: plots={len(page._fig_plots)} "
        f"cards={len(page._fig_cards)} selected={page._fig_selected}"
    )


def _figure_delete_selected(ctx, page) -> None:
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
        _figure_relayout(ctx, page)
        _figure_set_selection(ctx, page, page._fig_selected)
    else:
        page._fig_selected = []
        _figure_relayout(ctx, page)
        _figure_rebuild_settings(ctx, page)
    _figure_mark_dirty(ctx, page)


def _figure_relayout(ctx, page) -> None:
    """Flow-layout the cards (Word-like): one row, wrapped by line breaks."""
    positions = _figure_flow_positions(ctx, page._fig_cards, page._fig_plots)
    for (x, y, w, h), card in zip(positions, page._fig_cards):
        card.proxy().setPos(x, y)
    _figure_update_scene_rect(ctx, page)


def _figure_update_scene_rect(ctx, page) -> None:
    if not page._fig_cards:
        page._fig_scene.setSceneRect(0, 0, 800, 600)
        return
    rect = page._fig_scene.itemsBoundingRect()
    page._fig_scene.setSceneRect(rect.adjusted(-16, -16, 16, 16))


def _figure_add_plot(ctx, page, ptype: str) -> None:
    """Append a new plot (scatter or histogram) and select it."""
    records = _data_files._available_files(ctx, page._fig_task_id)
    rec = records[0] if records else None
    columns = _data_columns._columns_for(ctx,
        rec.get("parquet"), page._fig_task_id
    ) if rec else []
    x = columns[0] if columns else None
    y = columns[1] if len(columns) > 1 else x
    if ptype == "image":
        # Image plots default to coloring by the first non-coordinate channel.
        color_mode = "parameter"
        color_param = next((c for c in columns if c not in ("x", "y")), None)
    elif ptype == "overlay":
        # Overlay scatter colors by layer; no parameter coloring.
        color_mode = "overlay"
        color_param = None
    else:
        color_mode = "density"
        color_param = x
    plot_dict = {
        "type": ptype,
        "file": rec.get("parquet") if rec else None,
        "file_name": rec.get("name") if rec else None,
        "x": x,
        "y": y if _is_scatter_plot(ptype) else None,
        "last_x": x,
        "last_y": y if _is_scatter_plot(ptype) else None,
        "color_mode": color_mode,
        "color_param": color_param,
        "last_color_param": color_param,
        "palette": _default_palette(),
        "layers": (
            [{"parameter": color_param, "color": (255, 255, 255),
              "name": str(color_param), "visible": True}]
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
        "size": _figure_default_size(ptype),
    }
    page._fig_plots.append(plot_dict)
    index = _figure_new_card(ctx, page, plot_dict)
    _figure_mark_dirty(ctx, page)
    _figure_select(ctx, page, index)
    _figure_scroll_to(ctx, page, index)


def _figure_add_nxn(ctx, page) -> None:
    """Add a lower-triangle scatter matrix for a set of variables."""
    records = [
        rec for rec in _data_files._available_files(ctx, page._fig_task_id)
        if rec.get("parquet")
    ]
    if not records:
        QMessageBox.information(
            ctx.host, "Add N×N Plot", "No data file is available."
        )
        return
    # Candidate variables: everything visible to this Figure module, i.e.
    # the union over every file (upstream generated columns included).
    file_columns = {}
    columns = []
    for rec in records:
        cols = _data_columns._columns_for(ctx, rec.get("parquet"), page._fig_task_id)
        file_columns[rec.get("parquet")] = cols
        for name in cols:
            if name not in columns:
                columns.append(name)
    if len(columns) < 2:
        QMessageBox.information(
            ctx.host, "Add N×N Plot",
            "This Figure module has fewer than two variables.",
        )
        return

    def summary(count: int) -> str:
        if count < 2:
            return "Select at least 2 variables"
        return f"{count} variables → {count * (count - 1) // 2} plots"

    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Add N×N Plot", columns, [], "Filter variables…",
        reorder=True, summary=summary,
    )
    if chosen is None:
        return
    if len(chosen) < 2:
        QMessageBox.information(
            ctx.host, "Add N×N Plot", "Select at least two variables."
        )
        return
    # Use the file that actually has every chosen variable: the selected
    # plot's file when possible, otherwise the first one that fits.
    record = None
    if page._fig_selected:
        index = page._fig_selected[0]
        if 0 <= index < len(page._fig_plots):
            candidate = _widgets_plot_renderer._figure_record(ctx,
                page._fig_plots[index].get("file")
            )
            path = candidate.get("parquet") if candidate else None
            if path and all(c in file_columns.get(path, []) for c in chosen):
                record = candidate
    if record is None:
        record = next(
            (
                rec for rec in records
                if all(
                    c in file_columns.get(rec.get("parquet"), [])
                    for c in chosen
                )
            ),
            records[0],
        )
    total = len(chosen) * (len(chosen) - 1) // 2
    if total > 100:
        reply = QMessageBox.question(
            ctx.host,
            "Add N×N Plot",
            f"{len(chosen)} variables create {total} plots.\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    limit = int(_setting("plot", "nxn_max_points", 5000) or 5000)
    plots = []
    for row in range(1, len(chosen)):
        for column in range(row):
            plots.append({
                "type": "scatter",
                "file": record.get("parquet"),
                "file_name": record.get("name"),
                "x": chosen[column],
                "last_x": chosen[column],
                "y": chosen[row],
                "last_y": chosen[row],
                "color_mode": "density",
                "color_param": chosen[column],
                "last_color_param": chosen[column],
                "palette": _default_palette(),
                "layers": [],
                "xmin": None,
                "xmax": None,
                "ymin": None,
                "ymax": None,
                # Each matrix row starts its own line of cards.
                "break_before": column == 0,
                "show_axes": True,
                "show_axis_names": True,
                # A matrix would be unreadable with every label on.
                "show_indices": False,
                "show_sample_name": False,
                "point_size": 4,
                "fixed_point_size": False,
                "bins": 20,
                "hist_mode": "histogram",
                "hist_color": (0, 0, 200),
                "curve_color": (200, 40, 40),
                "solid_color": (0, 0, 200),
                "filter": None,
                "size": _figure_default_size("scatter"),
                # Matrix plots are one of many: keep fewer points.
                "max_points": limit,
            })
    _figure_add_batch(ctx, page, plots)


def _figure_add_batch(ctx, page, plots) -> None:
    """Create many cards at once and draw them over several event turns."""
    if not plots:
        return
    first = len(page._fig_cards)
    for plot in plots:
        page._fig_plots.append(plot)
        _figure_new_card(ctx, page, plot, render=False)
    _figure_relayout(ctx, page)
    _figure_mark_dirty(ctx, page)
    _figure_set_selection(ctx,
        page, list(range(first, first + len(plots)))
    )
    _figure_scroll_to(ctx, page, first)
    _figure_render_queue(ctx,
        page, list(range(first, first + len(plots)))
    )
    _debug_log(f"nxn added: plots={len(page._fig_plots)} batch={len(plots)}")


def _figure_render_queue(ctx, page, indices) -> None:
    """Draw plot cards in small batches so the window stays responsive."""
    indices = [i for i in indices if 0 <= i < len(page._fig_cards)]
    if not indices:
        return
    existing = getattr(page, "_fig_batch_timer", None)
    if existing is not None:
        existing.stop()
        existing.deleteLater()
    state = {"queue": list(indices), "total": len(indices)}
    page._fig_batch_state = state

    def step() -> None:
        queue = state["queue"]
        batch = queue[:2]
        del queue[:2]
        for i in batch:
            if not (0 <= i < len(page._fig_plots) and i < len(page._fig_cards)):
                continue
            try:
                _widgets_plot_renderer._figure_render(ctx,
                    page._fig_plots[i],
                    page._fig_cards[i].plot_widget(),
                    column_transforms=page._fig_column_transforms,
                    task_id=page._fig_task_id,
                )
            except Exception as exc:  # noqa: BLE001
                _log_exception(exc)
        # Paint the visible cards now: letting Qt coalesce the paints
        # would repaint every new card in one long blocking pass.
        view = getattr(page, "_fig_view", None)
        if view is not None:
            view.viewport().repaint()
        done = state["total"] - len(queue)
        _figure_update_progress(ctx, page, done, state["total"])
        ctx.statusBar().showMessage(
            f"Building plots: {done} / {state['total']}"
        )
        if not queue:
            timer.stop()
            _figure_update_progress(ctx, page, 0, 0)
            ctx.statusBar().showMessage("Plots ready", 3000)

    timer = QTimer(page)
    timer.setInterval(0)
    timer.timeout.connect(step)
    page._fig_batch_timer = timer
    timer.start()


def _figure_update_progress(ctx, page, done, total) -> None:
    label = getattr(page, "_fig_progress", None)
    button = getattr(page, "_fig_cancel_btn", None)
    if label is not None:
        label.setText(f"Rendering {done} / {total}" if total else "")
    if button is not None:
        button.setVisible(bool(total))


def _figure_cancel_rendering(ctx, page) -> None:
    """Stop the queued plot batch (plots already drawn stay)."""
    timer = getattr(page, "_fig_batch_timer", None)
    if timer is not None:
        timer.stop()
    state = getattr(page, "_fig_batch_state", None)
    if state is not None:
        for index in state["queue"]:
            if 0 <= index < len(page._fig_cards):
                widget = page._fig_cards[index].plot_widget()
                widget._fig_cancelled = True
                widget.clear()
                widget.setTitle(
                    "Cancelled", color="#6b7785", size="10pt"
                )
        state["queue"].clear()
    _figure_update_progress(ctx, page, 0, 0)
    ctx.statusBar().showMessage("Plot rendering cancelled", 3000)


def _figure_bump_render_token(ctx, plot_widget) -> int:
    """Mark the plot's latest render request; older callbacks must not draw."""
    token = int(getattr(plot_widget, "_fig_render_token", 0)) + 1
    plot_widget._fig_render_token = token
    return token


def _figure_render_histogram_when_ready(ctx, plot_dict, plot_widget, transforms, mask, task_id, stages, token=None) -> None:
    if getattr(plot_widget, "_fig_cancelled", False):
        return
    if token is not None and getattr(
        plot_widget, "_fig_render_token", token
    ) != token:
        # The page switched plot type / file while the pool was loading.
        return
    _widgets_plot_renderer._figure_render_histogram(ctx,
        plot_dict, plot_widget, transforms, mask, task_id, stages
    )


def _figure_render_when_ready(ctx, plot_dict, plot_widget, transforms, mask, task_id, stages, token=None) -> None:
    if getattr(plot_widget, "_fig_cancelled", False):
        return
    if token is not None and getattr(
        plot_widget, "_fig_render_token", token
    ) != token:
        # The page switched plot type / file while the pool was loading.
        return
    _widgets_plot_renderer._figure_render_scatter(ctx,
        plot_dict, plot_widget, transforms, mask, task_id, stages
    )


def _figure_filter_options(ctx, figure_task_id):
    """Return [(display_name, {"task_id":..,"gate_id":..}), ...] for gates
    in gating modules strictly upstream of the figure module."""
    options = []
    for task in _data_files._task_chain(ctx, figure_task_id):
        if task.get("id") == figure_task_id:
            continue
        if task.get("type") != "gating":
            continue
        module_name = task.get("name", "Gating")
        for gate in _data_stores._gating_gates_for(ctx, task):
            options.append((
                f"{module_name} → {gate.get('name', gate.get('id'))}",
                {"task_id": task.get("id"), "gate_id": gate.get("id")},
            ))
    return options


def _figure_filter_mask(ctx, plot_dict, row_task_id=None):
    """Return (mask, error) for the plot's filter, or (None, None) when
    there is no filter (or it is trivially empty).

    `row_task_id` is the plotting module: the mask is reduced to the rows
    that module can see, so it lines up with its (possibly sampled) data.
    """
    import numpy as np

    f = plot_dict.get("filter")
    if not f:
        return None, None
    gating_task = ctx._find_task_by_id(f.get("task_id"))
    if gating_task is None:
        return None, "Filter module not found"
    rec = _widgets_plot_renderer._figure_record(ctx, plot_dict.get("file"))
    if rec is None:
        return None, "No data file"
    gates = _data_stores._gating_gates_for(ctx, gating_task, rec["parquet"])
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
    filter_task = f.get("task_id")
    values = _data_columns._column_data(ctx,
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
    if row_task_id:
        index = _data_sampling._subsample_row_index(ctx, row_task_id, rec["parquet"])
        if index is not None and len(mask) == _data_columns._data_row_count(ctx, rec["parquet"]):
            mask = np.asarray(mask)[index]
    return mask, None


def _figure_image_filter_error(ctx, f, parquet_path):
    """Return an error string for an invalid filter, or None if it is valid."""
    gating_task = ctx._find_task_by_id(f.get("task_id"))
    if gating_task is None:
        return "Filter module not found"
    gates = _data_stores._gating_gates_for(ctx, gating_task, parquet_path)
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
    values = _data_columns._column_data(ctx, f.get("task_id"), parquet_path, list(needed))
    missing = needed - set(values)
    if missing:
        return "Filter parameter not in this file: " + ", ".join(
            sorted(str(m) for m in missing)
        )
    return None


def _figure_update_image_legend(ctx, plot_widget, layers, show=True) -> None:
    """Update the colour legend shown beside an image plot."""
    widget = getattr(plot_widget, "_legend_widget", None)
    if widget is None:
        return
    if not show or not layers:
        widget.setVisible(False)
        return
    widget.set_layers(layers)
    widget.setVisible(True)


def _figure_image_refresh(ctx, page, plot_dict, plot_widget) -> None:
    _widgets_plot_renderer._figure_render_image(ctx,
        plot_dict, plot_widget,
        column_transforms=page._fig_column_transforms,
        task_id=page._fig_task_id,
    )


def _figure_image_filter_on_timer(ctx, page) -> None:
    """Poll the filter worker: cache finished masks and re-render matching plots."""
    worker = getattr(ctx, "_image_filter_worker_obj", None)
    if worker is None or not hasattr(page, "_fig_plots"):
        return
    completed = []
    for key, result in worker.poll():
        getattr(ctx, "_image_filter_cache", {})[key] = result
        getattr(ctx, "_image_filter_pending", {}).pop(key, None)
        completed.append(key)
    if not completed:
        return
    for i, p in enumerate(page._fig_plots):
        if p.get("type") != "image" or i >= len(page._fig_cards):
            continue
        rec = _widgets_plot_renderer._figure_record(ctx, p.get("file"))
        path = rec.get("parquet") if rec else None
        f = p.get("filter")
        if not f or not path:
            continue
        key = (path, (f.get("task_id"), f.get("gate_id")))
        if key in completed:
            _widgets_plot_renderer._figure_render_image(ctx,
                p, page._fig_cards[i].plot_widget(),
                column_transforms=page._fig_column_transforms,
                task_id=page._fig_task_id,
            )


def _figure_draw_colored_scatter(ctx, plot_widget, x, y, values, cmap, size: float=4, px_mode: bool=True, solid_color=None, point_colors=None) -> None:
    """Draw points colored by `values`, normalized through the colormap.

    When `point_colors` is given, each point uses its own RGB colour
    (overlay scatter). When `solid_color` is given, every point uses that
    single color instead.
    """
    import numpy as np
    import pyqtgraph as pg

    if point_colors is not None:
        brushes = [
            _scatter_brush(int(c[0]), int(c[1]), int(c[2]))
            for c in point_colors
        ]
    elif solid_color is not None:
        brushes = _scatter_brush(
            int(solid_color[0]), int(solid_color[1]), int(solid_color[2])
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
        # Reuse QBrush objects: pyqtgraph caches a sprite per brush
        # *object*, so sharing them cuts the item build from hundreds of
        # milliseconds to tens for 10k–50k points.
        brushes = [
            _scatter_brush(int(r), int(g), int(b)) for r, g, b, _ in rgba
        ]

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
    if getattr(plot_widget, "_gate_cache_data", False):
        # Gating plot: cache the rendered points so moving a gate ROI does
        # not re-run the per-point paint.
        scatter.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)


def _figure_original_tick_state(ctx, plot_widget) -> dict:
    """Per-plot state for axes whose ticks show untransformed values."""
    state = getattr(plot_widget, "_orig_ticks", None)
    if state is not None:
        return state
    state = {"bottom": None, "left": None}
    plot_widget._orig_ticks = state
    timer = QTimer(plot_widget)
    timer.setSingleShot(True)
    timer.setInterval(40)
    timer.timeout.connect(
        lambda: _figure_refresh_original_ticks(ctx, plot_widget)
    )
    state["timer"] = timer
    # Zooming / panning re-picks the ticks for the visible range.
    plot_widget.getPlotItem().getViewBox().sigRangeChanged.connect(
        lambda *_: timer.start()
    )
    return state


def _figure_set_original_ticks(ctx, plot_widget, orientation, configs, values=None) -> None:
    """Label axis ticks with the *untransformed* values of a scaled column.

    The points stay in transformed space; the tick text shows original
    numbers (arcsinh / log10 axes, like FlowJo) and is recomputed for the
    visible range whenever the view is zoomed or panned.
    """
    state = _figure_original_tick_state(ctx, plot_widget)
    state[orientation] = list(configs) if configs else None
    _figure_update_original_axis(ctx, plot_widget, orientation)
    # The view range may still settle after this render: refresh shortly.
    timer = state.get("timer")
    if timer is not None:
        timer.start()


def _figure_refresh_original_ticks(ctx, plot_widget) -> None:
    state = getattr(plot_widget, "_orig_ticks", None)
    if not state:
        return
    _figure_update_original_axis(ctx, plot_widget, "bottom")
    _figure_update_original_axis(ctx, plot_widget, "left")


def _figure_update_original_axis(ctx, plot_widget, orientation) -> None:
    """Rebuild one axis' ticks from the currently visible range."""
    import numpy as np

    state = getattr(plot_widget, "_orig_ticks", None) or {}
    configs = state.get(orientation)
    axis = plot_widget.getPlotItem().getAxis(orientation)
    if not configs:
        axis.setTicks(None)
        return
    try:
        (x0, x1), (y0, y1) = plot_widget.getPlotItem().getViewBox().viewRange()
    except (TypeError, ValueError):
        axis.setTicks(None)
        return
    lo, hi = (x0, x1) if orientation == "bottom" else (y0, y1)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        axis.setTicks(None)
        return
    original_lo = _inverse_scale_value(lo, configs)
    original_hi = _inverse_scale_value(hi, configs)
    if (
        not np.isfinite(original_lo)
        or not np.isfinite(original_hi)
        or original_hi <= original_lo
    ):
        axis.setTicks(None)
        return
    ticks = []
    # Place the ticks evenly along the *displayed* (transformed) axis, then
    # snap each to a round data value. A uniform step in the original
    # domain bunches up under log10 / arcsinh.
    candidates = []
    for value in _round_value_candidates(original_lo, original_hi):
        position = float(
            np.asarray(_apply_scale_transforms(
                np.asarray([value], dtype="float64"), configs
            ))[0]
        )
        if np.isfinite(position) and lo <= position <= hi:
            candidates.append((position, value))
    targets = [
        lo + (hi - lo) * i / 5.0 for i in range(6)
    ]
    used = set()
    majors = []  # (position, data value)
    for target in targets:
        best = None
        for position, value in candidates:
            if value in used:
                continue
            if best is None or abs(position - target) < abs(best[0] - target):
                best = (position, value)
        if best is not None:
            used.add(best[1])
            majors.append(best)
    if len(majors) < 3:
        # Too few round values in view: fall back to a uniform data step.
        majors = []
        for value in _nice_ticks(original_lo, original_hi, 6):
            position = float(
                np.asarray(_apply_scale_transforms(
                    np.asarray([value], dtype="float64"), configs
                ))[0]
            )
            if np.isfinite(position):
                majors.append((position, value))
    majors.sort(key=lambda item: item[0])
    if not majors:
        axis.setTicks(None)
        return
    # Minor ticks: five even steps *in the data domain* between adjacent
    # labels, mapped through the transform. They end up unevenly spaced on
    # screen, which shows the axis is nonlinear.
    minors = []
    for (p0, v0), (p1, v1) in zip(majors[:-1], majors[1:]):
        span = v1 - v0
        if span == 0:
            continue
        for step in range(1, 6):
            value = v0 + span * step / 6.0
            position = float(
                np.asarray(_apply_scale_transforms(
                    np.asarray([value], dtype="float64"), configs
                ))[0]
            )
            if np.isfinite(position) and min(p0, p1) < position < max(p0, p1):
                minors.append((position, ""))
    labels = [(position, _format_sci(value)) for position, value in majors]
    axis.setTicks([labels, minors] if minors else [labels])


def _figure_apply_change(ctx, page, index: int, plot_dict: dict) -> None:
    """Re-render a plot after a settings change (marks the page dirty)."""
    if 0 <= index < len(page._fig_cards):
        _widgets_plot_renderer._figure_render(ctx,
            plot_dict, page._fig_cards[index].plot_widget(),
            column_transforms=page._fig_column_transforms,
            task_id=page._fig_task_id,
        )
    _figure_mark_dirty(ctx, page)


def _figure_apply_display(ctx, page, index: int, plot_dict: dict) -> None:
    """Re-apply only the labels/ticks/title of one plot.

    Display changes (checkboxes, font sizes) never touch the data, so
    skipping the full render keeps them instant.
    """
    if 0 <= index < len(page._fig_cards):
        _widgets_plot_renderer._figure_apply_display_options(ctx,
            plot_dict, page._fig_cards[index].plot_widget()
        )
    _figure_mark_dirty(ctx, page)


def _figure_apply_display_all(ctx, page) -> None:
    """Re-apply only the labels/ticks/title of every selected plot."""
    for i in page._fig_selected:
        if 0 <= i < len(page._fig_cards):
            _widgets_plot_renderer._figure_apply_display_options(ctx,
                page._fig_plots[i], page._fig_cards[i].plot_widget()
            )
    _figure_mark_dirty(ctx, page)


def _figure_apply_change_multi(ctx, page) -> None:
    """Re-render every selected plot after a batch edit (marks the page dirty)."""
    for i in page._fig_selected:
        if 0 <= i < len(page._fig_cards):
            _widgets_plot_renderer._figure_render(ctx,
                page._fig_plots[i], page._fig_cards[i].plot_widget(),
                column_transforms=page._fig_column_transforms,
                task_id=page._fig_task_id,
            )
    _figure_mark_dirty(ctx, page)


def _figure_refresh_all(ctx, page) -> None:
    """Re-render every plot card (used when an upstream module saved)."""
    page._fig_column_transforms = {}  # upstream axes are applied by _column_data
    # Gate definitions may have changed upstream; drop cached image filters.
    ctx._image_filter_cache = {}
    ctx._image_filter_pending = {}
    for i, card in enumerate(page._fig_cards):
        if i < len(page._fig_plots):
            plot_widget = card.plot_widget()
            vb = plot_widget.getPlotItem().getViewBox()
            xr, yr = vb.viewRange()
            _widgets_plot_renderer._figure_render(ctx,
                page._fig_plots[i], plot_widget,
                column_transforms=page._fig_column_transforms,
                task_id=page._fig_task_id,
            )
            # Keep the view range; only the data (and gate) changed.
            vb.disableAutoRange()
            vb.setRange(xRange=(xr[0], xr[1]), yRange=(yr[0], yr[1]), padding=0)


def _figure_set_selection(ctx, page, indices) -> None:
    """Set the selected plot set (a list of indices) and refresh settings."""
    indices = sorted(set(i for i in indices if 0 <= i < len(page._fig_cards)))
    page._fig_selected = indices
    sel = set(indices)
    for i, card in enumerate(page._fig_cards):
        card.set_selected(i in sel)
    _figure_rebuild_settings(ctx, page)
    page._fig_view.setFocus()


def _figure_select(ctx, page, index: int) -> None:
    """Select a single plot (clicking a card with no modifier)."""
    _figure_set_selection(ctx, page, [index])
    if page._fig_focused != index:
        _figure_unfocus(ctx, page)


def _figure_card_clicked(ctx, page, index: int, ctrl: bool) -> None:
    """Handle a click on a card: Ctrl toggles it in the selection."""
    if ctrl:
        sel = list(page._fig_selected)
        if index in sel:
            sel.remove(index)
        else:
            sel.append(index)
        _figure_unfocus(ctx, page)
        _figure_set_selection(ctx, page, sel)
    else:
        _figure_select(ctx, page, index)


def _figure_box_select(ctx, page, scene_rect, ctrl: bool) -> None:
    """Select every plot whose card intersects the rubber-band rectangle."""
    hits = []
    for i, card in enumerate(page._fig_cards):
        if scene_rect.intersects(card.proxy().sceneBoundingRect()):
            hits.append(i)
    if ctrl:
        sel = set(page._fig_selected)
        sel.update(hits)
        hits = sorted(sel)
    _figure_unfocus(ctx, page)
    _figure_set_selection(ctx, page, hits)


def _figure_select_all(ctx, page) -> None:
    """Select every plot (Ctrl+A on the canvas)."""
    if not page._fig_cards:
        return
    _figure_unfocus(ctx, page)
    _figure_set_selection(ctx, page, list(range(len(page._fig_cards))))


def _figure_clear_selection(ctx, page) -> None:
    """Deselect every plot (a plain click on empty canvas)."""
    _figure_unfocus(ctx, page)
    _figure_set_selection(ctx, page, [])


def _figure_focus(ctx, page, index: int) -> None:
    """Focus a plot: wheel/drag act on its data instead of the canvas."""
    if not (0 <= index < len(page._fig_cards)):
        return
    page._fig_focused = index
    page._fig_selected = [index]
    for i, card in enumerate(page._fig_cards):
        card.set_selected(i == index)
        card.set_focused(i == index)
    _figure_rebuild_settings(ctx, page)
    _figure_update_plot_mouse(ctx, page)
    page._fig_view.setFocus()


def _figure_unfocus(ctx, page) -> None:
    """Leave focus mode (wheel/drag act on the canvas again)."""
    page._fig_focused = None
    for card in page._fig_cards:
        card.set_focused(False)
    _figure_update_plot_mouse(ctx, page)


def _figure_update_plot_mouse(ctx, page) -> None:
    for i, card in enumerate(page._fig_cards):
        enabled = (i == page._fig_focused)
        card.plot_widget().getPlotItem().getViewBox().setMouseEnabled(enabled, enabled)


def _figure_plot_wheel(ctx, page, item, delta_y) -> bool:
    focused = page._fig_focused
    if focused is None or not (0 <= focused < len(page._fig_cards)):
        return False
    card = page._fig_cards[focused]
    if item is not card.proxy():
        return False
    factor = 1.15 if delta_y > 0 else 1 / 1.15
    card.plot_widget().getPlotItem().getViewBox().scaleBy((1 / factor, 1 / factor))
    return True


def _figure_plot_pan_start(ctx, page, item, scene_pos) -> bool:
    focused = page._fig_focused
    if focused is None or not (0 <= focused < len(page._fig_cards)):
        return False
    card = page._fig_cards[focused]
    if item is not card.proxy():
        return False
    local = card.proxy().mapFromScene(scene_pos)
    # Corner buttons (bottom-left): "A" resets to settings range, "F" fits all.
    if local.y() >= card.height() - 40:
        if local.x() <= 21:
            _widgets_plot_renderer._figure_apply_ranges(ctx,
                page._fig_plots[focused], card.plot_widget(),
                page._fig_task_id, page._fig_column_transforms,
            )
            return True
        if local.x() <= 40:
            _widgets_plot_renderer._figure_fit_all(ctx, page._fig_plots[focused], card.plot_widget())
            return True
    page._fig_pan_scene = scene_pos
    page._fig_pan_range = card.plot_widget().getPlotItem().getViewBox().viewRange()
    return True


def _figure_plot_pan_move(ctx, page, scene_pos) -> bool:
    if page._fig_pan_scene is None:
        return False
    focused = page._fig_focused
    if focused is None or not (0 <= focused < len(page._fig_cards)):
        return False
    card = page._fig_cards[focused]
    vb = card.plot_widget().getPlotItem().getViewBox()
    rect = card.proxy().boundingRect()
    plot_h = max(1.0, rect.height())
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


def _figure_plot_pan_end(ctx, page) -> bool:
    was_panning = page._fig_pan_scene is not None
    page._fig_pan_scene = None
    page._fig_pan_range = None
    return was_panning


def _figure_rebuild_settings(ctx, page) -> None:
    """Rebuild the right-hand settings panel for the current selection."""
    if len(page._fig_selected) > 1:
        _figure_rebuild_settings_multi(ctx, page)
    else:
        _figure_rebuild_settings_single(ctx, page)


def _figure_rebuild_settings_single(ctx, page) -> None:
    """Rebuild the right-hand settings panel for a single selected plot."""
    layout = page._fig_settings_layout
    _widgets_panels._clear_settings_layout(ctx, layout)
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

    records = _data_files._available_files(ctx, page._fig_task_id)
    rec = _widgets_plot_renderer._figure_record(ctx, plot_dict.get("file"))
    columns = _data_columns._columns_for(ctx,
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
    layout.addWidget(_widgets_panels._file_nav_row(ctx, file_combo, enabled))

    # X parameter (image plots have no x parameter: their axes are the
    # image's own spatial coordinates, fixed by the image itself).
    x_combo = None
    if plot_dict.get("type") != "image":
        layout.addWidget(QLabel("X parameter"))
        x_combo = _widgets_plot_renderer._figure_param_combo(ctx, columns, plot_dict.get("x"), enabled)
        layout.addWidget(x_combo)

    # Filter: apply an upstream gate (gate + ancestors) to the data.
    layout.addWidget(QLabel("Filter"))
    filter_combo = _NoWheelComboBox()
    filter_combo.addItem("None", None)
    filter_options = _figure_filter_options(ctx, page._fig_task_id)
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
            y_combo = _widgets_plot_renderer._figure_param_combo(ctx, columns, plot_dict.get("y"), enabled)
            layout.addWidget(y_combo)

        if plot_dict.get("type") == "overlay":
            _figure_overlay_settings(ctx,
                layout, plot_dict, plot_widget, enabled,
                on_change=lambda: _figure_apply_change(ctx,
                    page, index, plot_dict
                ),
                page=page, index=index,
            )
        else:
            _widgets_plot_renderer._figure_scatter_color_settings(ctx,
                layout, plot_dict, plot_widget, columns, enabled,
                on_change=lambda: _figure_apply_change(ctx, page, index, plot_dict),
                on_rebuild=lambda: (
                    _figure_rebuild_settings(ctx, page),
                    _figure_apply_change(ctx, page, index, plot_dict),
                ),
                task_id=page._fig_task_id,
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
                    QColor(current[0], current[1], current[2]), ctx.host, "Choose color"
                )
                if chosen.isValid():
                    plot_dict[key] = (chosen.red(), chosen.green(), chosen.blue())
                    btn.setStyleSheet(
                        f"background-color: rgb({chosen.red()},{chosen.green()},{chosen.blue()}); "
                        "border: 1px solid #555;"
                    )
                    _figure_apply_change(ctx, page, index, plot_dict)

            btn.clicked.connect(on_pick)
            return btn

        layout.addWidget(QLabel("Histogram color"))
        layout.addWidget(make_color_button("hist_color", (0, 0, 200)))
        layout.addWidget(QLabel("Curve color"))
        layout.addWidget(make_color_button("curve_color", (200, 40, 40)))

    # Range inputs are shown in original values: map through this
    # module's upstream Scale configs for each axis' column.
    ptype = plot_dict.get("type")
    file_path = plot_dict.get("file")
    x_configs = (
        _widgets_plot_renderer._figure_axis_scale_configs(ctx,
            page._fig_task_id, file_path, plot_dict.get("x"),
            page._fig_column_transforms,
        ) if ptype in ("scatter", "histogram", "overlay") else []
    )
    # Histograms show counts and images show pixel coordinates.
    y_configs = (
        _widgets_plot_renderer._figure_axis_scale_configs(ctx,
            page._fig_task_id, file_path, plot_dict.get("y"),
            page._fig_column_transforms,
        ) if ptype in ("scatter", "overlay") else []
    )

    # X range.
    layout.addWidget(QLabel("X range"))
    layout.addWidget(_widgets_plot_renderer._figure_range_row(ctx,
        plot_dict, "x", plot_widget, enabled,
        lambda: _figure_mark_dirty(ctx, page), configs=x_configs,
        task_id=page._fig_task_id,
        column_transforms=page._fig_column_transforms,
    ))

    # Y range (scatter/histogram/image: histograms show the Count axis).
    layout.addWidget(QLabel("Y range"))
    layout.addWidget(_widgets_plot_renderer._figure_range_row(ctx,
        plot_dict, "y", plot_widget, enabled,
        lambda: _figure_mark_dirty(ctx, page), configs=y_configs,
        task_id=page._fig_task_id,
        column_transforms=page._fig_column_transforms,
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

    # Display options: labels/ticks only, so no data re-read per keystroke.
    _widgets_plot_renderer._figure_display_toggles(ctx,
        layout, plot_dict, enabled,
        on_change=lambda: _figure_apply_display(ctx, page, index, plot_dict),
    )

    layout.addStretch()

    # Signal wiring (after initial values are set).
    def on_file_changed():
        plot_dict["file"] = file_combo.currentData()
        plot_dict["file_name"] = file_combo.currentText()
        new_rec = _widgets_plot_renderer._figure_record(ctx, plot_dict["file"])
        new_cols = _data_columns._columns_for(ctx,
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
        _figure_rebuild_settings(ctx, page)
        _figure_apply_change(ctx, page, index, plot_dict)

    file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())

    def on_filter_changed():
        plot_dict["filter"] = filter_combo.currentData()
        # Keep the current view range; only the data subset changes.
        vb = plot_widget.getPlotItem().getViewBox()
        xr, yr = vb.viewRange()
        _figure_apply_change(ctx, page, index, plot_dict)
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
            _figure_apply_change(ctx, page, index, plot_dict)
            _figure_rebuild_settings(ctx, page)
        return handler

    if x_combo is not None:
        x_combo.currentTextChanged.connect(on_axis_param("x"))
    if y_combo is not None:
        y_combo.currentTextChanged.connect(on_axis_param("y"))

    if bins_spin is not None:
        def on_bins(v):
            plot_dict["bins"] = int(v)
            _figure_apply_change(ctx, page, index, plot_dict)
        bins_spin.valueChanged.connect(on_bins)

    if hist_mode_combo is not None:
        def on_hist_mode(_=0):
            plot_dict["hist_mode"] = hist_mode_combo.currentData()
            _figure_apply_change(ctx, page, index, plot_dict)
        hist_mode_combo.currentIndexChanged.connect(on_hist_mode)

    def on_width_changed():
        w = width_field.value()
        if w is None:
            return
        h = height_field.value()
        h = h if h is not None else w
        plot_dict["size"] = [int(w), int(h)]
        resolution_field.set_value(int(round(w / 10.0)))
        _figure_resize_card(ctx, page, index, w, h)
        _figure_mark_dirty(ctx, page)

    def on_height_changed():
        w = width_field.value()
        h = height_field.value()
        if w is None or h is None:
            return
        plot_dict["size"] = [int(w), int(h)]
        _figure_resize_card(ctx, page, index, w, h)
        _figure_mark_dirty(ctx, page)

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
        _figure_resize_card(ctx, page, index, w_new, h_new)
        _figure_mark_dirty(ctx, page)

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
        _figure_resize_card(ctx, page, index, w_new, h)
        _figure_mark_dirty(ctx, page)

    def on_square():
        h = height_field.value()
        if h is None:
            return
        w_new = int(h) + _legend_extra()
        plot_dict["size"] = [w_new, int(h)]
        width_field.set_value(w_new)
        resolution_field.set_value(int(round(w_new / 10.0)))
        _figure_resize_card(ctx, page, index, w_new, h)
        _figure_mark_dirty(ctx, page)


def _figure_rebuild_settings_multi(ctx, page) -> None:
    """Rebuild the settings panel for a multi-selection of plots.

    Each field shows the value when all selected plots agree, and blank
    when they differ; setting a field applies it to every selected plot.
    """
    layout = page._fig_settings_layout
    _widgets_panels._clear_settings_layout(ctx, layout)
    indices = page._fig_selected
    plots = [page._fig_plots[i] for i in indices if 0 <= i < len(page._fig_plots)]
    if not plots:
        _figure_rebuild_settings_single(ctx, page)
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
            chosen = QColorDialog.getColor(QColor(0, 0, 200), ctx.host, "Choose color")
            if chosen.isValid():
                rgb = (chosen.red(), chosen.green(), chosen.blue())
                apply(ps, key, rgb)
                btn.setStyleSheet(
                    f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); "
                    "border: 1px solid #555;"
                )
                _figure_apply_change_multi(ctx, page)

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
            _figure_apply_change_multi(ctx, page)

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

    records = _data_files._available_files(ctx, page._fig_task_id)

    # Union of columns across every selected plot's file.
    col_set = []
    seen = set()
    for p in plots:
        rec = _widgets_plot_renderer._figure_record(ctx, p.get("file"))
        for c in (
            _data_columns._columns_for(ctx, rec.get("parquet"), page._fig_task_id)
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
            rec = _widgets_plot_renderer._figure_record(ctx, f)
            cols = _data_columns._columns_for(ctx,
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
        _figure_apply_change_multi(ctx, page)
        _figure_rebuild_settings_multi(ctx, page)

    file_combo.currentIndexChanged.connect(on_file_changed)

    # X parameter (omit when every selected plot is an image — image axes
    # are fixed to the image's own spatial coordinates).
    x_combo = None
    if any(p.get("type") != "image" for p in plots):
        layout.addWidget(QLabel("X parameter"))
        xv = common(plots, "x")
        x_combo = _widgets_plot_renderer._figure_param_combo(ctx, col_set, None if xv is _MIXED else xv, enabled)
        layout.addWidget(x_combo)

    def on_x(v):
        v = v or None
        for p in plots:
            p["x"] = v
            p["last_x"] = v
            p["xmin"] = None
            p["xmax"] = None
        _figure_apply_change_multi(ctx, page)
        _figure_rebuild_settings_multi(ctx, page)

    if x_combo is not None:
        x_combo.currentTextChanged.connect(on_x)

    # Filter: apply an upstream gate (gate + ancestors) to the data.
    layout.addWidget(QLabel("Filter"))
    filter_combo = _NoWheelComboBox()
    filter_combo.addItem("", _MIXED)
    filter_combo.addItem("None", None)
    filter_options = _figure_filter_options(ctx, page._fig_task_id)
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
                _widgets_plot_renderer._figure_render(ctx,
                    page._fig_plots[i], page._fig_cards[i].plot_widget(),
                    column_transforms=page._fig_column_transforms,
                    task_id=page._fig_task_id,
                )
                vb.disableAutoRange()
                vb.setRange(xRange=(xr[0], xr[1]), yRange=(yr[0], yr[1]), padding=0)
        _figure_mark_dirty(ctx, page)

    filter_combo.currentIndexChanged.connect(on_filter_changed)

    # Y parameter (scatter only).
    y_combo = None
    if scatter_plots:
        layout.addWidget(QLabel("Y parameter"))
        yv = common(scatter_plots, "y")
        y_combo = _widgets_plot_renderer._figure_param_combo(ctx, col_set, None if yv is _MIXED else yv, enabled)
        layout.addWidget(y_combo)

        def on_y(v):
            v = v or None
            for p in scatter_plots:
                p["y"] = v
                p["last_y"] = v
                p["ymin"] = None
                p["ymax"] = None
            _figure_apply_change_multi(ctx, page)
            _figure_rebuild_settings_multi(ctx, page)

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
            _figure_apply_change_multi(ctx, page)
            _figure_rebuild_settings_multi(ctx, page)

        mode_combo.currentIndexChanged.connect(on_mode_changed)

        mode_val = common(scatter_plots, "color_mode", "density")
        if mode_val is not _MIXED:
            if mode_val == "parameter":
                layout.addWidget(QLabel("Color parameter"))
                cpv = common(scatter_plots, "color_param")
                cp_combo = _widgets_plot_renderer._figure_param_combo(ctx,
                    col_set, None if cpv is _MIXED else cpv, enabled
                )
                layout.addWidget(cp_combo)

                def on_cp(v):
                    apply(scatter_plots, "color_param", v or None)
                    apply(scatter_plots, "last_color_param", v or None)
                    _figure_apply_change_multi(ctx, page)

                cp_combo.currentTextChanged.connect(on_cp)
            elif mode_val == "solid":
                layout.addWidget(QLabel("Solid color"))
                layout.addWidget(make_color_button(
                    scatter_plots, "solid_color", (0, 0, 200)
                ))
            else:
                layout.addWidget(QLabel("Palette"))
                palette_combo = make_value_combo(
                    [(p, p) for p in _palette_names()],
                    common(scatter_plots, "palette", _default_palette()),
                )
                _widgets_colormap._add_more_colormaps(ctx, palette_combo)
                layout.addWidget(palette_combo)

                def on_palette(_=0):
                    data = palette_combo.currentData()
                    if data is _MIXED:
                        return
                    apply(scatter_plots, "palette", data)
                    _figure_apply_change_multi(ctx, page)

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
            _figure_apply_change_multi(ctx, page)

        point_spin = _LogStepSpinBox(None if ps_val is _MIXED else ps_val, on_point_size)
        point_spin.set_enabled(enabled and point_fixed.isChecked())

        def on_point_fixed(_=0):
            st = point_fixed.checkState()
            if st == Qt.CheckState.PartiallyChecked:
                return
            apply(scatter_plots, "fixed_point_size", st == Qt.CheckState.Checked)
            point_spin.set_enabled(enabled and st == Qt.CheckState.Checked)
            _figure_apply_change_multi(ctx, page)

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
            _figure_apply_change_multi(ctx, page)

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
            _figure_apply_change_multi(ctx, page)

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
            _figure_resize_card(ctx, page, i, int(w), int(h))
        _figure_mark_dirty(ctx, page)

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
                _figure_apply_display_all(ctx, page)

            return h

        cb.stateChanged.connect(make_toggle(key, cb))
        layout.addWidget(cb)

    layout.addWidget(QLabel("Font sizes"))
    for label, key, default in (
        ("Axis names", "axis_name_font_size", 11),
        ("Indices", "index_font_size", 10),
        ("Sample name", "sample_name_font_size", 9),
    ):
        row = QWidget()
        rh = QHBoxLayout(row)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(6)
        rh.addWidget(QLabel(label))
        value = common(plots, key, default)
        spin = _NoWheelSpinBox()
        spin.setRange(6, 32)
        spin.setSuffix(" pt")
        spin.setValue(int(default if value is _MIXED else value))
        spin.setEnabled(enabled)
        rh.addWidget(spin)
        rh.addStretch()
        layout.addWidget(row)

        def on_font_size(v, k=key):
            apply(plots, k, int(v))
            _figure_apply_display_all(ctx, page)

        spin.valueChanged.connect(on_font_size)

    layout.addStretch()


def _figure_point_size_settings(ctx, layout, plot_dict, plot_widget, enabled, on_change) -> None:
    """Point-size row shared by scatter and overlay-scatter settings."""
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
    point_spin = _LogStepSpinBox(
        plot_dict.get("point_size", 4), on_point_size
    )
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


def _figure_overlay_gate_options(ctx, page, plot_dict) -> list:
    """Gates of the plot's file, one entry per upstream gate."""
    path = plot_dict.get("file")
    if not path:
        return []
    options = []
    for task in _data_files._task_chain(ctx, page._fig_task_id):
        if task.get("id") == page._fig_task_id:
            continue
        if task.get("type") != "gating":
            continue
        for gate in _data_stores._gating_gates_for(ctx, task, path):
            options.append({
                "task_id": task.get("id"),
                "gate_id": gate.get("id"),
                "name": str(gate.get("name") or gate.get("id")),
                "module": str(task.get("name") or "Gating"),
            })
    counts = Counter(opt["name"] for opt in options)
    for opt in options:
        label = opt["name"]
        if counts[opt["name"]] > 1:
            # Same gate name in several gating modules: disambiguate in
            # the picker only; the layer table keeps the plain name.
            label = f"{label} ({opt['module']})"
        opt["label"] = label
    return options


def _figure_overlay_pick(ctx, page, index, plot_dict, on_change) -> None:
    """Choose the layers of an overlay scatter (gates, in order)."""
    options = _figure_overlay_gate_options(ctx, page, plot_dict)
    if not options:
        QMessageBox.information(
            ctx.host, "Select gates",
            "This data file has no gates from an upstream Gating module.",
        )
        return
    layers = plot_dict.setdefault("layers", [])
    existing = {
        (layer.get("task_id"), layer.get("gate_id")): layer
        for layer in layers
    }
    current = [
        opt["label"] for opt in options
        if (opt["task_id"], opt["gate_id"]) in existing
    ]
    # The picker only chooses which gates are layers (its list keeps the
    # gating order); the layer order is controlled by dragging the table.
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select gates", [opt["label"] for opt in options], current,
        "Filter gates...",
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    by_key = {(opt["task_id"], opt["gate_id"]): opt for opt in options}
    # Keep the current table order for layers that stay selected.
    new_layers = []
    for layer in layers:
        key = (layer.get("task_id"), layer.get("gate_id"))
        opt = by_key.get(key)
        if opt is None or opt["label"] not in chosen_set:
            continue
        kept = dict(layer)
        kept["name"] = opt["name"]
        new_layers.append(kept)
    # Append newly selected gates in the picker's fixed order.
    for opt in options:
        key = (opt["task_id"], opt["gate_id"])
        if key in existing or opt["label"] not in chosen_set:
            continue
        new_layers.append({
            "task_id": opt["task_id"],
            "gate_id": opt["gate_id"],
            "name": opt["name"],
            "color": OVERLAY_LAYER_COLORS[
                len(new_layers) % len(OVERLAY_LAYER_COLORS)
            ],
            "visible": True,
        })
    plot_dict["layers"] = new_layers
    if page is not None:
        _figure_rebuild_settings(ctx, page)
    on_change()


def _figure_overlay_settings(ctx, layout, plot_dict, plot_widget, enabled, on_change, page=None, index=None) -> None:
    """Layers table (gate, visibility, colour) and point size."""
    hint = QLabel(
        "Each layer is a gate. Points are painted layer over layer: when "
        "several gates contain a point, the topmost layer's colour wins. "
        "Gates may overlap."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    layout.addWidget(hint)

    layout.addWidget(QLabel("Layers"))
    pick_btn = QPushButton("Select gates…")
    pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    pick_btn.setEnabled(enabled)
    pick_btn.clicked.connect(
        lambda _=False: _figure_overlay_pick(ctx,
            page, index, plot_dict, on_change
        )
    )
    layout.addWidget(pick_btn)

    layers = plot_dict.setdefault("layers", [])
    table = _OverlayLayerTable(len(layers), 3)
    table.setHorizontalHeaderLabels(["Show", "Layer", "Color"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: 1px solid #d0d7e2; "
        "color: #2c3a48; }"
        "QTableWidget::item:selected { background: #e1ecf9; "
        "color: #1a4a76; }"
        "QTableWidget::item:hover { background: #f0f7fe; }"
        "QHeaderView::section { background: #f5f8fb; color: #2c3a48; "
        "border: none; padding: 2px 4px; }"
        "QToolTip { background-color: #ffffff; color: #2c3a48; "
        "border: 1px solid #c9d3df; }"
    )
    table.setMinimumHeight(110)
    table.setMaximumHeight(220)
    table.setEnabled(enabled)
    layout.addWidget(table)

    hint_row = QLabel("Drag a row to change the layer order")
    hint_row.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    layout.addWidget(hint_row)

    # Current gate names (a gate may have been renamed upstream).
    names = {}
    if page is not None:
        for opt in _figure_overlay_gate_options(ctx, page, plot_dict):
            names[(opt["task_id"], opt["gate_id"])] = opt["name"]

    syncing = {"value": False}

    def on_check(item):
        if syncing["value"] or page is None:
            return
        row = item.row()
        if item.column() != 0 or not (0 <= row < len(layers)):
            return
        layers[row]["visible"] = (
            item.checkState() == Qt.CheckState.Checked
        )
        on_change()

    for row, layer in enumerate(layers):
        key = (layer.get("task_id"), layer.get("gate_id"))
        show_item = QTableWidgetItem("")
        show_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        )
        show_item.setCheckState(
            Qt.CheckState.Checked if layer.get("visible", True)
            else Qt.CheckState.Unchecked
        )
        table.setItem(row, 0, show_item)
        name_item = QTableWidgetItem(
            names.get(key) or str(layer.get("name") or layer.get("gate_id"))
        )
        name_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        table.setItem(row, 1, name_item)
        color = layer.get("color") or OVERLAY_LAYER_COLORS[
            row % len(OVERLAY_LAYER_COLORS)
        ]
        color_btn = QPushButton()
        color_btn.setFixedSize(32, 20)
        color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        color_btn.setEnabled(enabled)
        color_btn.setStyleSheet(
            f"background-color: rgb({color[0]},{color[1]},{color[2]}); "
            "border: 1px solid #555;"
        )

        def on_color(_=False, r=row, btn=color_btn):
            if not (0 <= r < len(layers)):
                return
            current = layers[r].get("color") or OVERLAY_LAYER_COLORS[
                r % len(OVERLAY_LAYER_COLORS)
            ]
            chosen = QColorDialog.getColor(
                QColor(current[0], current[1], current[2]),
                ctx.host, "Choose layer color",
            )
            if not chosen.isValid():
                return
            layers[r]["color"] = (
                chosen.red(), chosen.green(), chosen.blue()
            )
            btn.setStyleSheet(
                f"background-color: rgb({chosen.red()},"
                f"{chosen.green()},{chosen.blue()}); "
                "border: 1px solid #555;"
            )
            on_change()

        color_btn.clicked.connect(on_color)
        table.setCellWidget(row, 2, color_btn)

    table.itemChanged.connect(on_check)

    def on_move(source, target):
        if not (0 <= source < len(layers)):
            return
        layer = layers.pop(source)
        target = max(0, min(int(target), len(layers)))
        layers.insert(target, layer)
        if page is not None:
            QTimer.singleShot(
                0, lambda: _figure_rebuild_settings(ctx, page)
            )
        on_change()

    table.rowMoved.connect(on_move)

    if not layers:
        empty = QLabel("No layers yet — use Select gates… to add some.")
        empty.setStyleSheet("color: #8a94a6;")
        layout.addWidget(empty)

    _figure_point_size_settings(ctx,
        layout, plot_dict, plot_widget, enabled, on_change
    )


def _figure_overlay_masks(ctx, plot_dict, task_id, parquet_path) -> dict:
    """{'task|gate': full-row bool mask} for every visible layer."""
    import numpy as np

    layers = [
        layer for layer in plot_dict.get("layers") or []
        if layer.get("visible", True) and layer.get("task_id")
        and layer.get("gate_id")
    ]
    masks = {}
    if not layers or not parquet_path:
        return masks
    groups = {}
    for layer in layers:
        groups.setdefault(layer["task_id"], []).append(layer)
    full_rows = int(_data_columns._data_row_count(ctx, parquet_path) or 0)
    for gate_task_id, group in groups.items():
        gating_task = ctx._find_task_by_id(gate_task_id)
        if gating_task is None:
            continue
        gates = _data_stores._gating_gates_for(ctx, gating_task, parquet_path)
        if not gates:
            continue
        by_id = {g["id"]: g for g in gates}
        needed = set()
        gate_ids = []
        for layer in group:
            gate_id = layer.get("gate_id")
            if gate_id not in by_id:
                continue
            for gate in _gate_chain(gates, gate_id):
                needed |= _gate_needed_columns(gate, by_id)
            gate_ids.append(gate_id)
        if not needed:
            continue
        try:
            values = _data_columns._column_data(ctx,
                gate_task_id, parquet_path, list(needed)
            )
        except Exception:  # noqa: BLE001
            continue
        if not values:
            continue
        index = _data_sampling._subsample_row_index(ctx, task_id, parquet_path)
        for gate_id in gate_ids:
            try:
                mask = _compute_gate_filter_mask(
                    gates, gate_id, parquet_path, {},
                    existing=set(values), extra=values,
                )
            except Exception:  # noqa: BLE001
                mask = None
            if mask is None:
                continue
            mask = np.asarray(mask, dtype=bool)
            if index is not None and len(mask) == full_rows:
                mask = mask[index]
            masks[(gate_task_id, gate_id)] = mask
    return masks


def _figure_overlay_point_colors(ctx, plot_dict, task_id, parquet_path, n_points, keep, indices, pooled, picked, max_points):
    """Per-point RGB colours; later (lower) layers are painted first."""
    import numpy as np

    colors = np.empty((n_points, 3), dtype=np.uint8)
    colors[:, :] = OVERLAY_OTHER_COLOR
    masks = _figure_overlay_masks(ctx, plot_dict, task_id, parquet_path)
    keep = np.asarray(keep, dtype=bool)
    layers = [
        layer for layer in plot_dict.get("layers") or []
        if layer.get("visible", True)
    ]
    # Bottom row first so the topmost layer's colour overwrites it.
    for layer in reversed(layers):
        mask = masks.get((layer.get("task_id"), layer.get("gate_id")))
        if mask is None:
            continue
        if pooled:
            if indices is None or indices.size == 0:
                continue
            if int(indices.max()) >= mask.size:
                continue
            mask = mask[indices]
            if mask.size != keep.size:
                continue
            mask = mask[keep]
            if mask.size > max_points:
                mask = mask[:max_points]
        else:
            if mask.size != keep.size:
                continue
            mask = mask[keep]
            if picked is not None:
                mask = mask[picked]
        if mask.size != n_points:
            continue
        color = layer.get("color") or (0, 0, 200)
        colors[mask] = (
            int(color[0]), int(color[1]), int(color[2])
        )
    return colors


def _figure_channel_values(ctx, task_id, parquet_path, channel, max_points: int | None=200000, extra_stages=None):
    """Finite values of one image channel (sampled unless max_points=None)."""
    import numpy as np

    values = _data_columns._column_data(ctx,
        task_id, parquet_path, [channel], extra_stages=extra_stages
    ).get(channel)
    if values is None:
        return None
    values = np.asarray(values, dtype="float64")
    values = values[np.isfinite(values)]
    if max_points is not None and values.size > max_points:
        rng = np.random.default_rng(42)
        values = values[rng.choice(values.size, max_points, replace=False)]
    return values


_MIXED = object()

