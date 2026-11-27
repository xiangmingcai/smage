"""Subsample row indices and signatures.

Qt-free: reads file sizes and upstream module params through the context.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from smage.core.feature import _feature_safe_stem
from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_needed_columns,
)
from smage.core.sampling import (
    _stable_sample_selection,
    _subsample_allocate,
)
from smage.data import project_paths as _project_paths
from smage.runlog import runlog


def _subsample_tasks(ctx, task_id) -> list:
    """Subsample tasks above `task_id`, top-down (never the task itself)."""
    if not task_id or ctx.project is None:
        return []
    return [
        task for task in ctx._task_chain(task_id)
        if task.get("id") != task_id and task.get("type") == "subsample"
    ]


def _subsample_params_for(ctx, task_def) -> dict:
    """Saved params of a Subsample task (its live snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "subsample":
        snap = getattr(reg["page"], "_subs_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _subsample_gate_filters(params) -> list:
    """Normalised gate filters of a Subsample module's parameters."""
    out = []
    for entry in (params or {}).get("gate_filters") or []:
        if not isinstance(entry, dict):
            continue
        task_id = entry.get("task_id")
        gate_id = entry.get("gate_id")
        if task_id and gate_id:
            out.append({"task_id": task_id, "gate_id": gate_id})
    return out


def _subsample_gate_chain(ctx, gating_task, parquet_path, gate_id) -> list:
    """A gate plus its ancestors, as resolved for one file."""
    from smage.data import stores as _data_stores

    gates = _data_stores._gating_gates_for(ctx, gating_task, parquet_path)
    by_id = {g.get("id"): g for g in gates}
    chain = []
    seen = set()
    cur = by_id.get(gate_id)
    while cur is not None and cur.get("id") not in seen:
        seen.add(cur.get("id"))
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    chain.reverse()
    return chain


def _subsample_filter_options(ctx, task_id, files) -> list:
    """Gates that can be used as a Subsample filter, as ``(label, entry)``.

    A gate qualifies when every selected file is part of its Gating module
    and has the columns the gate (and its ancestors) need. This is the same
    "common gates" rule the page shows in the picker.
    """
    from smage.data import columns as _data_columns
    from smage.data import stores as _data_stores

    files = [str(p) for p in (files or []) if p]
    if not files:
        return []
    options = []
    labels = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "gating":
            continue
        gates = _data_stores._gating_gates_for(ctx, task)
        if not gates:
            continue
        by_id = {g.get("id"): g for g in gates}
        per_file = {}
        for path in files:
            resolved = _data_stores._gating_gates_for(ctx, task, path)
            ids = {g.get("id") for g in resolved}
            columns = set(
                _data_columns._columns_for(ctx, path, task.get("id"))
            )
            per_file[path] = (ids, columns)
        module_name = task.get("name") or "Gating"
        for gate in gates:
            gate_id = gate.get("id")
            if not gate_id:
                continue
            needed = _gate_needed_columns(gate, by_id)
            if not all(
                gate_id in per_file[path][0]
                and not (needed - per_file[path][1])
                for path in files
            ):
                continue
            label = f"{module_name} → {gate.get('name', gate_id)}"
            count = labels.get(label, 0) + 1
            labels[label] = count
            if count > 1:
                label = f"{label} ({count})"
            options.append((
                label,
                {"task_id": task.get("id"), "gate_id": gate_id},
            ))
    return options


def _subsample_filter_sig(ctx, params, parquet_path) -> str:
    """Fingerprint of the selected gates' geometry for one file.

    Gate edits are stored in the Gating module; hashing the resolved gate
    chains makes saved sampling masks and generated outputs stale when a
    gate is moved, renamed or deleted.
    """
    filters = _subsample_gate_filters(params)
    if not filters:
        return ""
    path = str(parquet_path)
    revision = getattr(ctx, "_stages_revision", 0)
    key = (
        path,
        revision,
        tuple((f["task_id"], f["gate_id"]) for f in filters),
    )
    cache = getattr(ctx, "_subsample_filter_sig_cache", None)
    if cache is None:
        cache = {}
        ctx._subsample_filter_sig_cache = cache
    if key in cache:
        return cache[key]
    parts = []
    for entry in filters:
        task = ctx._find_task_by_id(entry.get("task_id"))
        if task is None:
            parts.append({"task": entry["task_id"],
                          "gate": entry["gate_id"], "missing": True})
            continue
        parts.append({
            "task": entry["task_id"],
            "gate": entry["gate_id"],
            "gates": _subsample_gate_chain(
                ctx, task, path, entry["gate_id"]
            ),
        })
    sig = hashlib.sha1(
        json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    if len(cache) > 64:
        cache.clear()
    cache[key] = sig
    return sig


def _subsample_gate_mask_for(ctx, entry, parquet_path, this_rows, n):
    """One selected gate's mask, aligned with this module's visible rows."""
    import numpy as np

    from smage.data import columns as _data_columns
    from smage.data import stores as _data_stores

    path = str(parquet_path)
    gating_task = ctx._find_task_by_id(entry.get("task_id"))
    if gating_task is None:
        return None
    gates = _data_stores._gating_gates_for(ctx, gating_task, path)
    if not gates:
        return None
    by_id = {g.get("id"): g for g in gates}
    gates_for = _subsample_gate_chain(
        ctx, gating_task, path, entry.get("gate_id")
    )
    if not gates_for:
        return None
    needed = set()
    for gate in gates_for:
        needed |= _gate_needed_columns(gate, by_id)
    if not needed:
        return None
    try:
        values = _data_columns._column_data(
            ctx, gating_task.get("id"), path, sorted(needed)
        )
    except Exception as error:  # noqa: BLE001 - a broken gate must not break sampling
        runlog.error("subsample.gate_error", exc=error)
        return None
    if needed - set(values):
        return None
    try:
        mask = _compute_gate_filter_mask(
            gates, entry.get("gate_id"), path, {},
            existing=set(values), extra=values,
        )
    except Exception as error:  # noqa: BLE001
        runlog.error("subsample.gate_error", exc=error)
        return None
    if mask is None:
        return None
    mask = np.asarray(mask, dtype=bool)

    gating_rows = ctx._subsample_row_index(gating_task.get("id"), path)
    if gating_rows is None:
        if this_rows is None:
            return mask if mask.size == n else None
        full_n = ctx._data_row_count(path)
        if mask.size != full_n:
            return None
        return mask[np.asarray(this_rows, dtype=np.int64)]
    if mask.size != len(gating_rows):
        return None
    if this_rows is None:
        target = np.arange(ctx._data_row_count(path), dtype=np.int64)
    else:
        target = np.asarray(this_rows, dtype=np.int64)
    if target.size == 0:
        return np.zeros(0, dtype=bool)
    if len(gating_rows) == 0:
        return np.zeros(target.size, dtype=bool)
    pos = np.searchsorted(gating_rows, target)
    clipped = np.minimum(pos, len(gating_rows) - 1)
    valid = (pos < len(gating_rows)) & (gating_rows[clipped] == target)
    out = np.zeros(target.size, dtype=bool)
    if valid.any():
        out[valid] = mask[pos[valid]]
    return out


def _subsample_task_eligible_mask(ctx, task_def, parquet_path, params=None):
    """Union of the selected gates over the rows this module can see.

    Returns ``None`` when no usable gate filter is configured, so callers
    keep the old "no filtering" behaviour. Rows outside the selected gates
    are hidden downstream (and from the coverage mask).
    """
    import numpy as np

    path = str(parquet_path)
    if params is None:
        params = _subsample_params_for(ctx, task_def)
    filters = _subsample_gate_filters(params)
    if not filters:
        return None
    task_id = task_def.get("id")
    rows = ctx._subsample_row_index(task_id, path)
    n = len(rows) if rows is not None else ctx._data_row_count(path)
    if n <= 0:
        return np.zeros(0, dtype=bool)
    union = np.zeros(n, dtype=bool)
    usable = False
    for entry in filters:
        mask = _subsample_gate_mask_for(ctx, entry, path, rows, n)
        if mask is None:
            runlog.warn(
                "subsample.gate_skipped",
                file=path,
                task=entry.get("task_id"),
                gate=entry.get("gate_id"),
                why="gate or its columns are unavailable",
            )
            continue
        usable = True
        union |= mask
    return union if usable else None


def _subsample_count_sig(ctx, task_def, parquet_path, params=None) -> str:
    """Fingerprint of everything the in-gate point count depends on.

    The target count and the allocation do not change how many points lie in
    the gates, so they are deliberately not part of this signature; changing
    the target therefore does not rescan every file.
    """
    path = str(parquet_path)
    if params is None:
        params = _subsample_params_for(ctx, task_def)
    try:
        st = Path(path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "task": task_def.get("id"),
        "upstream": _subsample_sig(ctx, task_def.get("id"), path),
        "stat": stat,
        "gates": _subsample_filter_sig(ctx, params, path),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _subsample_eligible_count(ctx, task_def, parquet_path, params=None) -> int:
    """Rows of a file that lie inside the selected gate union.

    Without gate filters this is simply the number of visible rows; with
    them it is read from the counts saved next to the module, so a Total
    allocation does not rescan every file.
    """
    path = str(parquet_path)
    if params is None:
        params = _subsample_params_for(ctx, task_def)
    if not _subsample_gate_filters(params):
        return _subsample_visible_count(ctx, task_def.get("id"), path)
    sig = _subsample_count_sig(ctx, task_def, path, params)
    entry = _subsample_load_counts(ctx, task_def.get("id")).get(path)
    if entry and entry.get("sig") == sig:
        try:
            return max(0, int(entry.get("count")))
        except (TypeError, ValueError):
            pass
    mask = _subsample_task_eligible_mask(ctx, task_def, path, params)
    if mask is None:
        return _subsample_visible_count(ctx, task_def.get("id"), path)
    return int(mask.sum())


def _subsample_counts_path(ctx, task_id):
    """Where a Subsample module caches its per-file gate counts."""
    if ctx.project is None or not task_id:
        return None
    folder = ctx._module_dir(task_id)
    return folder / "eligible.json" if folder is not None else None


def _subsample_load_counts(ctx, task_id) -> dict:
    """Read the saved per-file gate counts for one module."""
    path = _subsample_counts_path(ctx, task_id)
    if path is None or not path.is_file():
        return {}
    try:
        st = path.stat()
        key = (str(path), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return {}
    cache = getattr(ctx, "_subsample_counts_cache", None)
    if cache is None:
        cache = {}
        ctx._subsample_counts_cache = cache
    if key in cache:
        return cache[key]
    try:
        data = _project_paths._load_json(path, ctx.project.path)
    except (OSError, ValueError, TypeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    result = {
        str(k): v for k, v in (entries or {}).items() if isinstance(v, dict)
    }
    if len(cache) > 4:
        cache.clear()
    cache[key] = result
    return result


def _subsample_write_counts(ctx, task_def, counts) -> None:
    """Persist per-file gate counts for downstream Total allocations."""
    path = _subsample_counts_path(ctx, task_def.get("id"))
    if path is None:
        return
    entries = {}
    for parquet_path, count in (counts or {}).items():
        entries[str(parquet_path)] = {
            "sig": _subsample_count_sig(ctx, task_def, parquet_path),
            "count": int(count),
        }
    try:
        _project_paths._save_json(
            path, {"version": 1, "entries": entries}, ctx.project.path
        )
    except OSError as error:
        runlog.error("subsample.counts_write", exc=error)
        return
    ctx._subsample_counts_cache = {}


def _subsample_clear_counts(ctx, task_id) -> None:
    """Drop the saved gate counts when filters are removed."""
    path = _subsample_counts_path(ctx, task_id)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    ctx._subsample_counts_cache = {}


def _subsample_seed(ctx, task_id, parquet_path) -> int:
    """Stable per-(module, file) seed: same data samples the same rows.

    Derived from the ids only (never Python's `hash()`), so the selection
    does not change between sessions or when other files are added.
    """
    payload = (
        f"smage-subsample-v1|{task_id}|"
        f"{_project_paths._stable_path_key(ctx, parquet_path)}"
    )
    return int(hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16], 16)


def _subsample_row_index(ctx, task_id, parquet_path):
    """Row indices (into the full file) visible at `task_id`, or None.

    Layers the sampling of every upstream Subsample module that lists this
    file. Each module picks a fixed random permutation of its own visible
    rows, so a larger target keeps every row picked before.
    """
    import numpy as np

    if not task_id or not parquet_path or ctx.project is None:
        return None
    tasks = ctx._subsample_tasks(task_id)
    if not tasks:
        return None
    path = str(parquet_path)
    sig = ctx._subsample_sig(task_id, path)
    if not sig:
        return None
    cache = getattr(ctx, "_subsample_cache", None)
    if cache is None:
        cache = {}
        ctx._subsample_cache = cache
    key = (task_id, path, sig)
    if key in cache:
        return cache[key]
    index = None
    for task in tasks:
        params = ctx._subsample_params_for(task)
        files = [str(p) for p in (params.get("files") or [])]
        if path not in files:
            continue
        n = int(len(index)) if index is not None else ctx._data_row_count(path)
        if n <= 0:
            continue
        sizes = {
            p: _subsample_eligible_count(ctx, task, p) for p in files
        }
        k = _subsample_allocate(params, sizes).get(path)
        eligible = _subsample_task_eligible_mask(ctx, task, path)
        if eligible is not None:
            eligible = np.asarray(eligible, dtype=bool)
            if eligible.size != n:
                eligible = None
        if eligible is None:
            if k is None or k >= n:
                continue
            if k <= 0:
                index = (index[:0] if index is not None
                         else np.zeros(0, dtype=np.int64))
                continue
            rng = np.random.default_rng(
                ctx._subsample_seed(task.get("id"), path)
            )
            keep = np.sort(rng.permutation(n)[:k]).astype(np.int64)
            index = keep if index is None else index[keep]
            continue
        # A module without a sampling method has no effect, even when gates
        # are selected; the filter only defines the pool to sample from.
        if k is None:
            continue
        # Gate filter: rows outside the selected gates are hidden even when
        # the target count does not cut anything.
        positions = np.flatnonzero(eligible).astype(np.int64)
        if k <= 0:
            index = (index[:0] if index is not None
                     else np.zeros(0, dtype=np.int64))
            continue
        if k >= positions.size:
            local = positions
        else:
            pick = _stable_sample_selection(
                eligible, int(k),
                ctx._subsample_seed(task.get("id"), path),
            )
            local = positions if pick is None else positions[pick]
        index = local if index is None else index[local]
    if len(cache) > 16:
        cache.clear()
    cache[key] = index
    return index


def _subsample_sig(ctx, task_id, parquet_path) -> str:
    """Signature of every upstream sampling decision that affects a file."""
    path = str(parquet_path)
    parts = []
    for task in ctx._subsample_tasks(task_id):
        params = ctx._subsample_params_for(task)
        files = {str(p) for p in (params.get("files") or [])}
        if path not in files:
            continue
        part = {
            "task": task.get("id"),
            "basis": params.get("basis"),
            "count": params.get("count"),
            "allocation": params.get("allocation"),
            "override": (params.get("overrides") or {}).get(path),
        }
        gate_sig = _subsample_filter_sig(ctx, params, path)
        if gate_sig:
            # Only modules with a gate filter change their existing hash, so
            # projects without one keep their generated outputs valid.
            part["gates"] = gate_sig
        if (params.get("unit") or "points") == "percent":
            # Percentage mode carries its target in `percent`, not `count`.
            part["unit"] = "percent"
            part["percent"] = params.get("percent")
        if params.get("basis") == "total":
            # A total is split across the selected files, so their set and
            # in-gate sizes are part of the decision.
            part["files"] = sorted(
                _project_paths._stable_path_key(ctx, item)
                for item in files
            )
        parts.append(part)
    if not parts:
        return ""
    return hashlib.sha1(
        json.dumps(parts, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _subsample_visible_count(ctx, task_id, parquet_path) -> int:
    """Rows of a file as seen at `task_id` (after upstream sampling)."""
    index = ctx._subsample_row_index(task_id, parquet_path)
    if index is None:
        return ctx._data_row_count(parquet_path)
    return int(len(index))


def _subsample_cover_crop(ctx, task_id, parquet_path, manifest, bbox, spacing):
    """Sampled-pixel visibility over the visible bbox, shaped like the raster.

    The image renderer draws either a pyramid level (coarse/medium zoom) or
    raw pixels (deep zoom); each needs its own crop rule, otherwise the
    mask would silently drop out once the view switches to raw pixels.
    """
    import math
    import numpy as np

    ent = ctx._subsample_cover_ent(task_id, parquet_path, manifest)
    if ent is None:
        return None
    levels = manifest.get("levels", [])
    raw = (not levels) or (
        spacing < levels[0]["block"] and levels[0]["block"] > 1
    )
    if not raw:
        cover, _rect = ctx._image_mask_crop(manifest, bbox, spacing, ent)
        return cover
    mask = ent.get("mask_2d")
    if mask is None:
        return None
    x0, x1, y0, y1 = bbox
    i0 = int(math.floor(x0 - float(manifest["xmin"])))
    j0 = int(math.floor(y0 - float(manifest["ymin"])))
    nbx = max(1, int(math.ceil(x1 - x0)))
    nby = max(1, int(math.ceil(y1 - y0)))
    out = np.zeros((nby, nbx), dtype=np.float32)
    sx0, sy0 = max(0, i0), max(0, j0)
    sx1 = min(mask.shape[1], i0 + nbx)
    sy1 = min(mask.shape[0], j0 + nby)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - j0:sy1 - j0, sx0 - i0:sx1 - i0] = mask[sy0:sy1, sx0:sx1]
    return out


def _subsample_own_mask(ctx, task, parquet_path, manifest):
    """Dense (H, W) mask of the pixels THIS module keeps, or None.

    None means "this module hides nothing from this file". The mask covers
    only the module's own sampling: upstream sampling is already excluded
    from the rows it sees.
    """
    import numpy as np
    import pandas as pd

    path = str(parquet_path)
    params = ctx._subsample_params_for(task)
    files = [str(p) for p in (params.get("files") or [])]
    if path not in files:
        return None
    sizes = {
        p: _subsample_eligible_count(ctx, task, p) for p in files
    }
    n = ctx._subsample_visible_count(task.get("id"), path)
    k = _subsample_allocate(params, sizes).get(path)
    eligible = _subsample_task_eligible_mask(ctx, task, path)
    if eligible is not None:
        eligible = np.asarray(eligible, dtype=bool)
        if eligible.size != n:
            eligible = None
    if k is None:
        # No sampling method: this module hides nothing.
        return None
    if n <= 0:
        return None
    base = ctx._subsample_row_index(task.get("id"), path)
    if eligible is None:
        if k >= n:
            return None
        if k <= 0:
            sel = np.zeros(0, dtype=np.int64)
        else:
            rng = np.random.default_rng(
                ctx._subsample_seed(task.get("id"), path)
            )
            local = np.sort(rng.permutation(n)[:k]).astype(np.int64)
            sel = local if base is None else np.asarray(base)[local]
    else:
        positions = np.flatnonzero(eligible).astype(np.int64)
        if k <= 0:
            local = np.zeros(0, dtype=np.int64)
        elif k >= positions.size:
            local = positions
        else:
            pick = _stable_sample_selection(
                eligible, int(k),
                ctx._subsample_seed(task.get("id"), path),
            )
            local = positions if pick is None else positions[pick]
        sel = local if base is None else np.asarray(base)[local]
    xmin = float(manifest["xmin"])
    ymin = float(manifest["ymin"])
    width = int(round(float(manifest["xmax"]) - xmin)) + 1
    height = int(round(float(manifest["ymax"]) - ymin)) + 1
    try:
        df = pd.read_parquet(path, columns=["x", "y"])
    except Exception:  # noqa: BLE001
        return None
    xi = np.rint(df["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
    yi = np.rint(df["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
    ok = (sel >= 0) & (sel < len(xi))
    sel = sel[ok]
    mask = np.zeros((height, width), dtype=np.uint8)
    if sel.size:
        sx, sy = xi[sel], yi[sel]
        good = (sx >= 0) & (sx < width) & (sy >= 0) & (sy < height)
        mask[sy[good], sx[good]] = 1
    return mask


def _subsample_coverage_dir(ctx, task_id):
    """Folder holding a Subsample module's saved image coverage masks."""
    if ctx.project is None or not task_id:
        return None
    folder = ctx._module_dir(task_id)
    return folder / "coverage" if folder is not None else None


def _subsample_coverage_paths(ctx, task, parquet_path):
    """(mask .npy path, metadata .json path) for one module + file."""
    folder = ctx._subsample_coverage_dir(task.get("id"))
    if folder is None:
        return None, None
    stable = _project_paths._stable_path_key(ctx, parquet_path)
    key = hashlib.sha1(stable.encode("utf-8")).hexdigest()[:16]
    stem = f"{_feature_safe_stem(Path(str(parquet_path)).stem)}.{key}"
    return folder / f"{stem}.npy", folder / f"{stem}.json"


def _subsample_mask_meta(ctx, task, parquet_path, manifest) -> dict:
    """Fingerprint of everything a coverage mask depends on."""
    params = ctx._subsample_params_for(task)
    path = str(parquet_path)
    try:
        st = Path(path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    meta = {
        "version": 1,
        "stat": stat,
        "upstream": ctx._subsample_sig(task.get("id"), path),
        "basis": params.get("basis"),
        "count": params.get("count"),
        "allocation": params.get("allocation"),
        "override": (params.get("overrides") or {}).get(path),
        "files": sorted(str(p) for p in (params.get("files") or [])),
        "xmin": float(manifest["xmin"]),
        "ymin": float(manifest["ymin"]),
        "xmax": float(manifest["xmax"]),
        "ymax": float(manifest["ymax"]),
    }
    gate_sig = _subsample_filter_sig(ctx, params, path)
    if gate_sig:
        meta["gates"] = gate_sig
    if (params.get("unit") or "points") == "percent":
        meta["unit"] = "percent"
        meta["percent"] = params.get("percent")
    return meta


# ---------------------------------------------------------------------------
# Sampled parquet reads
#
# A parquet table is stored as a sequence of row groups.  Reading a small
# batch from a few evenly spread row groups gives a representative sample of
# the file with a fraction of the I/O and, more importantly, without a
# full-file in-memory copy.  Used by the tile statistics of the Illumination
# module and by the plot histograms next to its previews.
# ---------------------------------------------------------------------------


def parquet_column_stats(path, columns):
    """Exact ``(min, max)`` per column from the parquet metadata.

    Statistics are written by the importer and by the generate modules, so
    the range of a column is available without reading any row.  Columns
    without complete statistics are omitted.
    """
    import pyarrow.parquet as pq

    try:
        meta = pq.ParquetFile(str(path)).metadata
    except Exception:  # noqa: BLE001
        return {}
    names = list(meta.schema.names)
    out = {}
    for column in columns:
        if column not in names:
            continue
        index = names.index(column)
        lo = None
        hi = None
        complete = True
        for i in range(int(meta.num_row_groups or 0)):
            stats = meta.row_group(i).column(index).statistics
            if stats is None or not stats.has_min_max:
                complete = False
                break
            lo = stats.min if lo is None else min(lo, stats.min)
            hi = stats.max if hi is None else max(hi, stats.max)
        if complete and lo is not None:
            out[column] = (lo, hi)
    return out


def _sample_spread(pf, columns, total, max_rows, max_groups):
    """One small batch from each of several evenly spread row groups."""
    import numpy as np

    meta = pf.metadata
    groups = int(meta.num_row_groups or 0)
    count = max(1, min(groups, int(max_groups), max(1, int(max_rows) // 65536)))
    positions = np.unique(
        np.linspace(0, groups - 1, count).round().astype(int)
    )
    first_rows = int(meta.row_group(0).num_rows)
    per_group = max(1, min(int(max_rows) // max(1, len(positions)), first_rows))
    parts = []
    for index in positions:
        iterator = pf.iter_batches(
            batch_size=per_group, columns=columns,
            row_groups=[int(index)],
        )
        try:
            parts.append(next(iterator))
        except StopIteration:
            continue
        finally:
            iterator.close()
    if not parts:
        return {}
    return {
        column: np.concatenate([
            part.column(column).to_numpy(zero_copy_only=False)
            for part in parts
        ])
        for column in columns
    }


def _sample_strided(pf, columns, total, max_rows):
    """Every stride-th row, for files with too few row groups to spread."""
    import numpy as np
    import pyarrow as pa

    stride = max(1, int(total) // max(1, int(max_rows)))
    parts = {column: [] for column in columns}
    start = 0
    for batch in pf.iter_batches(batch_size=1_000_000, columns=columns):
        rows = batch.num_rows
        offset = (-start) % stride
        selection = np.arange(offset, rows, stride)
        if selection.size:
            indices = pa.array(selection)
            for column in columns:
                parts[column].append(
                    batch.column(column).take(indices)
                    .to_numpy(zero_copy_only=False)
                )
        start += rows
    return {
        column: (np.concatenate(chunks) if chunks else np.empty(0))
        for column, chunks in parts.items()
    }


def lattice_strides(width, height, block_w, block_h, budget,
                    max_per_axis=16):
    """Pixel strides that give every block ``k × k`` samples on a budget.

    ``block_w``/``block_h`` is the estimator's block size in pixels (the
    tile divided by the bin count).  Both axes are sampled: a stride above
    the block size would leave whole blocks without data, and a fixed
    lattice could alias a periodic pattern — the caller adds a per-tile
    phase offset for that reason.
    """
    import math

    total = max(1.0, float(width) * float(height))
    block_w = max(1.0, float(block_w))
    block_h = max(1.0, float(block_h))
    k = int(math.sqrt(
        max(1.0, float(budget)) * block_w * block_h / total
    ))
    k = max(1, min(k, int(min(block_w, block_h)), int(max_per_axis)))
    sx = max(1, int(round(block_w / k)))
    sy = max(1, int(round(block_h / k)))
    return sx, sy


def sample_image_blocks(path, columns, x_name, y_name, origin, tile_w,
                        tile_h, sx, sy):
    """Stratified pixel sample: every tile block gets x *and* y samples.

    The file is streamed once (the columns are read in full), and pixels are
    kept on a two-dimensional lattice with a per-tile phase offset::

        keep = (x - x0 - phase_x) % sx == 0 and (y - y0 - phase_y) % sy == 0

    where the phases come from a hash of the tile row/column, so consecutive
    tiles sample different positions inside their blocks instead of one
    global grid.  ``sx``/``sy`` below the block size guarantee that no block
    of a tile is empty; this is what prevents the stripes a banded row
    sample leaves in the illumination mask.
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = list(columns)
    try:
        pf = pq.ParquetFile(str(path))
    except Exception:  # noqa: BLE001
        return {}
    if x_name not in columns or y_name not in columns:
        return {}
    x0 = int(round(float(origin[0])))
    y0 = int(round(float(origin[1])))
    step_x = max(1, int(sx))
    step_y = max(1, int(sy))
    tile_w = max(1, int(tile_w))
    tile_h = max(1, int(tile_h))
    gx = np.uint64(0x9E3779B97F4A7C15)
    gy = np.uint64(0xC2B2AE3D27D4EB4F)
    parts = {column: [] for column in columns}
    for batch in pf.iter_batches(batch_size=1_000_000, columns=columns):
        x = np.asarray(
            batch.column(x_name).to_numpy(zero_copy_only=False)
        )
        y = np.asarray(
            batch.column(y_name).to_numpy(zero_copy_only=False)
        )
        if not np.issubdtype(x.dtype, np.integer) or not (
            np.issubdtype(y.dtype, np.integer)
        ):
            # Non-integer coordinates: no lattice, keep the batch as is.
            indices = np.arange(batch.num_rows)
        else:
            xi = x.astype(np.int64) - x0
            yi = y.astype(np.int64) - y0
            ti = np.floor_divide(xi, tile_w).astype(np.uint64)
            tj = np.floor_divide(yi, tile_h).astype(np.uint64)
            hashed = ti * gx ^ tj * gy
            phase_x = (hashed % np.uint64(step_x)).astype(np.int64)
            phase_y = (
                (hashed >> np.uint64(32)) % np.uint64(step_y)
            ).astype(np.int64)
            keep = (
                np.remainder(xi - phase_x, step_x) == 0
            ) & (
                np.remainder(yi - phase_y, step_y) == 0
            )
            indices = np.flatnonzero(keep)
        if indices.size == 0:
            continue
        selection = pa.array(indices)
        for column in columns:
            parts[column].append(
                batch.column(column).take(selection)
                .to_numpy(zero_copy_only=False)
            )
    return {
        column: (np.concatenate(chunks) if chunks else np.empty(0))
        for column, chunks in parts.items()
    }


def sample_parquet_rows(path, columns, max_rows=400_000, max_groups=32,
                        method="spread"):
    """Sample up to ``max_rows`` rows of ``columns`` from one parquet file.

    ``method="spread"`` reads one batch from each of several evenly spread
    row groups: cheap, and fine for a distribution (a histogram), but a
    batch covers only a contiguous slice of the rows, so it must not be used
    to estimate anything that varies inside an image tile.

    ``method="stride"`` scans the whole file and keeps every k-th row.  I/O
    is the complete column chunks, but memory stays bounded and every part
    of the file — every row of an image tile — is represented.  Used for the
    illumination pattern estimate, where a missing band of rows becomes a
    visible stripe in the mask.

    Values keep their stored dtypes; the caller decides which ones need to
    be converted.
    """
    import pyarrow.parquet as pq

    columns = list(dict.fromkeys(columns))
    if not columns:
        return {}
    try:
        pf = pq.ParquetFile(str(path))
    except Exception:  # noqa: BLE001
        return {}
    meta = pf.metadata
    total = int(meta.num_rows or 0)
    if total <= 0:
        return {}
    present = [c for c in columns if c in set(meta.schema.names)]
    if not present:
        return {}
    if total <= int(max_rows):
        table = pf.read(columns=present)
        return {
            column: table.column(column).to_numpy(zero_copy_only=False)
            for column in present
        }
    if method == "stride":
        return _sample_strided(pf, present, total, max_rows)
    if int(meta.num_row_groups or 0) >= 4:
        return _sample_spread(pf, present, total, max_rows, max_groups)
    return _sample_strided(pf, present, total, max_rows)
