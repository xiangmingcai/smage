"""L6: end-to-end pipelines across several modules."""
from __future__ import annotations

import json
import queue
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.app import workflow_tree  # noqa: E402
from smage.modules.feature import page as feature_page  # noqa: E402
from smage.modules.scale import page as scale_page  # noqa: E402
from smage.data import columns as _data_columns
from smage.data import feature_outputs as _data_feature_outputs
from smage.data import files as _data_files
from smage.data import sampling as _data_sampling
from smage.data import stores as _data_stores
from smage.modules.export import page as _export_page
from smage.modules.feature import page as feature_page
from smage.modules.gate2col import page as _gate2col_page
from smage.modules.heatmap import page as _heatmap_page
from smage.modules.scale import page as scale_page
from smage.modules.segmentation import page as _segmentation_page


def _close_window(window) -> None:
    harness.close_window(window)


def _drain(q: queue.Queue) -> list:
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except queue.Empty:
            return messages


@harness.case("e2e.gating_gate2col_export", "L6", "e2e", tags=("fast", "e2e"))
def gating_gate2col_export() -> None:
    project = harness.new_project("e2e_g2c", rows=80)
    parquet = str(project.data_dir / "case.parquet")

    folder = project.path / "Module" / "g-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gates.json").write_text(json.dumps({
        "files": [parquet],
        "gates": [{"id": "in", "name": "In", "type": "range",
                   "x": "FL1-A", "y": None, "x1": 0.0, "x2": 50.0,
                   "parent": None}],
        "per_file": {},
    }), encoding="utf-8")

    export = harness.task_def("export", task_id="e-1", name="Export")
    g2c = harness.task_def(
        "gate2col", task_id="g2c-1", name="Gate2Col",
        params={"files": [parquet],
                "rules": [{"gate_task": "g-1", "gate_id": "in",
                           "name": "in_gate"}]},
        children=[export],
    )
    g2c["output"] = {"saved": False}
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[g2c])
    project.workflow["tasks"][0]["children"].append(gating)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        window._open_module("g2c-1", "gate2col", "Gate2Col")
        harness.pump(350)
        page = window.tabs.currentWidget()
        _gate2col_page._g2c_calculate(ctx, page)
        window._close_module_page(page)
        harness.pump(80)

        out_dir = harness.RUN_DIR / "cases" / "e2e_g2c" / "out"
        window._open_module("e-1", "export", "Export")
        harness.pump(300)
        export_page = window.tabs.currentWidget()
        q = queue.Queue()
        _export_page._export_job_table(ctx, export_page, q, {
            "files": [parquet],
            "columns": ["x", "in_gate"],
            "format": "csv",
            "dir": str(out_dir),
        })
        messages = _drain(q)
        harness.check(
            any(message[0] == "done" for message in messages),
            str(messages),
        )
        frame = pd.read_csv(out_dir / "case.csv")
        harness.eq(len(frame), 80)
        raw = pd.read_parquet(parquet)
        expected = ((raw["FL1-A"] >= 0) & (raw["FL1-A"] <= 50)).astype(int)
        harness.eq(frame["in_gate"].tolist(), expected.tolist())
    finally:
        _close_window(window)


