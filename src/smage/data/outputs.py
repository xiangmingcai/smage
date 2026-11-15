"""Output adapters exposed by analysis modules to downstream modules.

Qt-free: each adapter reads a module's saved params/outputs and returns
the columns or raster it contributes.
"""
from __future__ import annotations

from pathlib import Path


def _seg_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: mask_path} exposed by upstream Segmentation modules.

    Only masks whose saved signature still matches the file on disk are
    exposed, so downstream modules always see the saved state.
    """
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    out = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id:
            continue
        if task.get("type") != "segmentation":
            continue
        params = ctx._seg_params_for(task)
        applied = params.get("applied") or {}
        sign = applied.get(parquet_path)
        if not sign:
            continue
        path = ctx._seg_mask_file(ctx._seg_mask_record(params, parquet_path))
        if path is None or ctx._seg_signature(path) != sign:
            continue
        name = params.get("column") or "Mask"
        out.setdefault(name, path)
    return out


def _region_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: record} exposed by converted upstream Regions.

    Only Regions whose channel is converted for this file and marked
    downstream-visible are listed.  The record points at the mask parquet
    written by the Region module.
    """
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    from smage.data import regions as _regions

    out = {}
    for item in _regions._region_channel_options(ctx, task_id):
        rec = item["files"].get(str(parquet_path))
        if not isinstance(rec, dict):
            continue
        out.setdefault(item["name"], {
            "path": rec.get("abs_path"),
            "region_id": item["region_id"],
            "pixels": rec.get("pixels"),
            "kind": "region",
        })
    return out


def _region_channel_values(ctx, parquet_path, rec, name=None):
    """Per-row values of a converted Region channel (0 outside, 1 inside)."""
    if not isinstance(rec, dict):
        return None
    return _seg_mask_values(ctx, parquet_path, rec.get("path"))


def _region_channel_raster(ctx, rec, bbox, spacing):
    """One converted Region channel as (mask_2d, data_rect)."""
    if not isinstance(rec, dict):
        return None, None
    return _seg_mask_raster(ctx, rec.get("path"), bbox, spacing)


def _seg_mask_values(ctx, parquet_path, mask_path):
    """Mask labels aligned to a parquet's rows (float64, 0 where unknown)."""
    import numpy as np
    import pandas as pd

    ent = ctx._ensure_image_index(parquet_path)
    if ent is None or ent.get("manifest") is None:
        return None
    man = ent["manifest"]
    mask = ctx._seg_load_mask(mask_path)
    if mask is None:
        return None
    cache = getattr(ctx, "_seg_value_cache", None)
    if cache is None:
        cache = {}
        ctx._seg_value_cache = cache
    key = (str(parquet_path), str(mask_path))
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        df = pd.read_parquet(parquet_path, columns=["x", "y"])
    except Exception:  # noqa: BLE001
        return None
    xi = np.rint(
        df["x"].to_numpy(dtype="float64") - float(man["xmin"])
    ).astype(np.int64)
    yi = np.rint(
        df["y"].to_numpy(dtype="float64") - float(man["ymin"])
    ).astype(np.int64)
    ok = (
        (xi >= 0) & (xi < mask.shape[1])
        & (yi >= 0) & (yi < mask.shape[0])
    )
    out = np.zeros(len(df), dtype="float64")
    out[ok] = mask[yi[ok], xi[ok]].astype("float64")
    if len(cache) > 4:
        cache.clear()
    cache[key] = out
    return out


def _seg_mask_raster(ctx, mask_path, bbox, spacing):
    """Return (label_2d, data_rect) for a mask over the visible bbox.

    Reads the mask's own raster pyramid (mode per block) so zoomed-out
    views load only the coarse level; deep zooms read raw labels.
    """
    ent = ctx._ensure_image_index(mask_path)
    if ent is None or ent.get("manifest") is None:
        return None, None
    crop, rect, _frac = ctx._image_raster(
        mask_path, ent["manifest"], bbox, spacing, "mask", None, None
    )
    return crop, rect


def _seg_is_image_file(ctx, rec) -> bool:
    """Whether an imported (processed) file holds pixel data.

    Processed records do not carry the original dimensions, so this checks
    the source file's extension, then any recorded image dimensions, then
    the presence of an image index (which the importer builds for TIFFs).
    """
    dims = rec.get("dimensions") or {}
    if dims.get("kind") == "image":
        return True
    source = str(rec.get("source") or "")
    if Path(source).suffix.lower() in (".tif", ".tiff"):
        return True
    parquet = rec.get("parquet")
    if parquet and ctx._image_load_manifest(parquet) is not None:
        return True
    return False


def _dimred_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: record} exposed by upstream Dimension Reduction modules."""
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    out = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "dimred":
            continue
        params = ctx._dimred_params_for(task)
        rec = (params.get("outputs") or {}).get(parquet_path)
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        if rec.get("signature") != ctx._dimred_signature(
            params, parquet_path, task.get("id")
        ):
            continue
        path = ctx.project.path / rec["path"]
        if not path.is_file():
            continue
        for name in rec.get("columns") or []:
            out.setdefault(name, {
                "parquet": str(path),
                "channel": name,
                "kind": rec.get("kind", "table"),
            })
    return out


def _cluster_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: record} exposed by upstream Cluster modules."""
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    out = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "cluster":
            continue
        params = ctx._cluster_params_for(task)
        rec = (params.get("outputs") or {}).get(parquet_path)
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        if rec.get("signature") != ctx._cluster_signature(
            params, parquet_path, task.get("id")
        ):
            continue
        path = ctx.project.path / rec["path"]
        if not path.is_file():
            continue
        for name in rec.get("columns") or []:
            out.setdefault(name, {
                "parquet": str(path),
                "channel": name,
                "kind": rec.get("kind", "table"),
            })
    return out


