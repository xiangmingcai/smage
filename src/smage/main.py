"""Smage — desktop application entry point and main window.

Phase 1 UI skeleton: a File/Help menu bar and a single tabbed workspace.
The first tab is the Workflow (always open); further tabs are modules with
their own toolbar (Save/Settings/Close) and content. The "+" button on a task
opens an "Add Module" dialog with placeholder module types. Module
functionality comes in later phases.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Make the `smage` package importable when this file is run directly.
# A PyInstaller bundle provides the package itself, so the source path must
# not be inserted there.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import (QTimer, Qt)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox, QPushButton)

from smage.extensions_registry import EXTENSION_DEFS, _extension_def
from smage.app.logging_setup import (
    _install_excepthook, _install_faulthandler, _install_qt_message_handler,
)
from smage.runlog import runlog
from smage.app.module_sizes import ModuleSizesMixin
from smage.app.project_io import ProjectIOMixin
from smage.app.extensions import (ExtensionsMixin)
from smage.app.module_host import (ModuleHostMixin)
from smage.app.workflow_tree import (ADDABLE_TYPES, ADD_MODULE_GROUPS, MODULE_KINDS, TASK_TYPES, WorkflowTreeMixin, _ModuleChip, _task_is_locked)
from smage.modules.figure import page as _figure_page
from smage.modules.figure.page import (
    _FigureCard,
    _FigurePermuteDialog,
    _OverlayLayerTable,
    _ZoomableGraphicsView,
)
from smage.modules.heatmap.page import _HeatmapCanvas
from smage.core.constants import (APP_NAME)
from smage.widgets import colormap as _widgets_colormap
from smage.widgets.colormap import (_CustomColormapDialog, _colormap_groups, _colormap_name_taken, _figure_colormap, _palette_names)
from smage.widgets.controls import (_CardProxy, _NoWheelComboBox, _NoWheelSpinBox)
from smage.widgets.plot_axes import (_figure_image_layers)
from smage.widgets.resources import (_app_icon, _folder_size, _module_icon_pixmap)
from smage.widgets.settings import _GlobalSettingsDialog
from smage.widgets.ui_logger import _UIActionLogger
from smage.data.image_index import (_build_image_index_files)
from smage.data.context import AppContext
from smage.data.workers import (_GatingStatsWorker)
from smage.core.settings import (_app_stylesheet, _global_settings, _log_exception, _plot_background, _save_global_settings, _setting, _settings_dir, _settings_path)

from smage.core.formatting import (_format_bytes, _heatmap_cell_text)
from smage.core.gates import (_gate_mask, _gate_own_mask)
from smage.core.heatmap import (_heatmap_fill_nan, _heatmap_linkage)
from smage.core.sampling import (_row_priorities, _stable_sample_selection)
from smage.core.scale_math import (_forward_scale_value, _inverse_scale_value)
from smage.core.formatting import _format_sci
from smage.core.image_ops import _pool_mask
from smage.core.pipeline import _evaluate_stages
from smage.core.sampling import _subsample_allocate
from functools import partial
from smage.data import pool as _data_pool
from smage.widgets import settings as _widgets_settings


# Light-blue "tech" theme, loosely inspired by Positron's blue accents.


# Known task types: type key -> (display name, color).
# "import" is the fixed first task; ADDABLE_TYPES appear in the Add-Module dialog.

# The Add-Module map lists the modules grouped by what they do, in roughly
# workflow order. Each group occupies one horizontal line; a group listed in
# ADD_MODULE_MAP_ROWS is split into the rows given there (in that order).


# Import status colors (the dot shown before each file in the Input panel).


# Human-readable status descriptions (shown as the status dot's tooltip).


# Width of the "Original name" column in the Parameter panel.


# Figure module settings: scatter color modes and palette choices.


# Distinct colours auto-assigned to image channels as they are overlaid.


# Colours auto-assigned to overlay-scatter layers (top layer first).

# Colour of overlay-scatter points that are not inside any visible layer.

# Distinct line colours for the Unmix "Spectral curve" tab (one per signature).


# Figure module histogram display modes.

# Sentinel used by the Figure multi-select settings panel: the selected plots
# disagree on a value, so the field shows blank instead of any one value.


# Scaling (map) module: available transforms.


# Segmentation module: methods and mask storage.


# Per-file mask status shown in the file table.


# Mask Input import states (shown in the mask-input table).


# Default Nuclear Expansion parameters.


# Feature module: aggregation methods, in the order shown in the dropdown.


# Per-file status shown in the Feature file table.


# The Feature log is stored in the module's params; cap it so project.json
# does not grow without bound.


# Defaults shown when a Subsample module first gets a sampling method.


# Extensions: external Python environments managed outside the workflow. Each
# entry describes one optional method plus how to run/install it. `deps` are
# the heavy framework packages that must also be present (installed first,
# checked by Detect).


# PyTorch publishes CUDA wheels on a per-CUDA-version index. A machine may use
# an index only if its NVIDIA driver supports that CUDA version (or newer).
# Ordered newest-first so the first compatible entry is the "best" choice.


# --- Ordered map-module pipeline (Scale / Compensation / Unmix) -----------


# Geometry fields that can differ per file (never the gate type or axes).


# Display state captured on a gate when it is drawn, so clicking the gate later
# restores the plot type, colour mode and colour parameters it was drawn with.


# Default size (px) of the gating plot card on its canvas.


# Smallest span given to a gate preview. pyqtgraph crashes while painting an
# EllipseROI whose width or height is exactly 0, so a drag that is still
# perfectly horizontal/vertical must not reach that state.


# Stored next to extensions.json in %APPDATA%\Smage: preferences follow the
# user, not the project.


# Old palette names that no longer resolve, mapped to their replacement.


# --- Image plot: level-of-detail sample index (disk-cached) ----------------


# Module kinds: every non-import task is a map, generate, or visualize module.
# kind is derived from type (not stored in the manifest), so old projects need
# no migration.


# Open-module registry states: a module is unmodified, modified-but-unsaved, or
# just saved (transient, used to trigger a downstream update before returning
# to unmodified).


class MainWindow(QMainWindow, WorkflowTreeMixin, ModuleHostMixin, ExtensionsMixin, ProjectIOMixin, ModuleSizesMixin):
    """Main window: File/Help menu bar and a single tabbed workspace."""

    def __init__(self) -> None:
        super().__init__()
        # Application icon for the window, the task bar and every dialog.
        icon = _app_icon()
        self.setWindowIcon(icon)
        application = QApplication.instance()
        if application is not None:
            application.setWindowIcon(icon)
        # The context is a thin proxy over this window; create it before any
        # timer or menu wiring captures it.
        self._ctx = AppContext(self)
        self.project = None
        self._clipboard = None
        self._import_files = []
        self._processed_files = []
        self._column_index = {}
        self._updating_checks = False
        self._updating_sample = False
        self._current_entry = None
        self._module_registry = {}
        # Task ids opened as temporary drafts (locked modules). Their writes
        # are redirected to Module/<id>/draft/ and discarded on close.
        self._draft_task_ids = set()
        # Task ids unlocked for the current session (Unlock button). The lock
        # itself stays saved; closing the module removes the id again.
        self._session_unlocked = set()
        # Shared sampled-row pools + pooled columns for scatter/histogram
        # rendering (Figure, Gating, Scale preview). See _pool_request.
        self._pool_rows_cache = {}
        self._pool_col_cache = {}
        self._pool_col_order = []
        self._pool_col_bytes = 0
        self._pool_queued = set()
        self._pool_hit_logged = set()
        self._pool_stage_cache = {}
        self._pool_waiters = []
        self._pool_generation = 0
        self._channel_range_cache = {}
        self._pool_failed = set()
        self._pool_jobs = None
        self._pool_results = None
        self._pool_worker = None
        self._pool_timer = QTimer(self)
        self._pool_timer.setInterval(40)
        self._pool_timer.timeout.connect(partial(_data_pool._pool_poll, self._ctx))
        # Storage / memory readouts (tab corner + workflow cards).
        self._module_sizes = {}     # task id -> (bytes, file count)
        self._size_labels = {}      # task id -> the label on its card
        self._size_pending = set()
        self._size_jobs = None
        self._size_results = None
        self._size_worker = None
        self._size_timer = QTimer(self)
        self._size_timer.setInterval(150)
        self._size_timer.timeout.connect(self._poll_module_sizes)
        self._ram_timer = QTimer(self)
        self._ram_timer.setInterval(30_000)
        self._ram_timer.timeout.connect(self._refresh_memory_usage)
        self._storage_total = 0
        # Workflow-tree expansion state, restored per project (see ui_state.json).
        self._tree_project = None
        self._tree_expanded = {}
        self._tree_restoring = False
        self._tree_force_expand = set()
        # On-the-fly Unmix configs and computed map-stage outputs.
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._stage_cache = {}
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self._build_menu_bar()
        self._build_main_area()
        self._restore_window_geometry()
        _widgets_settings._apply_appearance_settings(self._ctx)
        # Session log: record user actions (see runlog.py). Installed after the
        # UI exists so menu actions are known.
        self._action_logger = _UIActionLogger(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._action_logger)
        self._action_logger.log_actions(self.menuBar())
        self.statusBar().showMessage("Ready")


    def closeEvent(self, event) -> None:
        """Run the project close path.

        ``QMainWindow`` provides a ``closeEvent`` stub earlier in the MRO than
        ``ProjectIOMixin``, so the mixin's unsaved-changes prompt, geometry
        save, draft cleanup and log stop would otherwise never run.
        """
        ProjectIOMixin.closeEvent(self, event)

    def _figure_permute_dialog(self, *args, **kwargs):
        """Construct the dialog through the module global (tests patch it)."""
        return _FigurePermuteDialog(self, *args, **kwargs)


    #
    # Scatter-style views only ever draw a few thousand points out of files
    # with millions of rows, yet they used to read and scan the whole column
    # every time. A pool is the set of rows with the lowest stable-sampling
    # priorities (the same rows the final sample would pick, in priority
    # order), so density and the drawn points can be computed on ~1M rows
    # instead of tens of millions. Column values are pooled in the
    # background, once per file + task + column, and shared by every view.
    _POOL_TIERS = (
        20_000, 50_000, 100_000, 200_000, 300_000,
        500_000, 1_000_000, 2_000_000,
    )
    _POOL_MAX_BYTES = 600 * 1024 * 1024


    # -- overlay scatter ---------------------------------------------------


        # Other module types have no live visualization to refresh yet.


    # --- compensation helpers


    # --- right side


    # --- segmentation page


    # --- segmentation preview (part 3) and plot settings (part 4)


    # -- page construction -------------------------------------------------


    # -- refresh -----------------------------------------------------------


    # -- calculation -------------------------------------------------------


    # -- rendering ---------------------------------------------------------


    # -- save / close ------------------------------------------------------


    #
    # Two sub-pages share one layout: files (left), columns (middle) and
    # settings + log (right). Every export runs in a background thread and
    # reports through a queue, so the UI never blocks.


    # -- page construction -------------------------------------------------


    # -- selection and summaries -------------------------------------------


        # Logs are output, not settings: they never make the module dirty.

    # -- output names and conflicts ----------------------------------------


    # -- background job ----------------------------------------------------


    # -- save / close ------------------------------------------------------


def _run_script(path: str) -> int:
    """Execute a regression case script inside this (possibly frozen) app."""
    import os
    import runpy

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    script = Path(path).resolve()
    # ``runpy`` does not put the script's directory on sys.path the way a
    # normal ``python script.py`` does; case helpers live next to the script.
    sys.path.insert(0, str(script.parent.parent))
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0 if code is None else 1
    return 0


def _run_tests_options(argv: list[str]) -> tuple[tuple[str, ...], str | None, str | None]:
    tags: list[str] = []
    case = None
    root = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        value = argv[i + 1] if i + 1 < len(argv) else None
        if arg == "--tag" and value:
            tags.append(value)
            i += 2
        elif arg == "--case" and value:
            case = value
            i += 2
        elif arg == "--root" and value:
            root = value
            i += 2
        else:
            i += 1
    return tuple(tags), case, root


def _run_self_test() -> int:
    """Import every dependency and check bundled files; used by frozen builds."""
    from smage.selfcheck import run_self_check, write_report

    problems = run_self_check()
    report = write_report(problems)
    for problem in problems:
        print(f"FAIL: {problem}")
    print("SELF-CHECK OK" if not problems else f"SELF-CHECK: {len(problems)} problem(s)")
    print(f"report: {report}")
    return 0 if not problems else 1


def main() -> int:
    """Run the application."""
    if "--self-test" in sys.argv:
        return _run_self_test()
    if "--run-tests" in sys.argv:
        from smage.testrun import run_tests

        tags, case, root = _run_tests_options(sys.argv)
        return run_tests(tags, case, root)
    if "--run-script" in sys.argv:
        index = sys.argv.index("--run-script")
        if index + 1 >= len(sys.argv):
            print("--run-script needs a path")
            return 2
        return _run_script(sys.argv[index + 1])
    _install_excepthook()
    _install_faulthandler()
    _install_qt_message_handler()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    font = app.font()
    font.setPointSize(int(_setting("appearance", "font_size", 9) or 9))
    app.setFont(font)
    app.setStyleSheet(_app_stylesheet())
    # Some Windows themes draw native tooltips with a dark palette even
    # though the app is light; pin the tooltip colours used by Qt.
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#2c3a48"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
