"""Gate geometry, masks and boolean-gate resolution.

No Qt: gates are plain dicts, values are arrays, and the functions read the
needed columns through a caller-supplied ``get_col`` callable.
"""
from __future__ import annotations

import copy

from smage.core.scale_math import _apply_scale_transforms

# Geometry fields that can differ per file (never the gate type or axes).
_GATE_GEOMETRY_KEYS = {
    "rect": ("pos", "size"),
    # Ellipse gates also carry a polygon approximation in data units: an
    # ellipse drawn on a non-linear axis (log / arcsinh) is not an ellipse in
    # data space, so the sampled outline is the shape that actually matches
    # the drawn gate.
    "ellipse": ("pos", "size", "points"),
    "polygon": ("points",),
    "quadrant": ("vx", "hy"),
    "split": ("vx",),
    "range": ("x1", "x2"),
    "skewed_quadrant": ("center", "p_right", "p_up", "p_left", "p_down"),
}


def _point_in_polygon(x, y, points):
    """Vectorized ray-casting point-in-polygon test (boolean mask)."""
    import numpy as np
    px = np.asarray([p[0] for p in points], dtype=float)
    py = np.asarray([p[1] for p in points], dtype=float)
    n = len(px)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    inside = np.zeros(x.shape, dtype=bool)
    if n < 3:
        return inside
    # Points outside the polygon's bounding box can never be inside; testing
    # only the rest keeps large polygons (and the sampled ellipse outline)
    # fast on files with millions of events.
    flat_x = x.ravel()
    flat_y = y.ravel()
    candidate = (
        (flat_x >= float(px.min())) & (flat_x <= float(px.max()))
        & (flat_y >= float(py.min())) & (flat_y <= float(py.max()))
    )
    if not candidate.any():
        return inside
    cx = flat_x[candidate]
    cy = flat_y[candidate]
    result = np.zeros(cx.shape, dtype=bool)
    for i in range(n):
        xi, yi = px[i], py[i]
        xj, yj = px[(i + 1) % n], py[(i + 1) % n]
        cond = (yi > cy) != (yj > cy)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (xj - xi) * (cy - yi) / (yj - yi) + xi
        result ^= cond & (cx < x_cross)
    flat_inside = np.zeros(flat_x.shape, dtype=bool)
    flat_inside[candidate] = result
    return flat_inside.reshape(x.shape)


def _gate_mask(x, y, gate):
    """Return a boolean mask for the points inside `gate` (in data space)."""
    import numpy as np
    t = gate.get("type")
    if t == "rect":
        x0, y0 = gate["pos"]
        w, h = gate["size"]
        return (x >= x0) & (x <= x0 + w) & (y >= y0) & (y <= y0 + h)
    if t == "ellipse":
        points = gate.get("points")
        if points and len(points) >= 3:
            # The stored outline is the inverse image of the drawn ellipse;
            # testing the polygon keeps the selection identical to the shape
            # on screen even on a non-linear axis.
            return _point_in_polygon(x, y, points)
        x0, y0 = gate["pos"]
        w, h = gate["size"]
        cx = x0 + w / 2.0
        cy = y0 + h / 2.0
        rx = w / 2.0
        ry = h / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0
    if t == "polygon":
        return _point_in_polygon(x, y, gate["points"])
    if t == "quadrant":
        vx = gate.get("vx", 0.0)
        hy = gate.get("hy", 0.0)
        q = gate.get("quadrant", 1)
        if q == 1:
            return (x >= vx) & (y >= hy)
        if q == 2:
            return (x < vx) & (y >= hy)
        if q == 3:
            return (x < vx) & (y < hy)
        if q == 4:
            return (x >= vx) & (y < hy)
    if t == "range":
        x1 = min(gate["x1"], gate["x2"])
        x2 = max(gate["x1"], gate["x2"])
        return (x >= x1) & (x <= x2)
    if t == "categorical":
        # Col2Gate: the point's `x` (the chosen column) equals a category value.
        return x == gate.get("value")
    if t == "split":
        vx = gate.get("vx", 0.0)
        if gate.get("side") == "left":
            return x < vx
        return x >= vx
    if t == "skewed_quadrant":
        cx, cy = gate["center"]
        vx = x - cx
        vy = y - cy
        dirs = []
        for key in ("p_right", "p_up", "p_left", "p_down"):
            px, py = gate[key]
            dirs.append((px - cx, py - cy))
        d_r, d_u, d_l, d_dn = dirs

        def wedge(a, b):
            # a point is in the wedge from arm a to arm b (counter-clockwise)
            return (a[0] * vy - a[1] * vx >= 0) & (vx * b[1] - vy * b[0] >= 0)

        q = gate.get("quadrant", 1)
        if q == 1:
            return wedge(d_r, d_u)
        if q == 2:
            return wedge(d_u, d_l)
        if q == 3:
            return wedge(d_l, d_dn)
        if q == 4:
            return wedge(d_dn, d_r)
    return np.ones(x.shape, dtype=bool)


