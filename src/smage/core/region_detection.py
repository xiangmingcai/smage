"""Region detection and polygon/area helpers (Qt-free).

Signal Expansion turns one intensity channel into connected signal regions;
SAM masks are converted with the same polygon/area code.  All polygons are
stored in image data coordinates (x right, y down), exactly like gate
geometry, so they can be reused by downstream gate masks.
"""
from __future__ import annotations

import math
import time

from smage.core.gates import _point_in_polygon
from smage.core.image_ops import _otsu_threshold


def _report_step(progress, step: str, seconds, detail: str = "") -> None:
    """Send one timed step to an optional progress callback.

    ``seconds=None`` sends a message without an elapsed time (warnings).
    """
    if progress is None:
        return
    try:
        value = None if seconds is None else float(seconds)
        progress(str(step), value, str(detail))
    except Exception:  # noqa: BLE001 - logging must not break detection
        pass


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


def _rings_from_mask(mask, x_origin: float, y_origin: float,
                     tolerance: float = 1.0):
    """Return ``(in_rings, out_rings)`` from a boolean mask.

    Rings are classified by nesting depth: even depth (0, 2, …) is filled
    (``in``), odd depth (1, 3, …) is a hole (``out``).  The final Region is
    the even-odd fill of all rings, so an island drawn inside a hole is
    represented without deleting the surrounding ``out`` ring.
    """
    import numpy as np

    binary = np.asarray(mask, dtype=bool)
    if not binary.any():
        return [], []
    # contourpy clips a shape that touches the array border into open
    # polylines.  Those cannot be classified against an enclosing ring, so a
    # border-touching component loses its holes (every line looks like an
    # `in` ring and the even-odd fill is inverted).  A one-pixel empty frame
    # makes every contour closed; the coordinates are shifted back below.
    binary = np.pad(binary, 1, mode="constant", constant_values=False)
    try:
        import contourpy
    except Exception:  # noqa: BLE001 - contourpy ships with matplotlib
        return [], []
    generator = contourpy.contour_generator(
        z=binary.astype("float32"), name="serial"
    )
    try:
        lines = list(generator.lines(0.5))
    except Exception:  # noqa: BLE001
        return [], []
    raw = []
    for line in lines:
        if len(line) < 3:
            continue
        points = [
            [float(x_origin) + float(point[0]) - 1.0,
             float(y_origin) + float(point[1]) - 1.0]
            for point in line
        ]
        if _shoelace_area(points) > 0:
            raw.append(points)
    if not raw:
        return [], []
    # Classify before simplification: a vertex on an enclosing ring has an
    # even/odd containment count that defines in/out.
    inside_rings = []
    outside_rings = []
    for index, points in enumerate(raw):
        point = points[0]
        depth = 0
        px = np.asarray([point[0]], dtype="float64")
        py = np.asarray([point[1]], dtype="float64")
        for other_index, other in enumerate(raw):
            if other_index == index:
                continue
            if bool(_point_in_polygon(px, py, other)[0]):
                depth += 1
        epsilon = min(float(tolerance), 1.0) if depth % 2 else tolerance
        simplified = _simplify_closed(points, epsilon)
        if len(simplified) < 3 or _shoelace_area(simplified) <= 0:
            continue
        ring = [[float(x), float(y)] for x, y in simplified]
        if depth % 2 == 0:
            inside_rings.append(ring)
        else:
            outside_rings.append(ring)
    return inside_rings, outside_rings


def _mask_region(mask, x_origin: float, y_origin: float,
                 tolerance: float = 1.0):
    """One region part from a mask; ``None`` when the mask is empty."""
    inside, outside = _rings_from_mask(
        mask, x_origin, y_origin, tolerance
    )
    if not inside:
        return None
    return {"kind": "region", "in": inside, "out": outside}


