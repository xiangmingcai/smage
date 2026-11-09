"""Map-stage pipeline: declarations, dependency analysis and evaluation.

A "stage" is one (kind, payload) tuple produced by a map module (Scale,
Compensation, Unmix, Illumination, Batch Alignment, Rename). Evaluating the
stages turns a file's raw columns into the values a downstream module sees.

No Qt and no project state: callers pass the stages and values in.
"""
from __future__ import annotations

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
        if kind == "illumination":
            for sub in stage.get("submodules", []):
                if sub.get("channel") == name:
                    return True
        if kind == "rename":
            if name in stage["map"] or name in stage["map"].values():
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
            if needed & set(stage["names"]):
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


def _evaluate_stages(stages, values, names, parquet_path=None, cache=None,
                     origin=None):
    """Apply map-module stages in order to a {column: array} mapping."""
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
        if kind == "illumination":
            for sub in stage.get("submodules", []):
                if parquet_path is not None and parquet_path not in sub["files"]:
                    continue
                channel = sub.get("channel")
                if channel not in values:
                    continue
                x_values = values.get(sub.get("x"))
                y_values = values.get(sub.get("y"))
                mask = sub.get("mask")
                if x_values is None or y_values is None or mask is None:
                    continue
                if origin is not None:
                    x_origin, y_origin = origin
                else:
                    try:
                        x_origin = float(np.nanmin(x_values))
                        y_origin = float(np.nanmin(y_values))
                    except (TypeError, ValueError):
                        continue
                xi = np.mod(
                    np.rint(np.asarray(x_values, dtype="float64") - x_origin)
                    .astype(np.int64),
                    int(sub["tile_w"]),
                )
                yi = np.mod(
                    np.rint(np.asarray(y_values, dtype="float64") - y_origin)
                    .astype(np.int64),
                    int(sub["tile_h"]),
                )
                factor_map = mask[yi, xi]
                threshold = float(sub.get("T") or 0.0)
                factor = np.where(
                    factor_map > 0,
                    threshold / np.where(factor_map > 0, factor_map, 1.0),
                    1.0,
                )
                values[channel] = values[channel] * factor
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
