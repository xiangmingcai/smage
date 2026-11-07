"""L4: Gate2Col — convert a gate into a 0/1 column and validate the rules."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.gate2col import page as gate2col_page  # noqa: E402


def _open(case_id: str):
    project = harness.new_project(case_id, rows=80)
    parquet = str(project.data_dir / "case.parquet")
    gates = [{
        "id": "in", "name": "In", "type": "range", "x": "FL1-A", "y": None,
        "x1": 0.0, "x2": 60.0, "parent": None,
    }]
    folder = project.path / "Module" / "g-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gates.json").write_text(
        json.dumps({"files": [parquet], "gates": gates, "per_file": {}}),
        encoding="utf-8",
    )
    task = harness.task_def(
        "gate2col", task_id="g2c-1", name="Gate2Col",
        params={
            "files": [parquet],
            "rules": [{"gate_task": "g-1", "gate_id": "in", "name": "in_gate"}],
        },
    )
    task["output"] = {"saved": False}
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[task])
    project.workflow["tasks"][0]["children"].append(gating)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g2c-1", "gate2col", "Gate2Col")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), parquet


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.gate2col.calculate", "L4", "gate2col", tags=("fast",))
def calculate() -> None:
    window, project, page, parquet = _open("gate2col_calc")
    try:
        ctx = window._ctx
        ctx._g2c_calculate(page)
        outputs = page._g2c_params.get("outputs") or {}
        harness.check(parquet in outputs, f"outputs: {outputs}")
        record = outputs[parquet]
        harness.eq(record["columns"], ["in_gate"])
        harness.eq(record["kind"], "table")

        output = project.path / record["path"]
        harness.check(output.is_file())
        frame = pd.read_parquet(output)
        expected = (
            (pd.read_parquet(parquet)["FL1-A"] >= 0)
            & (pd.read_parquet(parquet)["FL1-A"] <= 60)
        ).astype("int8").to_numpy()
        harness.check(np.array_equal(frame["in_gate"].to_numpy(), expected))
        harness.eq(int(frame["in_gate"].sum()), int(expected.sum()))
    finally:
        _close(window)


@harness.case("mod.gate2col.validation", "L4", "gate2col", tags=("fast",))
def validation() -> None:
    window, _project, page, _parquet = _open("gate2col_validation")
    try:
        ctx = window._ctx
        page._g2c_params["rules"] = []
        with harness.patch_dialogs(gate2col_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._g2c_calculate(page)
        harness.check(not page._g2c_params.get("outputs"))

        page._g2c_params["rules"] = [
            {"gate_task": "g-1", "gate_id": "in", "name": "x"}
        ]
        with harness.patch_dialogs(gate2col_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._g2c_calculate(page)
        harness.check(not page._g2c_params.get("outputs"),
                      "a name colliding with an existing column is rejected")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
