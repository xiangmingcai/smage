"""Column lookup and map-stage construction.

Qt-free: values are read through the context.
"""
from __future__ import annotations

import hashlib
import json

from smage.data.files import _subset_visible_columns
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
    }


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
        # An upstream Subset module whitelists column names for everything
        # below it (empty selection = no filtering).
        allowed = _subset_visible_columns(ctx, task_id)
        if allowed is not None:
            columns = [c for c in columns if c in allowed]
        # Upstream Rename modules relabel column names for downstream.
        columns = ctx._rename_columns_forward(task_id, parquet_path, columns)
    return columns


def _column_data(ctx, task_id, parquet_path, names, extra_stages=None, row_filter=True, numeric=True) -> dict:
    """Column values as seen at `task_id`, with upstream map stages applied.

    When an upstream Subsample module samples this file, the returned
    arrays hold only the sampled rows (unless `row_filter` is off).

    With ``numeric=False`` the values keep their stored dtype and only
    rename stages apply — for identifier columns (matching keys, names)
    where numeric coercion would turn text into NaN.
    """
    import pandas as pd

    if not parquet_path or not names:
        return {}
    names = list(names)
    stages = ctx._map_stages(task_id) if task_id else []
    feature_output = ctx._feature_output(parquet_path)
    if feature_output is not None:
        # A Feature output already contains every transform applied above
        # the Feature module. Only the modules *between* it and the reader
        # (e.g. a Scale placed after Feature) still apply, plus renames,
        # which are a name change rather than a value change.
        post = ctx._map_stages(
            task_id, after=feature_output.get("feature_task")
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
        # Gate2Col 0/1 columns from upstream Gate2Col modules.
        for name, rec in ctx._gate2col_applied_columns(
            task_id, parquet_path
        ).items():
            if name in needed and name not in values:
                arr = ctx._gate2col_column_values(parquet_path, rec, name)
                if arr is not None:
                    values[name] = arr
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
    if row_filter and task_id:
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
        cache=ctx._stage_cache,
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


def _map_stages(ctx, task_id, after=None) -> list:
    """Ordered Scale / Compensation / Unmix stages upstream of a task.

    Each module only declares its own input→output rule; this list is the
    workflow order the data flows through. With `after`, only the stages of
    modules *below* that ancestor are returned (used for files produced by
    a generate module, whose values already include what came before).
    """
    import numpy as np

    stages = []
    if not task_id:
        return stages
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
            config = ctx._unmix_flow_config(task)
            if config is not None:
                stages.append(("unmix", config))
        elif task_type == "illumination":
            submodules = [
                item for item in (
                    _illumination_stage_item(sub)
                    for sub in params.get("submodules", [])
                ) if item is not None
            ]
            if submodules:
                stages.append(("illumination", {
                    "task_id": task.get("id"),
                    "submodules": submodules,
                }))
        elif task_type == "batch_align":
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
    return stages


def _map_stage_token(ctx, task_id) -> str:
    """A short digest of the upstream map stages that shape the values."""
    import numpy as np

    def _array_token(value):
        try:
            arr = np.ascontiguousarray(np.asarray(value, dtype="float64"))
            return hashlib.sha1(arr.tobytes()).hexdigest()[:10]
        except Exception:  # noqa: BLE001
            return str(value)[:64]

    stages = ctx._map_stages(task_id) if task_id else []
    parts = []
    for kind, stage in stages:
        if kind == "scale":
            parts.append([
                "scale",
                {k: dict(v) for k, v in (stage.get("columns") or {}).items()},
            ])
        elif kind == "compensation":
            parts.append([
                "comp", stage.get("id"), sorted(stage.get("files") or []),
                list(stage.get("names") or []),
                _array_token(stage.get("matrix")),
            ])
        elif kind == "unmix":
            parts.append([
                "unmix", stage.get("task_id"), sorted(stage.get("samples") or []),
                list(stage.get("names") or []),
                _array_token(stage.get("matrix")),
            ])
        elif kind == "illumination":
            parts.append([
                "illum", stage.get("task_id"),
                [
                    [sub.get("channel"), sorted(sub.get("files") or []), sub.get("T")]
                    for sub in stage.get("submodules") or []
                ],
            ])
        elif kind == "batch_align":
            parts.append([
                "batch_align", stage.get("id"), stage.get("method"),
                stage.get("model_id"), list(stage.get("columns") or []),
                sorted((stage.get("files") or {}).items()),
            ])
        elif kind == "rename":
            parts.append(["rename", dict(stage.get("map") or {})])
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


def _upstream_unmix_configs(ctx, task_id, parquet_path) -> list:
    """Return the Unmix matrix configs upstream of `task_id` that apply to
    this file, in chain order (root first)."""
    configs = []
    if not task_id or not parquet_path or ctx.project is None:
        return configs
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id:
            continue  # exclude the module itself
        if task.get("type") != "unmix":
            continue
        params = task.get("params") or {}
        if parquet_path not in (params.get("samples") or []):
            continue
        config = ctx._unmix_flow_config(task)
        if config is not None:
            configs.append(config)
    return configs
