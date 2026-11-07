"""L1: heatmap sampling/normalization/linkage and array-level image helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.core.heatmap import (  # noqa: E402
    _heatmap_fill_nan,
    _heatmap_linkage,
    _heatmap_normalize,
    _heatmap_sample_counts,
)
from smage.core.image_ops import (  # noqa: E402
    _mode_pool,
    _otsu_threshold,
    _pool_mask,
    _relabel_sequential,
    _row_mode,
)


@harness.case("core.heatmap.counts", "L1", "core", tags=("smoke",))
def heatmap_counts() -> None:
    sizes = {"a": 100, "b": 300, "c": 0}
    harness.eq(
        _heatmap_sample_counts(sizes, "full", "uniform", 10),
        {"a": 100, "b": 300},
        "full mode keeps every non-empty file",
    )
    harness.eq(
        _heatmap_sample_counts(sizes, "sample", "uniform", 100),
        {"a": 100, "b": 100},
    )
    harness.eq(
        _heatmap_sample_counts(sizes, "sample", "proportional", 100),
        {"a": 25, "b": 75},
    )
    harness.eq(
        _heatmap_sample_counts(sizes, "sample", "proportional", 0),
        {"a": 100, "b": 300},
        "a zero sample size means full",
    )
    harness.eq(_heatmap_sample_counts({}, "full", "uniform", 10), {})

    harness.eq(
        _heatmap_sample_counts({"a": 1, "b": 1}, "sample", "proportional", 10),
        {"a": 1, "b": 1},
        "a file never contributes more rows than it has",
    )
    variable = _heatmap_sample_counts(
        {"a": 100, "b": 100}, "sample", "proportional", 101
    )
    harness.eq(sum(variable.values()), 101, "the rounding remainder is distributed")
    harness.check(set(variable.values()) <= {50, 51}, f"counts: {variable}")


@harness.case("core.heatmap.normalize", "L1", "core", tags=("smoke",))
def heatmap_normalize() -> None:
    matrix = np.array([[1.0, 2.0], [3.0, 4.0]])

    raw = _heatmap_normalize(matrix, "raw")
    harness.check(np.allclose(raw, matrix))
    harness.check(raw.dtype == np.float64)

    column = _heatmap_normalize(matrix, "column")
    harness.check(np.allclose(column, [[-1, -1], [1, 1]]), f"column: {column}")

    row = _heatmap_normalize(matrix, "row")
    harness.check(np.allclose(row, [[-1, 1], [-1, 1]]), f"row: {row}")

    constant = _heatmap_normalize(np.ones((2, 2)), "column")
    harness.check(np.allclose(constant, 0.0), "a constant column normalizes to zero")

    with_nan = _heatmap_normalize(np.array([[1.0, np.nan], [3.0, 4.0]]), "column")
    harness.check(np.allclose(with_nan[:, 0], [-1, 1]))
    harness.check(np.isnan(with_nan[0, 1]))


@harness.case("core.heatmap.fill_nan", "L1", "core", tags=("smoke",))
def heatmap_fill_nan() -> None:
    matrix = np.array([[1.0, np.nan], [np.nan, np.nan]])
    by_row = _heatmap_fill_nan(matrix, axis=1)
    harness.check(np.allclose(by_row, [[1, 1], [0, 0]]), f"row fill: {by_row}")

    by_column = _heatmap_fill_nan(matrix, axis=0)
    harness.check(np.allclose(by_column, [[1, 0], [1, 0]]), f"column fill: {by_column}")


@harness.case("core.heatmap.linkage", "L1", "core", tags=("smoke",))
def heatmap_linkage() -> None:
    result = _heatmap_linkage(np.array([[0.0, 0.0], [1.0, 1.0], [5.0, 5.0]]),
                              "euclidean", "average")
    harness.check(result is not None, "linkage is available")
    harness.eq(len(result["order"]), 3)
    harness.eq(len(result["linkage"]), 2)
    harness.eq(len(result["icoord"]), 2)
    harness.eq(len(result["dcoord"]), 2)

    harness.eq(
        _heatmap_linkage(np.array([[0.0, 0.0]]), "euclidean", "average"),
        None,
        "one row has no dendrogram",
    )
    harness.eq(
        _heatmap_linkage(np.zeros((3,)), "euclidean", "average"),
        None,
        "non-2D input is rejected",
    )
    harness.check(
        _heatmap_linkage(np.array([[0.0, 0.0], [1.0, 1.0]]), "euclidean", "bogus")
        is not None,
        "an unknown method falls back to average",
    )


@harness.case("core.image.otsu", "L1", "core", tags=("smoke",))
def otsu() -> None:
    harness.eq(_otsu_threshold(np.array([])), 0.0)
    harness.eq(_otsu_threshold(np.array([5.0, 5.0, 5.0])), 5.0)

    bimodal = np.array([0.0] * 50 + [10.0] * 50)
    threshold = _otsu_threshold(bimodal)
    harness.check(0.0 <= threshold < 5.0, f"threshold {threshold} separates modes")

    harness.check(
        1.0 <= _otsu_threshold(np.array([1.0, 2.0, 3.0])) <= 3.0,
        "threshold stays inside the data range",
    )
    harness.eq(
        _otsu_threshold(np.array([np.nan, 2.0, 2.0])),
        2.0,
        "non-finite values are ignored",
    )


@harness.case("core.image.relabel", "L1", "core", tags=("smoke",))
def relabel() -> None:
    labels = np.array([[1, 1, 2], [2, 3, 3]], dtype=np.int32)
    harness.check(
        np.array_equal(_relabel_sequential(labels, min_area=2), labels),
        "all large enough labels keep their order",
    )
    harness.check(
        np.allclose(_relabel_sequential(labels, min_area=3), 0),
        "labels below the minimum are removed",
    )

    small = np.array([[1, 1, 1, 2]], dtype=np.int32)
    harness.eq(_relabel_sequential(small, min_area=3).tolist(), [[1, 1, 1, 0]])

    empty = np.array([], dtype=np.int32)
    harness.eq(_relabel_sequential(empty).size, 0)


@harness.case("core.image.mode", "L1", "core", tags=("smoke",))
def modes() -> None:
    blocks = np.array([[1, 1, 2], [3, 4, 4]], dtype=np.int32)
    harness.eq(_row_mode(blocks).tolist(), [1, 4])

    grid = np.array(
        [[0, 0, 1, 1],
         [0, 0, 1, 1],
         [2, 2, 3, 3],
         [2, 2, 3, 3]],
        dtype=np.int32,
    )
    harness.eq(_mode_pool(grid, 2).tolist(), [[0, 1], [2, 3]])


@harness.case("core.image.pool_mask", "L1", "core", tags=("smoke",))
def pool_mask() -> None:
    mask = np.ones((4, 4), dtype=np.uint8)
    pooled = _pool_mask(mask, [1, 2, 4])
    harness.eq(sorted(pooled), [1, 2, 4])
    harness.eq(pooled[1].shape, (4, 4))
    harness.eq(pooled[2].shape, (2, 2))
    harness.eq(pooled[4].shape, (1, 1))
    for block, values in pooled.items():
        harness.check(np.allclose(values, 1.0), f"block {block} is fully covered")

    checker = np.zeros((4, 4), dtype=np.uint8)
    checker[0::2, 0::2] = 1
    checker[1::2, 1::2] = 1
    pooled = _pool_mask(checker, [2, 4])
    harness.check(np.allclose(pooled[2], 0.5), "half of each 2x2 block is set")
    harness.check(np.allclose(pooled[4], 0.5))

    harness.eq(_pool_mask(mask, []), {})
    harness.eq(sorted(_pool_mask(mask, [1, 3])), [1], "non-power-of-two blocks are skipped")


if __name__ == "__main__":
    raise SystemExit(harness.main())
