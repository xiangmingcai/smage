"""L1: gate geometry, boolean gates, per-file overrides and filter masks."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.core.gates import (  # noqa: E402
    _compute_gate_filter_mask,
    _gate_chain,
    _gate_geometry,
    _gate_group_id,
    _gate_mask,
    _gate_needed_columns,
    _gate_own_mask,
    _gate_source_ids,
    _merge_gate_geometry,
    _point_in_polygon,
    _resolve_gates_for_file,
)


@harness.case("core.gates.shapes", "L1", "core", tags=("smoke",))
def gate_shapes() -> None:
    x = np.array([0.0, 5.0, 10.0, 15.0, 20.0])
    y = x.copy()

    rect = {"type": "rect", "pos": (5, 5), "size": (10, 10)}
    harness.eq(
        _gate_mask(x, y, rect).tolist(),
        [False, True, True, True, False],
        "rectangle includes its boundary",
    )

    ellipse = {"type": "ellipse", "pos": (0, 0), "size": (10, 10)}
    ex = np.array([5.0, 5.0, 10.0, 0.0])
    ey = np.array([10.0, 5.0, 5.0, 0.0])
    harness.eq(
        _gate_mask(ex, ey, ellipse).tolist(),
        [True, True, True, False],
        "ellipse uses its bounding box",
    )

    polygon = {"type": "polygon", "points": [(0, 0), (10, 0), (10, 10), (0, 10)]}
    harness.eq(
        _point_in_polygon(np.array([5.0, 15.0]), np.array([5.0, 5.0]), polygon["points"]).tolist(),
        [True, False],
    )

    quadrant = {"type": "quadrant", "vx": 10, "hy": 10}
    for q, (px, py) in enumerate([(11, 11), (9, 11), (9, 9), (11, 9)], start=1):
        spec = dict(quadrant, quadrant=q)
        harness.check(
            bool(_gate_mask(np.array([px]), np.array([py]), spec)[0]),
            f"quadrant {q} contains ({px},{py})",
        )

    rng = {"type": "range", "x1": 15, "x2": 5}
    harness.eq(
        _gate_mask(np.array([4.0, 5.0, 10.0, 15.0, 16.0]),
                   np.zeros(5), rng).tolist(),
        [False, True, True, True, False],
        "range accepts reversed endpoints",
    )

    categorical = {"type": "categorical", "value": 2}
    harness.eq(
        _gate_mask(np.array([1, 2, 3]), np.zeros(3), categorical).tolist(),
        [False, True, False],
    )

    split_left = {"type": "split", "vx": 10, "side": "left"}
    split_right = {"type": "split", "vx": 10, "side": "right"}
    xs = np.array([9.0, 10.0, 11.0])
    harness.eq(_gate_mask(xs, np.zeros(3), split_left).tolist(), [True, False, False])
    harness.eq(_gate_mask(xs, np.zeros(3), split_right).tolist(), [False, True, True])

    harness.eq(
        _gate_mask(xs, np.zeros(3), {"type": "unknown"}).tolist(),
        [True, True, True],
        "an unknown type does not filter anything",
    )


@harness.case("core.gates.boolean", "L1", "core", tags=("smoke",))
def boolean_gates() -> None:
    x = np.array([0.0, 5.0, 10.0, 15.0, 20.0])
    y = x.copy()
    gates = [
        {"id": "a", "type": "rect", "x": "x", "y": "y", "pos": (0, 0), "size": (10, 10)},
        {"id": "b", "type": "rect", "x": "x", "y": "y", "pos": (5, 5), "size": (10, 10)},
        {"id": "not_a", "type": "not", "source": "a"},
        {"id": "and_ab", "type": "and", "sources": ["a", "b"]},
        {"id": "or_ab", "type": "or", "sources": ["a", "b"]},
    ]
    by_id = {gate["id"]: gate for gate in gates}
    columns = {"x": x, "y": y}

    def own(gate_id):
        return _gate_own_mask(
            by_id[gate_id], by_id, columns.__getitem__, {}, {"x", "y"}
        )

    harness.eq(own("a").tolist(), [True, True, True, False, False])
    harness.eq(own("b").tolist(), [False, True, True, True, False])
    harness.eq(own("not_a").tolist(), [False, False, False, True, True])
    harness.eq(own("and_ab").tolist(), [False, True, True, False, False])
    harness.eq(own("or_ab").tolist(), [True, True, True, True, False])

    missing = {"id": "m", "type": "rect", "x": "nope", "y": "y",
               "pos": (0, 0), "size": (1, 1)}
    harness.eq(
        _gate_own_mask(missing, {**by_id, "m": missing}, columns.__getitem__, {},
                       {"x", "y"}),
        None,
        "a missing column cannot produce a mask",
    )

    cycle = {"id": "c", "type": "not", "source": "c"}
    harness.eq(
        _gate_own_mask(cycle, {"c": cycle}, columns.__getitem__, {}, {"x", "y"}),
        None,
        "a boolean cycle returns None instead of recursing forever",
    )

    range_gate = {"id": "r", "type": "range", "x": "x", "x1": 0, "x2": 5}
    mask = _gate_own_mask(
        range_gate, {"r": range_gate}, columns.__getitem__, {}, {"x", "y"}
    )
    harness.eq(mask.tolist(), [True, True, False, False, False], "y is optional")


@harness.case("core.gates.meta", "L1", "core", tags=("smoke",))
def gate_metadata() -> None:
    harness.eq(_gate_source_ids({"type": "not", "source": "a"}), ["a"])
    harness.eq(_gate_source_ids({"type": "and", "sources": ["a", "b"]}), ["a", "b"])
    harness.eq(_gate_source_ids({"type": "rect"}), [])
    harness.eq(_gate_source_ids({"type": "not"}), [])

    harness.eq(_gate_group_id({"id": "g", "crosshair_id": "c"}), "c")
    harness.eq(_gate_group_id({"id": "g", "split_id": "s"}), "s")
    harness.eq(_gate_group_id({"id": "g"}), "g")

    rect = {"id": "g", "type": "rect", "pos": [1, 2], "size": [3, 4],
            "x": "x", "color": "#fff"}
    geometry = _gate_geometry(rect)
    harness.eq(geometry, {"pos": [1, 2], "size": [3, 4]})
    geometry["pos"][0] = 99
    harness.eq(rect["pos"], [1, 2], "geometry is a deep copy")

    polygon = {"type": "polygon", "points": [[0, 0], [1, 1]]}
    harness.eq(_gate_geometry(polygon), {"points": [[0, 0], [1, 1]]})
    harness.eq(_gate_geometry({"type": "unknown"}), {})


@harness.case("core.gates.merge_resolve", "L1", "core", tags=("smoke",))
def merge_and_resolve() -> None:
    gate = {"id": "g1", "crosshair_id": "c1", "type": "quadrant",
            "vx": 0, "hy": 0}
    merged = _merge_gate_geometry(gate, {"vx": 5})
    harness.eq(merged["vx"], 5)
    harness.eq(gate["vx"], 0, "the source gate is not modified")
    harness.check(merged is not gate, "a new dict is returned")

    gates = [gate, {"id": "g2", "type": "rect", "pos": (0, 0), "size": (1, 1)}]
    per_file = {"c1": {"/f": {"vx": 7, "hy": 8}}}
    resolved = _resolve_gates_for_file(gates, per_file, "/f")
    harness.eq(resolved[0]["vx"], 7)
    harness.eq(resolved[0]["hy"], 8)
    harness.eq(resolved[1], gates[1])

    harness.eq(
        _resolve_gates_for_file(gates, per_file, "/other"),
        gates,
        "a file without an override falls back to the shared geometry",
    )
    harness.eq(_resolve_gates_for_file(gates, None, "/f"), gates)


@harness.case("core.gates.columns", "L1", "core", tags=("smoke",))
def needed_columns() -> None:
    gates = [
        {"id": "a", "type": "rect", "x": "x", "y": "y"},
        {"id": "b", "type": "range", "x": "z"},
        {"id": "not_a", "type": "not", "source": "a"},
        {"id": "and_ab", "type": "and", "sources": ["a", "b"]},
        {"id": "orphan", "type": "and", "sources": ["missing"]},
    ]
    by_id = {gate["id"]: gate for gate in gates}
    harness.eq(_gate_needed_columns(gates[0], by_id), {"x", "y"})
    harness.eq(_gate_needed_columns(gates[1], by_id), {"z"})
    harness.eq(_gate_needed_columns(gates[2], by_id), {"x", "y"})
    harness.eq(_gate_needed_columns(gates[3], by_id), {"x", "y", "z"})
    harness.eq(_gate_needed_columns(gates[4], by_id), set())


@harness.case("core.gates.chain", "L1", "core", tags=("smoke",))
def gate_chain() -> None:
    gates = [
        {"id": "a"},
        {"id": "b", "parent": "a"},
        {"id": "c", "parent": "b"},
        {"id": "x"},
    ]
    harness.eq([gate["id"] for gate in _gate_chain(gates, "c")], ["a", "b", "c"])
    harness.eq([gate["id"] for gate in _gate_chain(gates, "a")], ["a"])
    harness.eq(_gate_chain(gates, "missing"), [])


@harness.case("core.gates.filter_mask", "L1", "core", tags=("smoke",))
def filter_mask() -> None:
    path = harness.RUN_DIR / "tmp" / "gates.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.arange(1, 11)
    pd.DataFrame({"x": values, "y": values}).to_parquet(path, index=False)

    gates = [
        {"id": "root", "type": "rect", "x": "x", "y": "y",
         "pos": (2, 2), "size": (5, 5)},
        {"id": "child", "type": "rect", "x": "x", "y": "y",
         "parent": "root", "pos": (3, 3), "size": (2, 2)},
    ]
    mask = _compute_gate_filter_mask(gates, "child", path, {})
    harness.eq(int(mask.sum()), 3, "parent and child masks are intersected")

    harness.eq(
        _compute_gate_filter_mask(gates, "missing", path, {}), None
    )
    harness.eq(
        _compute_gate_filter_mask(
            [{"id": "z", "type": "rect", "pos": (0, 0), "size": (1, 1)}],
            "z", path, {},
        ),
        None,
        "a gate without columns cannot be computed",
    )

    mask = _compute_gate_filter_mask(gates, "root", path, {}, existing={"x", "y"})
    harness.eq(int(mask.sum()), 6)

    extra_gate = [{"id": "e", "type": "rect", "x": "U", "y": "y",
                   "pos": (0, 0), "size": (100, 100)}]
    mask = _compute_gate_filter_mask(
        extra_gate, "e", path, {},
        existing={"U", "y"}, extra={"U": np.arange(1, 11)},
    )
    harness.eq(int(mask.sum()), 10, "extra columns are used when provided")

    scaled = [{"id": "t", "type": "range", "x": "x", "x1": 0, "x2": 1}]
    mask = _compute_gate_filter_mask(
        scaled, "t", path, {"x": [{"method": "log10"}]}
    )
    harness.eq(int(mask.sum()), 10, "scale transforms are applied before the gate")


if __name__ == "__main__":
    raise SystemExit(harness.main())
