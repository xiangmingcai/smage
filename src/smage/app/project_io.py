"""Project I/O, menu bar and window shell."""
from __future__ import annotations

from smage.core.constants import APP_NAME
from smage.widgets.controls import _HoverLabel

from smage import __version__ as APP_VERSION

from PySide6.QtCore import (
    QByteArray,
    Qt,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path
import os
from smage.app.workflow_tree import (
    _PinnedFirstTabBar,
    _count_tasks,
)
from smage.core.settings import (
    _global_settings,
    _save_global_settings,
    _setting,
)
from smage.project import Project
from smage.runlog import runlog
import shutil
from functools import partial
from smage.data import columns as _data_columns
from smage.data import pool as _data_pool
from smage.data import project_paths as _project_paths
from smage.data import scratch as _data_scratch
from smage.data import stores as _data_stores
from smage.modules.import_ import page as _import__page
from smage.modules.segmentation import page as _segmentation_page
from smage.widgets import settings as _widgets_settings


_INVALID_PROJECT_NAME_CHARS = set('<>:"/\\|?*')
_RESERVED_PROJECT_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _clean_project_name(text: str) -> str:
    """Trim a typed project name and drop one optional .smage suffix."""
    name = str(text or "").strip()
    if name.lower().endswith(".smage"):
        name = name[:-6].rstrip()
    return name


def _project_name_problem(name: str) -> str | None:
    """Return a user-facing validation error, or None when the name is valid."""
    if not name:
        return "Enter a project name."
    if name in (".", ".."):
        return "That name is not allowed."
    if any(ch in _INVALID_PROJECT_NAME_CHARS or ord(ch) < 32 for ch in name):
        return 'A project name cannot contain any of: < > : " / \\ | ? *'
    if name.endswith(".") or name.endswith(" "):
        return "A project name cannot end with a dot or a space."
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_PROJECT_NAMES:
        return "That name is reserved by Windows. Choose another name."
    return None


class _RenameProjectDialog(QDialog):
    """Small dialog with the current name and one new-name field."""

    def __init__(self, parent, current_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rename Project")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Current project: {current_name}"))
        self.name_edit = QLineEdit(current_name)
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Rename")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def project_name(self) -> str:
        return self.name_edit.text()


