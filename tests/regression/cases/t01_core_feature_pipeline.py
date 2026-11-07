"""L1: Feature aggregation, map-stage pipeline and settings helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.core import settings as settings_mod  # noqa: E402
from smage.core.feature import (  # noqa: E402
    _feature_aggregate_frame,
    _feature_auto_name,
    _feature_count_name,
    _feature_safe_stem,
)
from smage.core.pipeline import (  # noqa: E402
    _apply_batch_align,
    _assign_batch_clusters,
    _evaluate_stages,
    _linear_combine,
    _pipeline_dependencies,
    _pipeline_touches,
    _rename_applies,
    _rename_stage,
)


@harness.case("core.feature.names", "L1", "core", tags=("smoke",))
def feature_names() -> None:
    harness.eq(_feature_auto_name({"method": "count"}), "count")
    harness.eq(_feature_auto_name({"source": "A", "method": "mean"}), "A_mean")
    harness.eq(
        _feature_auto_name({"source": "A", "method": "percentile", "perc": 95}),
        "A_p95",
    )
    harness.eq(
        _feature_auto_name({"source": "A", "method": "percentile"}),
        "A_p",
    )
    harness.eq(_feature_auto_name({}), "value_mean")

    harness.eq(_feature_safe_stem('a/b:c*?"<>| d'), "a_b_c_d")
    harness.eq(_feature_safe_stem("..."), "feature")
    harness.eq(_feature_safe_stem("   "), "feature")
    harness.eq(_feature_safe_stem("a.b"), "a.b")

    harness.eq(
        _feature_count_name({"rules": [{"method": "count", "name": "counts"}],
                             "feature": "F"}),
        "counts",
    )
    harness.eq(
        _feature_count_name({"rules": [{"method": "count"}], "feature": "count"}),
        "count_1",
        "a clash with the feature column is renamed",
    )
    harness.eq(
        _feature_count_name({
            "rules": [{"method": "count"}, {"method": "mean", "name": "count"}],
            "feature": "F",
        }),
        "count_1",
        "a clash with another rule is renamed",
    )
    harness.eq(
        _feature_count_name({
            "rules": [{"method": "count"}, {"method": "mean", "name": "count"},
                      {"method": "mean", "name": "count_1"}],
            "feature": "F",
        }),
        "count_2",
    )


@harness.case("core.feature.aggregate", "L1", "core", tags=("smoke",))
def feature_aggregate() -> None:
    frame = pd.DataFrame({"feat": ["A", "A", "B"], "v": [1.0, 2.0, 10.0]})
    rules = [
        {"method": "count"},
        {"source": "v", "method": "mean", "name": "mean_v"},
    ]
    out, columns, warnings = _feature_aggregate_frame(frame, "feat", rules, "n")
    harness.eq(columns, ["feat", "n", "mean_v"])
    harness.eq(out["n"].tolist(), [2, 1])
    harness.eq(out["mean_v"].tolist(), [1.5, 10.0])
    harness.eq(warnings, [])

    out, _, _ = _feature_aggregate_frame(
        frame, "feat",
        [{"method": "count"},
         {"source": "v", "method": "percentile", "perc": 50, "name": "med"}],
        "n",
    )
    harness.eq(out["med"].tolist(), [1.5, 10.0])

    out, _, _ = _feature_aggregate_frame(
        frame, "feat",
        [{"method": "count"},
         {"source": "v", "method": "sum", "name": "total"}],
        "n",
    )
    harness.eq(out["total"].tolist(), [3.0, 10.0])

    out, _, _ = _feature_aggregate_frame(
        frame, "feat",
        [{"method": "count"},
         {"source": "v", "method": "mean", "lo": 0, "hi": 50}],
        "n",
    )
    harness.eq(out["v_mean"].tolist(), [1.0, 10.0], "the per-class range is applied")

    empty = pd.DataFrame({"feat": [], "v": []})
    out, columns, _ = _feature_aggregate_frame(
        empty, "feat", [{"method": "count"}], "n"
    )
    harness.eq(len(out), 0)
    harness.eq(columns, ["feat", "n"])


@harness.case("core.pipeline.rename", "L1", "core", tags=("smoke",))
def pipeline_rename() -> None:
    harness.check(_rename_applies({"files": None}, "/f"))
    harness.check(_rename_applies({"files": []}, "/f"))
    harness.check(_rename_applies({"files": ["/f"]}, "/f"))
    harness.check(not _rename_applies({"files": ["/g"]}, "/f"))
    harness.check(_rename_applies({"files": ["/g"]}, None), "no path means every file")

    harness.eq(_rename_stage({"mode": "file"}), None)
    stage = _rename_stage({
        "mode": "column",
        "column": {
            "files": ["/a"],
            "rename": {"old": "new", "same": "same", "": "x"},
        },
    })
    harness.eq(stage, {"files": {"/a"}, "map": {"old": "new"}})
    harness.eq(
        _rename_stage({"mode": "column", "column": {"files": [], "rename": {"a": "b"}}}),
        None,
    )
    harness.eq(
        _rename_stage({"mode": "column", "column": {"files": ["/a"], "rename": {}}}),
        None,
    )


@harness.case("core.pipeline.touches", "L1", "core", tags=("smoke",))
def pipeline_touches() -> None:
    stages = [
        ("scale", {"columns": {"A": {}}}),
        ("compensation", {"names": ["B"]}),
        ("unmix", {"names": ["C"]}),
        ("illumination", {"submodules": [{"channel": "D"}]}),
        ("rename", {"map": {"E": "F"}}),
    ]
    for name in ("A", "B", "C", "D", "E", "F"):
        harness.check(_pipeline_touches(stages, name), f"{name} is touched")
    harness.check(not _pipeline_touches(stages, "G"))


@harness.case("core.pipeline.dependencies", "L1", "core", tags=("smoke",))
def pipeline_dependencies() -> None:
    rename = ("rename", {"files": {"/a"}, "map": {"old": "new"}})
    harness.eq(
        _pipeline_dependencies([rename], {"new"}, "/a"), {"old"}
    )
    harness.eq(
        _pipeline_dependencies([rename], {"new"}, "/b"), {"new"},
        "a rename only applies to its files",
    )

    unmix = ("unmix", {"names": ["A"], "columns": ["x", "y"]})
    harness.eq(_pipeline_dependencies([unmix], {"A"}), {"A", "x", "y"})

    comp = ("compensation", {"names": ["A", "B"]})
    harness.eq(_pipeline_dependencies([comp], {"A"}), {"A", "B"})

    illum = ("illumination", {"submodules": [{"channel": "C", "x": "X", "y": "Y"}]})
    harness.eq(_pipeline_dependencies([illum], {"C"}), {"C", "X", "Y"})

    batch = ("batch_align", {"columns": ["A", "B"]})
    harness.eq(_pipeline_dependencies([batch], {"A"}), {"A", "B"})


@harness.case("core.pipeline.linear", "L1", "core", tags=("smoke",))
def linear_combine() -> None:
    harness.eq(_linear_combine([], np.zeros((0, 0))), [])

    sources = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    weights = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    out = _linear_combine(sources, weights)
    harness.check(np.allclose(out[0], [1, 2]))
    harness.check(np.allclose(out[1], [3, 4]))
    harness.check(np.allclose(out[2], [4, 6]))

    grid_a = np.ones((2, 2))
    grid_b = np.full((2, 2), 2.0)
    out = _linear_combine([grid_a, grid_b], np.array([[1.0, -1.0], [0.5, 0.5]]))
    harness.check(np.allclose(out[0], -1.0), "2-D sources combine elementwise")
    harness.check(np.allclose(out[1], 1.5))


@harness.case("core.pipeline.evaluate", "L1", "core", tags=("smoke",))
def evaluate_stages() -> None:
    values = {"A": np.array([1.0, 10.0, 100.0]), "B": np.array([2.0, 3.0, 4.0])}
    out = _evaluate_stages(
        [("scale", {"columns": {"A": {"method": "log10"}}})],
        values, ["A", "B"],
    )
    harness.check(np.allclose(out["A"], [0, 1, 2]))
    harness.check(np.allclose(out["B"], [2, 3, 4]))
    harness.check(np.array_equal(values["A"], [1, 10, 100]), "input is not mutated")

    out = _evaluate_stages(
        [("scale", {"files": {"/other"}, "columns": {"A": {"method": "log10"}}})],
        values, ["A"], parquet_path="/f",
    )
    harness.check(np.allclose(out["A"], [1, 10, 100]), "file scope is respected")

    out = _evaluate_stages(
        [("rename", {"files": None, "map": {"A": "renamed"}})],
        values, ["renamed"],
    )
    harness.check(np.allclose(out["renamed"], [1, 10, 100]))

    comp_values = {"A": np.array([1.0, 1.0]), "B": np.array([10.0, 10.0])}
    out = _evaluate_stages(
        [("compensation", {
            "files": {"/f"},
            "names": ["A", "B"],
            "matrix": np.array([[1.0, 2.0], [3.0, 4.0]]),
        })],
        comp_values, ["A", "B"], parquet_path="/f",
    )
    harness.check(np.allclose(out["A"], [21, 21]))
    harness.check(np.allclose(out["B"], [43, 43]))

    unmix_values = {"a": np.array([1.0, 2.0]), "b": np.array([3.0, 4.0])}
    out = _evaluate_stages(
        [("unmix", {
            "samples": {"/f"},
            "columns": ["a", "b"],
            "names": ["u", "v"],
            "matrix": np.eye(2),
        })],
        unmix_values, ["u", "v"], parquet_path="/f",
    )
    harness.check(np.allclose(out["u"], [1, 2]))
    harness.check(np.allclose(out["v"], [3, 4]))

    out = _evaluate_stages([], values, ["A", "missing"])
    harness.check("A" in out and "missing" not in out, "only requested existing names")


@harness.case("core.pipeline.batch", "L1", "core", tags=("smoke",))
def batch_alignment() -> None:
    model = {
        "codes": np.array([[0.0, 0.0], [10.0, 10.0]]),
        "node_cluster": np.array([0, 1]),
    }
    matrix = np.array([[0.0, 0.0], [9.0, 9.0], [10.0, 10.0]])
    clusters = _assign_batch_clusters(model, matrix, None, None)
    harness.eq(clusters.tolist(), [0, 1, 1])
    harness.eq(clusters.dtype, np.dtype("int16"))

    cache = {}
    first = _assign_batch_clusters(model, matrix, cache, "key")
    second = _assign_batch_clusters(model, matrix, cache, "key")
    harness.check(second is first, "cluster assignments are cached")

    stage = {
        "method": "combat",
        "columns": ["A"],
        "files": {"/f": 0},
        "affine": np.array([[[2.0, 1.0]]]),
    }
    out = _apply_batch_align(stage, {"A": np.array([1.0, 2.0])}, "/f")
    harness.check(np.allclose(out["A"], [3, 5]))

    harness.eq(
        _apply_batch_align(stage, {"A": np.array([1.0])}, "/other"), {}
    )
    harness.eq(
        _apply_batch_align({"method": "spline", "columns": ["A"],
                            "files": {"/f": 0}, "codes": None},
                           {"A": np.array([1.0])}, "/f"),
        {},
    )


@harness.case("core.settings.helpers", "L1", "core", tags=("smoke",))
def settings_helpers() -> None:
    defaults = {"a": {"x": 1, "y": 2}, "b": 3}
    merged = settings_mod._merge_settings(defaults, {"a": {"y": 5}, "c": 4})
    harness.eq(merged, {"a": {"x": 1, "y": 5}, "b": 3, "c": 4})
    harness.eq(defaults, {"a": {"x": 1, "y": 2}, "b": 3}, "defaults are unchanged")

    copy_one = settings_mod._merge_settings(defaults, "bad")
    copy_one["b"] = 99
    harness.eq(defaults["b"], 3, "a non-dict gives a deep copy")

    old = settings_mod._GLOBAL_SETTINGS
    try:
        settings_mod._GLOBAL_SETTINGS = {
            "plot": {"default_colormap": "magma"},
            "appearance": {"density": "compact", "plot_background": "grey"},
        }
        harness.eq(settings_mod._default_palette(), "magma")
        harness.eq(settings_mod._plot_background(), "#f2f4f6")
        harness.check(
            "compact UI density" in settings_mod._app_stylesheet(),
            "compact density adds CSS",
        )

        settings_mod._GLOBAL_SETTINGS = {
            "plot": {"default_colormap": ""},
            "appearance": {"density": "normal", "plot_background": "white"},
        }
        harness.eq(settings_mod._default_palette(), "viridis")
        harness.eq(settings_mod._plot_background(), "w")
        harness.eq(settings_mod._app_stylesheet(), settings_mod.APP_STYLE)
    finally:
        settings_mod._GLOBAL_SETTINGS = old


if __name__ == "__main__":
    raise SystemExit(harness.main())
