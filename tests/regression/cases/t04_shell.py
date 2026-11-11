"""L5: menu bar, tabs, workflow editing, locking, project dialogs and logging."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QEnterEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.app import project_io, workflow_tree  # noqa: E402
from smage.modules.scale import page as _scale_page  # noqa: E402
from smage.widgets.controls import (  # noqa: E402
    _HoverLabel,
    _NoWheelComboBox,
    _NoWheelSpinBox,
)
from smage.widgets import settings as _widgets_settings


def _open(case_id: str, tasks=(), rows: int = 20):
    project = harness.new_project(case_id, rows=rows, tasks=tasks)
    window = harness.new_window()
    harness.open_project(window, project)
    return window, project


def _close(window) -> None:
    harness.close_window(window)


@harness.case("shell.menu.actions", "L5", "shell", tags=("smoke",))
def menu_actions() -> None:
    window, _project = _open("shell_menu")
    try:
        bar = window.menuBar()
        titles = [action.text().replace("&", "") for action in bar.actions()]
        harness.eq(titles, ["File", "Extension", "Settings", "Help"])

        def action_texts(menu_title):
            for action in bar.actions():
                if action.text().replace("&", "") == menu_title:
                    return [item.text() for item in action.menu().actions()]
            return []

        harness.eq(action_texts("File"),
                   ["New Project", "Open Project…", "Save Project As…"])
        harness.eq(action_texts("Extension"), ["Manage Extension…"])
        harness.eq(action_texts("Settings"), ["Global Settings…"])
        harness.eq(action_texts("Help"), ["About Smage"])
    finally:
        _close(window)


@harness.case("shell.tabs.focus", "L5", "shell", tags=("smoke",))
def tabs_focus() -> None:
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    window, _project = _open("shell_tabs", [task])
    try:
        harness.eq(type(window.tabs.tabBar()).__name__, "_PinnedFirstTabBar")
        harness.check(window.tabs.isMovable(), "module tabs can be reordered")
        harness.eq(window.tabs.count(), 1)

        window._open_module("g-1", "gating", "Gating")
        harness.pump(250)
        harness.eq(window.tabs.count(), 2)
        first = window.tabs.currentWidget()

        window._open_module("g-1", "gating", "Gating")
        harness.pump(80)
        harness.eq(window.tabs.count(), 2, "opening the same module focuses it")
        harness.check(window.tabs.currentWidget() is first)

        window._close_module_page(first)
        harness.pump(80)
        harness.eq(window.tabs.count(), 1)

        window._open_module("g-1", "gating", "Gating")
        harness.pump(200)
        window._reset_module_tabs()
        harness.pump(80)
        harness.eq(window.tabs.count(), 1, "reset keeps only the Workflow tab")
    finally:
        _close(window)


@harness.case("shell.workflow.add", "L5", "shell", tags=("smoke",))
def workflow_add() -> None:
    window, _project = _open("shell_add")
    try:
        import_task = window.project.workflow["tasks"][0]
        window._add_module(import_task, "gating")
        harness.pump(80)
        child = import_task["children"][-1]
        harness.check(child["id"].startswith("gating-"), child["id"])
        harness.eq(child["type"], "gating")
        harness.check("output" not in child, "map modules have no saved-output state")

        window._add_module(import_task, "segmentation")
        harness.pump(80)
        generate = import_task["children"][-1]
        harness.eq(generate["output"], {"saved": False})

        from smage.project import Project

        saved = Project.load(window.project.path)
        harness.eq(len(saved.workflow["tasks"][0]["children"]), 2)
        harness.eq(saved.workflow["tasks"][0]["children"][-1]["type"], "segmentation")
    finally:
        _close(window)


@harness.case("shell.workflow.copy_paste", "L5", "shell", tags=("fast",))
def workflow_copy_paste() -> None:
    """Copied modules paste as new children and survive a reload."""
    task = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        children=[harness.task_def("export", task_id="e-1", name="Export")],
    )
    window, _project = _open("shell_copy_paste", [task])
    try:
        # open_project reloads the manifest: edit the window's project, not
        # the pre-open instance.
        project = window.project
        import_task = project.workflow["tasks"][0]
        source = import_task["children"][0]

        window._copy_task(source, with_branch=False)
        harness.check(window._clipboard is not None, "copy fills the clipboard")
        window._paste_task(import_task, with_branch=False)
        pasted = import_task["children"][-1]
        harness.eq(pasted["type"], "scale")
        harness.check(pasted["id"] != source["id"], "the pasted module gets a new id")
        harness.eq(pasted.get("children"), [], "single paste drops the children")

        window._copy_task(source, with_branch=True)
        window._paste_task(import_task, with_branch=True)
        branch = import_task["children"][-1]
        harness.eq(branch["type"], "scale")
        harness.eq([c["type"] for c in branch["children"]], ["export"])
        harness.check(branch["id"] != source["id"])
        harness.check(branch["children"][0]["id"] != source["children"][0]["id"],
                      "branch ids are reassigned too")

        from smage.project import Project

        saved = Project.load(project.path)
        harness.eq(len(saved.workflow["tasks"][0]["children"]), 3,
                   "single and branch pastes persist")
    finally:
        _close(window)


@harness.case("shell.workflow.lock", "L5", "shell", tags=("smoke",))
def workflow_lock() -> None:
    task = harness.task_def("scale", task_id="s-1", name="Scale")
    window, _project = _open("shell_lock", [task])
    try:
        task = window._find_task_by_id("s-1")
        with harness.patch_dialogs(
            workflow_tree, question=QMessageBox.StandardButton.No
        ):
            window._lock_task(task)
        harness.check(not task.get("locked"), "No cancels the lock")

        with harness.patch_dialogs(
            workflow_tree, question=QMessageBox.StandardButton.Yes
        ):
            window._lock_task(task)
        harness.check(task.get("locked"), "Yes sets the manual lock")
        harness.check(window._task_locked(task))

        window._session_unlocked.add("s-1")
        harness.check(not window._task_locked(task), "a session unlock overrides it")
        window._session_unlocked.discard("s-1")

        from smage.project import Project

        saved = Project.load(window.project.path)
        harness.check(
            saved.workflow["tasks"][0]["children"][0].get("locked") is True,
            "the lock is persisted",
        )
    finally:
        _close(window)


@harness.case("shell.workflow.auto_lock", "L5", "shell", tags=("smoke",))
def workflow_auto_lock() -> None:
    child = harness.task_def(
        "segmentation", task_id="seg-1", name="Segmentation",
        params={"outputs": {}},
    )
    child["output"] = {"saved": True}
    parent = harness.task_def(
        "scale", task_id="s-1", name="Scale", children=[child]
    )
    sibling = harness.task_def("gating", task_id="g-1", name="Gating")
    window, _project = _open("shell_auto_lock", [parent, sibling])
    try:
        harness.check(window._task_locked(parent), "saved output locks the parent")
        harness.check(not window._task_locked(child),
                      "a generate module is not locked by its own output")
        harness.check(not window._task_locked(sibling),
                      "an unrelated branch stays unlocked")
    finally:
        _close(window)


@harness.case("shell.workflow.delete", "L5", "shell", tags=("smoke",))
def workflow_delete() -> None:
    grandchild = harness.task_def("gating", task_id="g-1", name="Gating")
    scale = harness.task_def("scale", task_id="s-1", name="Scale",
                             children=[grandchild])
    other = harness.task_def("rename", task_id="r-1", name="Rename")
    window, _project = _open("shell_delete", [scale, other])
    try:
        import_task = window.project.workflow["tasks"][0]
        scale, other = import_task["children"][0], import_task["children"][1]
        with harness.patch_dialogs(
            workflow_tree, question=QMessageBox.StandardButton.Yes
        ):
            window._delete_task(scale, import_task, False)
        harness.pump(60)
        harness.eq(
            [task["id"] for task in import_task["children"]], ["g-1", "r-1"],
            "deleting a single task promotes its children",
        )

        scale = harness.task_def("scale", task_id="s-2", name="Scale",
                                 children=[harness.task_def("gating", task_id="g-2")])
        import_task["children"].append(scale)
        window.project.save()
        with harness.patch_dialogs(
            workflow_tree, question=QMessageBox.StandardButton.Yes
        ):
            window._delete_task(scale, import_task, True)
        harness.pump(60)
        harness.eq(
            [task["id"] for task in import_task["children"]], ["g-1", "r-1"],
            "deleting a branch removes its children",
        )

        from smage.project import Project

        saved = Project.load(window.project.path)
        harness.eq(
            [task["id"] for task in saved.workflow["tasks"][0]["children"]],
            ["g-1", "r-1"],
        )
    finally:
        _close(window)


@harness.case("shell.workflow.import_menu", "L5", "shell", tags=("smoke",))
def import_card_menu() -> None:
    """The Data Import card cannot be copied, pasted over or deleted."""
    scale = harness.task_def("scale", task_id="s-1", name="Scale")
    window, _project = _open("shell_import_menu", [scale])
    try:
        import_task = window.project.workflow["tasks"][0]
        child = import_task["children"][0]

        menu = window._build_actions_menu(import_task, None)
        labels = [action.text() for action in menu.actions() if action.text()]
        blocked = {
            "Copy", "Copy branch", "Delete", "Delete branch",
        }
        harness.check(not (blocked & set(labels)), str(labels))
        harness.check(
            {"Paste", "Paste branch"} <= set(labels),
            "the Paste actions stay: " + str(labels),
        )
        harness.check("Rename" in labels, labels)

        child_menu = window._build_actions_menu(child, import_task)
        child_labels = [
            action.text() for action in child_menu.actions() if action.text()
        ]
        harness.check(
            {"Copy", "Delete"} <= set(child_labels),
            "other module cards keep copy/delete: " + str(child_labels),
        )
    finally:
        _close(window)


@harness.case("shell.project.new_open", "L5", "shell", tags=("smoke",))
def project_new_open() -> None:
    window, project = _open("shell_project")
    try:
        parent = harness.RUN_DIR / "cases" / "shell_project" / "new"
        parent.mkdir(parents=True, exist_ok=True)
        with harness.patch_dialogs(
            project_io,
            directory=str(parent),
            text="fresh",
            question=QMessageBox.StandardButton.Yes,
        ):
            window._new_project()
        harness.eq(window.project.name, "fresh")
        harness.check((parent / "fresh.smage" / "project.json").is_file())

        with harness.patch_dialogs(
            project_io,
            directory=str(project.path),
            question=QMessageBox.StandardButton.Yes,
        ):
            window._open_project()
        harness.eq(window.project.name, project.name)
    finally:
        _close(window)


@harness.case("shell.project.save_as", "L5", "shell", tags=("smoke",))
def project_save_as() -> None:
    window, project = _open("shell_save_as")
    try:
        parent = harness.RUN_DIR / "cases" / "shell_save_as" / "out"
        parent.mkdir(parents=True, exist_ok=True)
        with harness.patch_dialogs(
            project_io,
            directory=str(parent),
            text="copy",
            question=QMessageBox.StandardButton.Yes,
        ):
            window._save_project_as()

        harness.eq(window.project.name, "copy")
        copy = parent / "copy.smage"
        harness.check((copy / "project.json").is_file())
        harness.check((copy / "data" / "case.parquet").is_file(),
                      "the data files are copied")
        from smage.project import Project

        loaded = Project.load(copy)
        harness.eq(len(loaded.workflow["tasks"][0]["params"]["processed"]), 1)
    finally:
        _close(window)


@harness.case("shell.logging.session", "L5", "shell", tags=("smoke",))
def logging_session() -> None:
    from smage.runlog import runlog

    project = harness.new_project("shell_logging", rows=5)
    runlog.stop("test reset")
    runlog.start(project.path, {"version": "test"})
    runlog.info("test.marker", value=7)
    runlog.error("test.error", exc=ValueError("boom"))
    runlog.stop("test done")
    time.sleep(0.8)

    logs = sorted((project.path / "log").glob("*.log"))
    harness.check(bool(logs), "a session log was created")
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace")
                     for path in logs)
    harness.check("app.start" in text, "the log starts with an app header")
    harness.check("test.marker" in text and "value=7" in text)
    harness.check("test.error" in text and "ValueError" in text)


def _reject_dialog(title: str):
    def tick() -> None:
        for widget in QApplication.topLevelWidgets():
            if (isinstance(widget, QDialog) and widget.isVisible()
                    and widget.windowTitle() == title):
                widget.reject()
                return

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    timer.start()
    return timer


@harness.case("shell.dialogs", "L5", "shell", tags=("fast",))
def settings_and_extension_dialogs() -> None:
    window, _project = _open("shell_dialogs")
    try:
        timer = _reject_dialog("Global Settings")
        try:
            _widgets_settings._open_settings(window._ctx)
        finally:
            timer.stop()

        timer = _reject_dialog("Manage Extensions")
        try:
            window._open_extension_manager()
        finally:
            timer.stop()

        harness.check(True, "both dialogs opened and closed")
    finally:
        _close(window)


@harness.case("shell.extension_runners", "L5", "extensions", tags=("fast",))
def extension_runners() -> None:
    """Every extension's runner script resolves inside the package."""
    from smage.extensions_registry import EXTENSION_DEFS

    window = harness.new_window()
    try:
        missing = [
            entry["id"]
            for entry in EXTENSION_DEFS
            if entry.get("runner")
            and not window._ext_runner_path(entry["id"]).is_file()
        ]
        harness.eq(missing, [], "runner scripts are missing")
    finally:
        harness.close_window(window)


