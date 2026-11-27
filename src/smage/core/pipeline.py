"""Map-stage pipeline: declarations, dependency analysis and evaluation.

A "stage" is one (kind, payload) tuple produced by a map module (Scale,
Compensation, Unmix, Illumination, Batch Alignment, Rename). Evaluating the
stages turns a file's raw columns into the values a downstream module sees.

No Qt and no project state: callers pass the stages and values in.
"""
from __future__ import annotations

import os

from smage.core.calculation import (
    _apply_calculation,
    _calculation_dependencies,
)
from smage.core.scale_math import _apply_scale_transform


def _rename_applies(stage, parquet_path) -> bool:
    """Whether a rename stage applies to this file (a None/empty scope = all)."""
    files = stage.get("files")
    if not files:
        return True
    return parquet_path is None or parquet_path in files


def _rename_stage(params) -> dict | None:
    """The column-rename stage declared by a Rename module's params, or None.

    Only column-mode renames produce a stage; file-mode renames are applied to
    display names elsewhere. Invalid rows are already excluded from `rename`.
    """
    if (params or {}).get("mode", "file") != "column":
        return None
    column_cfg = params.get("column") or {}
    files = {str(p) for p in (column_cfg.get("files") or [])}
    mapping = {
        str(k): str(v)
        for k, v in (column_cfg.get("rename") or {}).items()
        if k and v and str(k) != str(v)
    }
    if not files or not mapping:
        return None
    return {"files": files, "map": mapping}


def _pipeline_touches(stages, name) -> bool:
    """Whether any map stage changes or produces `name`."""
    for kind, stage in stages:
        if kind == "scale" and name in stage["columns"]:
            return True
        if kind in ("compensation", "unmix") and name in stage["names"]:
            return True
        if kind == "offset" and name in (stage.get("columns") or {}):
            return True
        if kind == "illumination":
            for sub in stage.get("submodules", []):
                if sub.get("channel") == name:
                    return True
        if kind == "rename":
            if name in stage["map"] or name in stage["map"].values():
                return True
        if kind == "calculation":
            for row in stage.get("rows") or []:
                if row.get("output") == name:
                    return True
    return False


def _pipeline_dependencies(stages, names, parquet_path=None) -> set:
    """Columns needed before the stages to obtain `names` after them.

    Rename stages map a downstream name back to the source column that feeds
    it (only for the files the rename is scoped to).
    """
    needed = set(names)
    for kind, stage in reversed(stages):
        if kind == "compensation":
            if needed & set(stage["names"]):
                needed |= set(stage["names"])
        elif kind == "batch_align":
            # The correction is per cell and needs every aligned channel to
            # decide which cluster the cell belongs to.
            if needed & set(stage.get("columns") or []):
                needed |= set(stage["columns"])
        elif kind == "unmix":
            if (
                not stage.get("generated")
                and needed & set(stage["names"])
            ):
                needed |= set(stage["columns"])
        elif kind == "illumination":
            for sub in stage.get("submodules", []):
                if sub.get("channel") in needed:
                    if sub.get("x"):
                        needed.add(sub["x"])
                    if sub.get("y"):
                        needed.add(sub["y"])
        elif kind == "rename":
            if not _rename_applies(stage, parquet_path):
                continue
            reverse = {v: k for k, v in stage["map"].items()}
            needed = {reverse.get(n, n) for n in needed}
        elif kind == "calculation":
            # The requested name may be produced by this stage; replace it
            # with the columns it reads (an output is not a source column).
            needed = _calculation_dependencies(stage, needed)
    needed.discard(None)
    return needed


def _stages_after(stages, kind, task_id):
    """Stages that follow the given map stage (matched by kind and task id)."""
    out = []
    started = task_id is None
    for stage_kind, stage in stages:
        if started:
            out.append((stage_kind, stage))
        elif stage_kind == kind and stage.get("task_id") == task_id:
            started = True
    return out


def _linear_combine(sources, weights):
    """Combine arrays with a (outputs × sources) weight matrix.

    Works for 1D arrays and for 2D arrays (image rasters).
    """
    import numpy as np

    if not sources:
        return []
    if sources[0].ndim == 1:
        matrix = np.column_stack(sources)
        return [matrix @ weights[i] for i in range(weights.shape[0])]
    stacked = np.stack(sources, axis=0)
    combined = np.tensordot(weights, stacked, axes=(1, 0))
    return [combined[i] for i in range(combined.shape[0])]


_ILLUM_ORIGIN_CACHE = {}


