"""Subsample allocation and stable row selection.

No Qt and no project state: sizes and parameters go in, row counts/indices
come out.
"""
from __future__ import annotations


def _subsample_allocate(params, sizes) -> dict:
    """Points to keep per file for one Subsample module.

    `sizes` maps every selected path to its row count as seen when it reaches
    the module. An unset or zero count means "not configured yet" and returns
    nothing to sample. A manual override wins over the computed value and is
    clamped to the file's own size.
    """
    basis = params.get("basis") or ""
    unit = params.get("unit") or "points"
    try:
        count = int(params.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        percent = float(params.get("percent") or 0.0)
    except (TypeError, ValueError):
        percent = 0.0
    files = [p for p in (params.get("files") or []) if p in sizes]
    out = {}
    if basis == "per_file" and unit == "percent":
        # Percentage per file: the count itself is filled in per file (and
        # stays editable in the table).
        if percent > 0.0 and files:
            for path in files:
                k = int(sizes[path] * percent / 100.0)
                out[path] = min(k, sizes[path])
    elif count > 0 and files:
        if basis == "per_file":
            for path in files:
                out[path] = min(count, sizes[path])
        elif (params.get("allocation") or "uniform") == "weighted":
            total = sum(sizes[p] for p in files)
            for path in files:
                share = int(count * sizes[path] // total) if total else 0
                out[path] = min(share, sizes[path])
        elif basis == "total":
            share = count // len(files)
            for path in files:
                out[path] = min(share, sizes[path])
    for path, value in (params.get("overrides") or {}).items():
        if path not in out:
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        out[path] = max(0, min(value, sizes[path]))
    return out


def _row_priorities(indices, seed: int = 42):
    """A deterministic pseudo-random priority in [0, 1) per row index."""
    import numpy as np

    x = np.asarray(indices, dtype=np.uint64) + np.uint64(int(seed) & 0xFFFFFFFF)
    with np.errstate(over="ignore"):
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    return (x >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)


def _stable_sample_selection(keep, max_points: int, seed: int = 42):
    """Boolean mask (over ``values[keep]``) of the rows to draw.

    Every row gets a priority from its *absolute* row index, so a gate that
    lets different rows through only adds/removes the rows near the cut-off
    instead of re-drawing the whole cloud (what ``rng.choice`` does). Returns
    ``None`` when every visible row fits.

    Priorities are computed in chunks, so a file with tens of millions of
    rows never needs a full-size float array.
    """
    import numpy as np

    count = int(np.count_nonzero(keep))
    if count <= max_points:
        return None
    selection = np.zeros(count, dtype=bool)
    chunk = max(1_000_000, int(max_points) * 8)
    best_priority = np.empty(0, dtype=np.uint64)
    best_position = np.empty(0, dtype=np.int64)
    position = 0
    for start in range(0, keep.size, chunk):
        block = keep[start:start + chunk]
        inside = np.flatnonzero(block)
        if inside.size == 0:
            continue
        priority = _row_priorities(inside + start, seed)
        # Visible rows are contiguous in the compressed array.
        compressed = np.arange(position, position + inside.size)
        position += inside.size
        if best_priority.size:
            priority = np.concatenate([best_priority, priority])
            compressed = np.concatenate([best_position, compressed])
        if priority.size > max_points:
            cut = np.argpartition(priority, max_points - 1)[:max_points]
            priority, compressed = priority[cut], compressed[cut]
        best_priority, best_position = priority, compressed
    if best_position.size > max_points:
        cut = np.argpartition(best_priority, max_points - 1)[:max_points]
        best_position = best_position[cut]
    selection[best_position] = True
    return selection
