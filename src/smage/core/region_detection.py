"""Region detection and polygon/area helpers (Qt-free).

Signal Expansion turns one intensity channel into connected signal regions;
SAM masks are converted with the same polygon/area code.  All polygons are
stored in image data coordinates (x right, y down), exactly like gate
geometry, so they can be reused by downstream gate masks.
"""
from __future__ import annotations

import math

from smage.core.gates import _point_in_polygon
from smage.core.image_ops import _otsu_threshold


def _disk(radius: int) -> "object":
    """A boolean disk structuring element for binary dilation."""
    import numpy as np

    radius = max(0, int(radius))
    if radius <= 0:
        return None
    yy, xx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return (xx * xx + yy * yy) <= radius * radius


def _shoelace_area(points) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        area += x0 * y1 - x1 * y0
    return abs(area) * 0.5


def _rdp(points, epsilon: float):
    """Ramer-Douglas-Peucker simplification of an open point list."""
    import numpy as np

    pts = np.asarray(points, dtype="float64")
    if pts.shape[0] < 3 or epsilon <= 0:
        return pts.tolist()
    keep = np.zeros(pts.shape[0], dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, pts.shape[0] - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        p0 = pts[start]
        p1 = pts[end]
        segment = p1 - p0
        length = float(np.hypot(segment[0], segment[1]))
        inner = pts[start + 1:end]
        if length <= 1e-12:
            distances = np.hypot(
                inner[:, 0] - p0[0], inner[:, 1] - p0[1]
            )
        else:
            distances = np.abs(
                segment[0] * (inner[:, 1] - p0[1])
                - segment[1] * (inner[:, 0] - p0[0])
            ) / length
        index = int(np.argmax(distances))
        if float(distances[index]) > epsilon:
            pivot = start + 1 + index
            keep[pivot] = True
            stack.append((start, pivot))
            stack.append((pivot, end))
    return pts[keep].tolist()


def _simplify_closed(points, epsilon: float):
    """Simplify a closed contour and keep at least three vertices."""
    import numpy as np

    pts = [list(point) for point in points]
    if len(pts) < 4 or epsilon <= 0:
        return pts
    # Split at the point farthest from the first one so the open RDP does
    # not collapse the ring into a line.
    arr = np.asarray(pts, dtype="float64")
    far = int(np.argmax(
        (arr[:, 0] - arr[0, 0]) ** 2 + (arr[:, 1] - arr[0, 1]) ** 2
    ))
    first = _rdp(pts[:far + 1], epsilon)
    second = _rdp(pts[far:] + pts[:1], epsilon)
    out = first[:-1] + second[:-1]
    return out if len(out) >= 3 else pts


def _mask_polygons(mask, x_origin: float, y_origin: float,
                   tolerance: float = 1.0):
    """Outer polygons of every connected component of a boolean mask.

    Contour coordinates are converted from grid cells to data coordinates
    (``x_origin + column``, ``y_origin + row``).  Holes are ignored: a
    component contributes its largest outer contour.
    """
    import numpy as np
    from scipy import ndimage

    binary = np.asarray(mask, dtype=bool)
    if not binary.any():
        return []
    labels, count = ndimage.label(binary, structure=np.ones((3, 3)))
    if count == 0:
        return []
    try:
        import contourpy
    except Exception:  # noqa: BLE001 - contourpy ships with matplotlib
        return []

    polygons = []
    for label_id in range(1, count + 1):
        component = labels == label_id
        if not component.any():
            continue
        generator = contourpy.contour_generator(
            z=component.astype("float32"), name="serial"
        )
        lines = list(generator.lines(0.5))
        if not lines:
            continue
        best = max(lines, key=lambda line: _shoelace_area(line))
        if len(best) < 3:
            continue
        points = [
            [float(x_origin) + float(point[0]),
             float(y_origin) + float(point[1])]
            for point in best
        ]
        simplified = _simplify_closed(points, tolerance)
        if len(simplified) >= 3 and _shoelace_area(simplified) > 0:
            polygons.append([[float(x), float(y)] for x, y in simplified])
    return polygons


def _filter_components(mask, min_area: int):
    """Drop connected components smaller than `min_area` pixels."""
    import numpy as np
    from scipy import ndimage

    binary = np.asarray(mask, dtype=bool)
    if not binary.any():
        return binary
    min_area = max(1, int(min_area))
    labels, count = ndimage.label(binary, structure=np.ones((3, 3)))
    if count == 0:
        return binary
    counts = np.bincount(labels.ravel())
    keep = np.zeros(counts.size, dtype=bool)
    keep[1:] = counts[1:] >= min_area
    if not keep.any():
        return np.zeros_like(binary)
    return keep[labels]


def signal_expansion(image, gate_mask=None, threshold=None, smooth: float = 1.0,
                     expand: int = 2, fill_holes: bool = True,
                     min_area: int = 50, tolerance: float = 1.0,
                     x_origin: float = 0.0, y_origin: float = 0.0):
    """Detect signal regions in one channel image.

    Returns ``(polygons, used_threshold, binary_mask)``.  Auto threshold uses
    Otsu's method over the smoothed values inside the detection gate.
    """
    import numpy as np
    from scipy import ndimage

    arr = np.asarray(image, dtype="float64")
    if arr.ndim != 2 or arr.size == 0:
        return [], 0.0, np.zeros((0, 0), dtype=bool)
    gate = None
    if gate_mask is not None:
        gate = np.asarray(gate_mask, dtype=bool)
        if gate.shape != arr.shape:
            gate = None
    finite = np.isfinite(arr)
    work = np.where(finite, arr, 0.0)
    if smooth and float(smooth) > 0:
        weights = finite.astype("float64")
        work = ndimage.gaussian_filter(
            work, float(smooth), mode="nearest"
        )
        weights = ndimage.gaussian_filter(
            weights, float(smooth), mode="nearest"
        )
        work = np.divide(
            work, weights,
            out=np.zeros_like(work), where=weights > 1e-8,
        )

    sample_mask = finite if gate is None else (finite & gate)
    sample = work[sample_mask]
    if threshold is None:
        used_threshold = _otsu_threshold(sample) if sample.size else 0.0
    else:
        try:
            used_threshold = float(threshold)
        except (TypeError, ValueError):
            used_threshold = _otsu_threshold(sample) if sample.size else 0.0

    binary = work > used_threshold
    if gate is not None:
        binary &= gate
    if int(expand) > 0:
        structure = _disk(int(expand))
        binary = ndimage.binary_dilation(binary, structure=structure)
    if fill_holes:
        binary = ndimage.binary_fill_holes(binary)
    if gate is not None:
        binary &= gate
    binary = _filter_components(binary, min_area)
    polygons = _mask_polygons(
        binary, x_origin, y_origin, tolerance
    )
    return polygons, float(used_threshold), binary


def sam_mask_polygons(mask, gate_mask=None, min_area: int = 0,
                      tolerance: float = 1.0,
                      x_origin: float = 0.0, y_origin: float = 0.0):
    """Convert one SAM mask to sub-region polygons, with the same filters."""
    import numpy as np

    binary = np.asarray(mask, dtype=bool)
    if gate_mask is not None:
        gate = np.asarray(gate_mask, dtype=bool)
        if gate.shape == binary.shape:
            binary = binary & gate
    if min_area and int(min_area) > 0:
        binary = _filter_components(binary, int(min_area))
    if not binary.any():
        return []
    return _mask_polygons(binary, x_origin, y_origin, tolerance)


def polygon_union_mask(polygons, x, y):
    """Boolean mask of points inside any polygon (works with n-D x/y)."""
    import numpy as np

    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    inside = np.zeros(x.shape, dtype=bool)
    for polygon in polygons or ():
        if len(polygon) < 3:
            continue
        inside |= _point_in_polygon(x, y, polygon)
    return inside


def polygon_pixel_count(polygons, x, y) -> int:
    """Number of points (pixels) inside the union of the polygons."""
    import numpy as np

    if not polygons:
        return 0
    return int(np.count_nonzero(polygon_union_mask(polygons, x, y)))
