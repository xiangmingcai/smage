"""Feature Up / Gate2Col output records and their values.

Qt-free: parquet reads only (numpy/pandas imported locally).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _feature_signature_rules(params) -> list:
    """Rules as they affect the aggregated output, for the change signature.

    The default group-key row is stripped from the list, so projects saved
    before that row existed keep matching their already-calculated outputs
    (the column it carries was always written). Renaming the row or deleting
    it (``feature_rule`` false) adds a marker and invalidates the output.
    """
    feature = params.get("feature")
    rules = []
    marker = None
    for rule in params.get("rules") or []:
        if rule.get("method") != "feature":
            rules.append(rule)
            continue
        stored = (rule.get("name") or "").strip()
        if stored and stored != feature:
            marker = {"kind": "feature-key", "name": stored}
    if params.get("feature_rule", True) is False:
        marker = {"kind": "feature-key", "included": False}
    if marker is not None:
        rules = rules + [marker]
    return rules


def _feature_signature(ctx, params, parquet_path, task_id=None) -> str:
    """A change signature for one source file's aggregation output.

    Covers the feature column, every aggregation rule, the source file's
    own size/mtime and any upstream sampling of it, so a saved output is
    considered stale when any of them changes.
    """
    try:
        st = Path(parquet_path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "feature": params.get("feature"),
        "rules": _feature_signature_rules(params),
        "source": str(parquet_path),
        "stat": stat,
    }
    # Ignored feature values change the aggregated rows. Projects saved
    # before this setting existed keep their old signature until the
    # default is materialized (None = legacy, same as before).
    ignore = params.get("ignore_values")
    if ignore is not None:
        payload["ignore"] = sorted(float(v) for v in ignore)
    # Only fold in sampling when it actually applies, so projects without a
    # Subsample module keep matching their previously saved outputs.
    sample = ctx._subsample_sig(task_id, parquet_path)
    if sample:
        payload["sample"] = sample
    payload = json.dumps(payload, sort_keys=True)
    # hashlib (not hash()) so the signature is stable across restarts.
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _feature_outputs(ctx, task_id) -> list:
    """Data files produced by upstream Feature modules, newest chain first.

    Only outputs whose saved signature still matches the module's current
    settings (and whose parquet exists) are returned, so downstream
    modules always see the committed, up-to-date results.
    """
    if not task_id or ctx.project is None:
        return []
    out = []
    seen = set()
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id:
            continue
        if task.get("type") != "feature":
            continue
        params = ctx._feature_params_for(task)
        outputs = params.get("outputs") or {}
        for source, rec in outputs.items():
            if not isinstance(rec, dict):
                continue
            rel = rec.get("path")
            if not rel:
                continue
            path = ctx.project.path / rel
            if not path.is_file():
                continue
            if rec.get("signature") != ctx._feature_signature(
                params, source, task.get("id")
            ):
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": rec.get("name") or path.stem,
                "parquet": key,
                "columns": list(rec.get("columns") or []),
                "source": source,
                "feature_task": task.get("id"),
            })
    return out


def _feature_output(ctx, parquet_path):
    """Return the Feature output record for a parquet path, or None."""
    if not parquet_path or ctx.project is None:
        return None
    for task in ctx.project.workflow.get("tasks", []):
        for node in ctx._walk_tasks(task):
            if node.get("type") != "feature":
                continue
            params = ctx._feature_params_for(node)
            for source, rec in (params.get("outputs") or {}).items():
                if not isinstance(rec, dict) or not rec.get("path"):
                    continue
                path = ctx.project.path / rec["path"]
                if str(path) == str(parquet_path):
                    return {**rec, "parquet": str(path), "source": source,
                            "feature_task": node.get("id")}
    return None


def _feature_params_for(ctx, task_def) -> dict:
    """Saved params of a Feature task (its live snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "feature":
        snap = getattr(reg["page"], "_feat_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _feature_up_signature(ctx, params, to_path, task_id=None) -> str:
    """Change signature for one target (To) file's mapped columns.

    Covers the key columns, the feature rules, the matched From file, the
    size/mtime of both files and any upstream sampling of either side, so
    a saved output goes stale whenever the mapping changes.
    """
    from_path = ctx._fup_matched_from(params, to_path)
    try:
        st = Path(to_path).stat()
        to_stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        to_stat = ""
    from_stat = ""
    if from_path:
        try:
            st = Path(from_path).stat()
            from_stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
        except OSError:
            from_stat = ""
    payload = {
        "key_from": params.get("key_from"),
        "key_to": params.get("key_to"),
        "features": params.get("features") or [],
        "from_path": from_path,
        "from_stat": from_stat,
        "to_stat": to_stat,
    }
    sample_from = ctx._subsample_sig(task_id, from_path)
    if sample_from:
        payload["sample_from"] = sample_from
    sample_to = ctx._subsample_sig(task_id, to_path)
    if sample_to:
        payload["sample_to"] = sample_to
    payload = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _feature_up_params_for(ctx, task_def) -> dict:
    """Saved params of a Feature Up task (its live snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "feature_up":
        snap = getattr(reg["page"], "_fup_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _feature_up_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: record} exposed by upstream Feature Up modules.

    Each record is ``{"parquet": abs_path, "channel": name, "kind":
    "image"|"table"}``. Only outputs whose saved signature still matches
    are exposed, so downstream always sees the committed state.
    """
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    out = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "feature_up":
            continue
        params = ctx._feature_up_params_for(task)
        rec = (params.get("outputs") or {}).get(parquet_path)
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        if rec.get("signature") != ctx._feature_up_signature(
            params, parquet_path, task.get("id")
        ):
            continue
        path = ctx.project.path / rec["path"]
        if not path.is_file():
            continue
        for name in rec.get("columns") or []:
            out.setdefault(name, {
                "parquet": str(path),
                "channel": name,
                "kind": rec.get("kind", "table"),
            })
    return out


def _feature_up_column_values(ctx, parquet_path, rec, name):
    """Row-aligned float64 values of one Feature Up column (for scatter)."""
    import numpy as np
    import pandas as pd

    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None
    fparquet = rec["parquet"]
    if rec.get("kind") == "image":
        # The mapped column lives in a [x, y, …] image parquet; scatter its
        # pixels back onto the source's rows via x/y (NA where unmatched).
        man = ctx._image_load_manifest(fparquet)
        if man is None:
            return None
        try:
            src = pd.read_parquet(parquet_path, columns=["x", "y"])
            fdf = pd.read_parquet(fparquet, columns=["x", "y", name])
        except Exception:  # noqa: BLE001
            return None
        xmin = float(man["xmin"])
        ymin = float(man["ymin"])
        width = int(round(float(man["xmax"]) - xmin)) + 1
        height = int(round(float(man["ymax"]) - ymin)) + 1
        fxi = np.rint(fdf["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        fyi = np.rint(fdf["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        grid = np.full((height, width), np.nan, dtype="float64")
        fok = (fxi >= 0) & (fxi < width) & (fyi >= 0) & (fyi < height)
        grid[fyi[fok], fxi[fok]] = fdf[name].to_numpy(dtype="float64")[fok]
        sxi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        syi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        sok = (sxi >= 0) & (sxi < width) & (syi >= 0) & (syi < height)
        out = np.full(len(src), np.nan, dtype="float64")
        out[sok] = grid[syi[sok], sxi[sok]]
        return out
    # Table source: the column is row-aligned directly.
    try:
        df = pd.read_parquet(fparquet, columns=[name])
    except Exception:  # noqa: BLE001
        return None
    return df[name].to_numpy(dtype="float64")


def _feature_up_raster(ctx, rec, bbox, spacing):
    """Return (scalar_2d, data_rect) for a Feature Up channel over a bbox."""
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None, None
    ent = ctx._ensure_image_index(rec["parquet"])
    if ent is None or ent.get("manifest") is None:
        return None, None
    crop, rect, _frac = ctx._image_raster(
        rec["parquet"], ent["manifest"], bbox, spacing, rec.get("channel"), None, None
    )
    return crop, rect


def _gate2col_params_for(ctx, task_def) -> dict:
    """Saved params of a Gate2Col task (its live snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "gate2col":
        snap = getattr(reg["page"], "_g2c_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _gate2col_signature(ctx, params, parquet_path, task_id=None) -> str:
    """Change signature for one source file's gate columns."""
    try:
        st = Path(parquet_path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "rules": params.get("rules") or [],
        "source": str(parquet_path),
        "stat": stat,
    }
    sample = ctx._subsample_sig(task_id, parquet_path)
    if sample:
        payload["sample"] = sample
    payload = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _gate2col_applied_columns(ctx, task_id, parquet_path) -> dict:
    """{column_name: record} exposed by upstream Gate2Col modules.

    Each record is ``{"parquet": abs_path, "channel": name, "kind":
    "image"|"table"}``. Only outputs whose saved signature still matches
    are exposed, so downstream always sees the committed state.
    """
    if not task_id or not parquet_path or ctx.project is None:
        return {}
    out = {}
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "gate2col":
            continue
        params = ctx._gate2col_params_for(task)
        rec = (params.get("outputs") or {}).get(parquet_path)
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        if rec.get("signature") != ctx._gate2col_signature(
            params, parquet_path, task.get("id")
        ):
            continue
        path = ctx.project.path / rec["path"]
        if not path.is_file():
            continue
        for name in rec.get("columns") or []:
            out.setdefault(name, {
                "parquet": str(path),
                "channel": name,
                "kind": rec.get("kind", "table"),
            })
    return out


def _gate2col_column_values(ctx, parquet_path, rec, name):
    """Row-aligned float64 values of one Gate2Col column (for scatter)."""
    import numpy as np
    import pandas as pd

    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None
    gparquet = rec["parquet"]
    if rec.get("kind") == "image":
        # The gate column lives in a [x, y, …] image parquet; scatter its
        # pixels back onto the source's rows via x/y.
        man = ctx._image_load_manifest(gparquet)
        if man is None:
            return None
        try:
            src = pd.read_parquet(parquet_path, columns=["x", "y"])
            gdf = pd.read_parquet(gparquet, columns=["x", "y", name])
        except Exception:  # noqa: BLE001
            return None
        xmin = float(man["xmin"])
        ymin = float(man["ymin"])
        width = int(round(float(man["xmax"]) - xmin)) + 1
        height = int(round(float(man["ymax"]) - ymin)) + 1
        gxi = np.rint(gdf["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        gyi = np.rint(gdf["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        grid = np.zeros((height, width), dtype="float64")
        gok = (gxi >= 0) & (gxi < width) & (gyi >= 0) & (gyi < height)
        grid[gyi[gok], gxi[gok]] = gdf[name].to_numpy(dtype="float64")[gok]
        sxi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        syi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        sok = (sxi >= 0) & (sxi < width) & (syi >= 0) & (syi < height)
        out = np.zeros(len(src), dtype="float64")
        out[sok] = grid[syi[sok], sxi[sok]]
        return out
    # Table source: the column is row-aligned directly.
    try:
        df = pd.read_parquet(gparquet, columns=[name])
    except Exception:  # noqa: BLE001
        return None
    return df[name].to_numpy(dtype="float64")
