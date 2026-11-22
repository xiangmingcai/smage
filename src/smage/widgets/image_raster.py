"""Image raster crops (QRectF in data coordinates) used by plot views."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF

from smage.core.pipeline import (
    _evaluate_stages, _pipeline_dependencies,
)


def _image_raster_raw(ctx, parquet_path, manifest, bbox, channel, mask_ent=None, stages=None):
    """Rasterise raw pixels inside a bbox (deep zoom beyond the finest level).

    `stages` evaluates the ordered map pipeline (Scale / Compensation /
    Unmix) per pixel instead of reading one channel.
    """
    import math
    import numpy as np
    import pyarrow.parquet as pq
    color_cols = manifest.get("color_cols", [])
    if stages is not None:
        needed = _pipeline_dependencies(stages, [channel], parquet_path)
        wanted = [c for c in needed if c not in ("x", "y")]
    else:
        needed = {channel}
        if channel is None:
            channel = color_cols[0] if color_cols else None
        elif channel not in color_cols:
            # Never substitute another column: a missing channel would then
            # be drawn as that column (often x, a left-to-right gradient).
            return None, None, None
        wanted = [channel] if channel else []
    x0, x1, y0, y1 = bbox
    cols = list(dict.fromkeys(["x", "y"] + wanted))
    filters = [("y", ">=", y0), ("y", "<=", y1), ("x", ">=", x0), ("x", "<=", x1)]
    try:
        df = pq.read_table(parquet_path, columns=cols, filters=filters).to_pandas()
    except Exception:
        return None, None, None
    if len(df) == 0:
        return None, None, None
    x = df["x"].to_numpy(dtype="float64")
    y = df["y"].to_numpy(dtype="float64")
    if stages is not None:
        values = {}
        if "x" in needed:
            values["x"] = x
        if "y" in needed:
            values["y"] = y
        for name in wanted:
            if name in df.columns:
                values[name] = df[name].to_numpy(dtype="float64")
            else:
                return None, None, None
        evaluated = _evaluate_stages(
            stages, values, [channel], parquet_path,
            origin=(float(manifest["xmin"]), float(manifest["ymin"])),
        )
        if channel not in evaluated:
            return None, None, None
        v = evaluated[channel]
    elif channel == "x":
        v = x
    elif channel == "y":
        v = y
    elif channel:
        v = df[channel].to_numpy(dtype="float64")
    else:
        v = np.ones(len(df))
    nbx = max(1, int(math.ceil(x1 - x0)))
    nby = max(1, int(math.ceil(y1 - y0)))
    bi = np.clip((x - x0).astype(np.int64), 0, nbx - 1)
    bj = np.clip((y - y0).astype(np.int64), 0, nby - 1)
    bidx = bj * nbx + bi
    cnt = np.bincount(bidx, minlength=nbx * nby).reshape(nby, nbx)
    frac = None
    if mask_ent is not None and mask_ent.get("mask_2d") is not None:
        mask2d = mask_ent["mask_2d"]
        xmin = int(manifest["xmin"])
        ymin = int(manifest["ymin"])
        mi = np.clip((x - xmin).astype(np.int64), 0, mask2d.shape[1] - 1)
        mj = np.clip((y - ymin).astype(np.int64), 0, mask2d.shape[0] - 1)
        pix_mask = mask2d[mj, mi].astype(np.float64)
        ssum = np.bincount(bidx, weights=v * pix_mask, minlength=nbx * nby).reshape(nby, nbx)
        cnt_pass = np.bincount(bidx, weights=pix_mask, minlength=nbx * nby).reshape(nby, nbx)
        chan = np.divide(ssum, cnt_pass, out=np.zeros_like(ssum), where=cnt_pass > 0)
        frac = np.divide(cnt_pass, cnt, out=np.zeros_like(cnt_pass), where=cnt > 0)
    else:
        ssum = np.bincount(bidx, weights=v, minlength=nbx * nby).reshape(nby, nbx)
        chan = np.divide(ssum, cnt, out=np.zeros_like(ssum), where=cnt > 0)
    rect = QRectF(x0, y0, x1 - x0, y1 - y0)
    return chan, rect, frac


def _image_raster(ctx, parquet_path, manifest, bbox, spacing, channel, mask_ent=None, stages=None, force_raw=False):
    """Return (channel_2d_array, data_rect, mask_fraction) for the visible bbox.

    `stages` evaluates the ordered map pipeline (Scale / Compensation /
    Unmix). Linear stages are exact on the block means; Scale stages are
    applied to the aggregated values. With `force_raw` (used by gate subset
    views) the pixels are read at full resolution: block means would blend
    masked and unmasked pixels and make the selection look inaccurate.
    """
    import math
    import numpy as np
    if force_raw:
        return ctx._image_raster_raw(
            parquet_path, manifest, bbox, channel, mask_ent, stages
        )
    color_cols = manifest.get("color_cols", [])
    if not color_cols:
        return None, None, None
    if stages is not None:
        needed = _pipeline_dependencies(stages, [channel], parquet_path)
        if not all(c in color_cols for c in needed):
            # Not all inputs are indexed; fall back to raw pixels.
            return ctx._image_raster_raw(
                parquet_path, manifest, bbox, channel, mask_ent, stages
            )
        levels = manifest.get("levels", [])
        if not levels or (
            spacing < levels[0]["block"] and levels[0]["block"] > 1
        ):
            return ctx._image_raster_raw(
                parquet_path, manifest, bbox, channel, mask_ent, stages
            )
        chosen = levels[-1]
        for info in levels:
            if info["block"] >= spacing:
                chosen = info
                break
        arr = ctx._image_level_2d(parquet_path, chosen["block"])
        if arr is None:
            return None, None, None
        x0, x1, y0, y1 = bbox
        xmin = manifest["xmin"]
        ymin = manifest["ymin"]
        b = chosen["block"]
        i0 = max(0, int(math.floor((x0 - xmin) / b)))
        i1 = min(arr.shape[1], int(math.ceil((x1 - xmin) / b)))
        j0 = max(0, int(math.floor((y0 - ymin) / b)))
        j1 = min(arr.shape[0], int(math.ceil((y1 - ymin) / b)))
        if i1 <= i0 or j1 <= j0:
            return None, None, None
        values = {
            name: arr[j0:j1, i0:i1, color_cols.index(name)]
            for name in needed if name in color_cols
        }
        evaluated = _evaluate_stages(
            stages, values, [channel], parquet_path,
            origin=(float(manifest["xmin"]), float(manifest["ymin"])),
        )
        if channel not in evaluated:
            return None, None, None
        rect = QRectF(
            xmin + i0 * b, ymin + j0 * b, (i1 - i0) * b, (j1 - j0) * b
        )
        frac = None
        if mask_ent is not None:
            frac_level = mask_ent.get("levels", {}).get(b)
            if frac_level is not None:
                frac = frac_level[j0:j1, i0:i1]
        return evaluated[channel], rect, frac
    if channel is None:
        channel = color_cols[0] if color_cols else None
    elif channel not in color_cols:
        # A requested channel that is not in this pyramid must be reported,
        # not silently replaced by color_cols[0] (usually x, a gradient).
        return None, None, None
    cidx = color_cols.index(channel) if channel else 0
    x0, x1, y0, y1 = bbox
    xmin = manifest["xmin"]
    ymin = manifest["ymin"]
    levels = manifest.get("levels", [])
    if not levels:
        return None, None, None
    if spacing < levels[0]["block"] and levels[0]["block"] > 1:
        return ctx._image_raster_raw(
            parquet_path, manifest, bbox, channel, mask_ent, None
        )
    chosen = levels[-1]
    for info in levels:
        if info["block"] >= spacing:
            chosen = info
            break
    arr = ctx._image_level_2d(parquet_path, chosen["block"])
    if arr is None:
        return None, None, None
    b = chosen["block"]
    i0 = max(0, int(math.floor((x0 - xmin) / b)))
    i1 = min(arr.shape[1], int(math.ceil((x1 - xmin) / b)))
    j0 = max(0, int(math.floor((y0 - ymin) / b)))
    j1 = min(arr.shape[0], int(math.ceil((y1 - ymin) / b)))
    if i1 <= i0 or j1 <= j0:
        return None, None, None
    crop = arr[j0:j1, i0:i1, cidx]
    rect = QRectF(xmin + i0 * b, ymin + j0 * b, (i1 - i0) * b, (j1 - j0) * b)
    frac = None
    if mask_ent is not None:
        frac_level = mask_ent.get("levels", {}).get(b)
        if frac_level is not None:
            frac = frac_level[j0:j1, i0:i1]
    return crop, rect, frac


def _image_mask_crop(ctx, manifest, bbox, spacing, mask_ent):
    """Return (mask_fraction_2d, data_rect) for the visible bbox (level path)."""
    import math
    if mask_ent is None:
        return None, None
    levels = manifest.get("levels", [])
    if not levels:
        return None, None
    x0, x1, y0, y1 = bbox
    xmin = manifest["xmin"]
    ymin = manifest["ymin"]
    chosen = levels[-1]
    for info in levels:
        if info["block"] >= spacing:
            chosen = info
            break
    frac_level = mask_ent.get("levels", {}).get(chosen["block"])
    if frac_level is None:
        return None, None
    b = chosen["block"]
    i0 = max(0, int(math.floor((x0 - xmin) / b)))
    i1 = min(frac_level.shape[1], int(math.ceil((x1 - xmin) / b)))
    j0 = max(0, int(math.floor((y0 - ymin) / b)))
    j1 = min(frac_level.shape[0], int(math.ceil((y1 - ymin) / b)))
    if i1 <= i0 or j1 <= j0:
        return None, None
    frac = frac_level[j0:j1, i0:i1]
    rect = QRectF(xmin + i0 * b, ymin + j0 * b, (i1 - i0) * b, (j1 - j0) * b)
    return frac, rect


