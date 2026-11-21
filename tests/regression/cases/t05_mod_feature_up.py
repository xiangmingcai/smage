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
from smage.modules.feature_up import page as feature_up_page


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


def _wait_calc(page) -> None:
    harness.wait_for(
        lambda: not getattr(page, "_fup_running", False),
        timeout=60,
        message="Feature Up calculation to finish",
    )


@harness.case("mod.feature_up.calculate", "L4", "feature_up", tags=("fast",))
def calculate() -> None:
    window, project, page, to_path = _open("feature_up_calc")
    try:
        ctx = window._ctx
        feature_up_page._fup_calculate(ctx, page)
        _wait_calc(page)
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


@harness.case("mod.feature_up.nonblocking", "L4", "feature_up", tags=("fast",))
def calculate_runs_in_background() -> None:
    """Calculate returns while the mapping continues on a worker thread."""
    import time

    window, _project, page, _to_path = _open("feature_up_nonblocking")
    original = feature_up_page._fup_compute_file

    def slow(*args, **kwargs):
        time.sleep(0.8)
        return original(*args, **kwargs)

    feature_up_page._fup_compute_file = slow
    try:
        ctx = window._ctx
        started = time.perf_counter()
        feature_up_page._fup_calculate(ctx, page)
        elapsed = time.perf_counter() - started
        harness.check(
            elapsed < 0.5,
            f"Calculate blocked the UI for {elapsed:.2f}s",
        )
        harness.check(
            page._fup_running,
            "the calculation continues after Calculate returns",
        )
        _wait_calc(page)
        harness.check(not page._fup_running)
    finally:
        feature_up_page._fup_compute_file = original
        _close(window)


@harness.case("mod.feature_up.validation", "L4", "feature_up", tags=("fast",))
def validation() -> None:
    window, _project, page, _to_path = _open("feature_up_validation")
    try:
        ctx = window._ctx
        page._fup_params["from_files"] = []
        with harness.patch_dialogs(feature_up_page,
                                   warning=QMessageBox.StandardButton.Ok):
            feature_up_page._fup_calculate(ctx, page)
        _wait_calc(page)
        harness.check(not page._fup_params.get("outputs"))

        page._fup_params["from_files"] = list(page._fup_params["matches"].keys())
        page._fup_params["features"] = []
        with harness.patch_dialogs(feature_up_page,
                                   warning=QMessageBox.StandardButton.Ok):
            feature_up_page._fup_calculate(ctx, page)
        _wait_calc(page)
        harness.check(not page._fup_params.get("outputs"))
    finally:
        _close(window)


@harness.case("mod.feature_up.match", "L4", "feature_up", tags=("fast",))
def match_uses_display_names() -> None:
    """Match pairs hashed To parquet files by their visible names."""
    project = harness.new_project("feature_up_match", rows=4)
    to_path = project.data_dir / "4ffec2de1d4b41409aa5364df06e3889.parquet"
    from_path = project.data_dir / "raw_sample.ome_Mask.parquet"
    pd.DataFrame({
        "cell": np.arange(1, 5, dtype="int32"),
        "x": np.arange(1, 5, dtype="int32"),
        "y": np.arange(1, 5, dtype="int32"),
    }).to_parquet(to_path, index=False)
    pd.DataFrame({
        "cell": np.arange(1, 5, dtype="int32"),
        "area": np.arange(1, 5, dtype="float64"),
    }).to_parquet(from_path, index=False)
    columns = project.load_columns()
    columns[to_path.name] = ["cell", "x", "y"]
    columns[from_path.name] = ["cell", "area"]
    project.save_columns(columns)
    project.workflow["tasks"][0]["params"]["processed"].extend([
        {"name": "raw_sample.ome.tiff", "source": "C:/data/sample.tiff",
         "parquet": str(to_path), "rows": 4},
        {"name": "raw_sample.ome_Mask", "source": "C:/data/sample_Mask",
         "parquet": str(from_path), "rows": 4},
    ])
    task = harness.task_def(
        "feature_up", task_id="fup-match", name="Feature Up",
        params={
            "from_files": [str(from_path)],
            "to_files": [str(to_path)],
            "key_from": "cell",
            "key_to": "cell",
            "features": [{"source": "area", "name": "area"}],
        },
    )
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fup-match", "feature_up", "Feature Up")
    harness.pump(300)
    try:
        ctx = window._ctx
        page = window.tabs.currentWidget()
        # The stored To stem is a hash; the display name is what matches.
        harness.check(
            not Path(str(to_path)).stem.startswith("raw_sample"),
            "the To parquet uses an opaque name",
        )
        from smage.data import naming as _data_naming

        harness.eq(
            _data_naming._comp_file_name(ctx, str(to_path), "fup-match"),
            "raw_sample.ome.tiff",
            "the To display name comes from its record",
        )
        harness.eq(
            _data_naming._comp_file_name(ctx, str(from_path), "fup-match"),
            "raw_sample.ome_Mask",
            "the From display name comes from its record",
        )
        harness.eq(page._fup_params.get("key_from"), "cell")
        harness.eq(page._fup_params.get("key_to"), "cell")
        harness.eq(
            page._fup_params.get("from_files"), [str(from_path)],
            "the From selection survives opening",
        )
        harness.eq(
            page._fup_params.get("to_files"), [str(to_path)],
            "the To selection survives opening",
        )
        harness.check(
            feature_up_page._fup_display_core(
                ctx, str(from_path), "fup-match"
            ).startswith(
                feature_up_page._fup_display_core(
                    ctx, str(to_path), "fup-match"
                )
            ),
            "the sample names share a prefix",
        )
        harness.eq(
            feature_up_page._fup_default_to(ctx, page, str(from_path)),
            str(to_path),
            "the display-name prefix pairs the files",
        )
        with harness.patch_dialogs(
            feature_up_page, information=QMessageBox.StandardButton.Ok
        ):
            feature_up_page._fup_match(ctx, page)
        harness.eq(
            page._fup_params.get("matches", {}).get(str(from_path)),
            str(to_path),
        )

        with harness.patch_dialogs(
            feature_up_page,
            information=QMessageBox.StandardButton.Ok,
            question=QMessageBox.StandardButton.Yes,
        ):
            feature_up_page._fup_calculate(ctx, page)
        _wait_calc(page)
        mapped = [
            line for line in page._fup_log_lines
            if "mapped.parquet" in line
        ]
        harness.check(mapped, "a per-file mapping line was logged")
        last = mapped[-1] if mapped else ""
        harness.check(
            "raw_sample.ome_mapped.parquet" in last,
            f"the log uses the display name: {last}",
        )
        harness.check(
            Path(str(to_path)).stem not in last,
            f"the internal file id is hidden: {last}",
        )
    finally:
        _close(window)


@harness.case("mod.feature_up.save", "L4", "feature_up", tags=("fast",))
def save_clears_dirty() -> None:
    """The Save button persists the settings and clears the dirty flag."""
    window, project, page, _to_path = _open("feature_up_save")
    try:
        ctx = window._ctx
        page._fup_params["key_from"] = "cell"
        feature_up_page._fup_mark_dirty(ctx, page)
        harness.check(page._fup_dirty, "the edit marks the module dirty")
        page._fup_save_btn.click()
        harness.pump(150)
        harness.check(not page._fup_dirty, "Save clears the dirty flag")
        from smage.project import Project

        saved = Project.load(project.path)
        task = saved.workflow["tasks"][0]["children"][0]
        harness.eq(task["params"]["key_from"], "cell")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
