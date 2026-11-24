"""Calculation module: user-defined rows that produce new channels.

No Qt: a module's rows are plain dicts and the values are arrays.  A saved
module contributes one ``("calculation", stage)`` entry to the map pipeline;
the module page also builds a draft stage from unsaved rows for its preview.

Rows reference earlier rows by their stable ``id`` (never by display name),
so renaming a channel cannot break the chain.  A row may reference only
rows that appear above it; the page enforces this and the evaluator simply
skips a row whose input is not available yet.
"""
from __future__ import annotations

import hashlib
import json
import math

CALC_TYPES = ("clip", "scale", "add", "contrast")
SCALE_METHODS = ("log10", "arcsinh")
_CONTRAST_LUT_SIZE = 4096


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _anchor_points(anchors):
    """Normalise contrast anchors: sorted, clamped, with both endpoints."""
    points = []
    for pair in anchors or []:
        try:
            x = float(pair[0])
            y = float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        points.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
    if not points:
        return [(0.0, 0.0), (1.0, 1.0)]
    points.sort(key=lambda item: item[0])
    # One point per x position (the last value wins).
    unique = []
    for x, y in points:
        if unique and abs(unique[-1][0] - x) < 1e-9:
            unique[-1] = (x, y)
        else:
            unique.append((x, y))
    if len(unique) < 2:
        return [(0.0, 0.0), (1.0, 1.0)]
    if unique[0][0] > 0.0:
        unique.insert(0, (0.0, unique[0][1]))
    if unique[-1][0] < 1.0:
        unique.append((1.0, unique[-1][1]))
    return unique


def _curve_lut(anchors, size: int = _CONTRAST_LUT_SIZE):
    """Sample the contrast curve into a normalized [0, 1] lookup table."""
    import numpy as np

    points = _anchor_points(anchors)
    xs = np.asarray([p[0] for p in points], dtype="float64")
    ys = np.asarray([p[1] for p in points], dtype="float64")
    grid = np.linspace(0.0, 1.0, max(2, int(size)))
    if xs.size < 2 or xs[-1] <= xs[0]:
        return grid.astype("float32")
    try:
        from scipy.interpolate import CubicSpline

        spline = CubicSpline(xs, ys, extrapolate=False)
        lut = np.asarray(spline(grid), dtype="float64")
        if not np.all(np.isfinite(lut)):
            lut = np.interp(grid, xs, ys)
    except Exception:  # noqa: BLE001 - duplicate x / tiny anchor sets
        lut = np.interp(grid, xs, ys)
    return np.clip(lut, 0.0, 1.0).astype("float32")


def _source_name(source, by_id):
    """Resolve an input reference to a column name (or None)."""
    if not isinstance(source, dict):
        return None
    row_id = source.get("row")
    if row_id:
        row = by_id.get(str(row_id))
        return str(row.get("output")) if row else None
    name = source.get("column")
    return str(name) if name else None


