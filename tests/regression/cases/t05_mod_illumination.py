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
from smage.data import columns as _data_columns
from smage.modules.illumination import page as illum_page


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


@harness.case("mod.illumination.sync_views", "L4", "illumination", tags=("fast",))
def sync_views() -> None:
    """The one-shot button copies the active preview's range to the other."""
    window, _project, page, _parquet = _open("illum_sync")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
        harness.pump(200)
        before = page._illum_before_plot
        after = page._illum_after_plot
        harness.check(before is not None, "the Before preview exists")
        harness.check(after is not None, "the After preview exists")

        before.getPlotItem().getViewBox().setRange(
            xRange=(10.0, 20.0), yRange=(30.0, 40.0), padding=0
        )
        page._illum_sync_source = before
        illum_page._illum_sync_views(ctx, page)
        xr, yr = after.getPlotItem().getViewBox().viewRange()
        harness.check(abs(xr[0] - 10.0) < 1e-6, f"x min: {xr}")
        harness.check(abs(xr[1] - 20.0) < 1e-6, f"x max: {xr}")
        harness.check(abs(yr[0] - 30.0) < 1e-6, f"y min: {yr}")
        harness.check(abs(yr[1] - 40.0) < 1e-6, f"y max: {yr}")
    finally:
        _close(window)


@harness.case("mod.illumination.submodules", "L4", "illumination", tags=("fast",))
def submodules() -> None:
    window, _project, page, _parquet = _open("illum_submodules")
    try:
        ctx = window._ctx
        harness.eq(page._illum_submodules, [])
        illum_page._illum_add_submodule(ctx, page)
        illum_page._illum_add_submodule(ctx, page)
        harness.eq(len(page._illum_submodules), 2)
        names = [sub["name"] for sub in page._illum_submodules]
        harness.eq(names, ["Channel1", "Channel2"])

        sub = page._illum_submodules[0]
        illum_page._illum_duplicate_submodule(ctx, page, sub)
        harness.eq(len(page._illum_submodules), 3)
        harness.eq(page._illum_submodules[-1]["name"], "Channel1 copy")
        harness.eq(page._illum_submodules[-1]["mask"], None)

        with harness.patch_dialogs(illum_page,
                                   question=QMessageBox.StandardButton.Yes):
            illum_page._illum_delete_submodule(ctx, page, page._illum_submodules[-1])
        harness.eq(len(page._illum_submodules), 2)
    finally:
        _close(window)


@harness.case("mod.illumination.correction", "L4", "illumination", tags=("fast",))
def correction() -> None:
    window, project, page, parquet = _open("illum_correction")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
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

        stage = illum_page._illum_sub_stage(ctx, sub)
        harness.check(stage is not None)
        harness.eq(stage["T"], 0.5, "median is the threshold")
        harness.eq(stage["channel"], "FL1-A")

        illum_page._illum_mark_dirty(ctx, page)
        illum_page._illum_save_now(ctx, page)
        stages = _data_columns._map_stages(ctx, "g-1")
        harness.eq([kind for kind, _stage in stages], ["illumination"])

        raw = pd.read_parquet(parquet)
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
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
        illum_page._illum_add_submodule(ctx, page)
        harness.eq(illum_page._illum_next_name(ctx, page), "Channel2")
        harness.eq(illum_page._illum_unique_name(ctx, page, "Channel1"), "Channel1 2")
        harness.eq(illum_page._illum_unique_name(ctx, page, "Other"), "Other")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
