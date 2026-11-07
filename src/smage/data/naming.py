"""Display-name helpers (file-mode renames, Feature source names).

Qt-free: they read the workflow through the context.
"""
from __future__ import annotations

from pathlib import Path


def _comp_file_name(ctx, path, task_id=None) -> str:
    rec = ctx._file_record(path)
    name = rec.get("name", "") if rec is not None else Path(str(path)).name
    if task_id:
        name = ctx._rename_file_name(task_id, path, name)
    return name


def _rename_file_name(ctx, task_id, parquet_path, name):
    """Apply upstream file-mode renames to a display name."""
    if not task_id or not parquet_path:
        return name
    path = str(parquet_path)
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "rename":
            continue
        params = task.get("params") or {}
        if params.get("mode", "file") != "file":
            continue
        new = (params.get("file") or {}).get("rename", {}).get(path)
        if new:
            name = new
    return name
