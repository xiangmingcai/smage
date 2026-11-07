"""Helpers for running from a PyInstaller bundle.

PyInstaller sets ``sys.frozen`` and ``sys._MEIPASS`` at runtime.  Code that
reads resources from the source tree must resolve them through
:func:`bundle_root` instead of ``__file__`` so the same code works in both
modes.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Whether this process runs from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Directory that contains the ``smage`` package.

    In a source checkout this is the ``src`` directory; in a bundle it is
    ``sys._MEIPASS`` (the ``_internal`` folder for onedir builds).
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def package_root() -> Path:
    """Directory holding Smage's bundled resources (icons, runner scripts)."""
    return bundle_root() / "smage"
