"""L7: window lifecycle and teardown stability.

A crash in this script is reported by ``run_all.py`` as a script-exit failure;
a clean exit is not enough on its own, but it catches the interpreter-shutdown
segfaults that used to follow many offscreen Qt tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


@harness.case("sys.window.empty", "L7", "infra", tags=("smoke",))
def empty_window() -> None:
    window = harness.new_window()
    harness.eq(window.tabs.count(), 1, "a fresh window shows only the Workflow tab")
    window.close()
    harness.pump(150)


@harness.case("sys.window.project", "L7", "infra", tags=("smoke",))
def project_window() -> None:
    task = harness.task_def("gating", task_id="gating-teardown", name="Gating")
    project = harness.new_project(
        "teardown_project", rows=50, tasks=[task], shared=True
    )
    window = harness.new_window()
    harness.open_project(window, project)
    harness.eq(window.project.name, project.name, "project was attached")
    window.close()
    harness.pump(150)


@harness.case("sys.page.reopen", "L7", "infra", tags=("smoke",))
def page_reopen() -> None:
    task = harness.task_def("scale", task_id="scale-teardown", name="Scale")
    project = harness.new_project(
        "teardown_reopen", rows=80, tasks=[task], shared=True
    )
    window = harness.new_window()
    harness.open_project(window, project)

    for cycle in range(3):
        window._open_module("scale-teardown", "scale", "Scale")
        harness.pump(250)
        page = window.tabs.currentWidget()
        harness.eq(
            page.property("task_id"),
            "scale-teardown",
            f"Scale page did not open on cycle {cycle + 1}",
        )
        window._close_module_page(page)
        harness.pump(120)
        harness.eq(window.tabs.count(), 1, f"Scale page did not close on cycle {cycle + 1}")

    window.close()
    harness.pump(150)


if __name__ == "__main__":
    raise SystemExit(harness.main())
