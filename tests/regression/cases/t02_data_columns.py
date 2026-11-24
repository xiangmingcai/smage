"""L2: column lookup, map stages and value reads through the workflow."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
from smage.data import columns as _data_columns
from smage.data import pool as _data_pool


def _open(case_id: str, tasks=(), rows: int = 60):
    project = harness.new_project(case_id, rows=rows, tasks=tasks)
    window = harness.new_window()
    harness.open_project(window, project)
    return window, project


def _close(window) -> None:
    harness.close_window(window)


def _chain_tasks(parquet: str):
    rename = harness.task_def(
        "rename", task_id="r-1", name="Rename",
        params={
            "mode": "column",
            "column": {"files": [parquet], "rename": {"FL2-A": "NewFL2"}},
        },
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "FL1-A", "method": "log10"}], "files": None},
        children=[rename],
    )
    return [scale]


@harness.case("data.columns.lookup", "L2", "data", tags=("smoke",))
def columns_lookup() -> None:
    project = harness.new_project("data_columns", rows=40)
    parquet = str(project.data_dir / "case.parquet")
    tasks = _chain_tasks(parquet)
    for task in tasks:
        project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        base = _data_columns._columns_for(ctx, parquet)
        harness.check("FL2-A" in base and "NewFL2" not in base)
        harness.eq(_data_columns._columns_for(ctx, None), [])

        renamed = _data_columns._columns_for(ctx, parquet, "g-1")
        harness.check("NewFL2" in renamed, f"downstream sees the rename: {renamed}")
        harness.check("FL2-A" not in renamed)

        unchanged = _data_columns._columns_for(ctx, parquet, "s-1")
        harness.check("FL2-A" in unchanged, "a rename below the reader is not applied")

        harness.eq(_data_columns._data_row_count(ctx, parquet), 40)
        harness.eq(_data_columns._data_row_count(ctx, str(project.data_dir / "missing.parquet")), 0)
    finally:
        _close(window)


@harness.case("data.columns.sample_rows", "L2", "data", tags=("fast",))
def sampled_rows() -> None:
    """sample_indices reduces source rows before stages are evaluated."""
    project = harness.new_project("data_columns_sample", rows=20)
    parquet = str(project.data_dir / "case.parquet")
    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        full = _data_columns._column_data(
            ctx, None, parquet, ["FL1-A"]
        )["FL1-A"]
        picked = np.array([0, 7, 19], dtype=np.int64)
        sampled = _data_columns._column_data(
            ctx, None, parquet, ["FL1-A"], sample_indices=picked
        )["FL1-A"]
        harness.check(np.array_equal(sampled, full[picked]))
        harness.check(
            not ctx._stage_cache,
            "sampled reads do not populate the full stage cache",
        )
    finally:
        _close(window)


@harness.case("data.pool.tiers", "L2", "data", tags=("fast",))
def pool_tiers() -> None:
    """Pool size uses 20x the draw limit, capped at 300k."""
    project = harness.new_project("data_pool_tiers", rows=1)
    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        harness.eq(_data_pool._pool_tier(ctx, 1000), 20_000)
        harness.eq(_data_pool._pool_tier(ctx, 10_000), 200_000)
        harness.eq(_data_pool._pool_tier(ctx, 50_000), 300_000)
        harness.eq(_data_pool._pool_tier(ctx, 500_000), 500_000)
        harness.eq(_data_pool._pool_stats_tier(ctx), 300_000)
    finally:
        _close(window)


@harness.case("data.columns.subset", "L2", "data", tags=("smoke",))
def columns_subset() -> None:
    project = harness.new_project("data_columns_subset", rows=10)
    parquet = str(project.data_dir / "case.parquet")
    subset = harness.task_def(
        "subset", task_id="sub-1", name="Subset",
        params={"files": [parquet], "columns": ["x", "FL1-A"]},
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project.workflow["tasks"][0]["children"].append(subset)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        harness.eq(_data_columns._columns_for(window._ctx, parquet, "g-1"), ["x", "FL1-A"])
    finally:
        _close(window)


@harness.case("data.columns.values", "L2", "data", tags=("smoke",))
def column_values() -> None:
    project = harness.new_project("data_values", rows=60)
    parquet = str(project.data_dir / "case.parquet")
    tasks = _chain_tasks(parquet)
    for task in tasks:
        project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        raw = pd.read_parquet(parquet)["FL1-A"].to_numpy(dtype="float64")

        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        harness.check(
            np.allclose(values["FL1-A"], raw),
            "Scale is display-only: the values stay raw",
        )

        renamed = _data_columns._column_data(ctx, "g-1", parquet, ["NewFL2"])
        expected = pd.read_parquet(parquet)["FL2-A"].to_numpy(dtype="float64")
        harness.check(np.allclose(renamed["NewFL2"], expected))

        missing = _data_columns._column_data(ctx, "g-1", parquet, ["missing"])
        harness.eq(missing, {})

        frame = _data_columns._read_columns(ctx, parquet, ["x", "FL1-A"], "g-1")
        harness.eq(list(frame.columns), ["x", "FL1-A"])
        harness.eq(len(frame), 60)
    finally:
        _close(window)


@harness.case("data.columns.text", "L2", "data", tags=("smoke",))
def text_columns() -> None:
    project = harness.new_project("data_text", rows=6)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["label"] = ["A", "B", "A", "B", "A", "B"]
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"].append("label")
    project.save_columns(columns)

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        values = _data_columns._column_data(
            window._ctx, None, str(parquet), ["label"], numeric=False
        )
        harness.eq(values["label"].tolist(), ["A", "B", "A", "B", "A", "B"])

        coerced = _data_columns._column_data(window._ctx, None, str(parquet), ["label"])
        harness.check(
            np.all(np.isnan(coerced["label"])),
            "numeric coercion turns text into NaN",
        )
    finally:
        _close(window)


@harness.case("data.columns.stages", "L2", "data", tags=("smoke",))
def map_stages() -> None:
    project = harness.new_project("data_stages", rows=20)
    parquet = str(project.data_dir / "case.parquet")
    compensation = harness.task_def(
        "compensation", task_id="c-1", name="Compensation",
        params={
            "params": ["A", "B"],
            "matrix": [[100, 0], [0, 100]],
            "files": [parquet],
        },
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "FL1-A", "method": "arcsinh", "param": 5}],
                "files": [parquet]},
        children=[compensation],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        stages = _data_columns._map_stages(ctx, "g-1")
        harness.eq([kind for kind, _stage in stages], ["compensation"],
                   "scale is display-only and stays out of the data pipeline")
        harness.eq(stages[0][1]["names"], ["A", "B"])
        harness.check(
            np.allclose(stages[0][1]["matrix"], np.eye(2)),
            "the matrix is stored as percentages",
        )
        display = _data_columns._map_stages(ctx, "g-1", include_scale=True)
        harness.eq([kind for kind, _stage in display], ["scale", "compensation"])
        harness.eq(display[0][1]["columns"]["FL1-A"]["method"], "arcsinh")
        harness.eq(display[0][1]["files"], {parquet})

        transforms = _data_columns._upstream_scale_transforms(ctx, "g-1")
        harness.eq(list(transforms), ["FL1-A"])
        harness.eq(transforms["FL1-A"][0]["method"], "arcsinh")
        harness.eq(_data_columns._upstream_scale_transforms(ctx, "s-1"), {})

        token = _data_columns._map_stage_token(ctx, "g-1")
        harness.eq(_data_columns._map_stage_token(ctx, "g-1"), token, "the token is deterministic")
        for task in window.project.workflow["tasks"][0]["children"]:
            if task.get("id") == "s-1":
                task["params"]["columns"][0]["method"] = "log10"
        window.project.save()
        harness.check(
            _data_columns._map_stage_token(ctx, "g-1") == token,
            "Scale is display-only: it does not change the data token",
        )
    finally:
        _close(window)


@harness.case("data.columns.unmix_generated_scope", "L2", "data",
              tags=("fast",))
def unmix_generated_column_scope() -> None:
    """Unmix contributes an inert marker from its generated outputs only."""
    unmix = harness.task_def(
        "unmix", task_id="u-1", name="Unmix",
        params={
            "samples": ["/image.parquet"],
            "matrix": [],
            "outputs": {
                "/image.parquet": {
                    "path": "Module/u-1/image_unmix.parquet",
                    "columns": ["U1"],
                    "kind": "image",
                    "signature": "sig",
                },
            },
        },
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "A", "method": "log10"}],
                "files": None},
        children=[unmix],
    )
    project = harness.new_project("unmix_scope", rows=10, tasks=[scale])
    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        original = ctx._unmix_flow_config
        calls = {"n": 0}

        def counting(task):
            calls["n"] += 1
            return original(task)

        ctx.__dict__["_unmix_flow_config"] = counting
        try:
            stages = _data_columns._map_stages(
                ctx, "g-1", parquet_path="/other.parquet"
            )
            harness.eq(
                stages, [],
                "scale stays out; a file without an output gets no marker",
            )
            harness.eq(calls["n"], 0, "no matrix is built while reading")

            stages = _data_columns._map_stages(
                ctx, "g-1", parquet_path="/image.parquet"
            )
            harness.eq(len(stages), 1)
            kind, stage = stages[0]
            harness.eq(kind, "unmix")
            harness.check(stage.get("generated"), "the stage is a marker")
            harness.eq(stage.get("names"), ["U1"])
            harness.eq(
                calls["n"], 0,
                "the generated output does not rebuild the matrix",
            )
        finally:
            ctx.__dict__.pop("_unmix_flow_config", None)
    finally:
        _close(window)


@harness.case("data.columns.illumination_stage", "L2", "data", tags=("smoke",))
def illumination_stage() -> None:
    from smage.data.columns import _illumination_stage_item

    sub = {
        "channel": "C",
        "x": "x",
        "y": "y",
        "files": ["/f"],
        "tile_w": 2,
        "tile_h": 2,
        "mask": [[0, 1], [2, 3]],
    }
    item = _illumination_stage_item(sub)
    harness.check(item is not None)
    harness.eq(item["channel"], "C")
    harness.eq(item["files"], {"/f"})
    harness.eq(item["T"], 1.5, "median is the default threshold")
    harness.eq(item["mask"].shape, (2, 2))

    harness.eq(
        _illumination_stage_item(dict(sub, method="down"))["T"], 0.0
    )
    harness.eq(
        _illumination_stage_item(dict(sub, method="up"))["T"], 3.0
    )
    harness.eq(_illumination_stage_item(dict(sub, channel="")), None)
    harness.eq(_illumination_stage_item(dict(sub, mask=[])), None)
    harness.eq(_illumination_stage_item(dict(sub, mask=[0, 1, 2])), None)


if __name__ == "__main__":
    raise SystemExit(harness.main())
