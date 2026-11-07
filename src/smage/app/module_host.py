"""Module host: toolbar, open/close, registry, locks and drafts."""
from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QObject,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path
from smage.app.workflow_tree import (
    _task_color,
    _task_kind,
)
from smage.core.constants import (
    MODULE_STATE_SAVED,
    MODULE_STATE_UNMODIFIED,
)
from smage.runlog import runlog
from smage.widgets.resources import (
    _lock_pixmap,
    _module_icon,
)
import copy
import shutil


class _DraftSaveGuard(QObject):
    """Keeps a draft module's Save button disabled.

    Module pages re-enable Save when their settings become dirty. A temporary
    draft has nothing to save, so the button must stay greyed out even then.
    """

    def __init__(self, button) -> None:
        super().__init__(button)
        self._blocked = False

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.EnabledChange
            and not self._blocked
            and hasattr(watched, "isEnabled")
            and watched.isEnabled()
        ):
            self._blocked = True
            try:
                watched.setEnabled(False)
            finally:
                self._blocked = False
        return False



class ModuleHostMixin:
    def _has_unsaved_module_changes(self) -> bool:
        for index in range(1, self.tabs.count()):  # skip Workflow at index 0
            widget = self.tabs.widget(index)
            if (
                getattr(widget, "_fig_dirty", False)
                or getattr(widget, "_scaling_dirty", False)
                or getattr(widget, "_gating_dirty", False)
                or getattr(widget, "_unmix_dirty", False)
                or getattr(widget, "_comp_dirty", False)
                or getattr(widget, "_illum_dirty", False)
                or getattr(widget, "_seg_dirty", False)
                or getattr(widget, "_feat_dirty", False)
                or getattr(widget, "_g2c_dirty", False)
                or getattr(widget, "_fup_dirty", False)
                or getattr(widget, "_subset_dirty", False)
                or getattr(widget, "_subs_dirty", False)
                or getattr(widget, "_rename_dirty", False)
                or getattr(widget, "_dimred_dirty", False)
                or getattr(widget, "_cluster_dirty", False)
                or getattr(widget, "_dc_dirty", False)
                or getattr(widget, "_ba_dirty", False)
                or getattr(widget, "_area_dirty", False)
            ):
                return True
        return False

    def _build_module_toolbar(self, name: str, page: QWidget, include_save: bool = True,
                              locked: bool = False, task_id: str | None = None,
                              include_settings: bool = True,
                              extra_buttons=None,
                              lock_text: str = "Draft (locked)") -> QFrame:
        """Build the module toolbar (title + Save/Settings/Close, plus a lock badge)."""
        bar = QFrame()
        bar.setObjectName("moduleBar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(4)
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_id, name)}</b>"))
        if locked:
            lock = QLabel()
            lock.setPixmap(_lock_pixmap())
            lock.setFixedSize(14, 14)
            lock.setToolTip("Locked — a downstream generate module has saved data")
            tl.addWidget(lock)
            bar._lock_icon = lock
            badge = QLabel(lock_text)
            badge.setStyleSheet("color: #b58900; font-weight: bold;")
            tl.addWidget(badge)
            bar._lock_label = badge
        tl.addStretch()
        for button in extra_buttons or ():
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            tl.addWidget(button)
        if include_save:
            save_btn = QPushButton("Save")
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setEnabled(not locked)
            tl.addWidget(save_btn)
        settings_btn = QPushButton("Settings")
        close_btn = QPushButton("Close")
        for b in (settings_btn, close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda _=False, p=page: self._close_module_page(p))
        if include_settings:
            tl.addWidget(settings_btn)
        tl.addWidget(close_btn)
        return bar

    def _build_module_page(self, name: str, locked: bool = False,
                           task_id: str | None = None) -> QWidget:
        """Build a generic (placeholder) module page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_module_toolbar(name, page, locked=locked, task_id=task_id))

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        if locked:
            note = QLabel("Draft (locked) — changes here are temporary and cannot be saved.")
            note.setStyleSheet("color: #b58900;")
            cl.addWidget(note)
        hint = QLabel(f"{name} is a placeholder module — not implemented yet.")
        hint.setStyleSheet("color: gray;")
        cl.addWidget(hint)
        cl.addStretch()
        layout.addWidget(content, 1)
        return page

    def _build_generate_page(self, name: str, task_def: dict) -> QWidget:
        """Build a generate-module page with Save (params) and Run (params + data)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("moduleBar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(12, 6, 12, 6)
        tl.setSpacing(4)
        tl.addWidget(QLabel(f"<b>{self._module_display_title(task_def.get('id'), name)}</b>"))
        lock_label = QLabel()
        lock_label.setPixmap(_lock_pixmap())
        lock_label.setFixedSize(14, 14)
        lock_label.setToolTip("Locked — generated data has been saved")
        tl.addWidget(lock_label)
        draft_label = QLabel("Draft (locked)")
        draft_label.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft_label)
        tl.addStretch()
        save_btn = QPushButton("Save")
        run_btn = QPushButton("Run")
        close_btn = QPushButton("Close")
        for b in (save_btn, run_btn, close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda _=False, p=page: self._close_module_page(p))
        tl.addWidget(save_btn)
        tl.addWidget(run_btn)
        tl.addWidget(close_btn)
        layout.addWidget(bar)

        content = QWidget()
        content.setStyleSheet("background: #f5f8fb;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        status = QLabel()
        status.setWordWrap(True)
        cl.addWidget(status)
        hint = QLabel("Algorithm parameters (placeholder)")
        hint.setStyleSheet("color: gray;")
        cl.addWidget(hint)
        cl.addStretch()
        layout.addWidget(content, 1)

        def refresh() -> None:
            locked_now = bool(task_def.get("output", {}).get("saved"))
            lock_label.setVisible(locked_now)
            draft_label.setVisible(locked_now)
            save_btn.setEnabled(not locked_now)
            run_btn.setEnabled(not locked_now)
            if locked_now:
                status.setText("Generated data saved ✓ — module and its upstream are locked.")
            else:
                status.setText(
                    "No generated data yet. Use Save to store parameters, "
                    "or Run to generate and save data."
                )

        save_btn.clicked.connect(lambda _=False: self._generate_save(task_def))
        run_btn.clicked.connect(lambda _=False: self._generate_run(task_def, refresh))
        refresh()
        return page

    def _generate_save(self, task_def: dict) -> None:
        """Save a generate module's parameters without generating data."""
        task_def.setdefault("output", {"saved": False})
        if self.project is not None:
            self.project.save()
        self.statusBar().showMessage("Parameters saved (no data generated)")

    def _generate_run(self, task_def: dict, refresh) -> None:
        """Generate data, save it, and lock the module and its upstream."""
        task_def["output"] = {"saved": True}
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()
        refresh()
        self.statusBar().showMessage("Data generated and saved — module locked")

    def _module_register(self, task_id, task_type, page) -> None:
        """Register an opened module with its initial (unmodified) state."""
        self._module_registry[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "page": page,
            "state": MODULE_STATE_UNMODIFIED,
        }

    def _module_set_state(self, page, state) -> None:
        """Update an open module's registry state."""
        task_id = page.property("task_id")
        if task_id and task_id in self._module_registry:
            self._module_registry[task_id]["state"] = state

    def _module_descendant_ids(self, task_id) -> list:
        """Return open descendants of `task_id`, top-down (pre-order)."""
        task = self._find_task_by_id(task_id)
        ordered = []

        def walk(tasks):
            for t in tasks:
                ordered.append(t.get("id"))
                walk(t.get("children", []))

        if task is not None:
            walk(task.get("children", []))
        return [tid for tid in ordered if tid in self._module_registry]

    def _add_draft_badge(self, page) -> None:
        """Mark a locked module that was opened for temporary editing."""
        bar = page.findChild(QFrame, "moduleBar")
        if bar is None or bar.layout() is None:
            return
        lay = bar.layout()
        label = QLabel("Temporary draft")
        label.setStyleSheet("color: #b58900; font-weight: bold;")
        label.setToolTip(
            "This module is locked by saved downstream data."
            + chr(10)
            + "Changes here are temporary: they are written to "
            "Module/<id>/draft/ and deleted when the module closes."
        )
        index = lay.count()
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is not None and item.spacerItem() is not None:
                index = i
                break
        lay.insertWidget(index, label)

    def _guard_draft_save(self, page) -> None:
        """Grey out Save on a temporary draft and keep it disabled.

        Module pages enable Save again as soon as their settings become dirty;
        a draft has nothing that can be saved, so the button stays off.
        """
        guards = []
        for button in page.findChildren(QPushButton):
            if button.text().strip() != "Save":
                continue
            button.setEnabled(False)
            button.setToolTip(
                "Save is disabled: this locked module is open as a temporary "
                "draft, so its changes cannot be saved."
            )
            guard = _DraftSaveGuard(button)
            button.installEventFilter(guard)
            guards.append(guard)
        page._draft_save_guards = guards

    def _install_unlock_button(self, page) -> None:
        """Add the session Unlock button left of Save (or Close)."""
        bar = page.findChild(QFrame, "moduleBar")
        if bar is None or bar.layout() is None:
            return
        lay = bar.layout()
        for i in range(lay.count()):
            widget = lay.itemAt(i).widget()
            if isinstance(widget, QPushButton) and widget.text() == "Unlock":
                return
        btn = QPushButton("Unlock")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(
            "Discard temporary changes and save normally until this module "
            "is closed"
        )
        btn.clicked.connect(lambda _=False: self._module_unlock(page))
        index = None
        for i in range(lay.count()):
            widget = lay.itemAt(i).widget()
            if not isinstance(widget, QPushButton):
                continue
            if widget.text() == "Save":
                index = i
                break
            if widget.text() == "Close" and index is None:
                index = i
        if index is None:
            index = lay.count()
        lay.insertWidget(index, btn)
        page._unlock_btn = btn

    def _add_session_badge(self, page) -> None:
        """Mark a module that was unlocked for the current session."""
        bar = page.findChild(QFrame, "moduleBar")
        if bar is None or bar.layout() is None:
            return
        lay = bar.layout()
        label = QLabel("Unlocked (this session)")
        label.setStyleSheet("color: #b58900; font-weight: bold;")
        label.setToolTip(
            "Changes save normally until this module is closed; it locks "
            "again afterwards."
        )
        index = lay.count()
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is not None and item.spacerItem() is not None:
                index = i
                break
        lay.insertWidget(index, label)

    def _module_unlock(self, page) -> None:
        """Unlock a locked module for this session (discard temporary work)."""
        task_id = page.property("task_id")
        task = self._find_task_by_id(task_id)
        if task is None:
            return
        name = task.get("name") or "Module"
        reply = QMessageBox.warning(
            self, "Unlock Module",
            f"Unlock '{name}'?\n\n"
            "• Unlocking lets you change and save this module again. "
            "Changes may affect downstream modules — please proceed with "
            "caution.\n"
            "• The module is restored to its last saved state: any temporary "
            "changes made while it was locked will be discarded.\n\n"
            "Unlock anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        task_type = task.get("type")
        index = self.tabs.indexOf(page)
        title = self.tabs.tabText(index) if index > 0 else name
        # Closing the draft discards Module/<id>/draft and stops its workers.
        self._close_module_page(page)
        self._session_unlocked.add(str(task_id))
        self._open_module(task_id, task_type, title)
        new_index = self.tabs.count() - 1
        if index > 0 and new_index != index:
            self.tabs.tabBar().moveTab(new_index, index)
        runlog.action(
            "module.unlock", id=str(task_id or ""),
            type=str(task_type or ""),
        )

    def _module_saved(self, page) -> None:
        """Mark a module as saved and push updates to its open downstream modules."""
        self._write_draft_params(page)
        # Any saved change can alter the data flowing downstream.
        self._pool_clear()
        self._channel_range_cache = {}
        self._unmix_flow_cache = {}
        self._unmix_value_cache = {}
        self._stage_cache = {}
        self._seg_value_cache = {}
        # Row filtering depends on the saved sampling settings.
        self._subsample_cache = {}
        self._subsample_mask_cache = {}
        # Batch-alignment models and their per-file cluster assignments.
        self._ba_cache = {}
        self._ba_row_cache = {}
        # Gating gate trees (stored in the module folder).
        self._gating_store_cache = {}
        self._module_set_state(page, MODULE_STATE_SAVED)
        saved_id = page.property("task_id")
        if saved_id:
            task_def = self._find_task_by_id(saved_id) or {}
            runlog.info(
                "module.saved", id=str(saved_id),
                type=str(task_def.get("type") or ""),
                name=str(task_def.get("name") or ""),
            )
        # Saved output can lock (or unlock) modules, so refresh the lock badges
        # and button states in the workflow tree.
        self._refresh_workflow_tree()
        task_id = page.property("task_id")
        if task_id:
            path = self._module_size_path(task_id)
            label = self._size_labels.get(str(task_id))
            if path is not None and Path(path).is_dir():
                self._schedule_module_size(task_id)
            elif label is not None:
                label.setVisible(False)
        for dtid in self._module_descendant_ids(task_id):
            reg = self._module_registry[dtid]
            self._module_refresh_from_upstream(reg["page"], dtid, reg["task_type"])
        self._module_set_state(page, MODULE_STATE_UNMODIFIED)

    def _module_refresh_from_upstream(self, page, task_id, task_type) -> None:
        """Refresh a downstream module's content (visualization only) after an
        upstream save. This module's own unsaved edits are left untouched."""
        if task_type == "figure":
            self._figure_refresh_all(page)
            self._figure_rebuild_settings(page)
        elif task_type == "scale":
            self._scaling_render_preview(page)
        elif task_type == "gating":
            self._gating_render(page)
            self._gating_rebuild_settings(page)
        elif task_type == "segmentation":
            self._seg_refresh_files(page)
        elif task_type == "feature":
            self._feat_refresh_files(page)
        elif task_type == "gate2col":
            self._g2c_refresh_files(page)
        elif task_type == "feature_up":
            self._fup_refresh_files(page)
        elif task_type == "subset":
            self._subset_refresh(page)
        elif task_type == "subsample":
            self._subs_refresh(page)
        elif task_type == "rename":
            self._rename_refresh(page)
        elif task_type == "dimred":
            self._dimred_refresh(page)
        elif task_type == "cluster":
            self._cluster_refresh(page)
        elif task_type == "area":
            self._area_refresh(page)
        elif task_type == "data_clean":
            self._dc_refresh(page)
        elif task_type == "batch_align":
            self._ba_refresh(page)
        elif task_type == "export":
            self._export_refresh(page)

    def _open_module(self, task_id: str | None, task_type: str | None, name: str) -> None:
        """Open (or focus) the module tab for the given task (identified by id)."""
        import time as _time

        started = _time.perf_counter()
        runlog.action("module.open_request", id=str(task_id or ""),
                      type=str(task_type or ""), name=name)
        for index in range(1, self.tabs.count()):  # skip Workflow at index 0
            page = self.tabs.widget(index)
            if page.property("task_id") == task_id:
                self.tabs.setCurrentIndex(index)
                runlog.action("module.focus", id=str(task_id or ""))
                return

        task_def = self._find_task_by_id(task_id)
        locked = task_def is not None and self._task_locked(task_def)
        was_locked = bool(locked)
        kind = _task_kind(task_type or "")
        # A locked module is still editable: it is opened on a deep copy of
        # its definition and everything it writes goes to Module/<id>/draft/,
        # which is removed when the module closes.
        draft = bool(locked and task_type != "import" and task_def is not None)
        if draft:
            task_def = copy.deepcopy(task_def)
            self._draft_task_ids.add(str(task_id))
            locked = False
            # Start from a clean draft folder, seeded with the module's small
            # state files so the page shows the saved state it had before.
            if self.project is not None:
                official = self.project.path / "Module" / str(task_id)
                pending = official / "draft"
                shutil.rmtree(pending, ignore_errors=True)
                try:
                    pending.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pending = None
                if pending is not None:
                    # Do not reuse `name` here: it is the module title.
                    for state_name in ("gates.json", "heatmap.json"):
                        source = official / state_name
                        if source.is_file():
                            try:
                                shutil.copy2(source, pending / state_name)
                            except OSError:
                                pass

        if task_type == "import":
            page = self._build_data_import_page()
        elif task_type == "figure":
            page = self._build_figure_page(name, task_def, locked)
        elif task_type == "heatmap":
            page = self._build_heatmap_page(name, task_def, locked)
        elif task_type == "export":
            page = self._build_export_page(name, task_def, locked)
        elif task_type == "scale":
            page = self._build_scaling_page(name, task_def, locked)
        elif task_type == "gating":
            page = self._build_gating_page(name, task_def, locked)
        elif task_type == "unmix":
            page = self._build_unmix_page(name, task_def, locked)
        elif task_type == "compensation":
            page = self._build_compensation_page(name, task_def, locked)
        elif task_type == "illumination":
            page = self._build_illumination_page(name, task_def, locked)
        elif task_type == "segmentation":
            page = self._build_segmentation_page(name, task_def, locked)
        elif task_type == "feature":
            page = self._build_feature_page(name, task_def, locked)
        elif task_type == "gate2col":
            page = self._build_gate2col_page(name, task_def, locked)
        elif task_type == "feature_up":
            page = self._build_feature_up_page(name, task_def, locked)
        elif task_type == "subset":
            page = self._build_subset_page(name, task_def, locked)
        elif task_type == "subsample":
            page = self._build_subsample_page(name, task_def, locked)
        elif task_type == "rename":
            page = self._build_rename_page(name, task_def, locked)
        elif task_type == "dimred":
            page = self._build_dimred_page(name, task_def, locked)
        elif task_type == "cluster":
            page = self._build_cluster_page(name, task_def, locked)
        elif task_type == "area":
            page = self._build_area_page(name, task_def, locked)
        elif task_type == "data_clean":
            page = self._build_dc_page(name, task_def, locked)
        elif task_type == "batch_align":
            page = self._build_ba_page(name, task_def, locked)
        elif kind == "generate":
            page = self._build_generate_page(name, task_def)
        else:
            page = self._build_module_page(name, locked=locked, task_id=task_id)
        page.setProperty("task_id", task_id)
        if draft:
            page._draft_task_def = task_def
            self._guard_draft_save(page)
            self._add_draft_badge(page)
        # Unlock is offered only while the module is locked (as a draft, or a
        # locked page that is not drafted, such as Data Import).
        if draft or was_locked:
            self._install_unlock_button(page)
        if task_id and str(task_id) in self._session_unlocked:
            self._add_session_badge(page)
        index = self.tabs.addTab(page, name)
        if task_type:
            self.tabs.setTabIcon(
                index, _module_icon(task_type, _task_color(task_type))
            )
        self.tabs.setCurrentWidget(page)
        if task_id:
            self._module_register(task_id, task_type, page)
        runlog.info(
            "module.open", id=str(task_id or ""), type=str(task_type or ""),
            name=name, locked=bool(locked),
            seconds=round(_time.perf_counter() - started, 3),
        )

    def _close_module_page(self, page: QWidget) -> None:
        """Close a module page's tab (never the Workflow tab)."""
        task_id = page.property("task_id")
        task = self._find_task_by_id(task_id) if task_id else None
        runlog.info(
            "module.close", id=str(task_id or ""),
            type=str((task or {}).get("type") or ""),
        )
        if task_id:
            self._module_registry.pop(task_id, None)
        if task_id and str(task_id) in self._draft_task_ids:
            self._draft_task_ids.discard(str(task_id))
            if self.project is not None:
                shutil.rmtree(
                    self.project.path / "Module" / str(task_id) / "draft",
                    ignore_errors=True,
                )
        self._cleanup_module_page(page, (task or {}).get("type"))
        # Closing a session-unlocked module puts it back under its lock.
        session_dropped = False
        if task_id and str(task_id) in getattr(self, "_session_unlocked", ()):
            self._session_unlocked.discard(str(task_id))
            session_dropped = True
        index = self.tabs.indexOf(page)
        if index > 0:
            self.tabs.removeTab(index)
        if session_dropped:
            self._refresh_workflow_tree()

    def _cleanup_module_page(self, page, task_type) -> None:
        """Stop page-owned timers and workers before its tab is removed.

        The module Close buttons call the same cleanups; doing it here as well
        makes the Unlock reload safe (it closes the page without prompts).
        Every cleanup is idempotent.
        """
        try:
            if task_type == "gating":
                self._gating_cleanup(page)
            elif task_type == "heatmap":
                self._heatmap_cleanup(page)
            elif task_type == "export":
                self._export_cleanup(page)
            elif task_type == "figure":
                self._figure_cancel_rendering(page)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        # Feature Down counts values in a background thread regardless of how
        # the page was opened.
        if hasattr(page, "_feat_ignore_requests"):
            try:
                self._feat_ignore_cleanup(page)
            except Exception:  # noqa: BLE001
                pass
        timer = getattr(page, "_fig_filter_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:  # noqa: BLE001
                pass
            page._fig_filter_timer = None
