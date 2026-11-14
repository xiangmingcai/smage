"""L4: Batch Alignment — validation, matrix reads and extension guard."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.modules.batch_align import page as ba_page  # noqa: E402
from smage.data import columns as _data_columns
from smage.modules.batch_align import page as ba_page


def _open(case_id: str):
    task = harness.task_def("batch_align", task_id="ba-1", name="Batch Alignment")
    project = harness.new_project(case_id, rows=40, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("ba-1", "batch_align", "Batch Alignment")
    harness.pump(400)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.batch_align.validate", "L4", "batch_align", tags=("fast",))
def validate() -> None:
    window, _project, page, parquet = _open("ba_validate")
    try:
        ctx = window._ctx
        ok, message = ba_page._ba_validate(ctx, page)
        harness.check(not ok and "data file" in message, message)

        page._ba_params["files"] = [parquet]
        ok, message = ba_page._ba_validate(ctx, page)
        harness.check("channel" in message, message)

        page._ba_params["columns"] = ["FL1-A"]
        ok, message = ba_page._ba_validate(ctx, page)
        harness.check("Every file needs a batch" in message, message)

        page._ba_params["batches"] = {parquet: "A"}
        page._ba_params["method"] = "cytonorm"
        ok, message = ba_page._ba_validate(ctx, page)
        harness.check("reference sample" in message, message)

        page._ba_params["references"] = [parquet]
        ok, message = ba_page._ba_validate(ctx, page)
        harness.check(ok, message)
    finally:
        _close(window)


@harness.case("mod.batch_align.matrix", "L4", "batch_align", tags=("fast",))
def matrix_read() -> None:
    window, _project, page, parquet = _open("ba_matrix")
    try:
        ctx = window._ctx
        matrix = ba_page._ba_read_matrix(ctx, page, parquet, ["FL1-A", "FL2-A"])
        harness.eq(matrix.shape, (40, 2))
        harness.check(np.isfinite(matrix).all())
    finally:
        _close(window)


@harness.case("mod.batch_align.signature", "L4", "batch_align", tags=("fast",))
def signature() -> None:
    window, _project, page, parquet = _open("ba_signature")
    try:
        ctx = window._ctx
        params = {"method": "combat", "files": [parquet],
                  "columns": ["FL1-A"], "batches": {parquet: "A"},
                  "combat": {"eb": True}}
        first = ba_page._ba_config_sig(ctx, params)
        harness.eq(ba_page._ba_config_sig(ctx, params), first)
        params = dict(params, method="cytonorm")
        harness.check(ba_page._ba_config_sig(ctx, params) != first)
        harness.eq(ba_page._ba_stage(ctx, page._ba_task_def), None,
                   "no model means no pipeline stage")
    finally:
        _close(window)


@harness.case("mod.batch_align.extension", "L4", "batch_align", tags=("fast",))
def extension_guard() -> None:
    window, _project, page, parquet = _open("ba_extension")
    try:
        ctx = window._ctx
        if not ctx._ext_ready("flowsom"):
            page._ba_params.update({
                "files": [parquet],
                "columns": ["FL1-A"],
                "batches": {parquet: "A"},
                "references": [parquet],
                "method": "cytonorm",
            })
            with harness.patch_dialogs(
                ba_page, information=QMessageBox.StandardButton.Ok
            ):
                ba_page._ba_calculate(ctx, page)
            harness.check(not page._ba_running,
                          "CytoNorm cannot start without FlowSOM")
    finally:
        _close(window)


@harness.case("mod.batch_align.combat", "L4", "batch_align", tags=("fast",))
def combat_fit() -> None:
    project = harness.new_project("ba_combat", rows=40)
    parquet_a = str(project.data_dir / "case.parquet")
    frame = pd.read_parquet(parquet_a)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame.to_parquet(parquet_a, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    shifted = pd.read_parquet(parquet_a).copy()
    shifted["FL1-A"] = shifted["FL1-A"] + 100.0
    parquet_b = e2e.add_parquet(project, "batch_b", shifted)

    task = harness.task_def(
        "batch_align", task_id="ba-1", name="Batch Alignment",
        params={
            "method": "combat",
            "files": [parquet_a, parquet_b],
            "columns": ["FL1-A"],
            "batches": {parquet_a: "A", parquet_b: "B"},
            "references": [],
            "combat": {"eb": True, "adjust_variance": True},
        },
        children=[harness.task_def("gating", task_id="g-1")],
    )
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("ba-1", "batch_align", "Batch Alignment")
    harness.pump(400)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        ba_page._ba_calculate(ctx, page)
        harness.check(page._ba_running)
        harness.wait_for(lambda: not page._ba_running, timeout=60,
                         message="ComBat fit")
        model = page._ba_params.get("model")
        harness.check(isinstance(model, dict), f"model: {model}")
        harness.eq(model["method"], "combat")
        harness.check((project.path / model["npz"]).is_file())

        task = window._find_task_by_id("ba-1")
        stage = ba_page._ba_stage(ctx, task)
        harness.check(stage is not None)
        harness.eq(stage["method"], "combat")

        raw = pd.read_parquet(parquet_a)
        affine = stage["affine"]
        for path, raw_frame in ((parquet_a, raw),
                                (parquet_b, pd.read_parquet(parquet_b))):
            batch = stage["files"][path]
            expected = (
                raw_frame["FL1-A"].to_numpy(dtype=float)
                * float(affine[batch, 0, 0])
                + float(affine[batch, 0, 1])
            )
            values = _data_columns._column_data(ctx, "g-1", path, ["FL1-A"])["FL1-A"]
            harness.check(np.allclose(values, expected),
                          f"{path}: corrected values differ")
    finally:
        harness.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
