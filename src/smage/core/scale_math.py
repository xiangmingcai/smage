"""Scale / axis value math shared by the Scale and Figure modules.

No Qt: the axis *widgets* live in ``widgets``; only the value transforms are
here.
"""
from __future__ import annotations


def _apply_scale_transform(x, config):
    """Apply a scaling transform (linear/log10/arcsinh) to a numeric array.

    log10 and arcsinh are applied to the magnitude with the sign restored, so
    negatives stay negative and zero stays zero.
    """
    import numpy as np
    if config is None:
        return x
    method = config.get("method", "linear")
    if method == "log10":
        abs_x = np.abs(x)
        with np.errstate(divide="ignore", invalid="ignore"):
            y = np.log10(abs_x)
        y = np.where(abs_x == 0, 0.0, y)  # zero stays zero
        return np.sign(x) * y
    if method == "arcsinh":
        cofactor = config.get("param")
        try:
            cofactor = float(cofactor) if cofactor else 5.0
        except (TypeError, ValueError):
            cofactor = 5.0
        abs_x = np.abs(x)
        y = np.arcsinh(abs_x / cofactor)
        return np.sign(x) * y
    return x


def _apply_scale_transforms(x, configs):
    """Apply a chain of scaling transforms (in order) to a numeric array."""
    for config in (configs or []):
        x = _apply_scale_transform(x, config)
    return x


def _forward_scale_value(value, configs):
    """Map an original data value into the plot's (transformed) domain."""
    import numpy as np

    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    for config in (configs or []):
        out = float(np.asarray(_apply_scale_transform(np.float64(out), config)))
    return out


def _inverse_scale_value(value: float, configs) -> float:
    """Map a transformed value back to the original data value."""
    import math

    out = float(value)
    for config in reversed(list(configs or [])):
        method = (config or {}).get("method", "linear")
        try:
            if method == "log10":
                out = math.copysign(10.0 ** abs(out), out) if out else 0.0
            elif method == "arcsinh":
                try:
                    cofactor = float((config or {}).get("param") or 5.0)
                except (TypeError, ValueError):
                    cofactor = 5.0
                out = math.copysign(math.sinh(abs(out)) * cofactor, out) if out else 0.0
        except (OverflowError, ValueError):
            # The transformed value is too far out to map back (e.g. an axis
            # auto-ranged to raw pixel coordinates under log10). Callers treat
            # a non-finite result as "no original ticks".
            return float("nan")
    return out


def _round_value_candidates(low: float, high: float) -> list:
    """Round data values (…, 1, 2, 5, 10, 20, 50, …) inside [low, high]."""
    import math

    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return []
    out = [0.0] if low <= 0.0 <= high else []
    top = max(abs(low), abs(high))
    bottom = min(abs(low), abs(high))
    if top <= 0:
        return sorted(out)
    start = math.floor(math.log10(bottom)) if bottom > 0 else 0
    end = math.ceil(math.log10(top))
    if end - start > 12:
        start = end - 12
    for exponent in range(start, end + 1):
        for mantissa in (1.0, 2.0, 5.0):
            value = mantissa * (10.0 ** exponent)
            for signed in (value, -value):
                if low <= signed <= high and signed not in out:
                    out.append(signed)
    return sorted(out)


def _nice_ticks(low: float, high: float, count: int = 6) -> list:
    """Round tick values (1/2/5 steps) covering [low, high]."""
    import math

    span = high - low
    if not math.isfinite(span) or span <= 0:
        return []
    raw = span / max(1, count - 1)
    exponent = math.floor(math.log10(raw))
    base = raw / (10 ** exponent)
    step = (1.0 if base <= 1 else 2.0 if base <= 2 else 5.0 if base <= 5 else 10.0)
    step *= 10 ** exponent
    if not math.isfinite(step) or step <= 0:
        return []
    value = math.ceil(low / step) * step
    out = []
    while value <= high + step * 1e-9 and len(out) < 60:
        out.append(round(value, 12))
        value += step
    return out
