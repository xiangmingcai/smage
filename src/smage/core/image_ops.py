"""Array-level image operations (thresholding, label relabeling, pooling).

No Qt: these operate on numpy arrays only.
"""
from __future__ import annotations


def _otsu_threshold(values) -> float:
    """Return an Otsu threshold for a finite 1-D array (0.0 when undecidable)."""
    import numpy as np

    v = np.asarray(values, dtype="float64")
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0
    vmin, vmax = float(v.min()), float(v.max())
    if not vmax > vmin:
        return vmin
    hist, edges = np.histogram(v, bins=256, range=(vmin, vmax))
    hist = hist.astype("float64")
    centers = (edges[:-1] + edges[1:]) / 2.0
    weight = np.cumsum(hist)
    total = weight[-1]
    if total <= 0:
        return vmin
    mean_bg = np.cumsum(hist * centers)
    mean_total = mean_bg[-1]
    # Between-class variance for every split (ignore empty sides).
    w0 = weight[:-1]
    w1 = total - w0
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return vmin
    m0 = np.zeros_like(w0)
    m0[valid] = mean_bg[:-1][valid] / w0[valid]
    m1 = np.zeros_like(w1)
    m1[valid] = (mean_total - mean_bg[:-1][valid]) / w1[valid]
    between = np.zeros_like(w0)
    between[valid] = w0[valid] * w1[valid] * (m0[valid] - m1[valid]) ** 2
    return float(centers[int(np.argmax(between))])


def _relabel_sequential(labels, min_area=1):
    """Drop labels smaller than `min_area` and renumber them 1..N (0 = background)."""
    import numpy as np
    from scipy import ndimage

    if labels.size == 0:
        return labels
    counts = np.bincount(labels.ravel())
    keep = np.zeros(counts.size, dtype=bool)
    if counts.size > 1:
        keep[1:] = counts[1:] >= max(1, int(min_area))
    if not keep.any():
        return np.zeros_like(labels)
    # Map old ids to 1..N in ascending order.
    remap = np.zeros(counts.size, dtype=np.int32)
    remap[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    out = remap[labels]
    # `ndimage.label` already yields contiguous ids, but filtering can leave gaps.
    return out.astype(np.int32)


def _row_mode(blocks):
    """Mode (most frequent value) of each row of an ``(N, k)`` integer array.

    Ties are broken toward the smallest value (a stable result independent of
    input order). Used to downsample a label image by taking each block's
    dominant label instead of its mean.
    """
    import warnings

    import numpy as np
    from scipy.stats import mode as _scipy_mode

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        modes, _counts = _scipy_mode(blocks, axis=1, keepdims=False)
    return np.asarray(modes, dtype=blocks.dtype).reshape(blocks.shape[0])


def _mode_pool(arr2d, block):
    """Downsample a 2-D integer array by ``block``, returning the mode per block."""
    import math

    import numpy as np

    height, width = arr2d.shape
    nby = int(math.ceil(height / block))
    nbx = int(math.ceil(width / block))
    ph = nby * block
    pw = nbx * block
    padded = np.zeros((ph, pw), dtype=arr2d.dtype)
    padded[:height, :width] = arr2d
    blocks = (
        padded.reshape(nby, block, nbx, block)
        .transpose(0, 2, 1, 3)
        .reshape(nby * nbx, block * block)
    )
    return _row_mode(blocks).reshape(nby, nbx)


def _pool_mask(mask, target_blocks):
    """Pool a (H, W) uint8 mask into {block: (nby, nbx) float32 pass-fraction}."""
    import numpy as np
    targets = sorted(int(b) for b in target_blocks)
    if not targets:
        return {}
    out = {}
    m = mask.astype(np.float64)
    c = np.ones(m.shape, dtype=np.float64)
    block = 1
    tset = set(targets)
    if block in tset:
        out[block] = (m / np.maximum(c, 1.0)).astype(np.float32)
    while block < targets[-1]:
        block *= 2
        h, w = m.shape
        nby = (h + 1) // 2
        nbx = (w + 1) // 2
        ph, pw = nby * 2, nbx * 2
        pm = np.zeros((ph, pw), dtype=np.float64)
        pc = np.zeros((ph, pw), dtype=np.float64)
        pm[:h, :w] = m
        pc[:h, :w] = c
        m = pm.reshape(nby, 2, nbx, 2).sum(axis=(1, 3))
        c = pc.reshape(nby, 2, nbx, 2).sum(axis=(1, 3))
        if block in tset:
            out[block] = (m / np.maximum(c, 1.0)).astype(np.float32)
    return out