def _illum_file_origin(path, x_name, y_name):
    """The (xmin, ymin) of an image file, from the parquet metadata.

    Position-dependent stages need the image origin, not the minimum of the
    rows they happen to see: a sampled or spatially cropped read would
    otherwise apply the pattern at a different phase — the correction would
    move with the view.  Row-group statistics carry the exact minimum without
    reading data.
    """
    if path is None or not x_name or not y_name:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (
        str(path), int(stat.st_size), float(stat.st_mtime), x_name, y_name,
    )
    if key in _ILLUM_ORIGIN_CACHE:
        return _ILLUM_ORIGIN_CACHE[key]
    origin = None
    try:
        from smage.data.sampling import parquet_column_stats

        stats = parquet_column_stats(path, [x_name, y_name])
        if x_name in stats and y_name in stats:
            origin = (float(stats[x_name][0]), float(stats[y_name][0]))
    except Exception:  # noqa: BLE001 - a missing origin falls back to the data
        origin = None
    if len(_ILLUM_ORIGIN_CACHE) > 256:
        _ILLUM_ORIGIN_CACHE.clear()
    _ILLUM_ORIGIN_CACHE[key] = origin
    return origin


def _illum_cell_factor(mask, tile_w, tile_h, threshold, cells, origin):
    """Illumination factor for raster cells that hold a mean over pixels.

    Each cell covers a window of the tiled pattern; the factor is taken from
    the pattern averaged over that window (a summed-area table of the tile),
    not from the single tile position of the cell's centre.  An image that
    the full-resolution correction would flatten stays flat at every zoom
    level, and the pattern keeps its absolute position because the window is
    found from the image origin.
    """
    import numpy as np

    try:
        x0 = np.asarray(cells.get("x0"), dtype="float64").reshape(-1)
        y0 = np.asarray(cells.get("y0"), dtype="float64").reshape(-1)
        cw = int(max(1, round(float(cells.get("w", 1.0)))))
        ch = int(max(1, round(float(cells.get("h", 1.0)))))
    except (TypeError, ValueError, AttributeError):
        return None
    if x0.size == 0 or y0.size == 0:
        return None
    # A window covering two whole tiles averages to the tile mean; cap it so
    # an extremely coarse zoom cannot allocate a huge support.
    cw = min(cw, 2 * tile_w)
    ch = min(ch, 2 * tile_h)
    origin_x, origin_y = float(origin[0]), float(origin[1])
    phase_x = np.mod(np.floor(x0 - origin_x).astype(np.int64), tile_w)
    phase_y = np.mod(np.floor(y0 - origin_y).astype(np.int64), tile_h)
    tile = np.asarray(mask, dtype="float64")[
        np.mod(np.arange(tile_h + ch), tile_h)[:, None],
        np.mod(np.arange(tile_w + cw), tile_w)[None, :],
    ]
    valid = np.isfinite(tile) & (tile > 0.0)
    sums = np.zeros((tile_h + ch + 1, tile_w + cw + 1), dtype="float64")
    sums[1:, 1:] = np.where(valid, tile, 0.0).cumsum(0).cumsum(1)
    counts = np.zeros_like(sums)
    counts[1:, 1:] = valid.astype("float64").cumsum(0).cumsum(1)
    y_lo = phase_y[:, None]
    x_lo = phase_x[None, :]
    y_hi = y_lo + ch
    x_hi = x_lo + cw
    total = (
        sums[y_hi, x_hi] - sums[y_lo, x_hi]
        - sums[y_hi, x_lo] + sums[y_lo, x_lo]
    )
    count = (
        counts[y_hi, x_hi] - counts[y_lo, x_hi]
        - counts[y_hi, x_lo] + counts[y_lo, x_lo]
    )
    mean = np.divide(
        total, count, out=np.full_like(total, np.nan), where=count > 0
    )
    return np.where(mean > 0, threshold / np.where(mean > 0, mean, 1.0), 1.0)