def _gate_source_ids(gate):
    """Return the ids of the source gates a boolean gate depends on."""
    if gate.get("type") == "not":
        s = gate.get("source")
        return [s] if s else []
    if gate.get("type") in ("and", "or"):
        return list(gate.get("sources", []))
    return []


def _gate_group_id(gate):
    """The id whose geometry is shared across a group of gates.

    Quadrant / split / skewed-quadrant gates come in groups bound to one
    crosshair / line / ray set; their geometry is shared, so per-file
    overrides are stored once per group instead of once per sub-gate.
    """
    for key in ("crosshair_id", "split_id", "skewed_id"):
        if gate.get(key):
            return gate[key]
    return gate.get("id")


def _gate_geometry(gate):
    """The editable geometry fields of a gate (shape + position only)."""
    keys = _GATE_GEOMETRY_KEYS.get(gate.get("type"), ())
    out = {}
    for key in keys:
        value = gate.get(key)
        if value is not None:
            out[key] = copy.deepcopy(value)
    return out


def _merge_gate_geometry(gate, geometry):
    """Return a shallow copy of `gate` with `geometry` fields overlaid."""
    out = dict(gate)
    for key, value in (geometry or {}).items():
        out[key] = copy.deepcopy(value)
    return out


def _resolve_gates_for_file(gates, per_file, file_key):
    """Return a copy of `gates` with the given file's overrides merged in.

    `per_file` maps a group id to ``{file_key: geometry}``. Files without an
    override fall back to the shared geometry, so only divergent files are
    stored.
    """
    if not per_file or not file_key:
        return list(gates)
    resolved = []
    for gate in gates:
        group = _gate_group_id(gate)
        override = (per_file.get(group) or {}).get(file_key)
        resolved.append(_merge_gate_geometry(gate, override) if override else dict(gate))
    return resolved


def _gate_needed_columns(gate, by_id, _seen=None):
    """Return the x/y column names needed to compute a gate's own mask.

    Boolean sources are followed recursively, so ``NOT (A OR B)`` also pulls
    in the columns of A and B.
    """
    if _seen is None:
        _seen = set()
    gid = gate.get("id")
    if gid is not None:
        if gid in _seen:
            return set()
        _seen.add(gid)
    cols = set()
    for key in ("x", "y"):
        v = gate.get(key)
        if v:
            cols.add(v)
    for sid in _gate_source_ids(gate):
        src = by_id.get(sid)
        if src is not None:
            cols |= _gate_needed_columns(src, by_id, _seen)
    return cols


