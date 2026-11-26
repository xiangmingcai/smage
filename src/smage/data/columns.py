"""Column lookup and map-stage construction.

Qt-free: values are read through the context.
"""
from __future__ import annotations

import hashlib
import json

from smage.data import project_paths as _project_paths
from smage.data.files import _subset_visible_columns
from smage.core.calculation import (
    _calculation_outputs,
    _calculation_stage,
)
from smage.core.pipeline import (
    _evaluate_stages,
    _pipeline_dependencies,
    _rename_stage,
)


def _illumination_stage_item(sub):
    """Convert one Illumination submodule into a pipeline stage entry."""
    import numpy as np

    channel = sub.get("channel")
    mask_data = sub.get("mask")
    if not channel or not mask_data:
        return None
    try:
        mask = np.asarray(mask_data, dtype="float64")
    except (TypeError, ValueError):
        return None
    if mask.ndim != 2 or mask.size == 0:
        return None
    finite = mask[np.isfinite(mask)]
    if finite.size == 0:
        return None
    method = sub.get("method", "median")
    if method == "down":
        threshold = float(finite.min())
    elif method == "up":
        threshold = float(finite.max())
    else:
        threshold = float(np.median(finite))
    return {
        "channel": channel,
        "x": sub.get("x"),
        "y": sub.get("y"),
        "files": set(sub.get("files") or []),
        "tile_w": int(sub.get("tile_w", 256)),
        "tile_h": int(sub.get("tile_h", 256)),
        "mask": mask,
        "T": threshold,
        "intensity": _illum_intensity_value(sub),
    }


def _illum_intensity_value(sub) -> float:
    """Correction strength of a submodule (0 = no correction, 1 = full)."""
    try:
        value = float(sub.get("intensity", 1.0))
    except (TypeError, ValueError):
        value = 1.0
    if not (value >= 0.0):
        value = 0.0
    return min(value, 100.0)


def _column_rename_stages(ctx, task_id) -> list:
    """Ordered column-rename stages above `task_id` (name transforms only)."""
    if not task_id or ctx.project is None:
        return []
    out = []
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "rename":
            continue
        stage = _rename_stage(task.get("params") or {})
        if stage is not None:
            out.append(stage)
    return out


