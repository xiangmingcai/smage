"""L1: scaling math, subsample allocation and stable row sampling."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.core.sampling import (  # noqa: E402
    _row_priorities,
    _stable_sample_selection,
    _subsample_allocate,
)
from smage.core.scale_math import (  # noqa: E402
    _apply_scale_transform,
    _apply_scale_transforms,
    _forward_scale_value,
    _inverse_scale_value,
    _nice_ticks,
    _round_value_candidates,
)


@harness.case("core.scale.transforms", "L1", "core", tags=("smoke",))
def scale_transforms() -> None:
    values = np.array([0.0, 1.0, 10.0, 100.0, -10.0, -1.0])
    out = _apply_scale_transform(values, {"method": "log10"})
    harness.check(
        np.allclose(out, [0.0, 0.0, 1.0, 2.0, -1.0, 0.0]),
        f"log10 keeps the sign: {out}",
    )

    out = _apply_scale_transform(np.array([5.0]), {"method": "arcsinh"})
    harness.check(np.isclose(out[0], np.arcsinh(1.0)), "arcsinh default cofactor is 5")

    out = _apply_scale_transform(
        np.array([10.0]), {"method": "arcsinh", "param": 10}
    )
    harness.check(np.isclose(out[0], np.arcsinh(1.0)), "arcsinh cofactor is honoured")

    out = _apply_scale_transform(
        np.array([5.0]), {"method": "arcsinh", "param": "bad"}
    )
    harness.check(np.isclose(out[0], np.arcsinh(1.0)), "bad cofactor falls back to 5")

    harness.check(
        _apply_scale_transform(values, None) is values,
        "no config leaves the array untouched",
    )
    harness.check(
        _apply_scale_transform(values, {"method": "linear"}) is values,
        "linear leaves the array untouched",
    )

    chained = _apply_scale_transforms(
        np.array([100.0]),
        [{"method": "log10"}, {"method": "arcsinh", "param": 5}],
    )
    harness.check(np.isclose(chained[0], np.arcsinh(0.4)), "transforms apply in order")


@harness.case("core.scale.values", "L1", "core", tags=("smoke",))
def scale_values() -> None:
    harness.eq(_forward_scale_value(1000, [{"method": "log10"}]), 3.0)
    harness.eq(_forward_scale_value(None, [{"method": "log10"}]), None)
    harness.eq(_forward_scale_value("bad", [{"method": "log10"}]), None)
    harness.eq(_forward_scale_value(5, []), 5.0)

    harness.eq(_inverse_scale_value(3.0, [{"method": "log10"}]), 1000.0)
    harness.eq(_inverse_scale_value(-3.0, [{"method": "log10"}]), -1000.0)
    harness.eq(_inverse_scale_value(0.0, [{"method": "log10"}]), 0.0)

    configs = [{"method": "arcsinh", "param": 5}]
    forward = _forward_scale_value(10.0, configs)
    harness.check(
        abs(_inverse_scale_value(forward, configs) - 10.0) < 1e-9,
        "arcsinh round-trips",
    )

    harness.check(
        math.isnan(_inverse_scale_value(400.0, [{"method": "log10"}])),
        "out-of-range inverse returns NaN",
    )


@harness.case("core.scale.candidates", "L1", "core", tags=("smoke",))
def scale_candidates() -> None:
    harness.eq(_round_value_candidates(0.5, 25), [0.5, 1, 2, 5, 10, 20])
    harness.eq(_round_value_candidates(-5, 5), [-5, -2, -1, 0, 1, 2, 5])
    harness.eq(_round_value_candidates(10, 10), [])
    harness.eq(_round_value_candidates(float("nan"), 1), [])

    harness.eq(_nice_ticks(0, 10, 6), [0, 2, 4, 6, 8, 10])
    harness.eq(_nice_ticks(0, 1, 5), [0, 0.5, 1.0])
    harness.eq(_nice_ticks(5, 5), [])
    harness.eq(_nice_ticks(float("nan"), 1), [])


@harness.case("core.sampling.allocate", "L1", "core", tags=("smoke",))
def subsample_allocate() -> None:
    sizes = {"a": 50, "b": 200}
    harness.eq(
        _subsample_allocate(
            {"basis": "per_file", "count": 100, "files": ["a", "b"]}, sizes
        ),
        {"a": 50, "b": 100},
    )
    harness.eq(
        _subsample_allocate(
            {"basis": "total", "count": 100, "files": ["a", "b"]}, sizes
        ),
        {"a": 50, "b": 50},
    )
    harness.eq(
        _subsample_allocate(
            {
                "basis": "total",
                "allocation": "weighted",
                "count": 100,
                "files": ["a", "b"],
            },
            {"a": 100, "b": 300},
        ),
        {"a": 25, "b": 75},
    )
    harness.eq(
        _subsample_allocate(
            {
                "basis": "per_file",
                "unit": "percent",
                "percent": 10,
                "files": ["a", "b"],
            },
            sizes,
        ),
        {"a": 5, "b": 20},
    )
    harness.eq(
        _subsample_allocate(
            {
                "basis": "per_file",
                "count": 10,
                "files": ["a", "b"],
                "overrides": {"a": 1000, "c": 5},
            },
            sizes,
        ),
        {"a": 50, "b": 10},
        "override is clamped and unknown paths are ignored",
    )
    harness.eq(
        _subsample_allocate({"basis": "per_file", "count": 0, "files": ["a"]}, sizes),
        {},
    )
    harness.eq(
        _subsample_allocate({"count": 10, "files": ["a", "b"]}, sizes),
        {},
        "no basis means nothing is allocated",
    )


@harness.case("core.sampling.priorities", "L1", "core", tags=("smoke",))
def row_priorities() -> None:
    indices = np.array([0, 1, 2, 3, 100, 1000], dtype=np.uint64)
    first = _row_priorities(indices)
    second = _row_priorities(indices)
    harness.check(np.array_equal(first, second), "priorities are deterministic")
    harness.check(first.dtype == np.float64, f"dtype is {first.dtype}")
    harness.check(np.all(first >= 0.0) and np.all(first < 1.0), "priorities in [0,1)")
    other = _row_priorities(indices, seed=7)
    harness.check(not np.array_equal(first, other), "seed changes the priorities")


@harness.case("core.sampling.selection", "L1", "core", tags=("smoke",))
def stable_selection() -> None:
    keep = np.ones(100, dtype=bool)
    selection = _stable_sample_selection(keep, 10)
    harness.check(selection is not None, "sampling returns a mask")
    harness.eq(selection.size, 100, "mask covers the compressed rows")
    harness.eq(int(selection.sum()), 10, "exactly max_points rows are kept")

    again = _stable_sample_selection(keep, 10)
    harness.check(np.array_equal(selection, again), "selection is deterministic")

    keep = np.zeros(20, dtype=bool)
    keep[[1, 3, 5, 7]] = True
    selection = _stable_sample_selection(keep, 2)
    harness.eq(int(selection.sum()), 2)
    chosen = np.flatnonzero(keep)[selection]
    harness.check(
        set(chosen.tolist()).issubset({1, 3, 5, 7}),
        f"sampled rows come from the visible rows: {chosen}",
    )

    harness.eq(
        _stable_sample_selection(np.ones(5, dtype=bool), 5),
        None,
        "when every row fits, no mask is returned",
    )


if __name__ == "__main__":
    raise SystemExit(harness.main())
