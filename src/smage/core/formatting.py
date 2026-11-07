"""Pure text/number formatting helpers.

No Qt and no project state: everything here turns a value into a string.
"""
from __future__ import annotations

import json

_SUPERSCRIPT = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def _format_bytes(size) -> str:
    """Human-readable size (1.2 GB, 340 MB, 12 KB, 512 B)."""
    try:
        value = float(max(0, int(size)))
    except (TypeError, ValueError):
        return "—"
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
    return f"{value:.0f} TB"


def _format_sci(value: float) -> str:
    """Mantissa×10^exponent (superscript); a mantissa of 1 is left out.

    The mantissa is an integer when it is whole and otherwise has one decimal
    ("2×10³", "1.5×10³", "10³" for 1000).
    """
    import math

    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(value):
        return str(value)
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    exponent = int(math.floor(math.log10(magnitude)))
    mantissa = round(magnitude / (10 ** exponent), 1)
    if mantissa >= 10:  # 9.96 rounds to 10
        mantissa /= 10
        exponent += 1
    text = str(int(mantissa)) if float(mantissa).is_integer() else f"{mantissa:.1f}"
    superscript = str(exponent).translate(_SUPERSCRIPT)
    if text == "1":
        return f"{sign}10{superscript}"
    return f"{sign}{text}×10{superscript}"


def _format_number(value) -> str:
    """Format a number compactly (no trailing zeros) for the range fields."""
    if value is None:
        return ""
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.10g}"


def _format_level(value) -> str:
    """Image channel Min/Max: one decimal is all the precision needed."""
    if value is None:
        return ""
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return ""


def _round_level(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _category_value_label(value) -> str:
    """Render a Col2Gate category value for the gate name (``<prefix><value>``)."""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def _json_stable(value) -> str:
    """Deterministic JSON text for cache keys (sets become sorted lists).

    Plain ``json.dumps(default=str)`` renders a set in hash order, which
    changes between runs, so a disk cache keyed that way would never hit.
    """
    def norm(item):
        if isinstance(item, dict):
            return {
                str(key): norm(val)
                for key, val in sorted(item.items(), key=lambda kv: str(kv[0]))
            }
        if isinstance(item, (list, tuple)):
            return [norm(entry) for entry in item]
        if isinstance(item, (set, frozenset)):
            return sorted(str(entry) for entry in item)
        return item

    return json.dumps(norm(value), sort_keys=True, default=str)


def _fcs_safe_channel_names(columns) -> tuple:
    """Channel names that are legal inside an FCS TEXT segment.

    TEXT keywords are separated by ``/``, so a channel name containing it
    would split into an empty keyword name (readers such as R's flowCore
    reject the file). ``$`` marks a keyword and is stripped by readers, so it
    is replaced too. Returns the safe names and one note per changed name.
    """
    names = []
    notes = []
    seen = {}

    def clean(value) -> str:
        text = str(value).replace("/", "_").replace("$", "_")
        text = "".join(ch for ch in text if ch >= " ").strip()
        return text or "channel"

    for column in columns:
        base = clean(column)
        count = seen.get(base.casefold(), 0) + 1
        seen[base.casefold()] = count
        name = base if count == 1 else f"{base}_{count}"
        if name != str(column):
            notes.append(
                f"channel '{column}' written as '{name}' "
                "(FCS does not allow '/' or '$' in names)"
            )
        names.append(name)
    return names, notes


def _heatmap_export_text(value) -> str:
    """Number written to the clipboard (more precision than a cell label)."""
    import math

    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(value):
        return ""
    return f"{value:.6g}"


def _heatmap_cell_text(value, decimals: int = 1) -> str:
    """Number shown inside a heatmap cell (fixed decimals, default 1)."""
    import math

    if value is None:
        return "NA"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(value):
        return "NA"
    try:
        decimals = max(0, min(6, int(decimals)))
    except (TypeError, ValueError):
        decimals = 1
    return f"{value:.{decimals}f}"