def _columns_for(ctx, parquet_path, task_id=None) -> list:
    """Return the column names for a parquet file from the column index.

    Feature outputs carry their own column list (stored when generated).
    When `task_id` is given, the parameters produced by upstream modules
    for this file are appended: Unmix output names, then any mask column
    exposed by an upstream Segmentation module.
    """
    if not parquet_path:
        return []
    feat = ctx._feature_output(parquet_path)
    if feat is not None:
        # Aggregated data: upstream transforms are already baked in, but
        # downstream virtual columns (Segmentation mask, Gate2Col 0/1) can
        # still be layered on top, so keep going past the base column list.
        columns = list(feat.get("columns") or [])
    else:
        columns = list(ctx._column_index.get(ctx._rel_data_key(parquet_path), []))
    if task_id:
        for name in ctx._unmix_output_names(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._seg_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._region_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._gate2col_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._feature_up_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._dimred_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._cluster_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        for name in ctx._dc_applied_columns(task_id, parquet_path):
            if name not in columns:
                columns.append(name)
        # New channels produced by upstream Calculation modules.  Only rows
        # marked "visible downstream" and scoped to this file are listed.
        for task in ctx._task_chain(task_id):
            if task.get("id") == task_id or task.get("type") != "calculation":
                continue
            stage = _calculation_stage(task.get("params") or {})
            for name in _calculation_outputs(stage, parquet_path, True):
                if name not in columns:
                    columns.append(name)
        # An upstream Subset module whitelists column names for everything
        # below it (empty selection = no filtering).
        allowed = _subset_visible_columns(ctx, task_id)
        if allowed is not None:
            columns = [c for c in columns if c in allowed]
        # Upstream Rename modules relabel column names for downstream.
        columns = ctx._rename_columns_forward(task_id, parquet_path, columns)
    return columns


def _stage_cached_values(ctx, stages, parquet_path, names, expected_rows,
                         cache=None) -> dict:
    """Values whose final form is already in the supplied stage cache.

    Compensation and Unmix store their complete output columns there.  When
    the *last* stage that touches a requested column is one of them, the
    cached array is the final value for this file and the parquet does not
    need to be read again.  Arrays whose length does not match the rows this
    caller expects (a different Subsample scope) are ignored.
    """
    if cache is None:
        cache = getattr(ctx, "_stage_cache", None)
    if not cache:
        return {}
    last = {}
    for index, (kind, stage) in enumerate(stages):
        if kind == "compensation":
            for name in stage.get("names") or ():
                last[name] = index
        elif kind == "unmix":
            for name in stage.get("names") or ():
                last[name] = index
        elif kind == "scale":
            for name in (stage.get("columns") or ()):
                last[name] = index
        elif kind == "illumination":
            for sub in stage.get("submodules") or ():
                channel = sub.get("channel")
                if channel:
                    last[channel] = index
        elif kind == "batch_align":
            for name in stage.get("columns") or ():
                last[name] = index
        elif kind == "rename":
            for old, new in (stage.get("map") or {}).items():
                last[old] = index
                last[new] = index

    found = {}
    for name in names:
        index = last.get(name)
        if index is None:
            continue
        kind, stage = stages[index]
        if kind == "compensation":
            entry = cache.get(
                ("comp", stage.get("id"), parquet_path, expected_rows)
            )
        elif kind == "unmix":
            entry = cache.get(
                ("unmix", stage.get("task_id"), parquet_path, expected_rows)
            )
        else:
            continue
        array = (entry or {}).get(name)
        if array is None or len(array) != expected_rows:
            continue
        found[name] = array
    return found


def _column_data(ctx, task_id, parquet_path, names, extra_stages=None,
                 row_filter=True, numeric=True, sample_indices=None) -> dict:
    """Column values as seen at `task_id`, with upstream map stages applied.

    When an upstream Subsample module samples this file, the returned
    arrays hold only the sampled rows (unless `row_filter` is off).

    With ``sample_indices`` the source columns are reduced to those raw row
    positions before the map stages run, so the stage cache holds sampled
    arrays (plot-pool size) instead of full-file compensation results.  The
    caller must not combine it with an upstream Subsample row filter.

    With ``numeric=False`` the values keep their stored dtype and only
    rename stages apply — for identifier columns (matching keys, names)
    where numeric coercion would turn text into NaN.
    """
    import pandas as pd

    if not parquet_path or not names:
        return {}
    names = list(names)
    if sample_indices is not None and task_id and row_filter:
        if ctx._subsample_row_index(task_id, parquet_path) is not None:
            # Pool rows are raw row numbers; an upstream Subsample changes
            # which raw rows are visible.  Let the caller's full-read
            # fallback handle that combination.
            sample_indices = None
    stage_cache = ctx._stage_cache if sample_indices is None else None
    if sample_indices is not None:
        stage_cache = getattr(ctx, "_pool_stage_cache", None)
        if stage_cache is None:
            stage_cache = {}
            ctx._pool_stage_cache = stage_cache
    stages = (
        ctx._map_stages(task_id, parquet_path=parquet_path) if task_id else []
    )
    feature_output = ctx._feature_output(parquet_path)
    if feature_output is not None:
        # A Feature output already contains every transform applied above
        # the Feature module. Only the modules *between* it and the reader
        # (e.g. a Scale placed after Feature) still apply, plus renames,
        # which are a name change rather than a value change.
        post = ctx._map_stages(
            task_id, after=feature_output.get("feature_task"),
            parquet_path=parquet_path,
        ) if task_id else []
        renames = [
            (kind, stage) for kind, stage in stages
            if kind == "rename" and (kind, stage) not in post
        ]
        stages = renames + post
    if extra_stages:
        stages = stages + list(extra_stages)
    if not numeric:
        stages = [(kind, stage) for kind, stage in stages if kind == "rename"]
    if (
        numeric
        and extra_stages is None
        and any(kind in ("compensation", "unmix") for kind, _ in stages)
    ):
        # After an upstream save the pool is rebuilt; the compensation/unmix
        # outputs are already cached, so avoid re-reading the parquet for
        # every requested column.
        if sample_indices is not None:
            expected_rows = len(sample_indices)
        else:
            expected_rows = ctx._data_row_count(parquet_path)
            if row_filter and task_id:
                sampled = ctx._subsample_row_index(task_id, parquet_path)
                if sampled is not None:
                    expected_rows = len(sampled)
        cached = _stage_cached_values(
            ctx, stages, parquet_path, names, expected_rows,
            cache=stage_cache,
        )
        if all(name in cached for name in names):
            return {name: cached[name] for name in names}
    if feature_output is not None:
        # Generated files are not in the import column index; their columns
        # were recorded when the module generated them.
        base = set(feature_output.get("columns") or [])
    else:
        base = set(ctx._column_index.get(ctx._rel_data_key(parquet_path), []))
    if not base:
        try:
            import pyarrow.parquet as pq
            base = set(pq.ParquetFile(parquet_path).schema.names)
        except Exception:  # noqa: BLE001
            base = set()
    needed = _pipeline_dependencies(stages, names, parquet_path) if stages else set(names)
    base_cols = [c for c in needed if c in base]
    values = {}
    if base_cols:
        try:
            df = pd.read_parquet(parquet_path, columns=base_cols)
        except Exception:  # noqa: BLE001
            df = None
        if df is not None:
            for column in base_cols:
                if numeric:
                    values[column] = pd.to_numeric(
                        df[column], errors="coerce"
                    ).to_numpy(dtype="float64")
                else:
                    values[column] = df[column].to_numpy()
    if task_id:
        # Mask columns exposed by an upstream Segmentation module are not
        # stored in the data file; they are resolved from the mask parquet.
        for name, mask_path in ctx._seg_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._seg_mask_values(parquet_path, mask_path)
                if arr is not None:
                    values[name] = arr
        # Converted Region channels (0 outside, 1 inside).
        for name, rec in ctx._region_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._region_channel_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
        # Gate2Col 0/1 columns from upstream Gate2Col modules.
        for name, rec in ctx._gate2col_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._gate2col_column_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
        # Generated Unmix columns from upstream Unmix modules.  Read all
        # requested columns of one companion parquet together; N×N image
        # plots routinely ask for a dozen channels at once.
        unmix_cols = ctx._unmix_applied_columns(task_id, parquet_path)
        wanted_unmix = [
            name for name in needed
            if name in unmix_cols and name not in values
        ]
        if wanted_unmix:
            values.update(ctx._unmix_column_values_many(
                parquet_path, unmix_cols, wanted_unmix
            ))
        # Feature Up mapped columns from upstream Feature Up modules.
        for name, rec in ctx._feature_up_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._feature_up_column_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
        # Dimension Reduction columns from upstream Dimension Reduction modules.
        for name, rec in ctx._dimred_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._dimred_column_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
        # Cluster label columns from upstream Cluster modules.
        for name, rec in ctx._cluster_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._cluster_column_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
        # PeacoQC 0/1 columns from upstream Data Clean modules.
        for name, rec in ctx._dc_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._dc_column_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
    if sample_indices is not None:
        # Reduce before evaluating stages: Compensation then works on the
        # plot-pool rows only and its stage-cache entry is correspondingly
        # small.
        total = ctx._data_row_count(parquet_path)
        values = {
            name: (arr[sample_indices] if len(arr) == total else arr)
            for name, arr in values.items()
        }
    elif row_filter and task_id:
        # Keep only the rows an upstream Subsample module lets through.
        # Every array here is row-aligned to the source file, so one index
        # applies to base and virtual columns alike.
        index = ctx._subsample_row_index(task_id, parquet_path)
        if index is not None:
            total = ctx._data_row_count(parquet_path)
            values = {
                name: (arr[index] if len(arr) == total else arr)
                for name, arr in values.items()
            }
    if not stages:
        return {n: values[n] for n in names if n in values}
    evaluated = _evaluate_stages(
        stages, values, names, parquet_path=parquet_path,
        cache=stage_cache,
    )
    return {n: evaluated[n] for n in names if n in evaluated}


def _read_columns(ctx, parquet_path, columns, task_id=None, extra_stages=None):
    """Read columns from a parquet with upstream map stages applied."""
    import pandas as pd

    values = ctx._column_data(
        task_id, parquet_path, list(columns), extra_stages=extra_stages
    )
    if not values:
        return pd.DataFrame()
    return pd.DataFrame({
        name: values[name] for name in columns if name in values
    })


def _data_row_count(ctx, parquet_path) -> int:
    """Number of rows in a data file (read from the parquet metadata)."""
    import pyarrow.parquet as pq

    try:
        return int(pq.ParquetFile(parquet_path).metadata.num_rows)
    except Exception:  # noqa: BLE001
        return 0


def _bump_stages_revision(ctx) -> None:
    """Invalidate every cached map-stage list.

    Stages embed a built Unmix matrix and other per-module data, so a saved
    module (or a deleted task) must drop the memoized lists.
    """
    ctx._stages_revision = getattr(ctx, "_stages_revision", 0) + 1


def _map_stages(ctx, task_id, after=None, include_scale=False, parquet_path=None) -> list:
    """Ordered data stages upstream of a task.

    Each module only declares its own input→output rule; this list is the
    workflow order the data flows through.  Scale is a *display* transform:
    it is excluded by default and only added when ``include_scale`` is set
    (the plot renderer asks for it).  With `after`, only the stages of
    modules *below* that ancestor are returned (used for files produced by
    a generate module, whose values already include what came before).
    With `parquet_path`, stages that do not apply to that file are skipped
    before they are built — building an Unmix matrix is expensive, and it
    is wasted work for Feature outputs and other generated files.

    The result is memoized per context and invalidated by
    ``_bump_stages_revision`` (called when a module is saved or a task is
    deleted).  Gate editing asks for the same stages many times per drag;
    rebuilding an Unmix matrix each time made label/statistics updates take
    seconds.
    """
    import numpy as np

    stages = []
    if not task_id:
        return stages
    cache_key = (
        task_id, after, bool(include_scale),
        None if parquet_path is None else str(parquet_path),
    )
    revision = getattr(ctx, "_stages_revision", 0)
    cache = getattr(ctx, "_map_stages_cache", None)
    if cache is None:
        cache = {}
        ctx._map_stages_cache = cache
    elif getattr(ctx, "_map_stages_cache_revision", None) != revision:
        cache.clear()
    ctx._map_stages_cache_revision = revision
    cached = cache.get(cache_key)
    if cached is not None:
        return list(cached)
    started = after is None
    for task in ctx._task_chain(task_id):
        if not started:
            if task.get("id") == after:
                started = True
            continue
        if task.get("id") == task_id:
            continue
        task_type = task.get("type")
        params = task.get("params") or {}
        if task_type == "scale":
            if not include_scale:
                continue
            columns = {
                c["name"]: c
                for c in params.get("columns", [])
                if c.get("name")
            }
            if columns:
                files = params.get("files")
                stages.append(("scale", {
                    "columns": columns,
                    # None (legacy projects) means every file.
                    "files": set(files) if files is not None else None,
                }))
        elif task_type == "compensation":
            names = list(params.get("params") or [])
            matrix = params.get("matrix") or []
            files = set(params.get("files") or [])
            if names and files and len(matrix) == len(names):
                stages.append(("compensation", {
                    "id": task.get("id"),
                    "files": files,
                    "names": names,
                    "matrix": np.asarray(matrix, dtype="float64") / 100.0,
                }))
        elif task_type == "unmix":
            # Unmix is a generate module: its values come from the saved
            # companion output, not from a matrix computed during reads.
            # Keep a lightweight marker so downstream transforms can still
            # be ordered relative to the Unmix task.
            if parquet_path not in (params.get("samples") or []):
                continue
            rec = (
                (params.get("outputs") or {}).get(parquet_path)
                if parquet_path else None
            )
            names = (
                list(rec.get("columns") or [])
                if isinstance(rec, dict) else []
            )
            if names:
                stages.append(("unmix", {
                    "task_id": task.get("id"),
                    "generated": True,
                    "names": names,
                    "samples": set(params.get("samples") or []),
                    "signature": rec.get("signature"),
                }))
        elif task_type == "illumination":
            # A live correction-parameter edit only reaches downstream after
            # Save: use the page's last saved snapshot while it is open.
            source = params
            reg = getattr(ctx, "_module_registry", {}).get(task.get("id"))
            if reg is not None and reg.get("task_type") == "illumination":
                snapshot = getattr(reg["page"], "_illum_snapshot", None)
                if isinstance(snapshot, dict):
                    source = snapshot
            submodules = [
                item for item in (
                    _illumination_stage_item(sub)
                    for sub in source.get("submodules", [])
                ) if item is not None
            ]
            if submodules:
                stages.append(("illumination", {
                    "task_id": task.get("id"),
                    "submodules": submodules,
                }))
        elif task_type == "batch_align":
            if parquet_path is not None and parquet_path not in (
                params.get("files") or {}
            ):
                continue
            stage = ctx._ba_stage(task)
            if stage is not None:
                stages.append(("batch_align", stage))
        elif task_type == "rename":
            # Column-mode renames are a name transform that participates in
            # the pipeline (file-mode renames are handled elsewhere, on the
            # display names only).
            stage = _rename_stage(params)
            if stage is not None:
                stages.append(("rename", stage))
        elif task_type == "calculation":
            stage = _calculation_stage(
                params,
                path_key=lambda item: _project_paths._stable_path_key(ctx, item),
            )
            if stage is not None:
                # Skip the stage entirely for files no row applies to.
                if parquet_path is None or any(
                    parquet_path in row["files"] for row in stage["rows"]
                ):
                    stage["task_id"] = task.get("id")
                    stages.append(("calculation", stage))
    cache[cache_key] = stages
    return list(stages)


def _map_stage_token(ctx, task_id, parquet_path=None) -> str:
    """A short digest of the upstream map stages that shape the values."""
    import numpy as np

    def _array_token(value):
        try:
            arr = np.ascontiguousarray(np.asarray(value, dtype="float64"))
            return hashlib.sha1(arr.tobytes()).hexdigest()[:10]
        except Exception:  # noqa: BLE001
            return str(value)[:64]

    stages = (
        ctx._map_stages(task_id, parquet_path=parquet_path)
        if task_id else []
    )
    parts = []
    for kind, stage in stages:
        if kind == "compensation":
            parts.append([
                "comp", stage.get("id"),
                sorted(
                    _project_paths._stable_path_key(ctx, item)
                    for item in (stage.get("files") or [])
                ),
                list(stage.get("names") or []),
                _array_token(stage.get("matrix")),
            ])
        elif kind == "unmix":
            parts.append([
                "unmix", stage.get("task_id"),
                bool(stage.get("generated")),
                stage.get("signature"),
                sorted(
                    _project_paths._stable_path_key(ctx, item)
                    for item in (stage.get("samples") or [])
                ),
                list(stage.get("names") or []),
                _array_token(stage.get("matrix")),
            ])
        elif kind == "illumination":
            parts.append([
                "illum", stage.get("task_id"),
                [
                    [
                        sub.get("channel"),
                        sorted(
                            _project_paths._stable_path_key(ctx, item)
                            for item in (sub.get("files") or [])
                        ),
                        sub.get("x"), sub.get("y"),
                        sub.get("tile_w"), sub.get("tile_h"),
                        sub.get("T"), _array_token(sub.get("mask")),
                    ]
                    for sub in stage.get("submodules") or []
                ],
            ])
        elif kind == "batch_align":
            parts.append([
                "batch_align", stage.get("id"), stage.get("method"),
                stage.get("model_id"), list(stage.get("columns") or []),
                sorted(
                    (
                        _project_paths._stable_path_key(ctx, item),
                        value,
                    )
                    for item, value in (stage.get("files") or {}).items()
                ),
            ])
        elif kind == "rename":
            parts.append(["rename", dict(stage.get("map") or {})])
        elif kind == "calculation":
            parts.append([
                "calculation", stage.get("task_id"),
                stage.get("signature"),
            ])
    return hashlib.sha1(
        json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]


def _upstream_scale_transforms(ctx, task_id) -> dict:
    """Return {column: [config, ...]} for every scale module strictly upstream
    of task_id, in chain order (root first), so transforms compose correctly."""
    transforms = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id:
            continue  # exclude the module itself
        if task.get("type") == "scale":
            for col in task.get("params", {}).get("columns", []):
                if col.get("name"):
                    transforms.setdefault(col["name"], []).append(col)
    return transforms