@harness.case("e2e.feature_export", "L6", "e2e", tags=("fast", "e2e"))
def feature_export() -> None:
    project = harness.new_project("e2e_feature", rows=30)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame["v"] = np.arange(len(frame), dtype="float64")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    export = harness.task_def("export", task_id="e-1", name="Export")
    feature = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "group",
            "files": [str(parquet)],
            "rules": [{"method": "count", "name": "n"},
                      {"source": "v", "method": "mean", "name": "mean_v"}],
            "ignore_values": [],
        },
        children=[export],
    )
    feature["output"] = {"saved": False}
    project.workflow["tasks"][0]["children"].append(feature)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        window._open_module("f-1", "feature", "Feature")
        harness.pump(350)
        page = window.tabs.currentWidget()
        feature_page._feat_calculate(ctx, page)
        record = page._feat_params["outputs"][str(parquet)]
        feature_path = str(project.path / record["path"])
        window._close_module_page(page)
        harness.pump(80)

        out_dir = harness.RUN_DIR / "cases" / "e2e_feature" / "out"
        window._open_module("e-1", "export", "Export")
        harness.pump(300)
        export_page = window.tabs.currentWidget()
        q = queue.Queue()
        _export_page._export_job_table(ctx, export_page, q, {
            "files": [feature_path],
            "columns": ["group", "n", "mean_v"],
            "format": "csv",
            "dir": str(out_dir),
        })
        messages = _drain(q)
        harness.check(
            any(message[0] == "done" for message in messages),
            str(messages),
        )
        output = list(out_dir.glob("*.csv"))[0]
        exported = pd.read_csv(output)
        harness.eq(sorted(exported["group"].tolist()), [1, 2, 3])
        harness.eq(int(exported["n"].sum()), 30)
    finally:
        _close_window(window)


@harness.case("e2e.lock_draft", "L6", "e2e", tags=("fast", "e2e"))
def lock_draft() -> None:
    project = harness.new_project("e2e_draft", rows=40)
    parquet = str(project.data_dir / "case.parquet")

    generate = harness.task_def("segmentation", task_id="seg-1",
                                name="Segmentation")
    generate["output"] = {"saved": True}
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "FL1-A", "method": "linear",
                             "param": None, "min": None, "max": None}],
                "files": [parquet]},
        children=[generate],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        task = window._find_task_by_id("s-1")
        harness.check(window._task_locked(task), "saved output locks Scale")

        window._open_module("s-1", "scale", "Scale")
        harness.pump(350)
        page = window.tabs.currentWidget()
        harness.check("s-1" in window._draft_task_ids,
                      "a locked module opens as a draft")
        harness.check(
            (project.path / "Module" / "s-1" / "draft").is_dir(),
            "the draft folder exists",
        )

        page._scaling_apply_vars = ["FL1-A"]
        page._scaling_apply_method.setCurrentIndex(
            page._scaling_apply_method.findData("log10")
        )
        scale_page._scaling_apply_batch(ctx, page)
        harness.check(page._scaling_dirty)

        with harness.patch_dialogs(scale_page,
                                   question=QMessageBox.StandardButton.Yes):
            scale_page._scaling_close(ctx, page)
        harness.pump(120)

        harness.check(not (project.path / "Module" / "s-1" / "draft").exists(),
                      "closing the draft removes its folder")

        from smage.project import Project

        saved = Project.load(project.path)
        official = saved.workflow["tasks"][0]["children"][0]
        harness.eq(official["params"]["columns"][0]["method"], "linear")
    finally:
        _close_window(window)


@harness.case("e2e.subset_export", "L6", "e2e", tags=("fast", "e2e"))
def subset_export() -> None:
    project = harness.new_project("e2e_subset", rows=40)
    parquet_a = str(project.data_dir / "case.parquet")
    frame_b = pd.DataFrame({
        "x": np.arange(30), "y": np.arange(30),
        "FL1-A": np.arange(30, dtype="float64"),
    })
    e2e.add_parquet(project, "second", frame_b)

    export = harness.task_def("export", task_id="e-1", name="Export")
    subset = harness.task_def(
        "subset", task_id="sub-1", name="Subset",
        params={"files": [parquet_a], "columns": ["x", "FL1-A"]},
        children=[export],
    )
    project.workflow["tasks"][0]["children"].append(subset)
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        available = _data_files._available_files(ctx, "e-1")
        harness.eq([rec["parquet"] for rec in available], [parquet_a])
        harness.eq(_data_columns._columns_for(ctx, parquet_a, "e-1"), ["x", "FL1-A"])

        out_dir = harness.RUN_DIR / "cases" / "e2e_subset" / "out"
        e2e.export_table(window, "e-1", [parquet_a], ["x", "FL1-A"], out_dir)
        frame = pd.read_csv(out_dir / "case.csv")
        harness.eq(list(frame.columns), ["x", "FL1-A"])
        harness.eq(len(frame), 40)
        harness.check(not (out_dir / "second.csv").exists(),
                      "the hidden file is not exported")
    finally:
        e2e.close_window(window)