def _mask_region_parts(mask, x_origin: float, y_origin: float,
                       tolerance: float = 1.0):
    """One region part per connected component of a boolean mask.

    This is the detection output: every connected mask component becomes a
    sub-region, with its holes stored in ``out``.
    """
    import numpy as np
    from scipy import ndimage

    binary = np.asarray(mask, dtype=bool)
    if not binary.any():
        return []
    labels, count = ndimage.label(binary, structure=np.ones((3, 3)))
    if count == 0:
        return []
    parts = []
    # Trace each component inside its own bounding box.  Running contourpy
    # over the whole image for every component made a detection with 2000
    # parts take over two minutes.
    for label_id, region in enumerate(
        ndimage.find_objects(labels), start=1
    ):
        if region is None:
            continue
        ys, xs = region
        component = labels[ys, xs] == label_id
        if not component.any():
            continue
        part = _mask_region(
            component,
            float(x_origin) + xs.start,
            float(y_origin) + ys.start,
            tolerance,
        )
        if part is not None:
            parts.append(part)
    return parts


def region_parts_mask(parts, x, y):
    """Boolean mask of the union of region parts (even-odd per part)."""
    import numpy as np

    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    inside = np.zeros(x.shape, dtype=bool)
    for part in parts or ():
        part_inside = np.zeros(x.shape, dtype=bool)
        for ring in list(part.get("in") or []) + list(part.get("out") or []):
            if len(ring) < 3:
                continue
            part_inside ^= _point_in_polygon(x, y, ring)
        inside |= part_inside
    return inside


def _region_parts_raster(parts, width, height, xmin: float = 0.0,
                         ymin: float = 0.0):
    """Rasterise region parts into a ``(height, width)`` boolean mask.

    Image filters evaluate a Region on every pixel of a 2040x2040 image; the
    per-point ray casting in :func:`region_parts_mask` is much slower than
    filling the rings with Pillow's C polygon rasteriser.  Each part is the
    XOR (even-odd fill) of its rings, and parts are OR-ed.
    """
    import numpy as np

    width = max(0, int(width))
    height = max(0, int(height))
    out = np.zeros((height, width), dtype=bool)
    if width == 0 or height == 0 or not parts:
        return out
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001 - Pillow is a normal dependency
        yy, xx = np.mgrid[0:height, 0:width]
        x = xx.ravel().astype("float64") + float(xmin)
        y = yy.ravel().astype("float64") + float(ymin)
        return region_parts_mask(parts, x, y).reshape(height, width)
    for part in parts or ():
        part_mask = np.zeros((height, width), dtype=bool)
        rings = list(part.get("in") or []) + list(part.get("out") or [])
        for ring in rings:
            if len(ring) < 3:
                continue
            image = Image.new("L", (width, height), 0)
            # Pixel (i, j) is sampled at its integer coordinate by the gate
            # masks; Pillow fills around pixel centres, so shift by half a
            # pixel to keep the filtered edges in the same place.
            ImageDraw.Draw(image).polygon(
                [
                    (float(point[0]) - float(xmin) - 0.5,
                     float(point[1]) - float(ymin) - 0.5)
                    for point in ring
                ],
                fill=1,
            )
            part_mask ^= np.asarray(image, dtype=bool)
        out |= part_mask
    return out


def region_parts_pixel_count(parts, x, y) -> int:
    """Number of points inside the union of region parts."""
    import numpy as np

    return int(np.count_nonzero(region_parts_mask(parts, x, y)))


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


def _fill_holes(binary, max_area: int = 0):
    """Fill all holes, or only holes up to ``max_area`` pixels."""
    import numpy as np
    from scipy import ndimage

    limit = int(max_area or 0)
    if limit <= 0:
        return ndimage.binary_fill_holes(binary)
    background = ~np.asarray(binary, dtype=bool)
    # Match binary_fill_holes: a hole is background not connected to the
    # image border through 4-neighbours.
    cross = np.array([
        [0, 1, 0],
        [1, 1, 1],
        [0, 1, 0],
    ], dtype=bool)
    labels, count = ndimage.label(background, structure=cross)
    if count == 0:
        return np.asarray(binary, dtype=bool)
    counts = np.bincount(labels.ravel())
    fill = np.zeros(count + 1, dtype=bool)
    fill[1:] = counts[1:] <= limit
    border = np.concatenate((
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    ))
    fill[np.unique(border)] = False
    fill[0] = False
    return np.asarray(binary, dtype=bool) | fill[labels]


