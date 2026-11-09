"""L4: Compensation — matrix editing, paste, save and downstream values."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.compensation import page as comp_page  # noqa: E402
from smage.data import columns as _data_columns
from smage.modules.compensation import page as comp_page


def _open(case_id: str):
    task = harness.task_def(
        "compensation", task_id="c-1", name="Compensation",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=60, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("c-1", "compensation", "Compensation")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.compensation.save", "L4", "compensation", tags=("fast",))
def save_matrix() -> None:
    window, project, page, parquet = _open("comp_save")
    try:
        ctx = window._ctx
        page._comp_files[:] = [parquet]
        page._comp_names[:] = ["FL1-A", "FL2-A"]
        page._comp_matrix[:] = [[100.0, 10.0], [0.0, 100.0]]
        comp_page._comp_mark_dirty(ctx, page)
        comp_page._comp_save_now(ctx, page)

        task = window.project.workflow["tasks"][0]["children"][0]
        harness.eq(task["params"]["params"], ["FL1-A", "FL2-A"])
        harness.eq(task["params"]["matrix"], [[100.0, 10.0], [0.0, 100.0]])

        raw = pd.read_parquet(parquet)
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A", "FL2-A"])
        expected_a = raw["FL1-A"].to_numpy() + 0.1 * raw["FL2-A"].to_numpy()
        harness.check(np.allclose(values["FL1-A"], expected_a))
        harness.check(
            np.allclose(values["FL2-A"], raw["FL2-A"].to_numpy())
        )
    finally:
        _close(window)


@harness.case("mod.compensation.cached_columns", "L4", "compensation", tags=("fast",))
def cached_columns() -> None:
    """A repeated read of compensated columns is served from the stage cache."""
    window, _project, page, parquet = _open("comp_cached")
    try:
        ctx = window._ctx
        page._comp_files[:] = [parquet]
        page._comp_names[:] = ["FL1-A", "FL2-A"]
        page._comp_matrix[:] = [[100.0, 10.0], [0.0, 100.0]]
        comp_page._comp_mark_dirty(ctx, page)
        comp_page._comp_save_now(ctx, page)

        first = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        harness.check("FL1-A" in first)

        calls = {"n": 0}
        original = pd.read_parquet

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        pd.read_parquet = counting
        try:
            second = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        finally:
            pd.read_parquet = original

        harness.eq(calls["n"], 0, "the second read skips the parquet")
        harness.check(
            np.array_equal(first["FL1-A"], second["FL1-A"]),
            "the cached value matches a fresh read",
        )
    finally:
        _close(window)


@harness.case("mod.compensation.paste", "L4", "compensation", tags=("fast",))
def paste_matrix() -> None:
    window, _project, page, _parquet = _open("comp_paste")
    try:
        ctx = window._ctx
        page._comp_names[:] = ["FL1-A", "FL2-A"]
        page._comp_matrix[:] = [[100.0, 0.0], [0.0, 100.0]]

        QApplication.clipboard().setText(
            "\tFL1-A\tFL2-A\nFL1-A\t100\t25\nFL2-A\t5\t100"
        )
        comp_page._comp_paste_matrix(ctx, page)
        harness.eq(page._comp_matrix, [[100.0, 25.0], [5.0, 100.0]])

        QApplication.clipboard().setText(
            "\tA\tB\nA\t100\t1\nB\t1\t100"
        )
        with harness.patch_dialogs(comp_page,
                                   warning=QMessageBox.StandardButton.Ok):
            comp_page._comp_paste_matrix(ctx, page)
        harness.eq(page._comp_matrix, [[100.0, 25.0], [5.0, 100.0]],
                   "a matrix with different names is rejected")
    finally:
        _close(window)


@harness.case("mod.compensation.apply_params", "L4", "compensation", tags=("fast",))
def apply_params() -> None:
    window, _project, page, _parquet = _open("comp_params")
    try:
        ctx = window._ctx
        page._comp_names[:] = ["A", "B"]
        page._comp_matrix[:] = [[100.0, 10.0], [20.0, 100.0]]
        comp_page._comp_apply_params(ctx, page, ["A", "C"])
        harness.eq(page._comp_names, ["A", "C"])
        harness.eq(page._comp_matrix, [[100.0, 0.0], [0.0, 100.0]],
                   "new names start with an empty off-diagonal")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
