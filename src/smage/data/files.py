"""Workflow file lookup: imported files, Feature outputs, subsets.

Qt-free: all state is read through the context.
"""
from __future__ import annotations

from pathlib import Path


def _task_chain(ctx, task_id) -> list:
    """Return the workflow task chain from root down to (and including) task_id."""
    if ctx.project is None or not task_id:
        return []
    result = []

    def walk(tasks, path):
        for task in tasks:
            new_path = path + [task]
            if task.get("id") == task_id:
                result.extend(new_path)
                return True
            if walk(task.get("children", []), new_path):
                return True
        return False

    walk(ctx.project.workflow.get("tasks", []), [])
    return result


def _parent_task_name(ctx, task_id) -> str | None:
    """Return the name of the parent task in the workflow, or None (root)."""
    chain = ctx._task_chain(task_id)
    if len(chain) >= 2:
        return chain[-2].get("name")
    return None


def _import_task(ctx):
    """Return the fixed 'import' task, or None."""
    if ctx.project is None:
        return None
    for task in ctx.project.workflow.get("tasks", []):
        if task.get("type") == "import":
            return task
    return None


def _imported_files(ctx) -> list:
    """Return the imported (Processed) file records from the import task."""
    task = ctx._import_task()
    if task is None:
        return []
    return task.get("params", {}).get("processed", [])


def _file_record(ctx, parquet_path):
    """Return the file record (imported or Feature output) for a path."""
    for rec in ctx._imported_files():
        if rec.get("parquet") == parquet_path:
            return rec
    return ctx._feature_output(parquet_path)


def _rel_data_key(ctx, parquet_path) -> str:
    """Return the parquet path relative to the data dir (index key)."""
    try:
        data_dir = ctx.project.data_dir if ctx.project is not None else None
        if data_dir is not None:
            return Path(parquet_path).relative_to(data_dir).as_posix()
    except (ValueError, AttributeError):
        pass
    return Path(parquet_path).name


def _module_display_title(ctx, task_id, name: str) -> str:
    """Return the toolbar title as '<parent> → <name>' when a parent exists."""
    parent = ctx._parent_task_name(task_id) if task_id else None
    if parent:
        return f"{parent} → {name}"
    return name


def _module_dir(ctx, task_id) -> Path | None:
    """Folder holding a module's own files.

    While the module is open as a temporary draft (it is locked), this is
    ``Module/<id>/draft`` so nothing official is touched.
    """
    if ctx.project is None or not task_id:
        return None
    base = ctx.project.path / "Module" / str(task_id)
    if str(task_id) in getattr(ctx, "_draft_task_ids", ()):
        return base / "draft"
    return base


def _module_rel(ctx, task_id, name: str) -> str:
    """Relative path of a file inside a module folder (draft-aware)."""
    if str(task_id) in getattr(ctx, "_draft_task_ids", ()):
        return f"Module/{task_id}/draft/{name}"
    return f"Module/{task_id}/{name}"


def _module_size_path(ctx, task_id):
    """The folder whose size stands for a module (None without one)."""
    if ctx.project is None or not task_id:
        return None
    import_task = ctx._import_task()
    if import_task is not None and str(task_id) == str(import_task.get("id")):
        # Imported data lives in the project's data folder.
        return ctx.project.data_dir
    return ctx.project.path / "Module" / str(task_id)


def _subset_filters(ctx, task_id) -> list:
    """Saved Subset filters above `task_id`, top-down.

    Each entry is ``(files, columns)``; a ``None`` member means "no
    filtering on that axis" (an empty selection is no filter). A module's
    own Subset task is skipped, so a Subset never hides its own content.
    """
    if not task_id or ctx.project is None:
        return []
    out = []
    for task in ctx._task_chain(task_id):
        if task.get("id") == task_id or task.get("type") != "subset":
            continue
        params = task.get("params") or {}
        files = {str(p) for p in (params.get("files") or []) if p}
        columns = {str(c) for c in (params.get("columns") or []) if c}
        out.append((files or None, columns or None))
    return out


def _subset_visible_files(ctx, task_id):
    """Set of visible parquet paths, or None when nothing is hidden."""
    allowed = None
    for files, _columns in ctx._subset_filters(task_id):
        if files is None:
            continue
        allowed = files if allowed is None else (allowed & files)
    return allowed


def _available_files(ctx, task_id=None) -> list:
    """Data files visible to a module: imported files + upstream Feature outputs.

    Files hidden by an upstream Subset module are dropped here, so the
    filter reaches every downstream module through this one entry point.
    Upstream file renames relabel the display name (the parquet path, the
    file's identity, is unchanged).
    """
    files = list(ctx._imported_files())
    if task_id:
        known = {rec.get("parquet") for rec in files}
        for rec in ctx._feature_outputs(task_id):
            if rec.get("parquet") not in known:
                files.append(rec)
        allowed = ctx._subset_visible_files(task_id)
        if allowed is not None:
            files = [
                rec for rec in files
                if str(rec.get("parquet")) in allowed
            ]
        # Rename display names on copies — never mutate the stored records.
        renamed_files = []
        for rec in files:
            new_name = ctx._rename_file_name(
                task_id, rec.get("parquet"), rec.get("name")
            )
            if new_name != rec.get("name"):
                rec = dict(rec)
                rec["name"] = new_name
            renamed_files.append(rec)
        files = renamed_files
    return files

def _subset_visible_columns(ctx, task_id):
    """Set of visible column names, or None when nothing is hidden."""
    allowed = None
    for _files, columns in ctx._subset_filters(task_id):
        if columns is None:
            continue
        allowed = columns if allowed is None else (allowed & columns)
    return allowed