def _cluster_column_values(ctx, parquet_path, rec, name):
    """Row-aligned float64 values of one Cluster label column."""
    import numpy as np
    import pandas as pd

    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None
    cparquet = rec["parquet"]
    if rec.get("kind") == "image":
        man = ctx._image_load_manifest(cparquet)
        if man is None:
            return None
        try:
            src = pd.read_parquet(parquet_path, columns=["x", "y"])
            cdf = pd.read_parquet(cparquet, columns=["x", "y", name])
        except Exception:  # noqa: BLE001
            return None
        xmin = float(man["xmin"])
        ymin = float(man["ymin"])
        width = int(round(float(man["xmax"]) - xmin)) + 1
        height = int(round(float(man["ymax"]) - ymin)) + 1
        cxi = np.rint(cdf["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        cyi = np.rint(cdf["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        grid = np.full((height, width), np.nan, dtype="float64")
        cok = (cxi >= 0) & (cxi < width) & (cyi >= 0) & (cyi < height)
        grid[cyi[cok], cxi[cok]] = cdf[name].to_numpy(dtype="float64")[cok]
        sxi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        syi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        sok = (sxi >= 0) & (sxi < width) & (syi >= 0) & (syi < height)
        out = np.full(len(src), np.nan, dtype="float64")
        out[sok] = grid[syi[sok], sxi[sok]]
        return out
    try:
        df = pd.read_parquet(cparquet, columns=[name])
    except Exception:  # noqa: BLE001
        return None
    return df[name].to_numpy(dtype="float64")


def _cluster_raster(ctx, rec, bbox, spacing):
    """Return (scalar_2d, data_rect) for a Cluster channel over a bbox."""
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None, None
    ent = ctx._ensure_image_index(rec["parquet"])
    if ent is None or ent.get("manifest") is None:
        return None, None
    crop, rect, _frac = ctx._image_raster(
        rec["parquet"], ent["manifest"], bbox, spacing, rec.get("channel"), None, None
    )
    return crop, rect


def _dc_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: record} exposed by upstream Data Clean modules."""
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    out = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "data_clean":
            continue
        params = ctx._dc_params_for(task)
        rec = (params.get("outputs") or {}).get(parquet_path)
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        if rec.get("signature") != ctx._dc_signature(
            params, parquet_path, task.get("id")
        ):
            continue
        path = ctx.project.path / rec["path"]
        if not path.is_file():
            continue
        for name in rec.get("columns") or []:
            out.setdefault(name, {
                "parquet": str(path),
                "channel": name,
                "kind": rec.get("kind", "table"),
            })
    return out


def _dc_raster(ctx, rec, bbox, spacing):
    """Return (scalar_2d, data_rect) for a Data Clean channel over a bbox."""
    return ctx._cluster_raster(rec, bbox, spacing)


def _g2c_raster(ctx, rec, bbox, spacing):
    """Return (scalar_2d, data_rect) for a Gate2Col 0/1 channel over a bbox."""
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None, None
    ent = ctx._ensure_image_index(rec["parquet"])
    if ent is None or ent.get("manifest") is None:
        return None, None
    crop, rect, _frac = ctx._image_raster(
        rec["parquet"], ent["manifest"], bbox, spacing, rec.get("channel"), None, None
    )
    return crop, rect


def _unmix_image_outputs(ctx, task_id, parquet_path):
    """Precomputed unmixed channels: (usable, stale names) for a file."""
    if not task_id or not parquet_path or ctx.project is None:
        return {}, set()
    usable = {}
    stale = set()
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "unmix":
            continue
        params = task.get("params") or {}
        rec = (params.get("outputs") or {}).get(parquet_path)
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        names = list(rec.get("columns") or [])
        config = ctx._unmix_flow_config(task)
        path = ctx.project.path / rec["path"]
        signature = (
            ctx._unmix_output_signature(task.get("id"), parquet_path, config)
            if config is not None else None
        )
        if (
            config is None
            or rec.get("signature") != signature
            or not path.is_file()
            or ctx._image_load_manifest(str(path)) is None
        ):
            stale.update(names)
            continue
        for name in names:
            usable.setdefault(name, {
                "parquet": str(path), "channel": name, "kind": "image",
                # Kept so the renderer can apply the map stages that sit
                # between this Unmix module and the reading task.
                "task_id": task.get("id"),
            })
    return usable, stale


def _unmix_raster(ctx, rec, bbox, spacing):
    """Return (scalar_2d, data_rect) for a precomputed unmixed channel."""
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None, None
    ent = ctx._ensure_image_index(rec["parquet"])
    if ent is None or ent.get("manifest") is None:
        return None, None
    crop, rect, _frac = ctx._image_raster(
        rec["parquet"], ent["manifest"], bbox, spacing,
        rec.get("channel"), None, None,
    )
    return crop, rect