def signal_expansion(image, gate_mask=None, threshold=None,
                     percentile=None, smooth: float = 1.0,
                     expand: int = 2, fill_holes: bool = True,
                     min_area: int = 50, tolerance: float = 1.0,
                     x_origin: float = 0.0, y_origin: float = 0.0,
                     fill_max_area: int = 0, progress=None):
    """Detect signal regions in one channel image.

    Returns ``(parts, used_threshold, binary_mask)``.  With neither
    ``threshold`` nor ``percentile`` set, Otsu's method is applied to the
    smoothed values inside the detection gate.  ``percentile`` uses that
    value of this image's own pixel distribution (30 -> about 70% of the
    pixels are above it).  Both are evaluated per image, never shared
    between files.  ``progress(step, seconds, detail)`` is called after each
    stage so the module can log where the time goes.
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
    started = time.perf_counter()
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
        _report_step(
            progress, "smooth", time.perf_counter() - started,
            f"sigma={float(smooth):g}",
        )
    else:
        _report_step(progress, "smooth", 0.0, "skipped")

    started = time.perf_counter()
    sample_mask = finite if gate is None else (finite & gate)
    sample = work[sample_mask]
    auto = threshold is None and percentile is None
    used_percentile = None
    if auto:
        used_threshold = _otsu_threshold(sample) if sample.size else 0.0
    elif percentile is not None:
        try:
            used_percentile = min(
                100.0, max(0.0, float(percentile))
            )
        except (TypeError, ValueError):
            used_percentile = None
        if used_percentile is None:
            auto = True
            used_threshold = _otsu_threshold(sample) if sample.size else 0.0
        else:
            used_threshold = (
                float(np.percentile(sample, used_percentile))
                if sample.size else 0.0
            )
    else:
        try:
            used_threshold = float(threshold)
        except (TypeError, ValueError):
            auto = True
            used_threshold = _otsu_threshold(sample) if sample.size else 0.0
    if auto:
        mode_label = "Otsu"
    elif used_percentile is not None:
        mode_label = f"P{used_percentile:g}"
    else:
        mode_label = "manual"
    _report_step(
        progress, "threshold", time.perf_counter() - started,
        f"{mode_label}={used_threshold:.6g}",
    )

    started = time.perf_counter()
    binary = work > used_threshold
    if gate is not None:
        binary &= gate
    _report_step(
        progress, "binarize", time.perf_counter() - started,
        "clipped to gate" if gate is not None else "full image",
    )

    started = time.perf_counter()
    if int(expand) > 0:
        structure = _disk(int(expand))
        binary = ndimage.binary_dilation(binary, structure=structure)
        _report_step(
            progress, "expand", time.perf_counter() - started,
            f"radius={int(expand)}",
        )
    else:
        _report_step(progress, "expand", 0.0, "skipped")

    started = time.perf_counter()
    if fill_holes:
        before = binary.copy()
        binary = _fill_holes(binary, fill_max_area)
        filled = int(np.count_nonzero(binary & ~before))
        limit = int(fill_max_area or 0)
        _report_step(
            progress, "fill holes", time.perf_counter() - started,
            f"filled {filled} px, "
            + (f"max hole area={limit} px" if limit > 0 else "all holes"),
        )
    else:
        _report_step(progress, "fill holes", 0.0, "skipped")

    started = time.perf_counter()
    if gate is not None:
        binary &= gate
    binary = _filter_components(binary, min_area)
    _report_step(
        progress, "filter components", time.perf_counter() - started,
        f"min area={int(min_area)}",
    )
    coverage = (
        float(np.count_nonzero(binary)) / float(binary.size)
        if binary.size else 0.0
    )
    if coverage >= 0.8:
        _report_step(
            progress, "warning", None,
            f"foreground covers {coverage * 100:.0f}% of the image; reduce "
            "Expand, raise the threshold or use a cleaner channel",
        )

    started = time.perf_counter()
    parts = _mask_region_parts(
        binary, x_origin, y_origin, tolerance
    )
    _report_step(
        progress, "contours", time.perf_counter() - started,
        f"{len(parts)} region part(s), tolerance={float(tolerance):g}",
    )
    return parts, float(used_threshold), binary


def sam_mask_polygons(mask, gate_mask=None, min_area: int = 0,
                      tolerance: float = 1.0,
                      x_origin: float = 0.0, y_origin: float = 0.0):
    """Convert one SAM mask to region parts, with the same filters."""
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
    return _mask_region_parts(binary, x_origin, y_origin, tolerance)


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
