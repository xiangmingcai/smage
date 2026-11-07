"""L2: workflow lookup, subsets, module folders and display names."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.data.files import _subset_visible_columns  # noqa: E402


def _open(case_id: str, tasks=(), rows: int = 50):
    project = harness.new_project(case_id, rows=rows, tasks=tasks)
    window = harness.new_window()
    harness.open_project(window, project)
    return window, project


def _close(window) -> None:
    harness.close_window(window)


def _add_file(project, name: str, rows: int = 20) -> str:
    """Add a second imported parquet and return its path."""
    import pandas as pd

    path = project.data_dir / f"{name}.parquet"
    pd.DataFrame({"x": range(rows), "y": range(rows),
                  "FL1-A": range(rows)}).to_parquet(path, index=False)
    columns = project.load_columns()
    columns[path.name] = ["x", "y", "FL1-A"]
    project.save_columns(columns)
    processed = project.workflow["tasks"][0]["params"]["processed"]
    processed.append({
        "name": f"{name}.fcs",
        "source": f"C:/data/{name}.fcs",
        "parquet": str(path),
        "rows": rows,
    })
    project.save()
    return str(path)


@harness.case("data.files.chain", "L2", "data", tags=("smoke",))
def task_chain() -> None:
    tasks = [harness.task_def(
        "scale", task_id="s-1", name="Scale",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )]
    window, _project = _open("data_chain", tasks)
    try:
        ctx = window._ctx
        harness.eq(
            [task["id"] for task in ctx._task_chain("g-1")],
            ["data-import", "s-1", "g-1"],
        )
        harness.eq(ctx._parent_task_name("g-1"), "Scale")
        harness.eq(ctx._parent_task_name("data-import"), None)
        harness.eq(ctx._task_chain("missing"), [])
        harness.eq(ctx._import_task()["id"], "data-import")
        harness.eq(len(ctx._imported_files()), 1)
        harness.eq(
            ctx._module_display_title("g-1", "Gating"), "Scale → Gating"
        )
        harness.eq(
            ctx._module_display_title("s-1", "Scale"), "Data Import → Scale"
        )
    finally:
        _close(window)


@harness.case("data.files.records", "L2", "data", tags=("smoke",))
def file_records() -> None:
    window, project = _open("data_records")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        record = ctx._file_record(parquet)
        harness.check(record is not None, "the imported file record is found")
        harness.eq(record["name"], "case.fcs")
        harness.eq(ctx._file_record(str(project.data_dir / "missing.parquet")), None)

        harness.eq(ctx._rel_data_key(parquet), "case.parquet")
        harness.eq(
            ctx._rel_data_key(r"C:\elsewhere\outside.parquet"), "outside.parquet"
        )

        harness.eq(ctx._comp_file_name(parquet), "case.fcs")
        harness.eq(
            ctx._comp_file_name(str(project.data_dir / "missing.parquet")),
            "missing.parquet",
        )

        harness.eq(ctx._module_dir("s-1"), project.path / "Module" / "s-1")
        harness.eq(ctx._module_dir(None), None)
        harness.eq(ctx._module_rel("s-1", "x.json"), "Module/s-1/x.json")
        ctx._draft_task_ids = {"s-1"}
        harness.eq(
            ctx._module_dir("s-1"),
            project.path / "Module" / "s-1" / "draft",
        )
        harness.eq(
            ctx._module_rel("s-1", "x.json"), "Module/s-1/draft/x.json"
        )

        harness.eq(ctx._module_size_path("data-import"), project.data_dir)
        harness.eq(
            ctx._module_size_path("s-1"), project.path / "Module" / "s-1"
        )
        harness.eq(ctx._module_size_path(None), None)
    finally:
        ctx._draft_task_ids = set()
        _close(window)


@harness.case("data.files.subsets", "L2", "data", tags=("smoke",))
def subset_filters() -> None:
    project = harness.new_project("data_subset", rows=30)
    parquet_a = str(project.data_dir / "case.parquet")
    _add_file(project, "second")
    subset = harness.task_def(
        "subset", task_id="sub-1", name="Subset",
        params={"files": [parquet_a], "columns": ["x", "FL1-A"]},
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project.workflow["tasks"][0]["children"].append(subset)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        harness.eq(
            ctx._subset_filters("g-1"),
            [({parquet_a}, {"x", "FL1-A"})],
        )
        harness.eq(ctx._subset_visible_files("g-1"), {parquet_a})
        harness.eq(_subset_visible_columns(ctx, "g-1"), {"x", "FL1-A"})
        harness.eq(ctx._subset_filters("sub-1"), [], "a subset never hides itself")

        available = ctx._available_files("g-1")
        harness.eq([rec["parquet"] for rec in available], [parquet_a])

        harness.eq(ctx._subset_visible_files("data-import"), None)
        harness.eq(_subset_visible_columns(ctx, "data-import"), None)
    finally:
        _close(window)


@harness.case("data.files.rename_display", "L2", "data", tags=("smoke",))
def rename_display() -> None:
    project = harness.new_project("data_rename", rows=20)
    parquet = str(project.data_dir / "case.parquet")
    rename = harness.task_def(
        "rename", task_id="r-1", name="Rename",
        params={"mode": "file", "file": {"rename": {parquet: "renamed.fcs"}}},
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project.workflow["tasks"][0]["children"].append(rename)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        harness.eq(ctx._rename_file_name("g-1", parquet, "case.fcs"), "renamed.fcs")
        harness.eq(ctx._rename_file_name("r-1", parquet, "case.fcs"), "case.fcs",
                   "a rename does not apply to itself")
        harness.eq(ctx._rename_file_name("g-1", "/other.parquet", "other.fcs"),
                   "other.fcs")

        available = ctx._available_files("g-1")
        harness.eq(available[0]["name"], "renamed.fcs")
        harness.eq(ctx._imported_files()[0]["name"], "case.fcs",
                   "the stored record is not mutated")
        harness.eq(ctx._comp_file_name(parquet, "g-1"), "renamed.fcs")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
