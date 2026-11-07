"""Shared helpers for the end-to-end regression scenarios."""
from __future__ import annotations

import json
import queue
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile

import harness
from smage.data.image_index import _build_image_index_files
from smage.importer import import_file


def open_window(project):
    window = harness.new_window()
    harness.open_project(window, project)
    return window


def close_window(window) -> None:
    harness.close_window(window)


def open_page(window, task_id: str, task_type: str, name: str):
    window._open_module(task_id, task_type, name)
    harness.pump(350)
    return window.tabs.currentWidget()


def write_gates(project, task_id: str, parquet: str, gates: list,
                per_file=None) -> None:
    folder = project.path / "Module" / str(task_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gates.json").write_text(json.dumps({
        "files": [parquet],
        "gates": gates,
        "per_file": per_file or {},
    }), encoding="utf-8")


def add_parquet(project, name: str, frame: pd.DataFrame) -> str:
    """Add a processed parquet file and return its absolute path."""
    path = project.data_dir / f"{name}.parquet"
    frame.to_parquet(path, index=False)
    columns = project.load_columns()
    columns[path.name] = list(frame.columns)
    project.save_columns(columns)
    project.workflow["tasks"][0]["params"]["processed"].append({
        "name": f"{name}.fcs",
        "source": f"C:/data/{name}.fcs",
        "parquet": str(path),
        "rows": int(len(frame)),
    })
    project.save()
    return str(path)


def image_project(case_id: str, tasks=(), size: int = 32,
                  squares=((5, 10), (20, 25)), value: int = 200):
    """A project holding one synthetic nuclei image, already indexed."""
    project = harness.new_project(case_id, rows=1)
    image = project.path.parent / "nuclei.tif"
    stack = np.zeros((1, size, size), dtype=np.uint8)
    for low, high in squares:
        stack[0, low:high, low:high] = value
    tifffile.imwrite(image, stack, metadata={"axes": "CYX"})

    parquet = project.data_dir / "nuclei.parquet"
    params = [{"name": "Channel 1", "extract": True, "rename": "Channel 1"}]
    result = import_file(str(image), params, "all", 100, parquet)
    project.save_columns({"nuclei.parquet": result["columns"]})
    project.workflow["tasks"][0]["params"]["processed"] = [{
        "name": "nuclei.tif",
        "source": str(image),
        "parquet": str(parquet),
        "rows": result["rows"],
    }]
    for task in tasks:
        project.workflow["tasks"][0]["children"].append(task)
    project.save()
    _build_image_index_files(str(parquet))
    return project, str(parquet)


def import_into_project(project, name: str, source, params=None):
    """Import one source file into a project and register it as processed."""
    from smage.dimensions import parameter_names, read_dimensions

    info = read_dimensions(source)
    if params is None:
        names = info.get("columns") or parameter_names(info)
        params = [{"name": item, "extract": True, "rename": item}
                  for item in names]
    out = project.data_dir / f"{name}.parquet"
    result = import_file(str(source), params, "all", 100, out)
    columns = project.load_columns()
    columns[out.name] = list(result["columns"])
    project.save_columns(columns)
    project.workflow["tasks"][0]["params"]["processed"].append({
        "name": Path(str(source)).name,
        "source": str(source),
        "parquet": str(out),
        "rows": result["rows"],
    })
    project.save()
    return str(out), result


def drain(q: queue.Queue) -> list:
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except queue.Empty:
            return messages


def export_table(window, task_id: str, files, columns, out_dir,
                 fmt: str = "csv") -> list:
    """Run one table export through the real Export page; return queue messages."""
    page = open_page(window, task_id, "export", "Export")
    q = queue.Queue()
    window._ctx._export_job_table(page, q, {
        "files": list(files),
        "columns": list(columns),
        "format": fmt,
        "dir": str(out_dir),
    })
    messages = drain(q)
    window._close_module_page(page)
    harness.pump(60)
    if not any(message[0] == "done" for message in messages):
        raise AssertionError(f"export did not finish: {messages}")
    return messages


def feature_record(window, parquet: str, task_id: str) -> dict:
    """The Feature output record visible at a downstream task."""
    records = window._ctx._feature_outputs(task_id)
    for record in records:
        if record.get("source") == parquet:
            return record
    raise AssertionError(f"no feature output for {parquet}: {records}")


def output_path(project, rel: str) -> Path:
    return project.path / rel