@harness.case("shell.resource_indicator", "L5", "shell", tags=("fast",))
def resource_indicator() -> None:
    window, _project = _open("shell_resources")
    try:
        window._refresh_memory_usage()
        harness.check("RAM" in window._ram_label.text(),
                      window._ram_label.text())
        harness.check("Storage" in window._storage_label.text(),
                      window._storage_label.text())
        harness.check(bool(window._ram_label._tip_text))
        harness.check(bool(window._storage_label._tip_text))
    finally:
        _close(window)


@harness.case("shell.close_prompt", "L5", "shell", tags=("fast",))
def close_prompt() -> None:
    task = harness.task_def("scale", task_id="s-1", name="Scale")
    window, _project = _open("shell_close", [task])
    try:
        window._open_module("s-1", "scale", "Scale")
        harness.pump(300)
        page = window.tabs.currentWidget()
        _scale_page._scaling_mark_dirty(window._ctx, page)
        harness.check(window._has_unsaved_module_changes())

        with harness.patch_dialogs(
            project_io, question=QMessageBox.StandardButton.No
        ):
            window.close()
        harness.pump(120)
        harness.check(
            window.isVisible(),
            "No keeps the window open (known bug: ProjectIOMixin.closeEvent "
            "is shadowed by QMainWindow.closeEvent)",
        )

        with harness.patch_dialogs(
            project_io, question=QMessageBox.StandardButton.Yes
        ):
            window.close()
        harness.pump(120)
        harness.check(not window.isVisible(), "Yes closes the window")
    finally:
        _close(window)


