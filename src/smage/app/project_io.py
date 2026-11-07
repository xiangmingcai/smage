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
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QTabWidget,
    QWidget,
)
from pathlib import Path
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
import copy
import shutil
from functools import partial
from smage.data import pool as _data_pool
from smage.data import stores as _data_stores
from smage.modules.import_ import page as _import__page
from smage.modules.segmentation import page as _segmentation_page
from smage.widgets import settings as _widgets_settings


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
        # Mask-edge images are temporary: drop this module's cache on exit.
        _segmentation_page._seg_clear_edge_cache(self._ctx)
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
        help_menu.addAction(QAction("About Smage", self))

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

    def _new_project(self) -> None:
        """Create a new project folder (name.smage with project.json + data/)."""
        parent_dir = QFileDialog.getExistingDirectory(
            self, "Choose a location for the project"
        )
        if not parent_dir:
            return

        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        name = name.strip()
        if not ok or not name:
            return

        _data_stores._discard_draft_folders(self._ctx)
        try:
            self.project = Project.create(Path(parent_dir), name)
        except OSError as error:
            QMessageBox.warning(self, "New Project", f"Could not create project:\n{error}")
            return
        runlog.start(self.project.path, self._log_environment())
        runlog.info("project.new", path=str(self.project.path), name=self.project.name)

        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        _data_pool._pool_clear(self._ctx)
        self._channel_range_cache = {}
        self._size_labels.clear()
        self._size_pending.clear()
        self._load_stored_sizes()
        self._refresh_storage_usage()
        self._column_index = self.project.load_columns()
        self._reset_module_tabs()
        _import__page._load_import_files(self._ctx)
        self._refresh_workflow_tree()
        self.statusBar().showMessage(f"Project created: {self.project.path}")

    def _open_project(self) -> None:
        """Open an existing project folder."""
        folder = QFileDialog.getExistingDirectory(self, "Open Project")
        if not folder:
            return
        _data_stores._discard_draft_folders(self._ctx)
        try:
            self.project = Project.load(Path(folder))
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "Open Project", f"Could not open project:\n{error}")
            return
        runlog.start(self.project.path, self._log_environment())
        runlog.info(
            "project.open", path=str(self.project.path),
            name=self.project.name, samples=len(self.project.samples),
            tasks=_count_tasks(self.project.workflow.get("tasks") or []),
        )

        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        self._column_index = self.project.load_columns()
        self._reset_module_tabs()
        _import__page._load_import_files(self._ctx)
        self._refresh_workflow_tree()
        self.statusBar().showMessage(f"Opened: {self.project.path}")

    def _save_project_as(self) -> None:
        """Save the current project under a new name/location."""
        if self.project is None:
            QMessageBox.information(self, "Save Project As", "No project is open yet.")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "Choose a location")
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(
            self, "Save Project As", "Project name:", text=self.project.name
        )
        name = name.strip()
        if not ok or not name:
            return

        _data_stores._discard_draft_folders(self._ctx)
        try:
            new_project = Project.create(Path(parent_dir), name)
            new_project.samples = copy.deepcopy(self.project.samples)
            new_project.workflow = copy.deepcopy(self.project.workflow)
            for item in self.project.data_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, new_project.data_dir / item.name)
            if self.project.ui_state_path.is_file():
                shutil.copy2(self.project.ui_state_path, new_project.ui_state_path)
            new_project.save()
        except OSError as error:
            QMessageBox.warning(self, "Save Project As", f"Could not save project:\n{error}")
            return

        self.project = new_project
        runlog.start(self.project.path, self._log_environment())
        runlog.info("project.save_as", path=str(self.project.path),
                    name=self.project.name)
        # Reload the tree layout from the copied UI state on the next rebuild.
        self._tree_project = None
        self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
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
