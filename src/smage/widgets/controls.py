"""Small input widgets with project-wide behaviour (no wheel edits)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QGraphicsProxyWidget,
    QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from smage.core.formatting import _format_number
from smage.widgets.picker import _SelectAllFilter


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
    """Narrow numeric field: text shown as typed; ▲/▼ step ×10 / ÷10.

    `decimals` fixes how many decimals are shown and stored (axis ranges use 1).
    """

    def __init__(self, value, on_change, decimals: int | None = None) -> None:
        super().__init__()
        self._value = value
        self._on_change = on_change
        self._decimals = decimals
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        self.edit = QLineEdit()
        self.edit.setFixedWidth(64)
        self.edit.setText(self._format(value))
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

    def _format(self, value) -> str:
        if self._decimals is not None and value is not None:
            try:
                return f"{float(value):.{self._decimals}f}"
            except (TypeError, ValueError):
                return _format_number(value)
        return _format_number(value)

    def _round(self, value):
        if self._decimals is not None and value is not None:
            try:
                return round(float(value), self._decimals)
            except (TypeError, ValueError):
                return value
        return value

    def value(self):
        return self._value

    def set_enabled(self, enabled: bool) -> None:
        self.edit.setEnabled(enabled)
        self._up.setEnabled(enabled)
        self._down.setEnabled(enabled)

    def set_value(self, value) -> None:
        self._value = value
        self.edit.setText(self._format(value))

    def _current_value(self) -> float:
        try:
            return float(self.edit.text().strip())
        except (ValueError, TypeError):
            return 0.0

    def _step(self, factor: float) -> None:
        base = 1.0 if self.edit.text().strip() == "" else self._current_value()
        self._value = self._round(base * factor)
        self.edit.setText(self._format(self._value))
        if self._on_change is not None:
            self._on_change(self._value)

    def _commit_edit(self) -> None:
        text = self.edit.text().strip()
        try:
            self._value = self._round(float(text))
        except (ValueError, TypeError):
            self.edit.setText(self._format(self._value))
            return
        self.edit.setText(self._format(self._value))
        if self._on_change is not None:
            self._on_change(self._value)


class _ZoomPixmapLabel(QLabel):
    """Image label whose wheel zooms the rendered picture."""

    def __init__(self, on_zoom=None, parent=None) -> None:
        super().__init__(parent)
        self._on_zoom = on_zoom

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if self._on_zoom is not None and delta:
            self._on_zoom(
                1.15 if delta > 0 else 1 / 1.15, event.position().toPoint()
            )
            event.accept()
            return
        super().wheelEvent(event)


class _CardProxy(QGraphicsProxyWidget):
    """A card proxy: drag anywhere on the card to move it, double-click to focus.

    Once a card is focused its wheel/drag act on the plot content instead
    (handled by the canvas view), so moving it again means leaving focus first.
    """

    # Pixels the pointer must travel before a click becomes a drag, so a plain
    # click never reorders the cards.
    DRAG_THRESHOLD = 6
    # The bottom-left corner of a card holds the plot's own "A"/"F" buttons;
    # presses there go through to the plot instead of starting a card drag.
    BUTTON_SIZE = 44

    def __init__(self, on_select, on_drop, on_drag=None, on_drag_end=None,
                 on_focus=None, on_context=None) -> None:
        super().__init__()
        self._on_select = on_select
        self._on_drop = on_drop
        self._on_drag = on_drag
        self._on_drag_end = on_drag_end
        self._on_focus = on_focus
        self._on_context = on_context
        self._dragging = False
        self._moved = False
        self._press_scene = None
        self._start_pos = None

    def _on_plot_buttons(self, pos) -> bool:
        """True when the press lands on the plot's own corner buttons."""
        widget = self.widget()
        if widget is None:
            return False
        return (
            pos.y() >= widget.height() - self.BUTTON_SIZE
            and pos.x() <= self.BUTTON_SIZE
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._on_context:
            pos = event.screenPos()
            if hasattr(pos, "toPoint"):  # QPointF on some bindings
                pos = pos.toPoint()
            self._on_context(pos)
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._on_plot_buttons(event.pos())
        ):
            self._dragging = True
            self._moved = False
            self._press_scene = event.scenePos()
            self._start_pos = self.pos()
            self.setZValue(1)
            self._on_select(bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._on_focus is not None
            and not self._on_plot_buttons(event.pos())
        ):
            # Cancel the drag the second click started, then focus the plot.
            self._dragging = False
            self._moved = False
            self.setZValue(0)
            self._on_focus()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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


class _HoverLabel(QLabel):
    """Label with a light tooltip drawn by the app itself.

    Native tooltips for widgets inside the tab bar come out dark on some
    Windows themes while the workflow cards' tooltips stay light; this label
    draws its own so the resource readouts always match the cards.
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._tip_text = ""
        self._tip = QLabel(None, Qt.WindowType.ToolTip)
        self._tip.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self._tip.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._tip.setStyleSheet(
            "QLabel { background-color: #ffffff; color: #2c3a48;"
            " border: 1px solid #c9d3df; padding: 4px 6px; }"
        )
        self._tip.hide()

    def set_tooltip(self, text: str) -> None:
        self._tip_text = str(text or "")
        if self._tip.isVisible():
            self._tip.setText(self._tip_text)
            self._tip.adjustSize()

    def enterEvent(self, event) -> None:
        if self._tip_text:
            self._tip.setText(self._tip_text)
            self._tip.adjustSize()
            cursor = QCursor.pos()
            position = cursor + QPoint(14, 18)
            screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                if position.x() + self._tip.width() > area.right():
                    position.setX(area.right() - self._tip.width() - 4)
                if position.y() + self._tip.height() > area.bottom():
                    position.setY(cursor.y() - self._tip.height() - 8)
            self._tip.move(position)
            self._tip.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._tip.hide()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._tip.hide()
        super().hideEvent(event)

