"""L4: Heatmap — gate/file/variable selection, calculation and persistence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.heatmap import page as heatmap_page  # noqa: E402


def _open(case_id: str):
    project = harness.new_project(case_id, rows=100)
    parquet = str(project.data_dir / "case.parquet")
    gates = [{
        "id": "in", "name": "In", "type": "range", "x": "FL1-A", "y": None,
        "x1": 0.0, "x2": 50.0, "parent": None,
    }]
    folder = project.path / "Module" / "g-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gates.json").write_text(
        json.dumps({"files": [parquet], "gates": gates, "per_file": {}}),
        encoding="utf-8",
    )
    heatmap = harness.task_def(
        "heatmap", task_id="h-1", name="Heatmap",
        params={
            "files": [parquet],
            "gates": [{"task": "g-1", "id": "in", "label": "In"}],
            "variables": ["x"],
            "sampling": {"mode": "full"},
            "plot": {},
        },
    )
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[heatmap])
    project.workflow["tasks"][0]["children"].append(gating)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("h-1", "heatmap", "Heatmap")
    harness.pump(400)
    return window, project, window.tabs.currentWidget(), parquet


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.heatmap.calculate", "L4", "heatmap", tags=("fast",))
def calculate() -> None:
    window, project, page, parquet = _open("heatmap_calc")
    try:
        ctx = window._ctx
        ctx._heatmap_calculate(page)
        harness.check(page._heatmap_running, "calculation starts")
        harness.wait_for(
            lambda: not page._heatmap_running, timeout=30,
            message="heatmap calculation",
        )

        store = project.path / "Module" / "h-1" / "heatmap.json"
        harness.check(store.is_file(), "the result store is written")
        payload = json.loads(store.read_text(encoding="utf-8"))
        harness.eq(payload["variables"], ["x"])
        harness.eq(len(payload["values"]), 1)
        harness.eq(len(payload["values"][0]), 1)

        frame = pd.read_parquet(parquet)
        selected = frame[(frame["FL1-A"] >= 0) & (frame["FL1-A"] <= 50)]
        expected = float(selected["x"].mean())
        harness.check(
            abs(float(payload["values"][0][0]) - expected) < 1e-9,
            f"mean {payload['values'][0][0]} vs {expected}",
        )
        harness.check("clustering" in payload and payload["clustering"])
        harness.golden("heatmap_values", payload["values"])
    finally:
        _close(window)


@harness.case("mod.heatmap.validation", "L4", "heatmap", tags=("fast",))
def validation() -> None:
    window, _project, page, _parquet = _open("heatmap_validation")
    try:
        ctx = window._ctx
        page._heatmap_params["files"] = []
        with harness.patch_dialogs(heatmap_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._heatmap_calculate(page)
        harness.check(not page._heatmap_running)

        page._heatmap_params["files"] = ["x"]
        page._heatmap_params["gates"] = []
        with harness.patch_dialogs(heatmap_page,
                                   warning=QMessageBox.StandardButton.Ok):
            ctx._heatmap_calculate(page)
        harness.check(not page._heatmap_running)
    finally:
        _close(window)


@harness.case("mod.heatmap.store", "L4", "heatmap", tags=("fast",))
def store_roundtrip() -> None:
    window, project, page, _parquet = _open("heatmap_store")
    try:
        ctx = window._ctx
        payload = {"version": 1, "values": [[1.0]], "files": []}
        ctx._heatmap_store_save("h-1", payload)
        loaded = ctx._heatmap_store_load("h-1")
        harness.eq(loaded["values"], [[1.0]])

        path = project.path / "Module" / "h-1" / "heatmap.json"
        harness.check(path.is_file())
        harness.eq(ctx._heatmap_store_load("missing"), None)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