@harness.case("shell.micro_interactions", "L5", "shell", tags=("fast",))
def micro_interactions() -> None:
    window, project = _open("shell_micro")
    try:
        label = _HoverLabel("Storage", window)
        label.set_tooltip("Hello")
        label.show()
        enter = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        label.enterEvent(enter)
        harness.check(label._tip.isVisible(), "the custom tooltip is shown")
        harness.eq(label._tip.text(), "Hello")
        label.leaveEvent(QEvent(QEvent.Type.Leave))
        harness.check(not label._tip.isVisible())

        wheel = QWheelEvent(
            QPointF(0, 0), QPointF(0, 0), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        combo = _NoWheelComboBox()
        combo.addItems(["one", "two"])
        combo.setCurrentIndex(0)
        combo.wheelEvent(wheel)
        harness.eq(combo.currentIndex(), 0,
                   "an unfocused combo ignores the wheel")

        spin = _NoWheelSpinBox()
        spin.setValue(5)
        spin.wheelEvent(wheel)
        harness.eq(spin.value(), 5, "an unfocused spin box ignores the wheel")

        import_task = window.project.workflow["tasks"][0]
        window._show_add_module_picker(import_task)
        harness.pump(150)
        overlay = getattr(window, "_add_module_overlay", None)
        harness.check(overlay is not None and overlay.isVisible(),
                      "the add-module overlay opens")
        overlay.close()
        harness.pump(60)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
