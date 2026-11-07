"""L4: Subsample — allocation, stable row selection and persisted settings."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


def _open(case_id: str):
    task = harness.task_def(
        "subsample", task_id="sub-1", name="Subsample",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=100, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("sub-1", "subsample", "Subsample")
    harness.pump(320)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


def _configure(page, parquet: str, **values) -> None:
    params = page._subs_params
    params["files"] = [parquet]
    params["basis"] = values.get("basis", "per_file")
    params["unit"] = values.get("unit", "points")
    params["count"] = values.get("count", 10)
    params["percent"] = values.get("percent", None)
    params["allocation"] = values.get("allocation", "uniform")
    params["overrides"] = values.get("overrides", {})


@harness.case("mod.subsample.count", "L4", "subsample", tags=("fast",))
def count_sampling() -> None:
    window, _project, page, parquet = _open("subsample_count")
    try:
        ctx = window._ctx
        _configure(page, parquet, count=10)
        ctx._subsample_cache = {}
        ctx._subs_save_now(page)

        index = ctx._subsample_row_index("g-1", parquet)
        harness.check(index is not None, "downstream sees a row filter")
        harness.eq(len(index), 10)
        harness.eq(ctx._subsample_visible_count("g-1", parquet), 10)
        harness.check(len(set(index.tolist())) == 10, "indices are unique")
        harness.check(all(0 <= int(i) < 100 for i in index))

        again = ctx._subsample_row_index("g-1", parquet)
        harness.check(
            (index == again).all(), "the selection is stable between calls"
        )
    finally:
        _close(window)


@harness.case("mod.subsample.growth", "L4", "subsample", tags=("fast",))
def stable_growth() -> None:
    window, _project, page, parquet = _open("subsample_growth")
    try:
        ctx = window._ctx
        _configure(page, parquet, count=10)
        ctx._subsample_cache = {}
        ctx._subs_save_now(page)
        small = ctx._subsample_row_index("g-1", parquet)

        _configure(page, parquet, count=25)
        ctx._subsample_cache = {}
        ctx._subs_save_now(page)
        large = ctx._subsample_row_index("g-1", parquet)

        harness.eq(len(small), 10)
        harness.eq(len(large), 25)
        harness.check(
            set(small.tolist()).issubset(set(large.tolist())),
            "a larger target keeps every previously sampled row",
        )
    finally:
        _close(window)


@harness.case("mod.subsample.override", "L4", "subsample", tags=("fast",))
def override_and_percent() -> None:
    window, _project, page, parquet = _open("subsample_override")
    try:
        ctx = window._ctx
        _configure(page, parquet, count=20, overrides={parquet: 4})
        ctx._subsample_cache = {}
        ctx._subs_save_now(page)
        harness.eq(len(ctx._subsample_row_index("g-1", parquet)), 4,
                   "a manual override wins")

        _configure(page, parquet, basis="per_file", unit="percent",
                   count=None, percent=10)
        ctx._subsample_cache = {}
        ctx._subs_save_now(page)
        harness.eq(len(ctx._subsample_row_index("g-1", parquet)), 10,
                   "10% of 100 rows")
    finally:
        _close(window)


@harness.case("mod.subsample.save", "L4", "subsample", tags=("fast",))
def save_params() -> None:
    window, _project, page, parquet = _open("subsample_save")
    try:
        ctx = window._ctx
        _configure(page, parquet, count=7)
        ctx._subs_save_now(page)

        from smage.project import Project

        saved = Project.load(window.project.path)
        task = saved.workflow["tasks"][0]["children"][0]
        params = task["params"]
        harness.eq(params["files"], [parquet])
        harness.eq(params["basis"], "per_file")
        harness.eq(params["count"], 7)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
