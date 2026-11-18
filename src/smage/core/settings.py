"""Global (user-level) settings I/O.

Qt-free: the settings dialog lives in ``widgets``; this module only reads
and writes ``%APPDATA%\Smage\settings.json``.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from smage.runlog import runlog

_SETTINGS_FILENAME = "settings.json"

GLOBAL_DEFAULT_SETTINGS = {
    "appearance": {
        "font_size": 9,
        "density": "normal",
        "plot_background": "white",
        "remember_geometry": True,
    },
    "plot": {
        # Points drawn per scatter plot (larger files are sampled).
        "max_points": 50000,
        # Placeholder for the future N-by-N plot grid: many small scatters at
        # once, so it keeps fewer points. Kept at 1/10 of `max_points` unless
        # the user edits it.
        "nxn_max_points": 5000,
        # Bins per axis used to estimate point density for colouring.
        "density_grid": 200,
        "default_colormap": "viridis",
    },
    "gating": {
        # Col2Gate refuses to build gates from a column with more categories
        # than this (a continuous column would otherwise create thousands).
        "col2gate_cate_max": 100,
    },
    "colormaps": [],
    "window_geometry": "",
}

_GLOBAL_SETTINGS = None

def _settings_dir() -> Path:
    import os

    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Smage"

def _settings_path() -> Path:
    return _settings_dir() / _SETTINGS_FILENAME

def _debug_log(message: str) -> None:
    """Append a line to %APPDATA%\\Smage\\debug.log (diagnostics only)."""
    try:
        _settings_dir().mkdir(parents=True, exist_ok=True)
        with (_settings_dir() / "debug.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass

def _log_exception(exc: BaseException) -> None:
    """Append a traceback to %APPDATA%\\Smage\\error.log (pythonw has no console)."""
    import traceback

    try:
        runlog.error("exception", exc=exc)
    except Exception:  # the logger must never raise into its caller
        pass
    try:
        _settings_dir().mkdir(parents=True, exist_ok=True)
        with (_settings_dir() / "error.log").open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except OSError:
        pass

def _merge_settings(defaults, loaded):
    out = copy.deepcopy(defaults)
    if not isinstance(loaded, dict):
        return out
    for key, value in loaded.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge_settings(out[key], value)
        else:
            out[key] = value
    return out

def _global_settings() -> dict:
    """The live settings dict (loads settings.json on first use)."""
    global _GLOBAL_SETTINGS
    if _GLOBAL_SETTINGS is None:
        try:
            loaded = json.loads(_settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        _GLOBAL_SETTINGS = _merge_settings(GLOBAL_DEFAULT_SETTINGS, loaded)
    return _GLOBAL_SETTINGS

def _save_global_settings(settings: dict) -> None:
    global _GLOBAL_SETTINGS
    _GLOBAL_SETTINGS = settings
    try:
        _settings_dir().mkdir(parents=True, exist_ok=True)
        _settings_path().write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _setting(section: str, key: str, default=None):
    return (_global_settings().get(section) or {}).get(key, default)


# The app's light tooltip look, shared by the application sheet and the few
# widgets that need a local QToolTip rule (for example combos inside settings
# panels). A bare "background: transparent" stylesheet must never be used on a
# tooltip's ancestor: it also matches QToolTip and turns its palette black.
# Scope such rules to the container with an object name instead.
TOOLTIP_QSS = (
    "QToolTip { background-color: #ffffff; color: #2c3a48; "
    "border: 1px solid #c9d3df; padding: 4px 6px; }"
)

APP_STYLE = TOOLTIP_QSS + """
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


def _default_palette() -> str:
    name = _setting("plot", "default_colormap", "viridis")
    return name if isinstance(name, str) and name else "viridis"


def _plot_background() -> str:
    """Plot canvas colour (white or a light grey that is easier on the eyes)."""
    return "#f2f4f6" if _setting("appearance", "plot_background", "white") == "grey" else "w"


def _app_stylesheet() -> str:
    """APP_STYLE plus the optional compact-density rules."""
    if _setting("appearance", "density", "normal") != "compact":
        return APP_STYLE
    return APP_STYLE + """
/* compact UI density */
QTreeView::item { padding: 0px; min-height: 18px; }
QTableView::item { padding: 0px; min-height: 18px; }
QHeaderView::section { padding: 1px 4px; }
"""
