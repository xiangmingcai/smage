"""L4: Feature Up — map aggregated features onto a keyed target file."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.feature_up import page as feature_up_page  # noqa: E402


def _open(case_id: str):
    project = harness.new_project(case_id, rows=8)
    to_path = project.data_dir / "cells.parquet"
    from_path = project.data_dir / "agg.parquet"
    pd.DataFrame({
        "cell": np.arange(1, 9, dtype="int32"),
        "x": np.arange(1, 9, dtype="int32"),
        "y": np.arange(1, 9, dtype="int32"),
        "FL1-A": np.arange(1, 9, dtype="float32"),
    }).to_parquet(to_path, index=False)
    pd.DataFrame({
        "cell": np.arange(1, 9, dtype="int32"),
        "area": np.arange(1, 9, dtype="float64") * 10,
    }).to_parquet(from_path, index=False)

    columns = project.load_columns()
    columns["cells.parquet"] = ["cell", "x", "y", "FL1-A"]
    columns["agg.parquet"] = ["cell", "area"]
    project.save_columns(columns)
    processed = project.workflow["tasks"][0]["params"]["processed"]
    processed.extend([
        {"name": "cells.fcs", "source": "C:/data/cells.fcs",
         "parquet": str(to_path), "rows": 8},
        {"name": "agg.fcs", "source": "C:/data/agg.fcs",
         "parquet": str(from_path), "rows": 8},
    ])

    task = harness.task_def(
        "feature_up", task_id="fup-1", name="Feature Up",
        params={
            "from_files": [str(from_path)],
            "to_files": [str(to_path)],
            "key_from": "cell",
            "key_to": "cell",
            "features": [{"source": "area", "name": "cell_area"}],
            "matches": {str(from_path): str(to_path)},
        },
    )
    task["output"] = {"saved": False}
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fup-1", "feature_up", "Feature Up")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(to_path)


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.feature_up.calculate", "L4", "feature_up", tags=("fast",))
def calculate() -> None:
    window, project, page, to_path = _open("feature_up_calc")
    try:
        ctx = window._ctx
        ctx._fup_calculate(page)
        outputs = page._fup_params.get("outputs") or {}
        harness.check(to_path in outputs, f"outputs: {outputs}")
        record = outputs[to_path]
        harness.eq(record["columns"], ["cell_area"])

        output = project.path / record["path"]
        harness.check(output.is_file())
        frame = pd.read_parquet(output)
        harness.eq(frame["cell_area"].tolist(), [float(v * 10) for v in range(1, 9)])
        harness.eq(len(frame), 8)
        harness.check(
            page._fup_task_def["output"] == {"saved": True},
            "the module records its saved output",
        )
    finally:
        _close(window)


@harness.case("mod.feature_up.validation", "L4", "feature_up", tags=("fast",))
def validation() -> None:
    window, _project, page, _to_path = _open("feature_up_validation")
    try:
        ctx = window._ctx
        page._fup_params["from_files"] = []
        with harness.patch_dialogs(feature_up_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._fup_calculate(page)
        harness.check(not page._fup_params.get("outputs"))

        page._fup_params["from_files"] = list(page._fup_params["matches"].keys())
        page._fup_params["features"] = []
        with harness.patch_dialogs(feature_up_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._fup_calculate(page)
        harness.check(not page._fup_params.get("outputs"))
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