def _evaluate_stages(stages, values, names, parquet_path=None, cache=None,
                     origin=None, cells=None):
    """Apply map-module stages in order to a {column: array} mapping.

    ``origin`` is the image's (xmin, ymin); position-dependent stages measure
    the illumination pattern from it.  ``cells`` describes a raster grid whose
    values are cell means rather than single pixels (see ``_image_cells``):
    the illumination stage then corrects each cell by the pattern averaged
    over the pixels the cell covers, so a zoomed-out raster does not sample
    one tile position for a whole block of pixels.
    """
    import numpy as np

    values = dict(values)
    for kind, stage in stages:
        if kind == "scale":
            files = stage.get("files")
            if (
                files is not None and parquet_path is not None
                and parquet_path not in files
            ):
                continue
            for column, config in stage["columns"].items():
                if column in values:
                    values[column] = _apply_scale_transform(
                        values[column], config
                    )
            continue
        if kind == "compensation":
            if parquet_path is not None and parquet_path not in stage["files"]:
                continue
            sources = stage["names"]
            if not all(name in values for name in sources):
                continue
            # The row count is part of the key: the same stage can be seen
            # through a different Subsample scope, and cached arrays are
            # already filtered to that scope.
            row_count = len(values[sources[0]]) if sources else 0
            key = ("comp", stage.get("id"), parquet_path, row_count)
            out = cache.get(key) if cache is not None else None
            if out is None:
                combined = _linear_combine(
                    [values[name] for name in sources], stage["matrix"]
                )
                out = dict(zip(sources, combined))
                if cache is not None:
                    cache[key] = out
            values.update(out)
            continue
        if kind == "unmix":
            if stage.get("generated"):
                # The values were already loaded from the saved companion
                # output; the stage only marks pipeline order.
                continue
            if parquet_path is not None and parquet_path not in stage["samples"]:
                continue
            sources = stage["columns"]
            if not all(name in values for name in sources):
                continue
            row_count = len(values[sources[0]]) if sources else 0
            key = ("unmix", stage.get("task_id"), parquet_path, row_count)
            out = cache.get(key) if cache is not None else None
            if out is None:
                pinv = stage.get("_pinv")
                if pinv is None:
                    pinv = np.linalg.pinv(stage["matrix"])
                    stage["_pinv"] = pinv
                # C = X · pinv(M): output k = Σ_i X_i · pinv[i, k]
                combined = _linear_combine(
                    [values[name] for name in sources], pinv.T
                )
                out = dict(zip(stage["names"], combined))
                if cache is not None:
                    cache[key] = out
            values.update(out)
            continue
        if kind == "offset":
            # A constant added to the named columns (the illumination
            # module's channel shift). Applied only to the module's files.
            files = stage.get("files")
            if (
                files is not None and parquet_path is not None
                and parquet_path not in files
            ):
                continue
            for name, value in (stage.get("columns") or {}).items():
                if name in values:
                    values[name] = values[name] + value
            continue
        if kind == "illumination":
            for sub in stage.get("submodules", []):
                if parquet_path is not None and parquet_path not in sub["files"]:
                    continue
                channel = sub.get("channel")
                if channel not in values:
                    continue
                mask = sub.get("mask")
                if mask is None:
                    continue
                tile_w = int(sub["tile_w"])
                tile_h = int(sub["tile_h"])
                threshold = float(sub.get("T") or 0.0)
                intensity = float(sub.get("intensity", 1.0))
                if origin is not None:
                    x_origin, y_origin = origin
                else:
                    # Sampled or cropped reads do not contain the image
                    # origin; take it from the file itself so the pattern
                    # keeps its absolute position.
                    file_origin = _illum_file_origin(
                        parquet_path, sub.get("x"), sub.get("y")
                    )
                    if file_origin is None:
                        x_origin = y_origin = None
                    else:
                        x_origin, y_origin = file_origin
                factor = None
                if cells is not None and x_origin is not None:
                    # Raster cells hold the mean over many pixels: use the
                    # pattern averaged over each cell's window.
                    factor = _illum_cell_factor(
                        mask, tile_w, tile_h, threshold, cells,
                        (x_origin, y_origin),
                    )
                if factor is None:
                    x_values = values.get(sub.get("x"))
                    y_values = values.get(sub.get("y"))
                    if x_values is None or y_values is None:
                        continue
                    if x_origin is None:
                        try:
                            x_origin = float(np.nanmin(x_values))
                            y_origin = float(np.nanmin(y_values))
                        except (TypeError, ValueError):
                            continue
                    xi = np.mod(
                        np.rint(np.asarray(x_values, dtype="float64") - x_origin)
                        .astype(np.int64),
                        tile_w,
                    )
                    yi = np.mod(
                        np.rint(np.asarray(y_values, dtype="float64") - y_origin)
                        .astype(np.int64),
                        tile_h,
                    )
                    factor_map = np.asarray(mask, dtype="float64")[yi, xi]
                    factor = np.where(
                        factor_map > 0,
                        threshold / np.where(factor_map > 0, factor_map, 1.0),
                        1.0,
                    )
                shift = float(sub.get("shift", 0.0) or 0.0)
                if shift and sub.get("add_shift", False):
                    values[channel] = values[channel] + shift
                if intensity != 1.0:
                    # Correction intensity: 0 = no correction, 1 = full and
                    # 2 = double strength, blended linearly around identity.
                    factor = 1.0 + intensity * (factor - 1.0)
                values[channel] = values[channel] * factor
                if shift and sub.get("remove_shift", False):
                    # The data passed downstream is returned to the original
                    # level; the previews keep the shifted values.
                    values[channel] = values[channel] - shift
            continue
        if kind == "batch_align":
            aligned = list(stage.get("columns") or [])
            if aligned and all(column in values for column in aligned):
                values.update(
                    _apply_batch_align(stage, values, parquet_path, cache)
                )
            continue
        if kind == "rename":
            if not _rename_applies(stage, parquet_path):
                continue
            for old, new in stage["map"].items():
                if old in values and old != new:
                    # On a name collision the renamed value wins (the user was
                    # warned in the module and chose to proceed).
                    values[new] = values.pop(old)
            continue
        if kind == "calculation":
            _apply_calculation(stage, values, parquet_path)
            continue
    return {name: values[name] for name in names if name in values}


