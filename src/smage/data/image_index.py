"""Image pyramid index: building, validating and loading levels.

Qt-free: the QRectF-producing raster crop in ``widgets/image_raster.py``
is the view-facing part.
"""
from __future__ import annotations

from pathlib import Path

from smage.core.image_ops import _mode_pool
from smage.data.workers import _ImageFilterWorker


_IMAGE_FINEST_CAP = 2_000_000   # most points in the finest precomputed level
_IMAGE_COARSEST = 4096          # stop coarsening once a level is this small

def _image_index_dir(parquet_path) -> Path:
    return Path(str(parquet_path) + ".imgidx")


def _image_geometry(parquet_path):
    """Return (xmin, xmax, ymin, ymax, total_rows, columns) for an image Parquet.

    x/y extents come from row-group statistics when present (no data is read);
    otherwise the x/y columns are streamed once to compute min/max.
    """
    import numpy as np
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(parquet_path)
    names = list(pf.schema_arrow.names)
    total = int(pf.metadata.num_rows)

    def col_stat(col):
        idx = names.index(col)
        cmin = float("inf")
        cmax = float("-inf")
        found = False
        for rg in range(pf.metadata.num_row_groups):
            st = pf.metadata.row_group(rg).column(idx).statistics
            if (
                st is not None
                and getattr(st, "min", None) is not None
                and getattr(st, "max", None) is not None
            ):
                cmin = min(cmin, float(st.min))
                cmax = max(cmax, float(st.max))
                found = True
        if found:
            return cmin, cmax
        return None

    xs = col_stat("x")
    ys = col_stat("y")
    if xs is not None and ys is not None and total > 0:
        return xs[0], xs[1], ys[0], ys[1], total, names

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for batch in pf.iter_batches(batch_size=1_000_000, columns=["x", "y"]):
        x = batch.column("x").to_numpy()
        y = batch.column("y").to_numpy()
        if x.size:
            xmin = min(xmin, float(np.nanmin(x)))
            xmax = max(xmax, float(np.nanmax(x)))
            ymin = min(ymin, float(np.nanmin(y)))
            ymax = max(ymax, float(np.nanmax(y)))
    if not np.isfinite(xmin):
        raise ValueError("image file has no x/y coordinates")
    return xmin, xmax, ymin, ymax, total, names


def _image_write_level(idx_dir, block, means2d):
    import numpy as np
    fname = f"level_{block}.npy"
    np.save(idx_dir / fname, means2d.astype(np.float32))
    return {"block": int(block), "file": fname}