@harness.case("e2e.subsample_heatmap", "L6", "e2e", tags=("fast", "e2e"))
def subsample_heatmap() -> None:
    project = harness.new_project("e2e_subsample", rows=100)
    parquet = str(project.data_dir / "case.parquet")
    e2e.write_gates(project, "g-1", parquet, [{
        "id": "in", "name": "In", "type": "range", "x": "FL1-A", "y": None,
        "x1": 0.0, "x2": 1e12, "parent": None,
    }])

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
    subsample = harness.task_def(
        "subsample", task_id="sub-1", name="Subsample",
        params={"files": [parquet], "basis": "per_file", "unit": "points",
                "count": 10, "allocation": "uniform", "overrides": {}},
        children=[heatmap],
    )
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[subsample])
    project.workflow["tasks"][0]["children"].append(gating)
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "h-1", "heatmap", "Heatmap")
        _heatmap_page._heatmap_calculate(ctx, page)
        harness.wait_for(lambda: not page._heatmap_running, timeout=30,
                         message="sampled heatmap")
        payload = json.loads(
            (project.path / "Module" / "h-1" / "heatmap.json").read_text(
                encoding="utf-8"
            )
        )
        harness.eq(payload["used_rows"][parquet], 10)
        harness.eq(payload["row_counts"][parquet], 10,
                   "upstream sampling is what the heatmap sees")
        harness.eq(_data_columns._data_row_count(ctx, parquet), 100,
                   "the full file still has 100 rows")

        raw = pd.read_parquet(parquet)
        index = _data_sampling._subsample_row_index(ctx, "h-1", parquet)
        sampled = raw.iloc[index]
        selected = sampled[(sampled["FL1-A"] >= 0) & (sampled["FL1-A"] <= 1e12)]
        harness.eq(len(selected), 10, "the gate keeps every sampled row")
        expected = float(selected["x"].mean())
        harness.check(abs(float(payload["values"][0][0]) - expected) < 1e-9,
                      f"{payload['values'][0][0]} vs {expected}")
        harness.eq(payload["counts"]["g-1|in"][parquet], len(selected))
    finally:
        e2e.close_window(window)


@harness.case("e2e.rename_feature_export", "L6", "e2e", tags=("fast", "e2e"))
def rename_feature_export() -> None:
    project = harness.new_project("e2e_rename", rows=40)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    export = harness.task_def("export", task_id="e-1", name="Export")
    feature = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "Cluster",
            "files": [str(parquet)],
            "rules": [{"method": "count", "name": "n"},
                      {"source": "x", "method": "mean", "name": "mean_x"}],
            "ignore_values": [],
        },
        children=[export],
    )
    feature["output"] = {"saved": False}
    rename = harness.task_def(
        "rename", task_id="r-1", name="Rename",
        params={"mode": "column",
                "column": {"files": [str(parquet)],
                           "rename": {"group": "Cluster"}}},
        children=[feature],
    )
    project.workflow["tasks"][0]["children"].append(rename)
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        harness.check("Cluster" in _data_columns._columns_for(ctx, str(parquet), "f-1"))
        page = e2e.open_page(window, "f-1", "feature", "Feature")
        feature_page._feat_calculate(ctx, page)
        record = page._feat_params["outputs"][str(parquet)]
        harness.eq(record["columns"], ["Cluster", "n", "mean_x"])
        output = project.path / record["path"]
        aggregated = pd.read_parquet(output)
        harness.eq(sorted(aggregated["Cluster"].tolist()), [1, 2, 3])
        harness.eq(int(aggregated["n"].sum()), 40)

        out_dir = harness.RUN_DIR / "cases" / "e2e_rename" / "out"
        e2e.export_table(window, "e-1", [str(output)],
                         ["Cluster", "n", "mean_x"], out_dir)
        exported = list(out_dir.glob("*.csv"))[0]
        harness.eq(len(pd.read_csv(exported)), 3)
    finally:
        e2e.close_window(window)


