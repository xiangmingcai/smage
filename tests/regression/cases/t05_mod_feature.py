"""L4: Feature — aggregate rules, output files and background-ignore filtering."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.feature import page as feature_page  # noqa: E402


def _open(case_id: str, ignore=None):
    project = harness.new_project(case_id, rows=30)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame["v"] = np.arange(len(frame), dtype="float64")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    task = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "group",
            "files": [str(parquet)],
            "rules": [
                {"method": "count", "name": "n"},
                {"source": "v", "method": "mean", "name": "mean_v"},
            ],
            "ignore_values": ignore,
        },
    )
    task["output"] = {"saved": False}
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("f-1", "feature", "Feature")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(parquet)


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.feature.calculate", "L4", "feature", tags=("fast",))
def calculate() -> None:
    window, project, page, parquet = _open("feature_calc", ignore=[])
    try:
        ctx = window._ctx
        ctx._feat_calculate(page)
        outputs = page._feat_params.get("outputs") or {}
        harness.check(parquet in outputs, f"outputs: {outputs}")
        record = outputs[parquet]
        harness.eq(record["columns"], ["group", "n", "mean_v"])

        output = project.path / record["path"]
        harness.check(output.is_file(), "the aggregated parquet is written")
        frame = pd.read_parquet(output)
        harness.eq(sorted(frame["group"].tolist()), [1, 2, 3])
        for group in (1, 2, 3):
            expected = int((pd.read_parquet(parquet)["group"] == group).sum())
            harness.eq(int(frame.loc[frame["group"] == group, "n"].iloc[0]),
                       expected)
        harness.check(
            page._feat_task_def["output"] == {"saved": True},
            "the generate module records its saved output",
        )
    finally:
        _close(window)


@harness.case("mod.feature.ignore", "L4", "feature", tags=("fast",))
def ignore_background() -> None:
    window, project, page, parquet = _open("feature_ignore", ignore=[3])
    try:
        ctx = window._ctx
        with harness.patch_dialogs(feature_page,
                                   question=QMessageBox.StandardButton.Yes):
            ctx._feat_calculate(page)
        output = project.path / page._feat_params["outputs"][parquet]["path"]
        frame = pd.read_parquet(output)
        harness.eq(sorted(frame["group"].tolist()), [1, 2],
                   "the ignored background value is dropped")
    finally:
        _close(window)


@harness.case("mod.feature.validation", "L4", "feature", tags=("fast",))
def validation() -> None:
    window, _project, page, _parquet = _open("feature_validation", ignore=[])
    try:
        ctx = window._ctx
        page._feat_params["feature"] = ""
        with harness.patch_dialogs(feature_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._feat_calculate(page)
        harness.check(not page._feat_params.get("outputs"))

        page._feat_params["feature"] = "group"
        page._feat_params["rules"] = [{"source": "v", "method": "mean"}]
        with harness.patch_dialogs(feature_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._feat_calculate(page)
        harness.check(not page._feat_params.get("outputs"),
                      "Count must be the first rule")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
