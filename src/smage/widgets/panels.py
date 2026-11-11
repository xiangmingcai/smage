"""Framed panels and small layout helpers shared by module pages."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from smage.widgets.plot_axes import _triangle_icon


def _param_hint(ctx, text: str) -> QLabel:
    """Small grey note shown directly under the parameter it explains."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(
        "color: #8a94a6; font-size: 8pt; margin: 0 0 6px 0;"
    )
    return label


def _log_widget(placeholder: str = "Calculation output appears here…",
                height: int | None = None) -> QPlainTextEdit:
    """The read-only dark log box shared by the calculation modules."""
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText(placeholder)
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    if height is not None:
        log.setFixedHeight(int(height))
    return log


def _build_panel_frame(ctx) -> QFrame:
    panel = QFrame()
    panel.setObjectName("modulePanel")
    panel.setStyleSheet(
        "QFrame#modulePanel { background: #ffffff; "
        "border: 1px solid #dbe3ec; border-radius: 6px; }"
    )
    return panel


def _build_placeholder_panel(ctx, title: str) -> QFrame:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.addWidget(QLabel(f"<b>{title}</b>"))
    hint = QLabel("(to be implemented)")
    hint.setStyleSheet("color: gray;")
    lay.addWidget(hint)
    lay.addStretch()
    return panel


def _build_middle_panel(ctx) -> QWidget:
    """Middle column: Parameter (top, 3) + Sample (bottom, 1)."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(ctx._build_parameter_panel(), 3)
    layout.addWidget(ctx._build_sample_panel(), 1)
    return container


def _clear_layout(ctx, layout) -> None:
    """Remove and delete every widget/layout inside a layout."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()  # avoid a visible top-level window while detaching
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            ctx._clear_layout(item.layout())


def _settings_scroll(ctx, layout):
    """The QScrollArea that contains a settings layout, if there is one."""
    widget = layout.parentWidget() if hasattr(layout, "parentWidget") else None
    while widget is not None:
        if isinstance(widget, QScrollArea):
            return widget
        widget = widget.parentWidget()
    return None


def _clear_settings_layout(ctx, layout) -> None:
    """Clear a settings layout without losing the panel's scroll position.

    Rebuilding briefly shrinks the content, which clamps the scroll bar to
    the bottom; the old offset is restored once the new widgets exist.
    """
    scroll = ctx._settings_scroll(layout)
    if scroll is None:
        ctx._clear_layout(layout)
        return
    value = scroll.verticalScrollBar().value()
    ctx._clear_layout(layout)
    bar = scroll.verticalScrollBar()
    QTimer.singleShot(0, lambda: bar.setValue(value))


def _file_nav_row(ctx, combo, enabled: bool) -> QWidget:
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

