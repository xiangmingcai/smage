"""L2: workflow lookup, subsets, module folders and display names."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.data.files import _subset_visible_columns  # noqa: E402
from smage.data import files as _data_files
from smage.data import naming as _data_naming


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


@harness.case("data.image_index.rebuilt", "L2", "data", tags=("fast",))
def image_index_rebuilt() -> None:
    """A rewritten output parquet replaces its cached image pyramid."""
    import numpy as np
    import pandas as pd

    from smage.data.image_index import (
        _build_image_index_files,
        _ensure_image_index,
    )

    window, project = _open("data_index_rebuilt")
    try:
        path = project.data_dir / "generated.parquet"
        grid = np.arange(32 * 32)
        base = {
            "x": (grid % 32).astype("int32"),
            "y": (grid // 32).astype("int32"),
        }
        pd.DataFrame({**base, "old": np.zeros(grid.size, dtype="int8")}) \
            .to_parquet(path, index=False)
        _build_image_index_files(str(path))
        ctx = window._ctx
        ent = _ensure_image_index(ctx, str(path))
        harness.eq(
            ent["manifest"]["color_cols"], ["x", "y", "old"]
        )
        harness.eq(ctx._image_level_2d(str(path), 2).shape[2], 3)
        crop, _rect, _frac = ctx._image_raster(
            str(path), ent["manifest"], (0, 31, 0, 31), 2, "missing",
            None, None,
        )
        harness.check(
            crop is None,
            "a missing channel is not silently replaced by x",
        )

        # A generate module rewrites the same path after a gate/channel rename.
        import os

        pd.DataFrame({**base, "new": np.ones(grid.size, dtype="int8")}) \
            .to_parquet(path, index=False)
        # Make sure the mtime clearly differs from the cached manifest.
        stamp = path.stat().st_mtime + 1.0
        os.utime(path, (stamp, stamp))
        _build_image_index_files(str(path))
        ent = _ensure_image_index(ctx, str(path))
        harness.eq(
            ent["manifest"]["color_cols"], ["x", "y", "new"],
            "the cached manifest is replaced by the rebuilt one",
        )
        level = ctx._image_level_2d(str(path), 2)
        harness.eq(level.shape[2], 3)
        harness.eq(
            round(float(level[:, :, 2].mean()), 3), 1.0,
            "level arrays are reloaded from the rebuilt pyramid",
        )
    finally:
        _close(window)


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
            [task["id"] for task in _data_files._task_chain(ctx, "g-1")],
            ["data-import", "s-1", "g-1"],
        )
        harness.eq(_data_files._parent_task_name(ctx, "g-1"), "Scale")
        harness.eq(_data_files._parent_task_name(ctx, "data-import"), None)
        harness.eq(_data_files._task_chain(ctx, "missing"), [])
        harness.eq(_data_files._import_task(ctx)["id"], "data-import")
        harness.eq(len(_data_files._imported_files(ctx)), 1)
        harness.eq(
            _data_files._module_display_title(ctx, "g-1", "Gating"), "Scale → Gating"
        )
        harness.eq(
            _data_files._module_display_title(ctx, "s-1", "Scale"), "Data Import → Scale"
        )
    finally:
        _close(window)


@harness.case("data.files.records", "L2", "data", tags=("smoke",))
def file_records() -> None:
    window, project = _open("data_records")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        record = _data_files._file_record(ctx, parquet)
        harness.check(record is not None, "the imported file record is found")
        harness.eq(record["name"], "case.fcs")
        harness.eq(_data_files._file_record(ctx, str(project.data_dir / "missing.parquet")), None)

        harness.eq(_data_files._rel_data_key(ctx, parquet), "case.parquet")
        harness.eq(
            _data_files._rel_data_key(ctx, r"C:\elsewhere\outside.parquet"), "outside.parquet"
        )

        harness.eq(_data_naming._comp_file_name(ctx, parquet), "case.fcs")
        harness.eq(
            _data_naming._comp_file_name(ctx, str(project.data_dir / "missing.parquet")),
            "missing.parquet",
        )

        harness.eq(_data_files._module_dir(ctx, "s-1"), project.path / "Module" / "s-1")
        harness.eq(_data_files._module_dir(ctx, None), None)
        harness.eq(_data_files._module_rel(ctx, "s-1", "x.json"), "Module/s-1/x.json")
        ctx._draft_task_ids = {"s-1"}
        harness.eq(
            _data_files._module_dir(ctx, "s-1"),
            project.path / "Module" / "s-1" / "draft",
        )
        harness.eq(
            _data_files._module_rel(ctx, "s-1", "x.json"), "Module/s-1/draft/x.json"
        )

        harness.eq(_data_files._module_size_path(ctx, "data-import"), project.data_dir)
        harness.eq(
            _data_files._module_size_path(ctx, "s-1"), project.path / "Module" / "s-1"
        )
        harness.eq(_data_files._module_size_path(ctx, None), None)
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
            _data_files._subset_filters(ctx, "g-1"),
            [({parquet_a}, {"x", "FL1-A"})],
        )
        harness.eq(_data_files._subset_visible_files(ctx, "g-1"), {parquet_a})
        harness.eq(_subset_visible_columns(ctx, "g-1"), {"x", "FL1-A"})
        harness.eq(_data_files._subset_filters(ctx, "sub-1"), [], "a subset never hides itself")

        available = _data_files._available_files(ctx, "g-1")
        harness.eq([rec["parquet"] for rec in available], [parquet_a])

        harness.eq(_data_files._subset_visible_files(ctx, "data-import"), None)
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
        harness.eq(_data_naming._rename_file_name(ctx, "g-1", parquet, "case.fcs"), "renamed.fcs")
        harness.eq(_data_naming._rename_file_name(ctx, "r-1", parquet, "case.fcs"), "case.fcs",
                   "a rename does not apply to itself")
        harness.eq(_data_naming._rename_file_name(ctx, "g-1", "/other.parquet", "other.fcs"),
                   "other.fcs")

        available = _data_files._available_files(ctx, "g-1")
        harness.eq(available[0]["name"], "renamed.fcs")
        harness.eq(_data_files._imported_files(ctx)[0]["name"], "case.fcs",
                   "the stored record is not mutated")
        harness.eq(_data_naming._comp_file_name(ctx, parquet, "g-1"), "renamed.fcs")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
