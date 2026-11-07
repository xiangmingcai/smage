"""L4: Illumination — submodules, masks and the per-chip correction stage."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.illumination import page as illum_page  # noqa: E402


def _open(case_id: str):
    task = harness.task_def(
        "illumination", task_id="i-1", name="Illumination",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=100, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("i-1", "illumination", "Illumination")
    harness.pump(400)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.illumination.submodules", "L4", "illumination", tags=("fast",))
def submodules() -> None:
    window, _project, page, _parquet = _open("illum_submodules")
    try:
        ctx = window._ctx
        harness.eq(page._illum_submodules, [])
        ctx._illum_add_submodule(page)
        ctx._illum_add_submodule(page)
        harness.eq(len(page._illum_submodules), 2)
        names = [sub["name"] for sub in page._illum_submodules]
        harness.eq(names, ["Channel1", "Channel2"])

        sub = page._illum_submodules[0]
        ctx._illum_duplicate_submodule(page, sub)
        harness.eq(len(page._illum_submodules), 3)
        harness.eq(page._illum_submodules[-1]["name"], "Channel1 copy")
        harness.eq(page._illum_submodules[-1]["mask"], None)

        with harness.patch_dialogs(illum_page,
                                   question=QMessageBox.StandardButton.Yes):
            ctx._illum_delete_submodule(page, page._illum_submodules[-1])
        harness.eq(len(page._illum_submodules), 2)
    finally:
        _close(window)


@harness.case("mod.illumination.correction", "L4", "illumination", tags=("fast",))
def correction() -> None:
    window, project, page, parquet = _open("illum_correction")
    try:
        ctx = window._ctx
        ctx._illum_add_submodule(page)
        sub = page._illum_submodules[0]
        sub["files"] = [parquet]
        sub["channel"] = "FL1-A"
        sub["x"] = "x"
        sub["y"] = "y"
        sub["tile_w"] = 2
        sub["tile_h"] = 2
        sub["method"] = "median"
        sub["mask"] = [[0.5, 0.25], [0.5, 0.5]]
        sub["mask_source"] = "manual"

        stage = ctx._illum_sub_stage(sub)
        harness.check(stage is not None)
        harness.eq(stage["T"], 0.5, "median is the threshold")
        harness.eq(stage["channel"], "FL1-A")

        ctx._illum_mark_dirty(page)
        ctx._illum_save_now(page)
        stages = ctx._map_stages("g-1")
        harness.eq([kind for kind, _stage in stages], ["illumination"])

        raw = pd.read_parquet(parquet)
        values = ctx._column_data("g-1", parquet, ["FL1-A"])
        mask = np.array([[0.5, 0.25], [0.5, 0.5]], dtype=float)
        xi = raw["x"].to_numpy(dtype=int) % 2
        yi = raw["y"].to_numpy(dtype=int) % 2
        factors = np.where(mask[yi, xi] > 0, 0.5 / mask[yi, xi], 1.0)
        expected = raw["FL1-A"].to_numpy(dtype=float) * factors
        harness.check(np.allclose(values["FL1-A"], expected))
    finally:
        _close(window)


@harness.case("mod.illumination.names", "L4", "illumination", tags=("fast",))
def names() -> None:
    window, _project, page, _parquet = _open("illum_names")
    try:
        ctx = window._ctx
        ctx._illum_add_submodule(page)
        harness.eq(ctx._illum_next_name(page), "Channel2")
        harness.eq(ctx._illum_unique_name(page, "Channel1"), "Channel1 2")
        harness.eq(ctx._illum_unique_name(page, "Other"), "Other")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
