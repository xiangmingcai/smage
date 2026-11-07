"""Subsample row indices and signatures.

Qt-free: reads file sizes and upstream module params through the context.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from smage.core.feature import _feature_safe_stem
from smage.core.sampling import _subsample_allocate


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


def _subsample_seed(ctx, task_id, parquet_path) -> int:
    """Stable per-(module, file) seed: same data samples the same rows.

    Derived from the ids only (never Python's `hash()`), so the selection
    does not change between sessions or when other files are added.
    """
    payload = f"smage-subsample-v1|{task_id}|{parquet_path}"
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
            p: ctx._subsample_visible_count(task.get("id"), p)
            for p in files
        }
        k = _subsample_allocate(params, sizes).get(path)
        if k is None or k >= n:
            continue
        if k <= 0:
            index = (index[:0] if index is not None
                     else np.zeros(0, dtype=np.int64))
            continue
        rng = np.random.default_rng(ctx._subsample_seed(task.get("id"), path))
        keep = np.sort(rng.permutation(n)[:k]).astype(np.int64)
        index = keep if index is None else index[keep]
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
        parts.append({
            "task": task.get("id"),
            "basis": params.get("basis"),
            "count": params.get("count"),
            "allocation": params.get("allocation"),
            "override": (params.get("overrides") or {}).get(path),
        })
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
        p: ctx._subsample_visible_count(task.get("id"), p) for p in files
    }
    n = sizes.get(path, 0)
    k = _subsample_allocate(params, sizes).get(path)
    if k is None or n <= 0 or k >= n:
        return None
    base = ctx._subsample_row_index(task.get("id"), path)
    if k <= 0:
        sel = np.zeros(0, dtype=np.int64)
    else:
        rng = np.random.default_rng(ctx._subsample_seed(task.get("id"), path))
        local = np.sort(rng.permutation(n)[:k]).astype(np.int64)
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
    key = hashlib.sha1(str(parquet_path).encode("utf-8")).hexdigest()[:16]
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
    return {
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
