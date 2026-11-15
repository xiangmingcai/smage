"""Region module state and Region → gate adapters (Qt-free).

A Region module keeps its internal detection gates and the Region tree in
``Module/<id>/regions.json``.  Downstream modules treat every Region as a
spatial gate: the union of its sub-region polygons on the current file.
"""
from __future__ import annotations

import json
from pathlib import Path

from smage.core.region_detection import region_parts_pixel_count


def _region_store_path(ctx, task_def) -> Path | None:
    if ctx.project is None or not task_def.get("id"):
        return None
    from smage.data import files as _data_files

    folder = _data_files._module_dir(ctx, task_def.get("id"))
    return folder / "regions.json" if folder is not None else None


def _empty_store() -> dict:
    return {
        "version": 1,
        "files": [],
        "gates": [],
        "per_file": {},
        "regions": [],
    }


def _region_store_load(ctx, task_def) -> dict:
    """Load a Region module's store (cached until the module is saved)."""
    cache = getattr(ctx, "_region_store_cache", None)
    if cache is None:
        cache = {}
        ctx._region_store_cache = cache
    path = _region_store_path(ctx, task_def)
    key = str(path) if path is not None else None
    if key is not None and key in cache:
        return cache[key]
    store = _empty_store()
    if path is not None and path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            store["files"] = [str(p) for p in data.get("files") or []]
            store["gates"] = list(data.get("gates") or [])
            store["per_file"] = dict(data.get("per_file") or {})
            store["regions"] = list(data.get("regions") or [])
        except (OSError, ValueError, TypeError):
            store = _empty_store()
    # The file list and detection settings also live in params; the JSON is
    # the canonical store for the trees.
    params = task_def.get("params") or {}
    if not store["files"] and params.get("files"):
        store["files"] = [str(p) for p in params.get("files") or []]
    if key is not None:
        if len(cache) > 32:
            cache.clear()
        cache[key] = store
    return store


def _region_store_save(ctx, task_def, store: dict) -> None:
    path = _region_store_path(ctx, task_def)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "files": [str(p) for p in store.get("files") or []],
        "gates": list(store.get("gates") or []),
        "per_file": dict(store.get("per_file") or {}),
        "regions": list(store.get("regions") or []),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    cache = getattr(ctx, "_region_store_cache", None)
    if cache is not None:
        cache.pop(str(path), None)


def _region_task_store(ctx, task_def) -> dict:
    """The store for a Region task, or an empty one for other task types."""
    if (task_def or {}).get("type") != "region":
        return _empty_store()
    return _region_store_load(ctx, task_def)


def _regions_for(ctx, task_def) -> list:
    return list(_region_task_store(ctx, task_def).get("regions") or [])


def _region_by_id(ctx, task_def, region_id) -> dict | None:
    rid = str(region_id)
    for region in _regions_for(ctx, task_def):
        if str(region.get("id")) == rid:
            return region
    return None


def _region_parts(region, parquet_path) -> list | None:
    """Region parts for one file, or None when the Sample is absent."""
    if not region or parquet_path is None:
        return None
    samples = region.get("samples") or {}
    key = str(parquet_path)
    sample = samples.get(key)
    if sample is None:
        # Tolerate Path-like keys written by older stores.
        for path, value in samples.items():
            if str(path) == key:
                sample = value
                break
    if sample is None:
        return None
    parts = []
    for sub in sample.get("sub_regions") or []:
        inside = sub.get("in") or []
        if inside:
            parts.append({
                "kind": "region",
                "in": inside,
                "out": list(sub.get("out") or []),
            })
    return parts


def _region_gate(ctx, task_def, region_id, parquet_path=None) -> dict | None:
    """A synthetic spatial gate for one Region (downstream representation)."""
    region = _region_by_id(ctx, task_def, region_id)
    if region is None:
        return None
    if parquet_path is not None:
        store = _region_task_store(ctx, task_def)
        if str(parquet_path) not in {
            str(path) for path in store.get("files") or []
        }:
            return None
    parts = _region_parts(region, parquet_path)
    # A missing Sample and an empty Sample both produce an empty parts
    # list downstream; Region Size distinguishes the two cases.
    return {
        "id": str(region.get("id")),
        "name": str(region.get("name") or "Region"),
        "type": "region",
        "x": "x",
        "y": "y",
        "parent": None,
        "parts": list(parts or []),
    }


def _region_gates_for(ctx, task_def, parquet_path=None) -> list:
    """All Regions of a Region task as synthetic gates."""
    gates = []
    for region in _regions_for(ctx, task_def):
        gate = _region_gate(ctx, task_def, region.get("id"), parquet_path)
        if gate is not None:
            gates.append(gate)
    return gates


