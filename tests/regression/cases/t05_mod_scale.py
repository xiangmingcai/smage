"""L4: Scale — apply settings to variables, save, and downstream value change."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.scale import page as scale_page  # noqa: E402
from smage.data import columns as _data_columns
from smage.modules.scale import page as scale_page


def _open(case_id: str):
    task = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=80, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("s-1", "scale", "Scale")
    harness.pump(400)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.scale.apply", "L4", "scale", tags=("fast",))
def apply_method() -> None:
    window, project, page = _open("scale_apply")
    try:
        ctx = window._ctx
        page._scaling_apply_vars = ["FL1-A"]
        index = page._scaling_apply_method.findData("log10")
        harness.check(index >= 0, "log10 is offered")
        page._scaling_apply_method.setCurrentIndex(index)
        page._scaling_apply_param.setText("")
        page._scaling_apply_min.setText("")
        page._scaling_apply_max.setText("")
        scale_page._scaling_apply_batch(ctx, page)

        config = next(
            item for item in page._scaling_columns if item["name"] == "FL1-A"
        )
        harness.eq(config["method"], "log10")
        harness.check(page._scaling_dirty)

        parquet = str(project.data_dir / "case.parquet")
        page._scaling_files = [parquet]
        scale_page._scaling_save_now(ctx, page)
        task = window.project.workflow["tasks"][0]["children"][0]
        saved = task["params"]
        harness.eq(saved["files"], [parquet])
        saved_config = next(
            item for item in saved["columns"] if item["name"] == "FL1-A"
        )
        harness.eq(saved_config["method"], "log10")

        raw = pd.read_parquet(parquet)["FL1-A"].to_numpy(dtype="float64")
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        harness.check(
            np.allclose(values["FL1-A"], np.log10(raw)),
            "downstream reads the transformed values",
        )
    finally:
        _close(window)


@harness.case("mod.scale.arcsinh", "L4", "scale", tags=("fast",))
def apply_arcsinh() -> None:
    window, project, page = _open("scale_arcsinh")
    try:
        ctx = window._ctx
        page._scaling_apply_vars = ["FL2-A"]
        page._scaling_apply_method.setCurrentIndex(
            page._scaling_apply_method.findData("arcsinh")
        )
        page._scaling_apply_param.setText("5")
        scale_page._scaling_apply_batch(ctx, page)
        config = next(
            item for item in page._scaling_columns if item["name"] == "FL2-A"
        )
        harness.eq(config["method"], "arcsinh")
        harness.eq(config["param"], 5.0)

        parquet = str(project.data_dir / "case.parquet")
        page._scaling_files = [parquet]
        scale_page._scaling_save_now(ctx, page)
        raw = pd.read_parquet(parquet)["FL2-A"].to_numpy(dtype="float64")
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL2-A"])
        harness.check(np.allclose(values["FL2-A"], np.arcsinh(raw / 5.0)))
    finally:
        _close(window)


@harness.case("mod.scale.validation", "L4", "scale", tags=("fast",))
def apply_validation() -> None:
    window, _project, page = _open("scale_validation")
    try:
        ctx = window._ctx
        before = [dict(item) for item in page._scaling_columns]

        page._scaling_apply_vars = []
        with harness.patch_dialogs(scale_page,
                                   information=QMessageBox.StandardButton.Ok):
            scale_page._scaling_apply_batch(ctx, page)
        harness.eq(page._scaling_columns, before, "no variables means no change")

        page._scaling_apply_vars = ["FL1-A"]
        page._scaling_apply_method.setCurrentIndex(
            page._scaling_apply_method.findData("arcsinh")
        )
        page._scaling_apply_param.setText("not-a-number")
        with harness.patch_dialogs(scale_page,
                                   warning=QMessageBox.StandardButton.Ok):
            scale_page._scaling_apply_batch(ctx, page)
        config = next(
            item for item in page._scaling_columns if item["name"] == "FL1-A"
        )
        harness.eq(config["method"], "linear", "the invalid edit is rejected")

        page._scaling_apply_vars = ["FL1-A"]
        page._scaling_apply_method.setCurrentIndex(0)
        page._scaling_apply_param.setText("")
        with harness.patch_dialogs(scale_page,
                                   information=QMessageBox.StandardButton.Ok):
            scale_page._scaling_apply_batch(ctx, page)
        harness.eq(page._scaling_columns, before, "empty fields do nothing")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
