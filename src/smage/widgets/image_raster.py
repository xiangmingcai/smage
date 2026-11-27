"""Image raster crops (QRectF in data coordinates) used by plot views."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF

from smage.core.pipeline import (
    _evaluate_stages, _pipeline_dependencies,
)

# Raw reads are binned to about one cell per screen pixel.  Binning at one
# cell per data unit made an intermediate zoom (before the finest pyramid
# level is available) allocate tens of millions of cells per plot.
_RAW_MIN_CELLS = 1


def _raw_cell_count(extent, spacing):
    return max(_RAW_MIN_CELLS, int(math.ceil(extent / max(float(spacing), 1e-12))))


def _image_raster_raw(ctx, parquet_path, manifest, bbox, channel, mask_ent=None,
                      stages=None, spacing=1.0, want_coords=(), coords_out=None):
    """Rasterise pixels inside a bbox (deep zoom beyond the finest level).

    `stages` evaluates the ordered map pipeline (Scale / Compensation /
    Unmix) per pixel instead of reading one channel.  Cells are about one
    screen pixel (`spacing` in data units), so an intermediate zoom does not
    build a full-resolution array.  `want_coords` fills `coords_out` with
    per-cell x/y means, which position-dependent stages need.
    """
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
    step = max(float(spacing), 1e-12)
    nbx = _raw_cell_count(x1 - x0, step)
    nby = _raw_cell_count(y1 - y0, step)
    bi = np.clip(
        np.floor((x - x0) / step).astype(np.int64), 0, nbx - 1
    )
    bj = np.clip(
        np.floor((y - y0) / step).astype(np.int64), 0, nby - 1
    )
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
    if coords_out is not None and want_coords:
        for name in want_coords:
            if name == "x":
                source = x
            elif name == "y":
                source = y
            else:
                continue
            total = np.bincount(
                bidx, weights=source, minlength=nbx * nby
            ).reshape(nby, nbx)
            coords_out[name] = np.divide(
                total, cnt, out=np.zeros_like(total), where=cnt > 0
            )
    rect = QRectF(x0, y0, x1 - x0, y1 - y0)
    return chan, rect, frac


def _image_raster(ctx, parquet_path, manifest, bbox, spacing, channel,
                  mask_ent=None, stages=None, force_raw=False,
                  extra_columns=(), want_coords=(), coords_out=None):
    """Return (channel_2d_array, data_rect, mask_fraction) for the visible bbox.

    `stages` evaluates the ordered map pipeline (Scale / Compensation /
    Unmix). Linear stages are exact on the block means; Scale stages are
    applied to the aggregated values. With `force_raw` (used by gate subset
    views) the pixels are read at full resolution: block means would blend
    masked and unmasked pixels and make the selection look inaccurate.

    `extra_columns` adds source columns (for example the x/y coordinates a
    later position-dependent stage needs) to the extraction, and
    `want_coords`/`coords_out` return those coordinates on the same grid as
    the channel.
    """
    import numpy as np
    extra = tuple(extra_columns or ())
    coords = {} if coords_out is None else coords_out
    if force_raw:
        return ctx._image_raster_raw(
            parquet_path, manifest, bbox, channel, mask_ent, stages,
            spacing=spacing, want_coords=want_coords, coords_out=coords,
        )
    color_cols = manifest.get("color_cols", [])
    if not color_cols:
        return None, None, None
    if stages is not None:
        needed = _pipeline_dependencies(stages, [channel], parquet_path)
        needed = set(needed) | set(extra)
        if not all(c in color_cols for c in needed):
            # Not all inputs are indexed; fall back to raw pixels.
            return ctx._image_raster_raw(
                parquet_path, manifest, bbox, channel, mask_ent, stages,
                spacing=spacing, want_coords=want_coords, coords_out=coords,
            )
        levels = manifest.get("levels", [])
        if not levels or (
            spacing < levels[0]["block"] and levels[0]["block"] > 1
        ):
            return ctx._image_raster_raw(
                parquet_path, manifest, bbox, channel, mask_ent, stages,
                spacing=spacing, want_coords=want_coords, coords_out=coords,
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
        if want_coords:
            for name in want_coords:
                if name in values:
                    coords[name] = values[name]
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
            parquet_path, manifest, bbox, channel, mask_ent, None,
            spacing=spacing, want_coords=want_coords, coords_out=coords,
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
    if want_coords:
        for name in want_coords:
            if name in color_cols:
                coords[name] = arr[j0:j1, i0:i1, color_cols.index(name)]
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


def _image_raster_texture(ctx, parquet_path, manifest, bbox, spacing, channel,
                          mask_ent=None, stages=None, force_raw=False,
                          stages_after=None, base_cache=None, base_sig=None,
                          base_coords=(), cache_limit=6):
    """Like :func:`_image_raster`, with a cached pre-correction base.

    The channel is first computed with `stages` only ("base"), and that base
    is cached per (file, channel, view, spacing, base signature).  A second
    stage list (`stages_after`) is then applied on top without reading or
    recomputing anything — this is what lets the Illumination preview redraw
    the corrected image when only its correction parameters change.

    `base_coords` names the coordinates the caller will need for
    `stages_after`; they are extracted into the base as well, so the plain
    (no correction) and corrected renders share one cache entry.

    Returns (channel, rect, frac, from_cache).
    """
    import numpy as np
    names = list(base_coords or ())
    for kind, stage in stages_after or ():
        if kind != "illumination":
            continue
        for sub in stage.get("submodules") or ():
            for name in (sub.get("x"), sub.get("y")):
                if name and name not in names:
                    names.append(name)
    needed_coords = tuple(names)
    key = None
    cached = None
    if base_cache is not None and base_sig is not None:
        key = (
            str(parquet_path), str(channel),
            tuple(round(float(v), 6) for v in bbox),
            round(float(spacing), 6), bool(force_raw),
            tuple(needed_coords), tuple(base_sig),
        )
        cached = base_cache.get(key)
    if cached is not None:
        chan, rect, frac, coords = cached
    else:
        coords = {}
        chan, rect, frac = _image_raster(
            ctx, parquet_path, manifest, bbox, spacing, channel,
            mask_ent=mask_ent, stages=stages, force_raw=force_raw,
            extra_columns=needed_coords, want_coords=needed_coords,
            coords_out=coords,
        )
        if key is not None and chan is not None:
            if len(base_cache) >= cache_limit:
                base_cache.pop(next(iter(base_cache)))
            base_cache[key] = (chan, rect, frac, dict(coords))
    if chan is None:
        return None, None, None, False
    if stages_after:
        values = {channel: np.array(chan, dtype="float64", copy=True)}
        for name, array in coords.items():
            values[name] = np.asarray(array, dtype="float64")
        evaluated = _evaluate_stages(
            stages_after, values, [channel], parquet_path=parquet_path,
            origin=(
                float(manifest["xmin"]), float(manifest["ymin"])
            ),
        )
        chan = evaluated.get(channel, chan)
    return chan, rect, frac, cached is not None
