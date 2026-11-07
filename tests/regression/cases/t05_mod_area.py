"""L4: Area — density grid, area result and signature behaviour."""
from __future__ import annotations

import math
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


def _open(case_id: str):
    task = harness.task_def("area", task_id="a-1", name="Area")
    project = harness.new_project(case_id, rows=100, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("a-1", "area", "Area")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.area.compute", "L4", "area", tags=("fast",))
def compute() -> None:
    window, _project, page, parquet = _open("area_compute")
    try:
        ctx = window._ctx
        page._area_params["files"] = [parquet]
        page._area_params["grid"] = 5
        cfg = ctx._area_row_cfg(page, parquet)
        cfg.update({"x": "x", "y": "y", "signal": "FL1-A",
                    "low": 0.0, "high": 1e9, "density": 0.0})
        sig = ctx._area_signature(page, parquet, cfg)
        job = {
            "path": parquet,
            "cfg": dict(cfg),
            "sig": sig,
            "cancel": threading.Event(),
        }
        result = ctx._area_compute(page, job, queue.Queue())

        harness.eq(result["rows"], 100)
        harness.eq(result["points"], 100)
        harness.check(result["nx"] >= 1 and result["ny"] >= 1)
        harness.check(result["cells"] <= result["nx"] * result["ny"])
        harness.check(
            math.isclose(result["area"], result["cells"] * result["cell"] ** 2),
            "area is cell count × cell size squared",
        )
        harness.check(result["area"] >= 0)
    finally:
        _close(window)


@harness.case("mod.area.signature", "L4", "area", tags=("fast",))
def signature() -> None:
    window, _project, page, parquet = _open("area_signature")
    try:
        ctx = window._ctx
        page._area_params["files"] = [parquet]
        cfg = ctx._area_row_cfg(page, parquet)
        first = ctx._area_signature(page, parquet, cfg)
        harness.eq(ctx._area_signature(page, parquet, cfg), first)
        cfg["density"] = 3
        harness.check(ctx._area_signature(page, parquet, cfg) != first)
    finally:
        _close(window)


@harness.case("mod.area.save", "L4", "area", tags=("fast",))
def save_params() -> None:
    window, _project, page, parquet = _open("area_save")
    try:
        ctx = window._ctx
        page._area_params["files"] = [parquet]
        page._area_params["grid"] = 8
        cfg = ctx._area_row_cfg(page, parquet)
        cfg["density"] = 2
        ctx._area_save_now(page)

        task = window.project.workflow["tasks"][0]["children"][0]
        harness.eq(task["params"]["files"], [parquet])
        harness.eq(task["params"]["grid"], 8)
        harness.eq(task["params"]["rows"][parquet]["density"], 2)
    finally:
        _close(window)


@harness.case("mod.area.format", "L4", "area", tags=("fast",))
def format_values() -> None:
    window, _project, page, _parquet = _open("area_format")
    try:
        ctx = window._ctx
        harness.eq(ctx._area_format(0), "0")
        harness.eq(ctx._area_format(12.0), "12")
        harness.eq(ctx._area_format(1234.5), "1,234.5")
        harness.eq(ctx._area_format("bad"), "")
        harness.eq(ctx._area_format(float("nan")), "")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
