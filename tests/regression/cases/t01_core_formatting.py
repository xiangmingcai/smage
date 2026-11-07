"""L1: pure formatting helpers (bytes, numbers, JSON, FCS names, heatmap text)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.core.formatting import (  # noqa: E402
    _category_value_label,
    _fcs_safe_channel_names,
    _format_bytes,
    _format_level,
    _format_number,
    _format_sci,
    _heatmap_cell_text,
    _heatmap_export_text,
    _json_stable,
    _round_level,
)


@harness.case("core.format.bytes", "L1", "core", tags=("smoke",))
def format_bytes() -> None:
    harness.eq(_format_bytes(0), "0 B")
    harness.eq(_format_bytes(-5), "0 B")
    harness.eq(_format_bytes(512), "512 B")
    harness.eq(_format_bytes(1024), "1.0 KB")
    harness.eq(_format_bytes(1536), "1.5 KB")
    harness.eq(_format_bytes(1024 ** 2), "1.0 MB")
    harness.eq(_format_bytes(100 * 1024 ** 2), "100 MB")
    harness.eq(_format_bytes(1024 ** 3), "1.0 GB")
    harness.eq(_format_bytes(123_456_789), "118 MB")
    harness.eq(_format_bytes("not a number"), "—")


@harness.case("core.format.sci", "L1", "core", tags=("smoke",))
def format_sci() -> None:
    harness.eq(_format_sci(0), "0")
    harness.eq(_format_sci(1000), "10³")
    harness.eq(_format_sci(1500), "1.5×10³")
    harness.eq(_format_sci(-1000), "-10³")
    harness.eq(_format_sci(0.001), "10⁻³")
    harness.eq(_format_sci(9960), "10⁴")
    harness.eq(_format_sci(float("nan")), "nan")
    harness.eq(_format_sci(float("inf")), "inf")


@harness.case("core.format.number_level", "L1", "core", tags=("smoke",))
def format_number_level() -> None:
    harness.eq(_format_number(None), "")
    harness.eq(_format_number(3.0), "3")
    harness.eq(_format_number(3.5), "3.5")
    harness.eq(_format_number(1 / 3), "0.3333333333")

    harness.eq(_format_level(None), "")
    harness.eq(_format_level(1.234), "1.2")
    harness.eq(_format_level("bad"), "")

    harness.eq(_round_level(1.26), 1.3)
    harness.eq(_round_level("bad"), None)


@harness.case("core.format.category", "L1", "core", tags=("smoke",))
def category_values() -> None:
    harness.eq(_category_value_label(1.0), "1")
    harness.eq(_category_value_label(1.5), "1.5")
    harness.eq(_category_value_label("A"), "A")
    harness.eq(_category_value_label(2), "2")


@harness.case("core.format.json_stable", "L1", "core", tags=("smoke",))
def json_stable() -> None:
    value = {"b": {2, 1}, "a": [1, 2], "nested": {"z": (3, 4)}}
    text = _json_stable(value)
    harness.eq(_json_stable(value), text, "same input gives the same text")
    parsed = json.loads(text)
    harness.eq(parsed["a"], [1, 2])
    harness.eq(parsed["b"], ["1", "2"], "sets are sorted strings")
    harness.eq(parsed["nested"]["z"], [3, 4], "tuples become lists")
    harness.check(text.startswith('{"a":'), "keys are sorted")


@harness.case("core.format.fcs_names", "L1", "core", tags=("smoke",))
def fcs_names() -> None:
    names, notes = _fcs_safe_channel_names(
        ["A/B", "A$B", "A B", "a b", "normal", "dup", "Dup"]
    )
    harness.eq(
        names,
        ["A_B", "A_B_2", "A B", "a b_2", "normal", "dup", "Dup_2"],
    )
    harness.eq(len(notes), 4, "four names were changed")

    names, notes = _fcs_safe_channel_names(["/", "$", "", "A\x01B"])
    harness.eq(names, ["_", "__2", "channel", "AB"])
    harness.eq(len(notes), 4, "every name was changed")


@harness.case("core.format.heatmap_text", "L1", "core", tags=("smoke",))
def heatmap_text() -> None:
    harness.eq(_heatmap_export_text(1.23456789), "1.23457")
    harness.eq(_heatmap_export_text(float("nan")), "")
    harness.eq(_heatmap_export_text("bad"), "")

    harness.eq(_heatmap_cell_text(1.234, 2), "1.23")
    harness.eq(_heatmap_cell_text(None), "NA")
    harness.eq(_heatmap_cell_text(float("nan")), "NA")
    harness.eq(_heatmap_cell_text("bad"), "NA")
    harness.eq(_heatmap_cell_text(1.234, 99), "1.234000", "decimals clamp to 6")
    harness.eq(_heatmap_cell_text(1.234, -1), "1", "negative decimals clamp to 0")


if __name__ == "__main__":
    raise SystemExit(harness.main())
