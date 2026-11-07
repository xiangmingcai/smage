"""L4: Data Clean — validation, output column and signature."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


def _open(case_id: str):
    try:
        import peacoqc  # noqa: F401
    except Exception:  # noqa: BLE001
        harness.skip("peacoqc runtime is not installed")

    project = harness.new_project(case_id, rows=40)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["Time"] = np.arange(len(frame), dtype="float64")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    task = harness.task_def("data_clean", task_id="dc-1", name="Data Clean",
                            params={})
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("dc-1", "data_clean", "Data Clean")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), parquet


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.dataclean.validate", "L4", "data_clean", tags=("fast",))
def validate() -> None:
    window, _project, page, parquet = _open("dc_validate")
    try:
        ctx = window._ctx
        ok, message = ctx._dc_validate(page)
        harness.check(not ok)
        harness.check("Select at least one data file" in message, message)

        page._dc_params["files"] = [str(parquet)]
        ok, message = ctx._dc_validate(page)
        harness.check("acquisition time" in message, message)

        page._dc_params["time"] = "Time"
        page._dc_params["columns"] = ["FL1-A"]
        page._dc_params["name"] = ""
        ok, message = ctx._dc_validate(page)
        harness.check("name for the new column" in message, message)

        page._dc_params["name"] = "x"
        ok, message = ctx._dc_validate(page)
        harness.check("already used" in message, message)

        page._dc_params["name"] = "clean_pass"
        ok, message = ctx._dc_validate(page)
        harness.check(ok, message)
    finally:
        _close(window)


@harness.case("mod.dataclean.write", "L4", "data_clean", tags=("fast",))
def write_output() -> None:
    window, project, page, parquet = _open("dc_write")
    try:
        ctx = window._ctx
        page._dc_params["files"] = [str(parquet)]
        page._dc_params["time"] = "Time"
        page._dc_params["columns"] = ["FL1-A"]
        page._dc_params["name"] = "clean_pass"

        flags = np.zeros(40, dtype="float64")
        flags[::2] = 1.0
        rel, columns, kind = ctx._dc_write_file(page, str(parquet), flags)
        harness.eq(columns, ["clean_pass"])
        harness.eq(kind, "table")
        output = project.path / rel
        harness.check(output.is_file())
        frame = pd.read_parquet(output)
        harness.eq(frame["clean_pass"].tolist(), flags.tolist())
    finally:
        _close(window)


@harness.case("mod.dataclean.signature", "L4", "data_clean", tags=("fast",))
def signature() -> None:
    window, _project, page, parquet = _open("dc_signature")
    try:
        ctx = window._ctx
        params = {"method": "peacoqc", "time": "Time", "columns": ["FL1-A"],
                  "mode": "all", "mad": 6, "it_limit": 0.6,
                  "consecutive_bins": 5, "min_cells": 150,
                  "remove_zeros": False, "name": "clean_pass"}
        first = ctx._dc_signature(params, str(parquet), "dc-1")
        harness.eq(ctx._dc_signature(params, str(parquet), "dc-1"), first)
        params = dict(params, mad=7)
        harness.check(ctx._dc_signature(params, str(parquet), "dc-1") != first)
    finally:
        _close(window)


@harness.case("mod.dataclean.calculate", "L4", "data_clean", tags=("slow",))
def calculate() -> None:
    project = harness.new_project("dc_calculate", rows=500)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["Time"] = np.arange(len(frame), dtype="float64")
    frame.loc[200:250, "FL1-A"] = frame.loc[200:250, "FL1-A"] + 1000.0
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    task = harness.task_def("data_clean", task_id="dc-1", name="Data Clean",
                            params={})
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("dc-1", "data_clean", "Data Clean")
    harness.pump(400)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        page._dc_params.update({
            "files": [str(parquet)],
            "time": "Time",
            "columns": ["FL1-A"],
            "name": "clean_pass",
            "mode": "all",
            "min_cells": 50,
            "consecutive_bins": 3,
        })
        ctx._dc_calculate(page)
        harness.check(page._dc_running)
        harness.wait_for(lambda: not page._dc_running, timeout=120,
                         message="PeacoQC")
        outputs = page._dc_params.get("outputs") or {}
        harness.check(str(parquet) in outputs, f"outputs: {outputs}")
        output = project.path / outputs[str(parquet)]["path"]
        harness.check(output.is_file())
        flags = pd.read_parquet(output)["clean_pass"]
        harness.eq(len(flags), 500)
        harness.check(set(flags.unique().tolist()) <= {0, 1})
    finally:
        harness.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
