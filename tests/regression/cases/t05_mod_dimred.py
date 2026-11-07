"""L4: Dimension Reduction — validation, preparation and embedding output."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


def _open(case_id: str):
    task = harness.task_def(
        "dimred", task_id="dr-1", name="Dimension Reduction",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=60, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("dr-1", "dimred", "Dimension Reduction")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.dimred.validate", "L4", "dimred", tags=("fast",))
def validate() -> None:
    window, _project, page, _parquet = _open("dimred_validate")
    try:
        ctx = window._ctx
        ok, message = ctx._dimred_validate(page)
        harness.check(not ok and "data file" in message, message)

        page._dimred_params["files"] = [_parquet]
        ok, message = ctx._dimred_validate(page)
        harness.check("variable" in message, message)

        page._dimred_params["columns"] = ["FL1-A"]
        page._dimred_params["names"] = ["a", "a"]
        ok, message = ctx._dimred_validate(page)
        harness.check("differ" in message, message)

        page._dimred_params["names"] = ["x", "y"]
        ok, message = ctx._dimred_validate(page)
        harness.check("collides" in message, message)
    finally:
        _close(window)


@harness.case("mod.dimred.prepare", "L4", "dimred", tags=("fast",))
def prepare() -> None:
    window, _project, _page, _parquet = _open("dimred_prepare")
    try:
        ctx = window._ctx
        matrix = np.array([[1.0, np.nan], [3.0, 4.0]])
        filled = ctx._dimred_prepare(matrix, False, False)
        harness.check(np.allclose(filled, [[1.0, 4.0], [3.0, 4.0]]),
                      "NaN is filled with the column mean")

        normalized = ctx._dimred_prepare(matrix, True, False)
        harness.check(np.allclose(normalized.mean(axis=0), 0.0, atol=1e-9))
        harness.check(np.allclose(normalized[:, 0].std(), 1.0))
        harness.check(np.allclose(normalized[:, 1], 0.0),
                      "a constant column normalizes to zero")
    finally:
        _close(window)


@harness.case("mod.dimred.write", "L4", "dimred", tags=("fast",))
def write_output() -> None:
    window, project, page, parquet = _open("dimred_write")
    try:
        ctx = window._ctx
        page._dimred_params["names"] = ["d1", "d2"]
        coords = np.column_stack([np.arange(60), np.arange(60) * 2]).astype(float)
        rel, columns, kind = ctx._dimred_write_file(page, parquet, coords)
        harness.eq(columns, ["d1", "d2"])
        harness.eq(kind, "table")
        output = project.path / rel
        harness.check(output.is_file())
        import pandas as pd

        frame = pd.read_parquet(output)
        harness.check(np.allclose(frame["d1"].to_numpy(), coords[:, 0]))
        harness.check(np.allclose(frame["d2"].to_numpy(), coords[:, 1]))
    finally:
        _close(window)


@harness.case("mod.dimred.umap", "L4", "dimred", tags=("slow",))
def umap_run() -> None:
    window, project, page, parquet = _open("dimred_umap")
    try:
        ctx = window._ctx
        page._dimred_params.update({
            "files": [parquet],
            "columns": ["FL1-A", "FL2-A", "FL3-A"],
            "names": ["umap_1", "umap_2"],
            "method": "umap",
            "max_events": 60,
            "umap": {"n_neighbors": 5, "min_dist": 0.1},
            "normalize": False,
            "pca": False,
        })
        ctx._dimred_calculate(page)
        harness.check(page._dimred_running, "the calculation starts")
        harness.wait_for(
            lambda: not page._dimred_running, timeout=120,
            message="UMAP to finish",
        )
        outputs = page._dimred_params.get("outputs") or {}
        harness.check(parquet in outputs, f"outputs: {outputs}")
        output = project.path / outputs[parquet]["path"]
        harness.check(output.is_file())
    finally:
        _close(window)


@harness.case("mod.dimred.tsne", "L4", "dimred", tags=("slow",))
def tsne_fit() -> None:
    window, _project, _page, _parquet = _open("dimred_tsne")
    try:
        ctx = window._ctx
        rng = np.random.default_rng(42)
        matrix = rng.normal(size=(60, 3))
        coords = ctx._dimred_fit(
            "tsne", {"perplexity": 5, "n_iter": 250}, matrix, 42
        )
        harness.eq(coords.shape, (60, 2))
        harness.check(np.isfinite(coords).all())
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