class ProjectIOMixin:
    def _log_environment(self) -> dict:
        """A compact environment snapshot for the session log header."""
        import platform

        info = {
            "version": APP_VERSION,
            "python": platform.python_version(),
        }
        try:
            import PySide6
            from PySide6.QtCore import qVersion

            info["pyside"] = PySide6.__version__
            info["qt"] = qVersion()
        except Exception:
            pass
        try:
            import numpy
            import pandas

            info["numpy"] = numpy.__version__
            info["pandas"] = pandas.__version__
        except Exception:
            pass
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                info["screen"] = f"{geo.width()}x{geo.height()}@{screen.logicalDotsPerInch():.0f}dpi"
        except Exception:
            pass
        info["os"] = f"{platform.system()} {platform.release()}"
        return info

    def closeEvent(self, event) -> None:
        """Warn before closing the app if any open module has unsaved changes."""
        if getattr(self, "_import_running", False):
            QMessageBox.warning(
                self,
                "Close Smage",
                "A data import is still running.\nCancel it first, then close.",
            )
            event.ignore()
            return
        if self._has_unsaved_module_changes():
            reply = QMessageBox.question(
                self,
                "Close Smage",
                "There are unsaved changes.\nReally close? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        # Every module's scratch data is disposable: drop it on exit.
        _segmentation_page._seg_cleanup_temp(self._ctx)
        _data_scratch._scratch_cleanup_all(self._ctx)
        self._save_window_geometry()
        _data_stores._discard_draft_folders(self._ctx)
        runlog.info("window.close")
        runlog.stop("window closed")
        event.accept()

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")

        new_action = QAction("New Project", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project…", self)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        self._rename_project_action = QAction("Rename Project…", self)
        self._rename_project_action.triggered.connect(self._rename_project)
        self._rename_project_action.setEnabled(False)
        file_menu.addAction(self._rename_project_action)

        save_as_action = QAction("Save Project As…", self)
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)

        ext_menu = bar.addMenu("&Extension")
        manage_ext_action = QAction("Manage Extension…", self)
        manage_ext_action.triggered.connect(self._open_extension_manager)
        ext_menu.addAction(manage_ext_action)

        settings_menu = bar.addMenu("&Settings")
        settings_action = QAction("Global Settings…", self)
        settings_action.triggered.connect(partial(_widgets_settings._open_settings, self._ctx))
        settings_menu.addAction(settings_action)

        help_menu = bar.addMenu("&Help")
        about_action = QAction("About Smage", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self) -> None:
        """Version, copyright and license — also what a bug report needs."""
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME} {APP_VERSION}</h3>"
            "<p>Spectral image analysis.</p>"
            "<p>Research use only. Not a medical device and not for clinical "
            "decision-making. You are responsible for your analysis and its "
            "results. Provided without warranty.</p>"
            "<p>Copyright (C) 2026 Amsterdam UMC, the Netherlands.<br>"
            "Author: Xiangming Cai<br>"
            "Advisor: Juan J. Garcia Vallejo</p>"
            "<p>This program is free software under the GNU General Public "
            "License, version 3, released with the approval of Amsterdam UMC. "
            "It comes with ABSOLUTELY NO WARRANTY.<br>"
            "See the LICENSE file for details.</p>"
            "<p><a href='https://github.com/xiangmingcai/smage/blob/main/"
            "DISCLAIMER.md'>Full disclaimer</a> · "
            "<a href='https://github.com/xiangmingcai/smage'>"
            "github.com/xiangmingcai/smage</a></p>",
        )

    def _save_window_geometry(self) -> None:
        settings = _global_settings()
        if (settings.get("appearance") or {}).get("remember_geometry", True):
            geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        else:
            geometry = ""
        settings["window_geometry"] = geometry
        _save_global_settings(settings)

    def _restore_window_geometry(self) -> None:
        if not _setting("appearance", "remember_geometry", True):
            return
        geometry = _global_settings().get("window_geometry") or ""
        if not geometry:
            return
        try:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        except Exception:
            pass

    def _update_project_actions(self) -> None:
        action = getattr(self, "_rename_project_action", None)
        if action is not None:
            action.setEnabled(self.project is not None)

    def _clear_project_runtime_caches(self) -> None:
        """Drop every in-memory value keyed by the old project paths."""
        _data_pool._pool_clear(self._ctx)
        _data_columns._bump_stages_revision(self._ctx)
        self._channel_range_cache = {}
        self._stage_cache = {}
        self._pool_stage_cache = {}
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._unmix_column_cache = {}
        self._seg_value_cache = {}
        self._seg_mask_cache = {}
        self._seg_edge_cache = {}
        self._image_index_cache = {}
        self._subsample_cache = {}
        self._subsample_mask_cache = {}
        self._subsample_counts_cache = {}
        self._subsample_filter_sig_cache = {}
        self._ba_cache = {}
        self._ba_row_cache = {}
        self._gating_store_cache = {}
        self._region_store_cache = {}
        self._tree_project = None
        self._module_sizes.clear()
        self._size_labels.clear()
        self._size_pending.clear()
        self._storage_total = 0

    def _reload_project_views(self) -> None:
        """Reload everything that reads from ``self.project``."""
        self._column_index = self.project.load_columns()
        self._reset_module_tabs()
        _import__page._load_import_files(self._ctx)
        self._load_stored_sizes()
        self._refresh_storage_usage()
        self._refresh_workflow_tree()

    def _prepare_project_transition(self, title: str) -> bool:
        """Close open modules and persist the current project before a switch."""
        if self.project is None:
            return True
        if getattr(self, "_import_running", False):
            QMessageBox.warning(
                self, title,
                "A data import is still running.\nCancel it first, then try again.",
            )
            return False
        if self._has_unsaved_module_changes():
            QMessageBox.warning(
                self, title,
                "There are unsaved module changes.\n"
                "Save or close those modules first.",
            )
            return False
        if self._project_transition_busy():
            QMessageBox.warning(
                self, title,
                "A module calculation is still running.\n"
                "Wait for it to finish, or cancel it, then try again.",
            )
            return False
        if not self._close_all_modules_for_transition():
            return False
        _segmentation_page._seg_cleanup_temp(self._ctx)
        _data_scratch._scratch_cleanup_all(self._ctx)
        _data_stores._discard_draft_folders(self._ctx)
        self.project.save()
        return True

    def _new_project(self) -> None:
        """Create a new project folder (name.smage with project.json + data/)."""
        parent_dir = QFileDialog.getExistingDirectory(
            self, "Choose a location for the project"
        )
        if not parent_dir:
            return

        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        name = _clean_project_name(name)
        if not ok:
            return
        problem = _project_name_problem(name)
        if problem:
            QMessageBox.warning(self, "New Project", problem)
            return

        if self.project is not None and not self._prepare_project_transition("New Project"):
            return
        _segmentation_page._seg_cleanup_temp(self._ctx)
        _data_scratch._scratch_cleanup_all(self._ctx)
        _data_stores._discard_draft_folders(self._ctx)
        try:
            self.project = Project.create(Path(parent_dir), name)
        except OSError as error:
            QMessageBox.warning(self, "New Project", f"Could not create project:\n{error}")
            return
        runlog.start(self.project.path, self._log_environment())
        runlog.info("project.new", path=str(self.project.path), name=self.project.name)

        self._update_project_actions()
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._clear_project_runtime_caches()
        self._reload_project_views()
        self.statusBar().showMessage(f"Project created: {self.project.path}")

    def _open_project(self) -> None:
        """Open an existing project folder."""
        folder = QFileDialog.getExistingDirectory(self, "Open Project")
        if not folder:
            return
        if self.project is not None and not self._prepare_project_transition("Open Project"):
            return
        _segmentation_page._seg_cleanup_temp(self._ctx)
        _data_scratch._scratch_cleanup_all(self._ctx)
        _data_stores._discard_draft_folders(self._ctx)
        try:
            self.project = Project.load(Path(folder))
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "Open Project", f"Could not open project:\n{error}")
            return
        # Clear run folders left by a previous, interrupted session.
        _data_scratch._scratch_cleanup_stale(self._ctx)
        runlog.start(self.project.path, self._log_environment())
        runlog.info(
            "project.open", path=str(self.project.path),
            name=self.project.name, samples=len(self.project.samples),
            tasks=_count_tasks(self.project.workflow.get("tasks") or []),
        )

        self._update_project_actions()
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._clear_project_runtime_caches()
        self._reload_project_views()
        self.statusBar().showMessage(f"Opened: {self.project.path}")

    def _rename_project(self) -> None:
        """Rename the project folder and its manifest in one operation."""
        if self.project is None:
            QMessageBox.information(self, "Rename Project", "No project is open yet.")
            return
        dialog = _RenameProjectDialog(self, self.project.name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = _clean_project_name(dialog.project_name())
        problem = _project_name_problem(name)
        if problem:
            QMessageBox.warning(self, "Rename Project", problem)
            return
        old_path = self.project.path
        new_path = old_path.parent / f"{name}.smage"
        if name == self.project.name:
            return
        same_path = (
            os.path.normcase(str(old_path)) == os.path.normcase(str(new_path))
        )
        if not same_path and new_path.exists():
            QMessageBox.warning(
                self, "Rename Project",
                f"A project folder already exists at:\n{new_path}\n\n"
                "Choose another name.",
            )
            return
        if not self._prepare_project_transition("Rename Project"):
            return

        runlog.stop("project rename")
        try:
            self.project.move_to(new_path, name)
        except Exception as error:
            runlog.start(old_path, self._log_environment())
            QMessageBox.warning(
                self, "Rename Project", f"Could not rename the project:\n{error}"
            )
            return

        runlog.start(self.project.path, self._log_environment())
        runlog.info(
            "project.rename", old=str(old_path),
            new=str(self.project.path), name=self.project.name,
        )
        self._update_project_actions()
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._clear_project_runtime_caches()
        self._reload_project_views()
        self.statusBar().showMessage(f"Renamed: {self.project.path}")

    def _save_project_as(self) -> None:
        """Copy the complete project to a new name/location."""
        if self.project is None:
            QMessageBox.information(self, "Save Project As", "No project is open yet.")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "Choose a location")
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(
            self, "Save Project As", "Project name:", text=self.project.name
        )
        name = _clean_project_name(name)
        if not ok:
            return
        problem = _project_name_problem(name)
        if problem:
            QMessageBox.warning(self, "Save Project As", problem)
            return

        target = Path(parent_dir).resolve() / f"{name}.smage"
        if target.exists():
            QMessageBox.warning(
                self, "Save Project As",
                f"A project folder already exists at:\n{target}\n\n"
                "Choose another name.",
            )
            return
        if target == self.project.path or self.project.path in target.parents:
            QMessageBox.warning(
                self, "Save Project As",
                "Choose a location outside the current project folder.",
            )
            return
        if not self._prepare_project_transition("Save Project As"):
            return

        old_path = self.project.path
        runlog.stop("project save as")
        try:
            shutil.copytree(
                old_path, target,
                ignore=shutil.ignore_patterns("tmp", "log", "draft"),
            )
            # Harden a copy made from an older store layout: any remaining
            # absolute Module paths are made portable before the copy opens.
            _project_paths._migrate_module_stores(target, old_path)
            new_project = Project.load(target)
            new_project.name = name
            new_project.save()
        except (OSError, ValueError, KeyError) as error:
            shutil.rmtree(target, ignore_errors=True)
            runlog.start(old_path, self._log_environment())
            QMessageBox.warning(
                self, "Save Project As", f"Could not save project:\n{error}"
            )
            return

        self.project = new_project
        runlog.start(self.project.path, self._log_environment())
        runlog.info(
            "project.save_as", path=str(self.project.path),
            name=self.project.name, old=str(old_path),
        )
        self._update_project_actions()
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._clear_project_runtime_caches()
        self._reload_project_views()
        self.statusBar().showMessage(f"Saved as: {self.project.path}")

    def _reset_module_tabs(self) -> None:
        """Close all module tabs, keeping only the Workflow tab."""
        while self.tabs.count() > 1:
            self.tabs.removeTab(self.tabs.count() - 1)
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._stage_cache = {}
        self._seg_value_cache = {}
        self._seg_mask_cache = {}
        _segmentation_page._seg_clear_edge_cache(self._ctx)

    def _build_main_area(self) -> None:
        self.tabs = QTabWidget()
        # Module tabs (everything except Workflow) can be dragged to reorder.
        self.tabs.setTabBar(_PinnedFirstTabBar())
        self.tabs.setMovable(True)
        self.setCentralWidget(self.tabs)
        self._build_resource_indicator()

        self._build_workflow_tab()

    def _build_resource_indicator(self) -> None:
        """Project storage and process memory at the right of the tab bar."""
        corner = QWidget()
        corner.setStyleSheet("background: transparent;")
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 10, 0)
        row.setSpacing(14)
        self._storage_label = _HoverLabel("Storage —")
        self._storage_label.setStyleSheet("color: #6b7785; font-size: 9pt;")
        self._storage_label.set_tooltip(
            "Disk space used by this project (its data and module folders)"
        )
        self._ram_label = _HoverLabel("RAM —")
        self._ram_label.setStyleSheet("color: #6b7785; font-size: 9pt;")
        self._ram_label.set_tooltip("Memory used by the Smage process")
        row.addWidget(self._storage_label)
        row.addWidget(self._ram_label)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._refresh_memory_usage()
        self._ram_timer.start()