def _region_options(ctx, task_id) -> list:
    """[(label, {"task_id","gate_id","kind":"region"}), ...] upstream."""
    from smage.data import files as _data_files

    options = []
    for task in _data_files._task_chain(ctx, task_id):
        if task.get("id") == task_id or task.get("type") != "region":
            continue
        module_name = task.get("name", "Region")
        for region in _regions_for(ctx, task):
            options.append((
                f"{module_name} → {region.get('name', region.get('id'))}",
                {
                    "task_id": task.get("id"),
                    "gate_id": str(region.get("id")),
                    "kind": "region",
                },
            ))
    return options


def _region_task_regions(ctx, task_id) -> list:
    """Every upstream Region with source info (for Region Size)."""
    from smage.data import files as _data_files

    out = []
    for task in _data_files._task_chain(ctx, task_id):
        if task.get("id") == task_id or task.get("type") != "region":
            continue
        store = _region_store_load(ctx, task)
        module_name = str(task.get("name") or "Region")
        for region in store.get("regions") or []:
            out.append({
                "task_id": task.get("id"),
                "module_name": module_name,
                "region": region,
            })
    return out


def _region_area(ctx, task_id, parquet_path, region_record,
                 x=None, y=None) -> int | None:
    """Pixel area of one Region on one file; None when the Sample is absent."""
    parts = _region_parts(region_record, parquet_path)
    if parts is None:
        return None
    if not parts:
        return 0
    if x is None or y is None:
        from smage.data import columns as _data_columns

        values = _data_columns._column_data(
            ctx, task_id, parquet_path, ["x", "y"]
        )
        x = values.get("x")
        y = values.get("y")
    if x is None or y is None:
        return None
    return region_parts_pixel_count(parts, x, y)


def _region_image_grid(ctx, task_id, parquet_path, channel):
    """Reconstruct one channel as ``(image, x_origin, y_origin)``.

    Returns ``(None, None, None)`` when the file has no image index or the
    channel is not available.  The map pipeline is applied, so unmixed and
    calculated channels work too.
    """
    import numpy as np

    manifest = ctx._image_load_manifest(parquet_path)
    if manifest is None:
        return None, None, None
    from smage.data import columns as _data_columns

    values = _data_columns._column_data(
        ctx, task_id, parquet_path, ["x", "y", channel]
    )
    if channel not in values or "x" not in values or "y" not in values:
        return None, None, None
    x0 = float(manifest["xmin"])
    y0 = float(manifest["ymin"])
    width = int(round(float(manifest["xmax"]) - x0)) + 1
    height = int(round(float(manifest["ymax"]) - y0)) + 1
    if width <= 0 or height <= 0:
        return None, None, None
    xi = np.rint(np.asarray(values["x"], dtype="float64") - x0)
    yi = np.rint(np.asarray(values["y"], dtype="float64") - y0)
    data = np.asarray(values[channel], dtype="float64")
    image = np.full((height, width), np.nan, dtype="float64")
    ok = (
        (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        & np.isfinite(data)
    )
    image[yi[ok].astype("int64"), xi[ok].astype("int64")] = data[ok]
    return image, x0, y0


def _region_image_gate_mask(ctx, task_id, parquet_path, gates, gate_id,
                            per_file=None):
    """2-D detection-range mask for the selected internal gate.

    ``per_file`` holds the per-file geometry overrides of the gate tree, so
    each file is detected inside its own gate shape.  Boolean gates (NOT /
    AND / OR) are resolved by the shared gate code.
    """
    import numpy as np

    manifest = ctx._image_load_manifest(parquet_path)
    if manifest is None or not gates or not gate_id:
        return None
    from smage.core.gates import (
        _compute_gate_filter_mask,
        _resolve_gates_for_file,
    )

    gates = _resolve_gates_for_file(
        gates, per_file or {}, str(parquet_path)
    )

    x0 = float(manifest["xmin"])
    y0 = float(manifest["ymin"])
    width = int(round(float(manifest["xmax"]) - x0)) + 1
    height = int(round(float(manifest["ymax"]) - y0)) + 1
    yy, xx = np.mgrid[0:height, 0:width]
    x = (xx.ravel() + x0).astype("float64")
    y = (yy.ravel() + y0).astype("float64")
    # Internal Region gates have no axis names; every gate runs on image x/y.
    image_gates = [
        {**gate, "x": "x", "y": "y"} for gate in gates
    ]
    try:
        mask = _compute_gate_filter_mask(
            image_gates, gate_id, parquet_path, {},
            existing={"x", "y"}, extra={"x": x, "y": y},
        )
    except Exception:  # noqa: BLE001
        return None
    if mask is None:
        return None
    return np.asarray(mask, dtype=bool).reshape(height, width)


def _clear_region_cache(ctx) -> None:
    ctx._region_store_cache = {}
