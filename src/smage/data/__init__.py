"""Qt-free data access layer.

Modules here take an :class:`~smage.data.context.AppContext` (or ``Session``)
as their first argument and return plain values. They must not import
PySide6/pyqtgraph.

During the ``main.py`` split the context is a transparent proxy over the host
window, so the code can move out of the window before cache ownership does.
"""