def _calculation_stage(params, path_key=None) -> dict | None:
    """Compile a Calculation module's params into a pipeline stage.

    Invalid or incomplete rows are skipped (the page prevents saving them,
    but a draft preview may still contain a row that is being edited).
    """
    raw_rows = (params or {}).get("rows") or []
    if not raw_rows:
        return None
    by_id = {
        str(row.get("id")): row
        for row in raw_rows
        if isinstance(row, dict) and row.get("id")
    }
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        row_id = str(raw.get("id") or "")
        output = str(raw.get("output") or "").strip()
        kind = str(raw.get("type") or "")
        if not row_id or not output or kind not in CALC_TYPES:
            continue
        files = {
            str(path) for path in (raw.get("files") or []) if path
        }
        if not files:
            continue

        if kind == "add":
            terms = []
            for term in raw.get("terms") or []:
                if not isinstance(term, dict):
                    continue
                name = _source_name(term.get("source"), by_id)
                if not name:
                    continue
                try:
                    weight = float(term.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                if not math.isfinite(weight):
                    weight = 1.0
                terms.append({"source": name, "weight": weight})
            if not terms:
                continue
            sources = [term["source"] for term in terms]
            payload = {"terms": terms}
        else:
            name = _source_name(raw.get("source"), by_id)
            if not name:
                continue
            sources = [name]
            if kind == "clip":
                try:
                    low = float(raw.get("min"))
                    high = float(raw.get("max"))
                except (TypeError, ValueError):
                    continue
                if not (math.isfinite(low) and math.isfinite(high)):
                    continue
                if low > high:
                    low, high = high, low
                if low == high:
                    continue
                payload = {"min": low, "max": high}
            elif kind == "scale":
                method = str(raw.get("method") or "log10").lower()
                if method not in SCALE_METHODS:
                    method = "log10"
                try:
                    cofactor = float(raw.get("cofactor") or 5.0)
                except (TypeError, ValueError):
                    cofactor = 5.0
                if not math.isfinite(cofactor) or cofactor <= 0.0:
                    cofactor = 5.0
                payload = {"method": method, "cofactor": cofactor}
            else:  # contrast
                domain = raw.get("domain") or {}
                try:
                    low = float(domain.get("min"))
                    high = float(domain.get("max"))
                except (TypeError, ValueError):
                    continue
                if not (math.isfinite(low) and math.isfinite(high)):
                    continue
                if high <= low:
                    continue
                anchors = _anchor_points(raw.get("anchors"))
                payload = {
                    "domain_min": low,
                    "domain_max": high,
                    "anchors": [list(p) for p in anchors],
                    "lut": _curve_lut(anchors),
                }

        rows.append({
            "id": row_id,
            "type": kind,
            "output": output,
            "visible": bool(raw.get("visible", True)),
            "files": files,
            "sources": sources,
            "payload": payload,
        })

    if not rows:
        return None
    signature_data = [
        [
            row["id"], row["type"], row["output"], row["visible"],
            sorted(
                path_key(item) if path_key else str(item)
                for item in row["files"]
            ),
            row["sources"],
            [
                [term["source"], round(float(term["weight"]), 9)]
                for term in row["payload"].get("terms", [])
            ],
            row["payload"].get("min"),
            row["payload"].get("max"),
            row["payload"].get("method"),
            row["payload"].get("cofactor"),
            row["payload"].get("domain_min"),
            row["payload"].get("domain_max"),
            row["payload"].get("anchors"),
        ]
        for row in rows
    ]
    signature = hashlib.sha1(
        json.dumps(signature_data, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return {"task_id": "", "rows": rows, "signature": signature}


def _calculation_dependencies(stage, names) -> set:
    """Columns needed before the stage to obtain ``names`` after it."""
    if not stage:
        return set(names)
    outputs = {row["output"]: row for row in stage.get("rows") or []}
    needed = set(names)
    pending = [name for name in needed if name in outputs]
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        row = outputs.get(name)
        if row is None:
            continue
        for source in row.get("sources") or ():
            if source not in needed:
                needed.add(source)
            if source in outputs and source not in seen:
                pending.append(source)
    return needed - set(outputs)


def _calculation_outputs(stage, parquet_path=None, visible_only: bool = True):
    """New channel names the stage exposes (optionally for one file)."""
    names = []
    for row in (stage or {}).get("rows") or []:
        if visible_only and not row.get("visible", True):
            continue
        files = row.get("files") or set()
        if parquet_path is not None and files and parquet_path not in files:
            continue
        names.append(row["output"])
    return names


def _apply_calculation(stage, values, parquet_path=None) -> None:
    """Apply the rows in order, adding their outputs to ``values``."""
    import numpy as np

    from smage.core.scale_math import _apply_scale_transform

    for row in (stage or {}).get("rows") or []:
        files = row.get("files") or set()
        if parquet_path is not None and files and parquet_path not in files:
            continue
        sources = [values.get(name) for name in row.get("sources") or ()]
        if not sources or any(source is None for source in sources):
            continue
        kind = row["type"]
        payload = row["payload"]
        if kind == "clip":
            out = np.clip(sources[0], payload["min"], payload["max"])
        elif kind == "scale":
            out = _apply_scale_transform(
                sources[0],
                {"method": payload["method"], "param": payload["cofactor"]},
            )
        elif kind == "add":
            out = None
            for term, source in zip(payload["terms"], sources):
                weight = float(term.get("weight", 1.0))
                if weight == 0.0:
                    continue
                part = source * weight
                out = part if out is None else out + part
            if out is None:
                out = np.zeros_like(sources[0], dtype="float64")
        else:  # contrast
            raw = np.asarray(sources[0], dtype="float64")
            low = float(payload["domain_min"])
            high = float(payload["domain_max"])
            span = high - low
            if not math.isfinite(span) or span <= 0.0:
                continue
            lut = payload["lut"]
            x = np.clip((raw - low) / span, 0.0, 1.0)
            pos = x * (lut.size - 1)
            lower = np.floor(pos).astype(np.int64)
            upper = np.minimum(lower + 1, lut.size - 1)
            frac = pos - lower
            y = lut[lower] * (1.0 - frac) + lut[upper] * frac
            out = low + y * span
            out = np.where(np.isfinite(raw), out, np.nan)
        values[row["output"]] = out
