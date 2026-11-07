"""Qt-free building blocks used by the application.

Modules in this package must not import PySide6, pyqtgraph or any project
state. They take plain values (arrays, dicts, paths) and return plain values,
so they can be unit-tested without a QApplication.

The public names keep the leading underscore they had inside ``smage.main``
during the split, so existing call sites remain valid.
"""
