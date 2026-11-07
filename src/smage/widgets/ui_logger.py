"""UI action logger (records clicks for reproducible bug reports)."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject

from smage.core.settings import _log_exception
from smage.runlog import runlog


class _UIActionLogger(QObject):
    """Turns user interactions into ``ACTION`` lines in the session log.

    It is installed as an application-wide event filter and lazily connects to
    each widget's own signal the first time the widget is shown.  That keeps
    the instrumentation independent of how a panel is built (and rebuilt).
    Nothing here may raise: the filter runs inside Qt's event dispatch.
    """

    _WATCH = frozenset({QEvent.Type.Show, QEvent.Type.Polish})

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._last = {}
        self._pages = {}
        self._wheel_at = {}
        self._action_logged = set()

    # -- Qt event filter ---------------------------------------------------
    def eventFilter(self, obj, event) -> bool:
        try:
            kind = event.type()
            if kind in self._WATCH:
                self._instrument(obj)
            elif kind == QEvent.Type.Wheel:
                self._log_wheel(obj, event)
        except Exception as exc:  # noqa: BLE001
            _log_exception(exc)
        return False

    def _log_wheel(self, obj, event) -> None:
        """Log plot zooming (throttled: at most twice a second per widget)."""
        import time

        # Only zooming inside a plot (the viewport of a pyqtgraph view), not
        # scrolling the settings panel's scroll bars.
        parent = obj.parentWidget() if hasattr(obj, "parentWidget") else None
        if not isinstance(parent, pg.GraphicsView):
            return
        page = self._pages.get(id(obj)) or self._page_name(obj)
        if not page:
            return
        now = time.perf_counter()
        if now - self._wheel_at.get(id(obj), 0.0) < 0.5:
            return
        self._wheel_at[id(obj)] = now
        try:
            delta = int(event.angleDelta().y())
        except Exception:
            delta = 0
        runlog.action(
            "plot.wheel", widget=type(obj).__name__, page=page, delta=delta
        )

    # -- descriptions ------------------------------------------------------
    def _page_name(self, widget) -> str:
        tabs = getattr(self._window, "tabs", None)
        node = widget
        while node is not None:
            if tabs is not None:
                try:
                    index = tabs.indexOf(node)
                except Exception:
                    index = -1
                if index >= 0:
                    return tabs.tabText(index)
            node = node.parentWidget()
        return ""

    @staticmethod
    def _widget_text(widget) -> str:
        for attr in ("text", "title", "placeholderText"):
            getter = getattr(widget, attr, None)
            if callable(getter):
                try:
                    value = getter()
                except Exception:
                    value = None
                if value:
                    return str(value)
        return ""

    def _describe(self, widget, extra=None) -> dict:
        fields = {"widget": type(widget).__name__}
        # The most recent page name wins: a settings panel may rebuild itself
        # (detaching this widget) before the action is logged.
        page = self._pages.get(id(widget)) or self._page_name(widget)
        if page:
            fields["page"] = page
        text = self._widget_text(widget)
        if text:
            fields["text"] = text
        try:
            tip = widget.toolTip()
        except Exception:
            tip = ""
        if tip and tip != text:
            fields["tooltip"] = tip
        try:
            name = widget.objectName()
        except Exception:
            name = ""
        if name:
            fields["name"] = name
        if extra:
            fields.update({k: v for k, v in extra.items() if v is not None})
        return fields

    # -- instrumentation ---------------------------------------------------
    def _instrument(self, obj) -> None:
        import weakref

        from PySide6.QtWidgets import (
            QAbstractButton, QAbstractItemView, QComboBox, QDialog,
            QDoubleSpinBox, QLineEdit, QSlider, QSpinBox, QTabWidget, QWidget,
        )

        if not isinstance(obj, QWidget):
            return
        try:
            if obj.property("_smage_logged"):
                return
            obj.setProperty("_smage_logged", True)
        except Exception:
            return
        try:
            self._pages[id(obj)] = self._page_name(obj)
        except Exception:
            self._pages[id(obj)] = ""

        # Spin boxes / editable combos own an internal line edit; the outer
        # widget already logs their commits, so skip the child.
        parent = obj.parentWidget()
        if isinstance(obj, QLineEdit) and isinstance(
            parent, (QSpinBox, QDoubleSpinBox, QComboBox)
        ):
            return

        ref = weakref.ref(obj)

        if isinstance(obj, QAbstractButton):
            def on_click(_checked=False, ref=ref):
                widget = ref()
                if widget is None:
                    return
                extra = None
                try:
                    if widget.isCheckable():
                        extra = {"checked": bool(widget.isChecked())}
                except Exception:
                    extra = None
                runlog.action("button.click", **self._describe(widget, extra))
            obj.clicked.connect(on_click)
        elif isinstance(obj, QComboBox):
            self._last[id(obj)] = obj.currentText()

            def on_combo(index, ref=ref):
                widget = ref()
                if widget is None:
                    return
                old = self._last.get(id(widget), "")
                new = widget.currentText()
                if new == old:
                    return
                self._last[id(widget)] = new
                runlog.action(
                    "combo.change", **self._describe(widget, {"old": old, "new": new})
                )
            obj.currentIndexChanged.connect(on_combo)
        elif isinstance(obj, (QSpinBox, QDoubleSpinBox)):
            def on_value(ref=ref):
                widget = ref()
                if widget is None:
                    return
                runlog.action(
                    "value.change", **self._describe(widget, {"value": widget.value()})
                )
            obj.editingFinished.connect(on_value)
        elif isinstance(obj, QLineEdit):
            def on_text(ref=ref):
                widget = ref()
                if widget is None:
                    return
                try:
                    if widget.echoMode() == QLineEdit.EchoMode.Password:
                        value = "***"
                    else:
                        value = widget.text()
                except Exception:
                    return
                runlog.action("text.change", **self._describe(widget, {"value": value}))
            obj.editingFinished.connect(on_text)
        elif isinstance(obj, QAbstractItemView):
            def on_item(index, ref=ref):
                widget = ref()
                if widget is None:
                    return
                value = ""
                try:
                    model = widget.model()
                    data = model.data(index) if model is not None else None
                    value = "" if data is None else str(data)
                except Exception:
                    value = ""
                runlog.action(
                    "item.click",
                    **self._describe(widget, {
                        "row": index.row(), "col": index.column(), "value": value,
                    }),
                )
            obj.clicked.connect(on_item)
        elif isinstance(obj, QTabWidget):
            def on_tab(index, ref=ref):
                widget = ref()
                if widget is None:
                    return
                runlog.action(
                    "tab.change",
                    tab=widget.tabText(index), index=int(index),
                )
            obj.currentChanged.connect(on_tab)
        elif isinstance(obj, QSlider):
            def on_slider(ref=ref):
                widget = ref()
                if widget is None:
                    return
                runlog.action(
                    "slider.change", **self._describe(widget, {"value": widget.value()})
                )
            obj.sliderReleased.connect(on_slider)
        elif isinstance(obj, QDialog):
            def on_dialog(result, ref=ref):
                widget = ref()
                if widget is None:
                    return
                runlog.action(
                    "dialog.close",
                    title=widget.windowTitle(),
                    result=int(result),
                )
            obj.finished.connect(on_dialog)

    def log_actions(self, menu) -> None:
        """Log every QAction of a menu bar / menu when it is triggered."""
        from PySide6.QtWidgets import QMenu, QMenuBar

        if not isinstance(menu, (QMenu, QMenuBar)):
            return
        if id(menu) in self._action_logged:
            return
        self._action_logged.add(id(menu))
        for action in menu.actions():
            submenu = action.menu()
            if submenu is not None:
                self.log_actions(submenu)
                continue
            text = action.text().replace("&", "")
            if not text:
                continue
            try:
                title = menu.title().replace("&", "")
            except Exception:
                title = ""

            def on_trigger(_checked=False, text=text, path=title):
                runlog.action("menu.action", text=text, menu=path)
            action.triggered.connect(on_trigger)
