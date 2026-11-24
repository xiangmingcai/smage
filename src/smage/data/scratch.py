"""Disposable project-local scratch directories.

Long-running module operations write intermediate inputs and outputs under
``<project>/tmp/<area>/runs/<run-id>/``.  Keeping them inside the project
means they live on the same volume as the analysis, are excluded from the
project data, and can be removed by the same project cleanup hooks.

Without a project (standalone unit tests) the system temporary directory is
used as a fallback.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path


def _scratch_root(ctx) -> Path:
    """Root of all disposable working files for the current project."""
    project = getattr(ctx, "project", None)
    if project is not None:
        base = getattr(project, "tmp_dir", None)
        if base is None:
            base = project.path / "tmp"
        return Path(base)
    key = hashlib.md5(b"no-project").hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "smage" / "tmp" / key


def _scratch_area(ctx, area: str) -> Path:
    """One module's scratch area, e.g. ``tmp/cluster``."""
    return _scratch_root(ctx) / str(area)


def _scratch_run_dir(ctx, area: str) -> Path:
    """Create and return a unique run directory inside an area."""
    run_id = (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    path = _scratch_area(ctx, area) / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def _scratch_run(ctx, area: str):
    """Context manager: a run directory removed even when the run fails."""
    path = _scratch_run_dir(ctx, area)
    try:
        yield path
    finally:
        _scratch_remove(path)


def _scratch_remove(path) -> None:
    if path is None:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _scratch_cleanup_area(ctx, area: str) -> None:
    """Remove one module's whole scratch area."""
    _scratch_remove(_scratch_area(ctx, area))


def _scratch_cleanup_stale(ctx, areas=None) -> None:
    """Remove interrupted ``runs`` folders; keep caches such as edges."""
    root = _scratch_root(ctx)
    if not root.is_dir():
        return
    if areas is None:
        areas = [child.name for child in root.iterdir() if child.is_dir()]
    for area in areas:
        _scratch_remove(root / str(area) / "runs")


def _scratch_cleanup_all(ctx) -> None:
    """Remove every disposable working file for the current project."""
    _scratch_remove(_scratch_root(ctx))