def _assign_batch_clusters(model, matrix, cache, key):
    """Cluster id of every row, from the trained SOM codebook.

    The nearest code vector is found in chunks with the BLAS expansion of the
    squared distance, so a million cells take well under a second. The result
    is cached per (model, file) because the same rows are re-read by every
    downstream module.
    """
    import numpy as np

    if cache is not None and key in cache:
        return cache[key]
    codes = np.asarray(model["codes"], dtype="float64")
    node_cluster = np.asarray(model["node_cluster"])
    code_norm = np.einsum("ij,ij->i", codes, codes)
    out = np.empty(matrix.shape[0], dtype=np.int16)
    chunk = 200_000
    for start in range(0, matrix.shape[0], chunk):
        block = matrix[start:start + chunk]
        distances = (
            np.einsum("ij,ij->i", block, block)[:, None]
            - 2.0 * (block @ codes.T)
            + code_norm[None, :]
        )
        out[start:start + chunk] = node_cluster[distances.argmin(axis=1)]
    if cache is not None:
        if len(cache) > 8:
            cache.clear()
        cache[key] = out
    return out


def _apply_batch_align(stage, values, parquet_path, cache=None) -> dict:
    """Correct the aligned columns of one file (see the Batch Alignment module)."""
    import numpy as np

    path = str(parquet_path) if parquet_path is not None else ""
    batch = (stage.get("files") or {}).get(path)
    if batch is None:
        return {}
    columns = list(stage.get("columns") or [])
    arrays = [np.asarray(values[column], dtype="float64") for column in columns]
    n_rows = arrays[0].size
    if any(a.size != n_rows for a in arrays):
        return {}
    finite = np.ones(n_rows, dtype=bool)
    for array in arrays:
        finite &= np.isfinite(array)
    if not finite.any():
        return {}

    if stage.get("method") == "combat":
        affine = np.asarray(stage["affine"])
        out = {}
        for j, column in enumerate(columns):
            scale = float(affine[batch, j, 0])
            shift = float(affine[batch, j, 1])
            result = np.array(values[column], dtype="float64", copy=True)
            result[finite] = result[finite] * scale + shift
            out[column] = result
        return out

    if stage.get("codes") is None:
        return {}
    rows = np.flatnonzero(finite)
    matrix = np.column_stack([array[finite] for array in arrays])
    # The row subset can change (an upstream Subsample), so the key carries a
    # cheap fingerprint of the actual values, not just the row count.
    fingerprint = round(float(matrix.sum()), 3) if matrix.size else 0.0
    key = ("ba_nodes", stage.get("id"), stage.get("model_id"), path,
           matrix.shape[0], fingerprint)
    clusters = _assign_batch_clusters(stage, matrix, cache, key)
    knots_x = np.asarray(stage["knots_x"])
    knots_y = np.asarray(stage["knots_y"])
    has_spline = np.asarray(stage["has_spline"])
    out = {}
    for j, column in enumerate(columns):
        out[column] = np.array(values[column], dtype="float64", copy=True)
    n_clusters = knots_y.shape[1]
    global_x = stage.get("global_x")
    global_y = stage.get("global_y")
    has_global = stage.get("has_global")
    for cluster in np.unique(clusters):
        cluster = int(cluster)
        if cluster < 0 or cluster >= n_clusters:
            continue
        target = rows[clusters == cluster]
        if target.size == 0:
            continue
        if bool(has_spline[batch, cluster]):
            for j, column in enumerate(columns):
                out[column][target] = np.interp(
                    out[column][target], knots_x[batch, cluster, j],
                    knots_y[batch, cluster, j],
                )
        elif has_global is not None and bool(has_global[batch]):
            for j, column in enumerate(columns):
                out[column][target] = np.interp(
                    out[column][target], global_x[batch, j], global_y[batch, j]
                )
    return out