@harness.case("e2e.map_pipeline_feature", "L6", "e2e", tags=("fast", "e2e"))
def map_pipeline_feature() -> None:
    project = harness.new_project("e2e_map", rows=60)
    parquet = str(project.data_dir / "case.parquet")
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    feature = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "group",
            "files": [parquet],
            "rules": [{"method": "count", "name": "n"},
                      {"source": "FL1-A", "method": "mean", "name": "mean_a"}],
            "ignore_values": [],
        },
    )
    feature["output"] = {"saved": False}
    illumination = harness.task_def(
        "illumination", task_id="i-1", name="Illumination",
        params={"submodules": [{
            "id": "i1", "channel": "FL1-A", "x": "x", "y": "y",
            "files": [parquet], "tile_w": 2, "tile_h": 2,
            "mask": [[0.5, 0.25], [0.5, 0.5]], "method": "median",
        }]},
        children=[feature],
    )
    compensation = harness.task_def(
        "compensation", task_id="c-1", name="Compensation",
        params={"params": ["FL1-A", "FL2-A"],
                "matrix": [[100, 10], [0, 100]],
                "files": [parquet]},
        children=[illumination],
    )
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [
            {"name": "FL1-A", "method": "log10"},
            {"name": "FL2-A", "method": "log10"},
        ], "files": [parquet]},
        children=[compensation],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        harness.eq(
            [kind for kind, _stage in _data_columns._map_stages(ctx, "f-1")],
            ["scale", "compensation", "illumination"],
        )
        page = e2e.open_page(window, "f-1", "feature", "Feature")
        feature_page._feat_calculate(ctx, page)
        record = page._feat_params["outputs"][parquet]
        output = pd.read_parquet(project.path / record["path"]).sort_values("group")

        raw = pd.read_parquet(parquet)
        a = np.log10(raw["FL1-A"].to_numpy(dtype=float))
        b = np.log10(raw["FL2-A"].to_numpy(dtype=float))
        combined = a + 0.1 * b
        mask = np.array([[0.5, 0.25], [0.5, 0.5]])
        xi = raw["x"].to_numpy(dtype=int) % 2
        yi = raw["y"].to_numpy(dtype=int) % 2
        factor = np.where(mask[yi, xi] > 0, 0.5 / mask[yi, xi], 1.0)
        final = combined * factor
        expected = (
            pd.DataFrame({"group": raw["group"], "v": final})
            .groupby("group")["v"].mean().sort_index()
        )
        harness.check(
            np.allclose(output["mean_a"].to_numpy(), expected.to_numpy()),
            f"{output['mean_a'].tolist()} vs {expected.tolist()}",
        )
    finally:
        e2e.close_window(window)


@harness.case("e2e.image_seg_feature_export", "L6", "e2e",
              tags=("fast", "e2e"))
