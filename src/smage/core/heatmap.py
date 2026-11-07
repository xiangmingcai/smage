"""Heatmap sampling, normalization and dendrogram math.

No Qt: the canvas lives in the Heatmap module; these functions only handle
the matrix.
"""
from __future__ import annotations


def _heatmap_sample_counts(sizes: dict, mode: str, method: str, value: int) -> dict:
    """Rows to keep per file for the Heatmap module.

    ``mode`` is "full" or "sample"; with sampling, ``method`` is "uniform"
    (``value`` rows per file) or "proportional" (``value`` rows distributed by
    file size). A file never contributes more rows than it has.
    """
    sizes = {str(p): int(n) for p, n in (sizes or {}).items() if int(n) > 0}
    if not sizes:
        return {}
    if mode != "sample":
        return dict(sizes)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return dict(sizes)
    if method == "uniform":
        return {path: min(rows, value) for path, rows in sizes.items()}
    total = sum(sizes.values())
    counts = {
        path: min(rows, max(0, int(round(value * rows / total))))
        for path, rows in sizes.items()
    }
    # Distribute the rounding remainder to files that still have room.
    leftover = value - sum(counts.values())
    for _ in range(8):
        if leftover <= 0:
            break
        room = {
            path: sizes[path] - counts[path]
            for path in sizes if sizes[path] > counts[path]
        }
        if not room:
            break
        room_total = sum(room.values())
        given = 0
        for path, capacity in room.items():
            if leftover <= 0:
                break
            share = min(
                capacity, leftover,
                max(1, int(round(leftover * capacity / room_total))),
            )
            counts[path] += share
            leftover -= share
            given += share
        if given == 0:
            break
    return counts


def _heatmap_normalize(matrix, mode: str):
    """Return a float copy normalized by 'raw', 'column' or 'row' z-score."""
    import warnings

    import numpy as np

    values = np.array(matrix, dtype="float64", copy=True)
    if values.ndim != 2 or mode not in ("column", "row"):
        return values
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(invalid="ignore", divide="ignore"):
            axis = 0 if mode == "column" else 1
            mean = np.nanmean(values, axis=axis, keepdims=True)
            std = np.nanstd(values, axis=axis, keepdims=True)
            std = np.where(np.isfinite(std) & (std > 0), std, 1.0)
            return (values - mean) / std


def _heatmap_fill_nan(matrix, axis: int):
    """Fill NaN cells with the mean of their row (axis=1) or column (axis=0)."""
    import warnings

    import numpy as np

    values = np.array(matrix, dtype="float64", copy=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        fill = np.nanmean(values, axis=axis, keepdims=True)
    fill = np.where(np.isfinite(fill), fill, 0.0)
    return np.where(np.isfinite(values), values, fill)


def _heatmap_linkage(matrix, metric: str, method: str):
    """Dendrogram for a 2-D matrix (linkage + leaf order + draw coordinates)."""
    import numpy as np

    try:
        from scipy.cluster.hierarchy import dendrogram, linkage
    except ImportError:
        return None
    values = np.asarray(matrix, dtype="float64")
    if values.ndim != 2 or values.shape[0] < 2:
        return None
    if method not in ("average", "complete", "single", "ward"):
        method = "average"
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            warnings.simplefilter("ignore", UserWarning)
            z = linkage(
                values, method=method,
                metric="euclidean" if method == "ward" else metric,
            )
            info = dendrogram(z, no_plot=True)
    except Exception:  # noqa: BLE001
        return None
    return {
        "linkage": [[float(a), float(b), float(c), int(d)] for a, b, c, d in z],
        "order": [int(i) for i in info["leaves"]],
        "icoord": [[float(v) for v in item] for item in info["icoord"]],
        "dcoord": [[float(v) for v in item] for item in info["dcoord"]],
    }