def _gate_own_mask(gate, by_id, get_col, transforms, existing, _visiting=None):
    """Return a gate's own boolean mask (transformed data), handling boolean
    gates (NOT / AND / OR). Boolean gates may reference other boolean gates,
    for example ``NOT (A OR B)``."""
    kind = gate.get("type")
    if kind not in ("not", "and", "or"):
        x_name, y_name = gate.get("x"), gate.get("y")
        if not x_name or x_name not in existing or (
            y_name and y_name not in existing
        ):
            return None
        x = _apply_scale_transforms(get_col(x_name), transforms.get(x_name))
        y = (
            _apply_scale_transforms(get_col(y_name), transforms.get(y_name))
            if y_name else None
        )
        return _gate_mask(x, y, gate)

    if _visiting is None:
        _visiting = set()
    gid = gate.get("id")
    if gid in _visiting:
        return None
    visiting = _visiting | {gid} if gid is not None else _visiting

    def source_mask(source):
        if source is None:
            return None
        if source.get("type") in ("not", "and", "or"):
            return _gate_own_mask(
                source, by_id, get_col, transforms, existing, visiting
            )
        sx, sy = source.get("x"), source.get("y")
        if not sx or sx not in existing or (sy and sy not in existing):
            return None
        x = _apply_scale_transforms(get_col(sx), transforms.get(sx))
        y = (
            _apply_scale_transforms(get_col(sy), transforms.get(sy))
            if sy else None
        )
        return _gate_mask(x, y, source)

    if kind == "not":
        mask = source_mask(by_id.get(gate.get("source")))
        return None if mask is None else ~mask
    combine_and = kind == "and"
    result = None
    for sid in gate.get("sources", []):
        sm = source_mask(by_id.get(sid))
        if sm is None:
            continue
        if result is None:
            result = sm
        elif combine_and:
            result = result & sm
        else:
            result = result | sm
    return result
    x_name, y_name = gate.get("x"), gate.get("y")
    if not x_name or x_name not in existing or (y_name and y_name not in existing):
        return None
    x = _apply_scale_transforms(get_col(x_name), transforms.get(x_name))
    y = _apply_scale_transforms(get_col(y_name), transforms.get(y_name)) if y_name else None
    return _gate_mask(x, y, gate)


def _compute_gate_filter_mask(gates, gate_id, parquet_path, transforms,
                              existing=None, extra=None):
    """Return the AND mask of a gate and all its ancestors (transformed data).

    `existing` lists the usable column names and `extra` supplies values for
    columns that are computed on the fly (unmixed parameters). Returns None if
    the gate cannot be computed (no x/y columns). Raises KeyError if a needed
    column is missing.
    """
    import numpy as np
    import pandas as pd
    by_id = {g["id"]: g for g in gates}
    chain = []
    cur = by_id.get(gate_id)
    while cur is not None:
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    chain.reverse()
    if not chain:
        return None
    needed = set()
    for g in chain:
        needed |= _gate_needed_columns(g, by_id)
    if not needed:
        return None
    extra = extra or {}
    if existing is None:
        base_cols = [c for c in sorted(needed) if c not in extra]
        df = pd.read_parquet(parquet_path, columns=base_cols)
        existing = set(df.columns) | set(extra)
    else:
        existing = set(existing)
        base_cols = [
            c for c in sorted(needed) if c in existing and c not in extra
        ]
        df = (
            pd.read_parquet(parquet_path, columns=base_cols)
            if base_cols else pd.DataFrame()
        )
    cache = {}

    def get_col(name):
        if name not in cache:
            if name in extra:
                cache[name] = np.asarray(extra[name], dtype="float64")
            else:
                cache[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).to_numpy(dtype="float64")
        return cache[name]

    mask = None
    for g in chain:
        gm = _gate_own_mask(g, by_id, get_col, transforms, existing)
        if gm is None:
            continue
        mask = gm if mask is None else (mask & gm)
    return mask


def _gate_chain(gates, gate_id):
    """Return the gates from root down to `gate_id` (inclusive), in order."""
    by_id = {g["id"]: g for g in gates}
    chain = []
    cur = by_id.get(gate_id)
    while cur is not None:
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    chain.reverse()
    return chain