def image_seg_feature_export() -> None:
    feature = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "Mask",
            "ignore_values": [0],
            "rules": [{"method": "count", "name": "n"},
                      {"source": "Channel 1", "method": "mean",
                       "name": "mean_intensity"}],
        },
        children=[harness.task_def("export", task_id="e-1", name="Export")],
    )
    feature["output"] = {"saved": False}
    segmentation = harness.task_def("segmentation", task_id="seg-1",
                                    name="Segmentation", children=[feature])
    segmentation["output"] = {"saved": False}

    project, parquet = e2e.image_project("e2e_image", tasks=[segmentation])
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        mask, error = _segmentation_page._seg_nuclear_expansion(ctx, parquet, {
            "channel": "Channel 1", "threshold_mode": "manual",
            "threshold": 100.0, "min_area": 4, "expand": 0,
        })
        harness.eq(error, None)
        rel = _segmentation_page._seg_write_mask(ctx, "seg-1", parquet, mask)
        mask_path = project.path / rel

        task = window._find_task_by_id("seg-1")
        params = task.setdefault("params", {})
        params["masks"] = {parquet: {"path": rel}}
        params["applied"] = {parquet: _segmentation_page._seg_signature(ctx, str(mask_path))}
        params["column"] = "Mask"

        feature_task = window._find_task_by_id("f-1")
        feature_task.setdefault("params", {})["files"] = [parquet]
        window.project.save()

        page = e2e.open_page(window, "f-1", "feature", "Feature")
        with harness.patch_dialogs(feature_page,
                                   warning=QMessageBox.StandardButton.Ok,
                                   information=QMessageBox.StandardButton.Ok):
            feature_page._feat_calculate(ctx, page)
        harness.check(
            parquet in (page._feat_params.get("outputs") or {}),
            "feature validation/calculation produced no output",
        )
        record = page._feat_params["outputs"][parquet]
        harness.eq(record["columns"], ["Mask", "n", "mean_intensity"])
        aggregated = pd.read_parquet(project.path / record["path"])
        harness.eq(sorted(aggregated["Mask"].tolist()), [1, 2])
        harness.eq(int(aggregated["n"].sum()), 50,
                   "both 5x5 nuclei are counted")

        out_dir = harness.RUN_DIR / "cases" / "e2e_image" / "out"
        e2e.export_table(window, "e-1",
                         [str(project.path / record["path"])],
                         ["Mask", "n", "mean_intensity"], out_dir)
        exported = list(out_dir.glob("*.csv"))[0]
        harness.eq(len(pd.read_csv(exported)), 2)
    finally:
        e2e.close_window(window)


@harness.case("e2e.mixed_import_export", "L6", "e2e", tags=("fast", "e2e"))
def mixed_import_export() -> None:
    project = harness.new_project("e2e_mixed", rows=1)
    project.workflow["tasks"][0]["params"]["processed"] = []
    project.save_columns({})
    project.save()

    fcs, fcs_result = e2e.import_into_project(
        project, "fcs", harness.FIXTURES_DIR / "fcs" / "synthetic_8ch.fcs"
    )
    csv_path, csv_result = e2e.import_into_project(
        project, "csv", harness.FIXTURES_DIR / "tables" / "base.csv"
    )
    image, image_result = e2e.import_into_project(
        project, "image", harness.FIXTURES_DIR / "images" / "multichannel.tif"
    )
    export = harness.task_def("export", task_id="e-1", name="Export")
    project.workflow["tasks"][0]["children"].append(export)
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        harness.eq(len(_data_files._available_files(ctx, "e-1")), 3)
        out_dir = harness.RUN_DIR / "cases" / "e2e_mixed" / "out"

        e2e.export_table(window, "e-1", [fcs],
                         fcs_result["columns"][:2], out_dir)
        e2e.export_table(window, "e-1", [csv_path], ["x", "y"], out_dir)
        e2e.export_table(window, "e-1", [image], ["x", "y"], out_dir)

        outputs = {path.name: path for path in out_dir.glob("*.csv")}
        harness.eq(len(outputs), 3, f"outputs: {sorted(outputs)}")
        harness.eq(len(pd.read_csv(outputs["synthetic_8ch.csv"])), 2000)
        harness.eq(len(pd.read_csv(outputs["base.csv"])), 5000)
        harness.eq(len(pd.read_csv(outputs["multichannel.csv"])), image_result["rows"])
    finally:
        e2e.close_window(window)


