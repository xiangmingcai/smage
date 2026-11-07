"""L4: Cluster — validation, k-means labels, output column and extension guard."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.cluster import page as cluster_page  # noqa: E402
from smage.modules.cluster import page as cluster_page


def _open(case_id: str):
    task = harness.task_def(
        "cluster", task_id="cl-1", name="Cluster",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=60, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("cl-1", "cluster", "Cluster")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.cluster.fit", "L4", "cluster", tags=("fast",))
def kmeans_fit() -> None:
    window, _project, _page, _parquet = _open("cluster_fit")
    try:
        ctx = window._ctx
        matrix = np.array([
            [0.0, 0.0], [0.1, 0.2], [10.0, 10.0], [10.2, 9.8],
            [0.2, -0.1], [9.9, 10.1],
        ])
        labels = cluster_page._cluster_fit(ctx, "kmeans", {"k": 2}, matrix, 42)
        harness.eq(labels.dtype, np.dtype("int64"))
        harness.eq(sorted(set(labels.tolist())), [1, 2])
        harness.check(
            labels[0] == labels[1] == labels[4],
            "the first cluster is consistent",
        )
        harness.check(
            labels[2] == labels[3] == labels[5],
            "the second cluster is consistent",
        )
        again = cluster_page._cluster_fit(ctx, "kmeans", {"k": 2}, matrix, 42)
        harness.check(np.array_equal(labels, again), "k-means is reproducible")
    finally:
        _close(window)


@harness.case("mod.cluster.write", "L4", "cluster", tags=("fast",))
def write_labels() -> None:
    window, project, page, parquet = _open("cluster_write")
    try:
        ctx = window._ctx
        page._cluster_params["name"] = "cluster"
        labels = np.array([1, 1, 2, 2, 3, 3] * 10, dtype="int64")
        rel, columns, kind = cluster_page._cluster_write_file(ctx, page, parquet, labels)
        harness.eq(columns, ["cluster"])
        harness.eq(kind, "table")
        output = project.path / rel
        harness.check(output.is_file())
        frame = pd.read_parquet(output)
        harness.eq(frame["cluster"].tolist(), labels.tolist())
    finally:
        _close(window)


@harness.case("mod.cluster.calculate", "L4", "cluster", tags=("fast",))
def calculate() -> None:
    window, project, page, parquet = _open("cluster_calc")
    try:
        ctx = window._ctx
        page._cluster_params.update({
            "files": [parquet],
            "columns": ["FL1-A", "FL2-A", "FL3-A"],
            "name": "cluster",
            "method": "kmeans",
            "max_events": 60,
            "kmeans": {"k": 3},
            "normalize": False,
        })
        cluster_page._cluster_calculate(ctx, page)
        harness.check(page._cluster_running, "the calculation starts")
        harness.wait_for(
            lambda: not page._cluster_running, timeout=60,
            message="k-means to finish",
        )
        outputs = page._cluster_params.get("outputs") or {}
        harness.check(parquet in outputs, f"outputs: {outputs}")
        output = project.path / outputs[parquet]["path"]
        harness.check(output.is_file())
        frame = pd.read_parquet(output)
        harness.eq(sorted(set(frame["cluster"].tolist())), [1, 2, 3])
    finally:
        _close(window)


@harness.case("mod.cluster.validation", "L4", "cluster", tags=("fast",))
def validation() -> None:
    window, _project, page, parquet = _open("cluster_validation")
    try:
        ctx = window._ctx
        ok, message = cluster_page._cluster_validate(ctx, page)
        harness.check(not ok and "data file" in message, message)

        page._cluster_params["files"] = [parquet]
        page._cluster_params["columns"] = ["x"]
        page._cluster_params["name"] = "x"
        ok, message = cluster_page._cluster_validate(ctx, page)
        harness.check("collides" in message, message)

        if not ctx._ext_ready("flowsom"):
            page._cluster_params.update({
                "files": [parquet], "columns": ["FL1-A"],
                "name": "flow_cluster", "method": "flowsom",
            })
            with harness.patch_dialogs(
                cluster_page, information=QMessageBox.StandardButton.Ok
            ):
                cluster_page._cluster_calculate(ctx, page)
            harness.check(not page._cluster_running,
                          "FlowSOM cannot start without the extension")
    finally:
        _close(window)


@harness.case("mod.cluster.dbscan", "L4", "cluster", tags=("fast",))
def dbscan_fit() -> None:
    window, _project, _page, _parquet = _open("cluster_dbscan")
    try:
        ctx = window._ctx
        matrix = np.array([
            [0.0, 0.0], [0.1, 0.1], [0.2, -0.1], [0.5, 0.5],
            [10.0, 10.0], [10.1, 10.2], [9.9, 9.8], [10.3, 10.0],
        ])
        labels = cluster_page._cluster_fit(ctx,
            "dbscan", {"eps": 1.0, "min_samples": 2}, matrix, 42
        )
        harness.eq(sorted(set(labels.tolist())), [1, 2])
        harness.check(labels[0] == labels[1] == labels[2] == labels[3])
        harness.check(labels[4] == labels[5] == labels[6] == labels[7])
    finally:
        _close(window)


@harness.case("mod.cluster.graph", "L4", "cluster", tags=("slow",))
def graph_clustering() -> None:
    window, _project, _page, _parquet = _open("cluster_graph")
    try:
        ctx = window._ctx
        rng = np.random.default_rng(42)
        first = rng.normal(0.0, 0.1, size=(25, 3))
        second = rng.normal(10.0, 0.1, size=(25, 3))
        matrix = np.vstack([first, second])

        leiden = cluster_page._cluster_fit(ctx,
            "leiden", {"k": 5, "resolution": 1.0}, matrix, 42
        )
        harness.eq(leiden.dtype, np.dtype("int64"))
        harness.check(leiden.min() >= 1)
        harness.check(len(set(leiden.tolist())) >= 2)

        phenograph = cluster_page._cluster_fit(ctx, "phenograph", {"k": 5}, matrix, 42)
        harness.eq(phenograph.dtype, np.dtype("int64"))
        harness.check(phenograph.min() >= 1)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
