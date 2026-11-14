"""L4: Unmix — signature table, unique names and saved configuration."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
from smage.data import columns as _data_columns
from smage.modules.unmix import page as _unmix_page
from smage.widgets.gate_editor.canvas import _GatePolygonROI


def _open(case_id: str):
    task = harness.task_def("unmix", task_id="u-1", name="Unmix")
    project = harness.new_project(case_id, rows=30, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("u-1", "unmix", "Unmix")
    harness.pump(400)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.unmix.signatures", "L4", "unmix", tags=("fast",))
def signatures() -> None:
    window, project, page = _open("unmix_signatures")
    try:
        ctx = window._ctx
        page._unmix_spectra.append({"id": "sp-1", "name": "Signature 1"})
        _unmix_page._unmix_rebuild_spectrum_list(ctx, page)

        _unmix_page._unmix_add_signature(ctx, page)
        harness.eq(len(page._unmix_matrix), 1)
        row = page._unmix_matrix[0]
        harness.eq(row["positive"], "sp-1")
        harness.eq(row["name"], "Signature 1")

        _unmix_page._unmix_add_signature(ctx, page)
        harness.eq(page._unmix_matrix[1]["name"], "Signature 1 2",
                   "signature names are made unique")

        _unmix_page._unmix_remove_signature(ctx, page, 0)
        harness.eq(len(page._unmix_matrix), 1)

        _unmix_page._unmix_save_now(ctx, page)
        task = window.project.workflow["tasks"][0]["children"][0]
        harness.eq(len(task["params"]["spectra"]), 1)
        harness.eq(len(task["params"]["matrix"]), 1)
    finally:
        _close(window)


@harness.case("mod.unmix.unique_name", "L4", "unmix", tags=("fast",))
def unique_name() -> None:
    window, _project, page = _open("unmix_unique")
    try:
        ctx = window._ctx
        page._unmix_matrix.append({"id": "s1", "name": "A"})
        harness.eq(_unmix_page._unmix_unique_signature_name(ctx, page, "A"), "A 2")
        page._unmix_matrix.append({"id": "s2", "name": "A 2"})
        harness.eq(_unmix_page._unmix_unique_signature_name(ctx, page, "A"), "A 3")
        harness.eq(_unmix_page._unmix_unique_signature_name(ctx, page, "B"), "B")
    finally:
        _close(window)


@harness.case("mod.unmix.flow_config", "L4", "unmix", tags=("fast",))
def flow_config() -> None:
    window, _project, page = _open("unmix_flow")
    try:
        ctx = window._ctx
        harness.eq(_unmix_page._unmix_flow_config(ctx, page._unmix_task_def), None,
                   "an empty configuration cannot unmix")
        harness.eq(_unmix_page._unmix_output_names(ctx, "g-1", "x"), [])
    finally:
        _close(window)


@harness.case("mod.unmix.duplicate_ellipse", "L4", "unmix", tags=("fast",))
def duplicate_ellipse() -> None:
    """A duplicated spectrum reloads its ellipse gate as an ellipse."""
    from smage.core.gates import _gate_mask
    from smage.modules.gating import page as _gating_page
    from smage.modules.gating.page import _gating_ellipse_points

    project = harness.new_project("unmix_duplicate_ellipse", rows=80)
    parquet = str(project.data_dir / "case.parquet")
    points = _gating_ellipse_points((10.0, 10.0), (20.0, 12.0))
    gate = {
        "id": "g1", "name": "Ellipse", "type": "ellipse",
        "x": "FL1-A", "y": "FL2-A", "parent": None,
        "pos": [10.0, 10.0], "size": [20.0, 12.0], "points": points,
    }
    task = harness.task_def(
        "unmix", task_id="u-1", name="Unmix",
        params={
            "spectra": [{
                "id": "sp-1", "name": "Signature 1",
                "tree_id": "tree-1", "gate_id": "g1",
            }],
            "trees": [{
                "id": "tree-1", "spectrum_id": "sp-1",
                "params": {
                    "gates": [gate],
                    "preview": {
                        "file": parquet, "type": "scatter",
                        "x": "FL1-A", "y": "FL2-A",
                    },
                },
            }],
            "matrix": [],
            "samples": [parquet],
        },
    )
    project.workflow["tasks"][0].setdefault("children", []).append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("u-1", "unmix", "Unmix")
    harness.pump(450)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        original = page._unmix_spectra[0]
        _unmix_page._unmix_copy_spectrum(ctx, page, original)
        harness.eq(len(page._unmix_spectra), 2)
        copied = page._unmix_spectra[1]
        tree = _unmix_page._unmix_tree_by_id(
            ctx, page, copied.get("tree_id")
        )
        harness.check(tree is not None)
        _unmix_page._unmix_activate_tree(ctx, page, tree)
        harness.pump(300)
        holder = tree["holder"]
        _gating_page._gating_load_rois(ctx, holder)
        harness.eq(len(holder._gating_gates), 1)
        harness.check(holder._gating_gates[0].get("points"),
                      "the mask outline is kept")

        roi = holder._gating_rois[0][0]
        harness.check(isinstance(roi, pg.EllipseROI),
                      "the duplicated ellipse is not a polygon")
        harness.check(not isinstance(roi, _GatePolygonROI))
        harness.check(
            np.allclose([roi.pos().x(), roi.pos().y()], [10.0, 10.0])
        )
        harness.check(
            np.allclose([roi.size().x(), roi.size().y()], [20.0, 12.0])
        )

        gate_copy = holder._gating_gates[0]
        inside = _gate_mask(
            np.array([20.0]), np.array([16.0]), gate_copy
        )
        outside = _gate_mask(
            np.array([31.0]), np.array([16.0]), gate_copy
        )
        harness.check(bool(inside[0]) and not bool(outside[0]),
                      "the ellipse mask still selects the drawn shape")
    finally:
        _close(window)


@harness.case("mod.unmix.values", "L4", "unmix", tags=("fast",))
def unmix_values() -> None:
    project = harness.new_project("unmix_values", rows=100)
    parquet = str(project.data_dir / "case.parquet")
    gates = [
        {"id": "neg", "name": "Negative", "type": "range", "x": "FL1-A",
         "y": None, "x1": 0.0, "x2": 50.0, "parent": None},
        {"id": "pos", "name": "Positive", "type": "range", "x": "FL1-A",
         "y": None, "x1": 50.0, "x2": 1e12, "parent": None},
    ]
    params = {
        "spectra": [
            {"id": "sp-neg", "name": "Negative", "tree_id": "tree-1",
             "gate_id": "neg"},
            {"id": "sp-pos", "name": "Positive", "tree_id": "tree-1",
             "gate_id": "pos"},
        ],
        "trees": [{
            "id": "tree-1",
            "params": {
                "gates": gates,
                "preview": {"file": parquet, "type": "scatter",
                            "x": "FL1-A", "y": "FL2-A"},
            },
        }],
        "spectrum_settings": {
            "params": ["FL1-A", "FL2-A"],
            "enabled": ["FL1-A", "FL2-A"],
            "stat": "median",
        },
        "matrix": [{"positive": "sp-pos", "negative": "sp-neg",
                    "normalize": False, "name": "Signal"}],
        "samples": [parquet],
        "method": "ols",
    }
    task = harness.task_def("unmix", task_id="u-1", name="Unmix",
                            params=params,
                            children=[harness.task_def("gating", task_id="g-1")])
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        ctx._unmix_flow_cache = {}
        config = _unmix_page._unmix_flow_config(ctx, window._find_task_by_id("u-1"))
        harness.check(config is not None, "a complete configuration is valid")
        harness.eq(config["names"], ["Signal"])
        harness.eq(config["columns"], ["FL1-A", "FL2-A"])
        harness.eq(config["matrix"].shape, (1, 2))

        raw = pd.read_parquet(parquet)
        negative = raw[(raw["FL1-A"] >= 0) & (raw["FL1-A"] <= 50)]
        positive = raw[raw["FL1-A"] > 50]
        expected = np.array([[
            positive["FL1-A"].median() - negative["FL1-A"].median(),
            positive["FL2-A"].median() - negative["FL2-A"].median(),
        ]])
        harness.check(np.allclose(config["matrix"], expected),
                      f"{config['matrix']} vs {expected}")

        harness.eq(_unmix_page._unmix_output_names(ctx, "g-1", parquet), ["Signal"])
        values = _data_columns._column_data(ctx, "g-1", parquet, ["Signal"])["Signal"]
        weights = np.linalg.pinv(config["matrix"]).T
        expected_values = (
            raw[["FL1-A", "FL2-A"]].to_numpy(dtype=float) @ weights[0]
        )
        harness.check(np.allclose(values, expected_values))
    finally:
        harness.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
