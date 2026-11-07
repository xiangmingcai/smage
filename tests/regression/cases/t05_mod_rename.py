"""L4: Rename — column/file rename maps, validation and downstream names."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


def _open(case_id: str):
    task = harness.task_def(
        "rename", task_id="r-1", name="Rename",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=30, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("r-1", "rename", "Rename")
    harness.pump(320)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.rename.column", "L4", "rename", tags=("fast",))
def column_rename() -> None:
    window, project, page = _open("rename_column")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._rename_params["mode"] = "column"
        cfg = page._rename_params["column"]
        cfg["files"] = [parquet]
        cfg["rows"] = [{"original": "FL2-A", "new": "NewFL2"}]
        ctx._rename_save_now(page)

        harness.eq(cfg["rename"], {"FL2-A": "NewFL2"})
        harness.check("NewFL2" in ctx._columns_for(parquet, "g-1"))
        harness.check("FL2-A" not in ctx._columns_for(parquet, "g-1"))

        from smage.project import Project

        saved = Project.load(project.path)
        task = saved.workflow["tasks"][0]["children"][0]
        harness.eq(task["params"]["column"]["rename"], {"FL2-A": "NewFL2"})
    finally:
        _close(window)


@harness.case("mod.rename.validation", "L4", "rename", tags=("fast",))
def rename_validation() -> None:
    window, project, page = _open("rename_validation")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._rename_params["mode"] = "column"
        cfg = page._rename_params["column"]
        cfg["files"] = [parquet]
        cfg["rows"] = [
            {"original": "FL1-A", "new": ""},
            {"original": "FL2-A", "new": "Bad/Name"},
            {"original": "FL3-A", "new": "Good"},
        ]
        ctx._rename_save_now(page)
        harness.eq(cfg["rename"], {"FL3-A": "Good"},
                   "empty and invalid names are skipped")
    finally:
        _close(window)


@harness.case("mod.rename.file", "L4", "rename", tags=("fast",))
def file_rename() -> None:
    window, project, page = _open("rename_file")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._rename_params["mode"] = "file"
        cfg = page._rename_params["file"]
        cfg["files"] = [parquet]
        cfg["rows"] = [{"original": parquet, "new": "renamed.fcs"}]
        ctx._rename_save_now(page)

        harness.eq(cfg["rename"], {parquet: "renamed.fcs"})
        available = ctx._available_files("g-1")
        harness.eq(available[0]["name"], "renamed.fcs")
        harness.eq(ctx._imported_files()[0]["name"], "case.fcs")
    finally:
        _close(window)


@harness.case("mod.rename.file_conflict", "L4", "rename", tags=("fast",))
def file_conflict() -> None:
    window, project, page = _open("rename_conflict")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._rename_params["mode"] = "file"
        cfg = page._rename_params["file"]
        cfg["files"] = [parquet]
        cfg["rows"] = [{"original": parquet, "new": "case.fcs"}]
        ctx._rename_save_now(page)
        harness.eq(cfg["rename"], {parquet: "case.fcs"},
                   "the map may repeat the current name")
        harness.eq(ctx._available_files("g-1")[0]["name"], "case.fcs")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