class _ImageIndexBuilder:
    """Accumulate the finest pyramid level while data is being written.

    Unmix writes its output chunks itself; feeding the same chunks here means
    the display pyramid is built without reading the new parquet back.  The
    grid geometry comes from the source file, so only the values are consumed.

    Each level is a 2D array `(H/b, W/b, C)` of per-block channel means.  The
    finest block size `b0` is chosen so the level fits _IMAGE_FINEST_CAP;
    coarser levels double the block via 2x2 count-weighted pooling.
    """

    def __init__(self, columns, xmin, xmax, ymin, ymax, total):
        import math
        import numpy as np

        self.columns = list(columns)
        self.xmin = float(xmin)
        self.xmax = float(xmax)
        self.ymin = float(ymin)
        self.ymax = float(ymax)
        self.total = int(total)
        self.width = self.xmax - self.xmin + 1.0
        self.height = self.ymax - self.ymin + 1.0
        if self.width < 1 or self.height < 1 or self.total == 0:
            raise ValueError("image file has no pixels")
        block = 1
        while True:
            nbx = int(math.ceil(self.width / block))
            nby = int(math.ceil(self.height / block))
            if nbx * nby <= _IMAGE_FINEST_CAP:
                break
            block *= 2
        self.block = block
        self.nbx = nbx
        self.nby = nby
        self.nblocks = nbx * nby
        self.ncol = len(self.columns)
        self.count = np.zeros(self.nblocks, dtype=np.int64)
        self.sums = np.zeros((self.nblocks, self.ncol), dtype=np.float64)
        self.ch_min = np.full(self.ncol, np.inf)
        self.ch_max = np.full(self.ncol, -np.inf)

    def update(self, x_values, y_values, values) -> None:
        """Accumulate one written chunk.

        ``values`` holds every non-x/y column of ``self.columns``.  Missing
        columns are treated as all-NaN, which keeps them out of the ranges.
        """
        import numpy as np

        x = np.asarray(x_values, dtype="float64")
        if x.size == 0:
            return
        y = np.asarray(y_values, dtype="float64")
        bi = ((x - self.xmin) // self.block).astype(np.int64)
        bi += ((y - self.ymin) // self.block).astype(np.int64) * self.nbx
        np.clip(bi, 0, self.nblocks - 1, out=bi)
        self.count += np.bincount(bi, minlength=self.nblocks)
        for j, name in enumerate(self.columns):
            if name == "x":
                col = x
            elif name == "y":
                col = y
            elif name in values:
                col = values[name]
            else:
                continue
            if col.size != x.size:
                raise ValueError(f"column {name!r} has the wrong length")
            if col.dtype.kind in "iu":
                # Integers cannot be NaN: skip the mask and the float copy.
                self.sums[:, j] += np.bincount(
                    bi, weights=col, minlength=self.nblocks
                )
                self.ch_min[j] = min(self.ch_min[j], float(col.min()))
                self.ch_max[j] = max(self.ch_max[j], float(col.max()))
                continue
            v = np.asarray(col, dtype="float64")
            if np.isfinite(v).all():
                self.sums[:, j] += np.bincount(
                    bi, weights=v, minlength=self.nblocks
                )
                self.ch_min[j] = min(self.ch_min[j], float(v.min()))
                self.ch_max[j] = max(self.ch_max[j], float(v.max()))
                continue
            self.sums[:, j] += np.bincount(
                bi, weights=np.nan_to_num(v), minlength=self.nblocks
            )
            finite = np.isfinite(v)
            if finite.any():
                vals = v[finite]
                self.ch_min[j] = min(self.ch_min[j], float(vals.min()))
                self.ch_max[j] = max(self.ch_max[j], float(vals.max()))

    def finish(self, parquet_path) -> dict:
        """Write the pyramid levels and manifest for a finished parquet."""
        import json
        import math
        import numpy as np

        means = (self.sums / np.maximum(self.count, 1)[:, None]).reshape(
            self.nby, self.nbx, self.ncol
        ).astype(np.float32)
        idx_dir = _image_index_dir(parquet_path)
        idx_dir.mkdir(parents=True, exist_ok=True)
        levels = [_image_write_level(idx_dir, self.block, means)]

        # Coarser levels by 2x2 count-weighted pooling of the previous level.
        # float32 keeps the temporary padding arrays half the size; the levels
        # are stored as float32 anyway.
        prev = means
        prev_cnt = self.count.reshape(self.nby, self.nbx).astype(np.float32)
        prev_b = self.block
        while prev_b < max(self.width, self.height):
            nb = prev_b * 2
            h, w = prev.shape[0], prev.shape[1]
            nbx2 = int(math.ceil(w / 2.0))
            nby2 = int(math.ceil(h / 2.0))
            ph, pw = nby2 * 2, nbx2 * 2
            pad_m = np.zeros((ph, pw, self.ncol), dtype=np.float32)
            pad_c = np.zeros((ph, pw), dtype=np.float32)
            pad_m[:h, :w] = prev * prev_cnt[:, :, None]
            pad_c[:h, :w] = prev_cnt
            sum_m = pad_m.reshape(nby2, 2, nbx2, 2, self.ncol).sum(
                axis=(1, 3), dtype=np.float32
            )
            sum_c = pad_c.reshape(nby2, 2, nbx2, 2).sum(
                axis=(1, 3), dtype=np.float32
            )
            prev = sum_m / np.maximum(sum_c, 1)[:, :, None]
            prev_cnt = sum_c
            levels.append(_image_write_level(idx_dir, nb, prev))
            prev_b = nb
            if nby2 * nbx2 <= _IMAGE_COARSEST:
                break

        color_ranges = {}
        for j, cc in enumerate(self.columns):
            color_ranges[cc] = [
                float(self.ch_min[j]) if np.isfinite(self.ch_min[j]) else 0.0,
                float(self.ch_max[j]) if np.isfinite(self.ch_max[j]) else 1.0,
            ]
        st = Path(parquet_path).stat()
        manifest = {
            "version": 2,
            "file_size": int(st.st_size),
            "file_mtime": float(st.st_mtime),
            "xmin": float(self.xmin),
            "xmax": float(self.xmax),
            "ymin": float(self.ymin),
            "ymax": float(self.ymax),
            "total": int(self.total),
            "color_cols": list(self.columns),
            "color_ranges": color_ranges,
            "levels": levels,
        }
        with open(idx_dir / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        return manifest


def _build_image_index_files(parquet_path, progress_cb=None):
    """Build the multi-level raster pyramid for one image file (disk-cached).

    Reads every column once and feeds ``_ImageIndexBuilder``.  Writers that
    already have the chunk in memory (Unmix) use the builder directly instead
    of calling this function.
    """
    import numpy as np
    import pyarrow.parquet as pq

    xmin, xmax, ymin, ymax, total, names = _image_geometry(parquet_path)
    # Store per-block means for every column (including x/y, so the user may
    # color the image by a coordinate too). The default color parameter is
    # still the first non-x/y column, chosen in _figure_add_plot.
    names = list(names)
    builder = _ImageIndexBuilder(names, xmin, xmax, ymin, ymax, total)
    pf = pq.ParquetFile(parquet_path)
    processed = 0
    for batch in pf.iter_batches(batch_size=1_000_000, columns=names):
        values = {
            name: np.asarray(
                batch.column(name).to_numpy(zero_copy_only=False),
                dtype="float64",
            )
            for name in names
            if name not in ("x", "y") and name in batch.schema.names
        }
        builder.update(
            np.asarray(
                batch.column("x").to_numpy(zero_copy_only=False),
                dtype="float64",
            ),
            np.asarray(
                batch.column("y").to_numpy(zero_copy_only=False),
                dtype="float64",
            ),
            values,
        )
        processed += batch.num_rows
        if progress_cb is not None:
            progress_cb(min(1.0, processed / max(total, 1)))
    manifest = builder.finish(parquet_path)
    if progress_cb is not None:
        progress_cb(1.0)
    return manifest


def _build_label_index(parquet_path, mask_2d, xmin, xmax, ymin, ymax):
    """Build a label (mode) raster pyramid for a mask parquet.

    Each level stores the per-block dominant label, so a zoomed-out view shows
    the most frequent cell in each block. The mask is a self-contained
    ``[x, y, mask]`` parquet; the pyramid lives in ``<parquet>.imgidx/`` with
    the same geometry as the source image.
    """
    import json
    import math

    import numpy as np

    idx_dir = _image_index_dir(parquet_path)
    idx_dir.mkdir(parents=True, exist_ok=True)
    height, width = mask_2d.shape
    geo_w = xmax - xmin + 1.0
    geo_h = ymax - ymin + 1.0

    block = 1
    while True:
        nbx = int(math.ceil(geo_w / block))
        nby = int(math.ceil(geo_h / block))
        if nbx * nby <= _IMAGE_FINEST_CAP:
            break
        block *= 2
    b0 = block

    levels = []
    # At block size 1 the level is the mask itself; pooling would pad the
    # whole image and run a per-pixel mode, which is pure overhead for the
    # common sub-2 MP image.
    level = mask_2d if b0 == 1 else _mode_pool(mask_2d, b0)
    fname = f"level_{b0}.npy"
    np.save(idx_dir / fname, level[..., None].astype(np.int32))
    levels.append({"block": int(b0), "file": fname})

    prev = level
    prev_b = b0
    while prev_b < max(geo_w, geo_h):
        nb = prev_b * 2
        nxt = _mode_pool(prev, 2)
        fname = f"level_{nb}.npy"
        np.save(idx_dir / fname, nxt[..., None].astype(np.int32))
        levels.append({"block": int(nb), "file": fname})
        prev = nxt
        prev_b = nb
        if (nxt.shape[0] * nxt.shape[1]) <= _IMAGE_COARSEST:
            break

    max_label = int(mask_2d.max()) if mask_2d.size else 0
    st = Path(parquet_path).stat()
    manifest = {
        "version": 2,
        "file_size": int(st.st_size),
        "file_mtime": float(st.st_mtime),
        "xmin": float(xmin),
        "xmax": float(xmax),
        "ymin": float(ymin),
        "ymax": float(ymax),
        "total": int(mask_2d.size),
        "color_cols": ["mask"],
        "color_ranges": {"mask": [0.0, float(max_label)]},
        "levels": levels,
        "kind": "label",
    }
    with open(idx_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return manifest


def _image_load_level(parquet_path, info):
    import numpy as np
    return np.load(_image_index_dir(parquet_path) / info["file"])


def _ensure_image_index(ctx, parquet_path):
    """Return the cached index entry for a file, or None when it has none.

    Image indexes are built at import time. A file without one is left
    alone (the caller prompts the user); it may be a flow/table file, or
    an image that needs re-importing.
    """
    cache = getattr(ctx, "_image_index_cache", None)
    if cache is None:
        cache = {}
        ctx._image_index_cache = cache
    ent = cache.get(parquet_path)
    if ent is not None and ent.get("manifest") is not None:
        # A generate module (Gate2Col, Feature Up, Unmix, …) can rewrite the
        # same output parquet and rebuild its pyramid.  Returning the cached
        # entry would keep the old manifest and level arrays, so a renamed
        # channel would silently fall back to the first column (x) and look
        # like a gradient.  Revalidate against the parquet on every lookup.
        if ctx._image_index_valid(parquet_path, ent["manifest"]):
            return ent
        cache.pop(parquet_path, None)
    manifest = ctx._image_load_manifest(parquet_path)
    if manifest is not None and ctx._image_index_valid(parquet_path, manifest):
        ent = {"manifest": manifest, "levels": {}}
        cache[parquet_path] = ent
        return ent
    return None


def _image_index_valid(ctx, parquet_path, manifest):
    try:
        st = Path(parquet_path).stat()
        return (
            manifest.get("version") == 2
            and manifest.get("file_size") == int(st.st_size)
            and abs(manifest.get("file_mtime", 0.0) - st.st_mtime) < 1e-6
        )
    except Exception:
        return False


def _stored_pyramid_ok(ctx, rec) -> bool:
    """Whether a generated column's image pyramid can be read."""
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return False
    ent = ctx._ensure_image_index(rec["parquet"])
    return bool(ent is not None and ent.get("manifest") is not None)


def _image_load_manifest(ctx, parquet_path):
    import json
    mf = _image_index_dir(parquet_path) / "manifest.json"
    if not mf.exists():
        return None
    try:
        with open(mf, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _image_level_2d(ctx, parquet_path, block):
    ent = getattr(ctx, "_image_index_cache", {}).get(parquet_path)
    if ent is None:
        return None
    lv = ent.get("levels", {}).get(block)
    if lv is None:
        manifest = ent.get("manifest") or {}
        for info in manifest.get("levels", []):
            if info["block"] == block:
                lv = _image_load_level(parquet_path, info)
                ent.setdefault("levels", {})[block] = lv
                break
    return lv


def _channel_full_range(ctx, task_id, parquet_path, channel, extra_stages=None):
    """(min, max) over every finite value of a channel, or None.

    Uses the same data path as the level histogram (upstream stages
    applied) but never samples, so "Reset" shows the true full range.
    Results are cached until the module saves or another project opens.
    """
    import numpy as np

    if not parquet_path or not channel:
        return None
    key = (str(task_id or ""), str(parquet_path), str(channel))
    cache = getattr(ctx, "_channel_range_cache", None)
    if cache is None:
        cache = {}
        ctx._channel_range_cache = cache
    if extra_stages is None and key in cache:
        return cache[key]
    values = ctx._figure_channel_values(
        task_id, parquet_path, channel, max_points=None,
        extra_stages=extra_stages,
    )
    if values is None or values.size == 0:
        result = None
    else:
        result = (float(np.nanmin(values)), float(np.nanmax(values)))
    if extra_stages is None:
        if len(cache) > 32:
            cache.clear()
        cache[key] = result
    return result


def _image_filter_worker(ctx):
    worker = getattr(ctx, "_image_filter_worker_obj", None)
    if worker is None:
        worker = _ImageFilterWorker()
        ctx._image_filter_worker_obj = worker
        ctx._image_filter_cache = {}
        ctx._image_filter_pending = {}
    return worker
