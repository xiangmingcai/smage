"""L4: Subsample — allocation, stable row selection and persisted settings."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
from smage.data import sampling as _data_sampling
from smage.modules.subsample import page as _subsample_page


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
        _subsample_page._subs_save_now(ctx, page)

        index = _data_sampling._subsample_row_index(ctx, "g-1", parquet)
        harness.check(index is not None, "downstream sees a row filter")
        harness.eq(len(index), 10)
        harness.eq(_data_sampling._subsample_visible_count(ctx, "g-1", parquet), 10)
        harness.check(len(set(index.tolist())) == 10, "indices are unique")
        harness.check(all(0 <= int(i) < 100 for i in index))

        again = _data_sampling._subsample_row_index(ctx, "g-1", parquet)
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
        _subsample_page._subs_save_now(ctx, page)
        small = _data_sampling._subsample_row_index(ctx, "g-1", parquet)

        _configure(page, parquet, count=25)
        ctx._subsample_cache = {}
        _subsample_page._subs_save_now(ctx, page)
        large = _data_sampling._subsample_row_index(ctx, "g-1", parquet)

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
        _subsample_page._subs_save_now(ctx, page)
        harness.eq(len(_data_sampling._subsample_row_index(ctx, "g-1", parquet)), 4,
                   "a manual override wins")

        _configure(page, parquet, basis="per_file", unit="percent",
                   count=None, percent=10)
        ctx._subsample_cache = {}
        _subsample_page._subs_save_now(ctx, page)
        harness.eq(len(_data_sampling._subsample_row_index(ctx, "g-1", parquet)), 10,
                   "10% of 100 rows")
    finally:
        _close(window)


@harness.case("mod.subsample.save", "L4", "subsample", tags=("fast",))
def save_params() -> None:
    window, _project, page, parquet = _open("subsample_save")
    try:
        ctx = window._ctx
        _configure(page, parquet, count=7)
        _subsample_page._subs_save_now(ctx, page)

        from smage.project import Project

        saved = Project.load(window.project.path)
        task = saved.workflow["tasks"][0]["children"][0]
        params = task["params"]
        harness.eq(params["files"], [parquet])
        harness.eq(params["basis"], "per_file")
        harness.eq(params["count"], 7)
    finally:
        _close(window)


def _open_gated(case_id: str):
    """Open a Subsample whose upstream Gating task owns two range gates."""
    downstream = harness.task_def("gating", task_id="g-2", name="Gating 2")
    sub = harness.task_def(
        "subsample", task_id="sub-1", name="Subsample",
        children=[downstream],
    )
    gating = harness.task_def(
        "gating", task_id="g-1", name="Gating", children=[sub],
    )
    project = harness.new_project(case_id, rows=200, tasks=[gating])
    parquet = str(project.data_dir / "case.parquet")
    gates = [
        {"id": "low", "name": "Low", "type": "range", "x": "FL1-A",
         "y": None, "x1": 0.0, "x2": 100.0, "parent": None},
        {"id": "high", "name": "High", "type": "range", "x": "FL1-A",
         "y": None, "x1": 300.0, "x2": 2000.0, "parent": None},
    ]
    folder = project.path / "Module" / "g-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gates.json").write_text(
        json.dumps({"files": [parquet], "gates": gates, "per_file": {}}),
        encoding="utf-8",
    )
    project.save()
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("sub-1", "subsample", "Subsample")
    harness.pump(320)
    return window, project, window.tabs.currentWidget(), parquet


def _gate_value(parquet: str):
    return pd.read_parquet(parquet, columns=["FL1-A"])["FL1-A"].to_numpy(
        dtype="float64"
    )


@harness.case("mod.subsample.gate", "L4", "subsample", tags=("fast",))
def gate_filtered_sample() -> None:
    window, _project, page, parquet = _open_gated("subsample_gate")
    try:
        ctx = window._ctx
        params = page._subs_params
        params["files"] = [parquet]
        params["basis"] = "per_file"
        params["unit"] = "points"
        params["count"] = 10
        params["percent"] = None
        params["overrides"] = {}
        params["gate_filters"] = [{"task_id": "g-1", "gate_id": "low"}]
        ctx._subsample_cache = {}
        _subsample_page._subs_save_now(ctx, page)

        index = _data_sampling._subsample_row_index(ctx, "g-2", parquet)
        harness.check(index is not None, "a gate filter restricts the rows")
        harness.eq(len(index), 10)
        values = _gate_value(parquet)
        harness.check(
            (values[index] <= 100.0).all(),
            "every sampled row is inside the selected gate",
        )
        eligible = _data_sampling._subsample_eligible_count(
            ctx, page._subs_task_def, parquet
        )
        harness.eq(eligible, int((values <= 100.0).sum()))

        params["unit"] = "percent"
        params["percent"] = 10.0
        params["count"] = None
        ctx._subsample_cache = {}
        _subsample_page._subs_save_now(ctx, page)
        percent_index = _data_sampling._subsample_row_index(
            ctx, "g-2", parquet
        )
        harness.eq(len(percent_index), int(eligible * 0.1),
                   "percentage is based on the in-gate count")
    finally:
        _close(window)


@harness.case("mod.subsample.gate_union", "L4", "subsample", tags=("fast",))
def gate_union() -> None:
    window, _project, page, parquet = _open_gated("subsample_gate_union")
    try:
        ctx = window._ctx
        params = page._subs_params
        params["files"] = [parquet]
        params["basis"] = "per_file"
        params["unit"] = "points"
        params["count"] = 10
        params["percent"] = None
        params["overrides"] = {}
        params["gate_filters"] = [
            {"task_id": "g-1", "gate_id": "low"},
            {"task_id": "g-1", "gate_id": "high"},
        ]
        ctx._subsample_cache = {}
        _subsample_page._subs_save_now(ctx, page)

        index = _data_sampling._subsample_row_index(ctx, "g-2", parquet)
        harness.check(index is not None, "two gates still restrict the rows")
        harness.eq(len(index), 10)
        values = _gate_value(parquet)
        harness.check(
            ((values[index] <= 100.0) | (values[index] >= 300.0)).all(),
            "multiple gates are combined with OR",
        )
        eligible = _data_sampling._subsample_eligible_count(
            ctx, page._subs_task_def, parquet
        )
        harness.eq(
            eligible,
            int(((values <= 100.0) | (values >= 300.0)).sum()),
        )
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