@harness.case("e2e.project_roundtrip", "L6", "e2e", tags=("fast", "e2e"))
def project_roundtrip() -> None:
    project = harness.new_project("e2e_roundtrip", rows=40)
    parquet = str(project.data_dir / "case.parquet")
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)
    e2e.write_gates(project, "g-1", parquet, [{
        "id": "in", "name": "In", "type": "range", "x": "FL1-A", "y": None,
        "x1": 0.0, "x2": 50.0, "parent": None,
    }])

    export = harness.task_def("export", task_id="e-1", name="Export")
    feature = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "group", "files": [parquet],
            "rules": [{"method": "count", "name": "n"},
                      {"source": "FL1-A", "method": "mean", "name": "mean_a"}],
            "ignore_values": [],
        },
        children=[export],
    )
    feature["output"] = {"saved": False}
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[feature])
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "FL1-A", "method": "log10"}],
                "files": [parquet]},
        children=[gating],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "f-1", "feature", "Feature")
        feature_page._feat_calculate(ctx, page)
        record = page._feat_params["outputs"][parquet]
        feature_path = str(project.path / record["path"])
    finally:
        e2e.close_window(window)

    from smage.project import Project

    reopened = e2e.open_window(Project.load(project.path))
    try:
        ctx = reopened._ctx
        harness.eq(
            [kind for kind, _stage in _data_columns._map_stages(ctx, "e-1")],
            ["scale"],
        )
        gating_task = reopened._find_task_by_id("g-1")
        gates = _data_stores._gating_gates_for(ctx, gating_task, parquet)
        harness.eq([gate["id"] for gate in gates], ["in"])

        outputs = _data_feature_outputs._feature_outputs(ctx, "e-1")
        harness.eq(len(outputs), 1)
        harness.eq(outputs[0]["parquet"], feature_path)
        available = _data_files._available_files(ctx, "e-1")
        harness.check(
            any(rec["parquet"] == feature_path for rec in available),
            "the feature output survives the reopen",
        )
        values = _data_columns._column_data(ctx, "e-1", feature_path, ["n"])
        harness.eq(len(values["n"]), 3)
    finally:
        e2e.close_window(reopened)


@harness.case("e2e.lock_cascade", "L6", "e2e", tags=("fast", "e2e"))
def lock_cascade() -> None:
    project = harness.new_project("e2e_lock", rows=20)
    generate = harness.task_def("segmentation", task_id="seg-1",
                                name="Segmentation")
    generate["output"] = {"saved": True}
    rename = harness.task_def("rename", task_id="r-1", name="Rename",
                              children=[generate])
    scale = harness.task_def("scale", task_id="s-1", name="Scale",
                             children=[rename])
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = e2e.open_window(project)
    try:
        scale_task = window._find_task_by_id("s-1")
        rename_task = window._find_task_by_id("r-1")
        generate_task = window._find_task_by_id("seg-1")
        harness.check(window._task_locked(scale_task), "Scale is locked")
        harness.check(window._task_locked(rename_task), "Rename is locked")
        harness.check(not window._task_locked(generate_task),
                      "the generate module is not locked by its own output")

        with harness.patch_dialogs(
            workflow_tree, question=QMessageBox.StandardButton.Yes
        ):
            window._delete_task(generate_task, rename_task, True)
        harness.pump(100)
        harness.check(not window._task_locked(scale_task),
                      "deleting the generate module releases the lock")
        harness.check(not window._task_locked(rename_task))

        regenerated = harness.task_def("segmentation", task_id="seg-2",
                                       name="Segmentation")
        regenerated["output"] = {"saved": True}
        rename_task["children"].append(regenerated)
        window.project.save()
        harness.check(window._task_locked(scale_task), "the lock is re-applied")

        window._session_unlocked.add("s-1")
        harness.check(not window._task_locked(scale_task),
                      "a session unlock releases only this session")
        window._session_unlocked.discard("s-1")
        harness.check(window._task_locked(scale_task))
    finally:
        e2e.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
