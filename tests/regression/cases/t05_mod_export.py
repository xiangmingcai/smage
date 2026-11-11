"""L4: Export — CSV/Parquet/FCS table jobs, naming and conflict detection."""
from __future__ import annotations

import queue
import sys
from pathlib import Path

import pandas as pd
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402
from smage.modules.export import page as _export_page


def _open(case_id: str):
    task = harness.task_def("export", task_id="e-1", name="Export")
    project = harness.new_project(case_id, rows=50, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("e-1", "export", "Export")
    harness.pump(350)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


def _drain(q: queue.Queue) -> list:
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except queue.Empty:
            return messages


def _payloads(messages, kind):
    return [message[1] for message in messages
            if message and message[0] == kind]


def _run(window, page, project, out_dir: Path, fmt: str, columns):
    q = queue.Queue()
    params = {
        "files": [str(project.data_dir / "case.parquet")],
        "columns": list(columns),
        "format": fmt,
        "dir": str(out_dir),
    }
    _export_page._export_job_table(window._ctx, page, q, params)
    return _drain(q)


@harness.case("mod.export.reorder", "L4", "export", tags=("fast",))
def reorder_keeps_channels() -> None:
    """Dropping a row onto another must not lose a channel.

    The list reports the drag and the page reorders its parameters; the
    widget itself never removes rows, so the rebuilt list is always complete.
    """
    window, project, page = _open("export_reorder")
    try:
        ctx = window._ctx
        kind = "table"
        section = _export_page._export_params_for(ctx, page, kind)
        section["files"] = [str(project.data_dir / "case.parquet")]
        section["columns"] = ["x", "FL1-A"]
        _export_page._export_refresh_pane(ctx, page, kind)
        pane = _export_page._export_pane(ctx, page, kind)
        harness.eq(pane.cols_box.count(), 2)

        # Move the last row to the top, as a drop on the first row would.
        _export_page._export_reorder(ctx, page, kind, 1, 0)

        harness.eq(section["columns"], ["FL1-A", "x"],
                   "the channel order follows the drop")
        harness.eq(pane.cols_box.count(), 2,
                   "no row disappears from the list")
        harness.eq(
            [pane.cols_box.item(i).text() for i in range(2)],
            ["FL1-A", "x"],
            "the list is rebuilt from the reordered parameters",
        )
    finally:
        _close(window)


@harness.case("mod.export.csv", "L4", "export", tags=("fast",))
def export_csv() -> None:
    window, project, page = _open("export_csv")
    try:
        out = harness.RUN_DIR / "cases" / "export_csv" / "out_csv"
        messages = _run(window, page, project, out, "csv", ["x", "FL1-A"])
        done = _payloads(messages, "done")
        harness.eq(len(done), 1, f"messages: {messages}")
        harness.eq(done[0]["files"], 1)
        harness.eq(done[0]["rows"], 50)

        target = out / "case.csv"
        harness.check(target.is_file())
        frame = pd.read_csv(target)
        harness.eq(list(frame.columns), ["x", "FL1-A"])
        harness.eq(len(frame), 50)
        harness.golden("export_csv", {
            "columns": list(frame.columns),
            "rows": int(len(frame)),
            "sum": float(frame["FL1-A"].sum()),
        })
    finally:
        _close(window)


@harness.case("mod.export.parquet", "L4", "export", tags=("fast",))
def export_parquet() -> None:
    window, project, page = _open("export_parquet")
    try:
        out = harness.RUN_DIR / "cases" / "export_parquet" / "out_parquet"
        messages = _run(window, page, project, out, "parquet", ["x", "FL1-A"])
        harness.check(bool(_payloads(messages, "done")), str(messages))
        frame = pd.read_parquet(out / "case.parquet")
        harness.eq(list(frame.columns), ["x", "FL1-A"])
        harness.eq(len(frame), 50)
    finally:
        _close(window)


@harness.case("mod.export.fcs", "L4", "export", tags=("fast",))
def export_fcs() -> None:
    window, project, page = _open("export_fcs")
    try:
        out = harness.RUN_DIR / "cases" / "export_fcs" / "out_fcs"
        messages = _run(window, page, project, out, "fcs", ["x", "FL1-A"])
        harness.check(bool(_payloads(messages, "done")), str(messages))
        target = out / "case.fcs"
        harness.check(target.is_file())
        harness.eq(target.read_bytes()[:6], b"FCS3.1")
    finally:
        _close(window)


@harness.case("mod.export.conflict", "L4", "export", tags=("fast",))
def export_conflict() -> None:
    window, project, page = _open("export_conflict")
    try:
        out = harness.RUN_DIR / "cases" / "export_conflict" / "out_conflict"
        out.mkdir(parents=True, exist_ok=True)
        target = out / "case.csv"
        target.write_text("already here", encoding="utf-8")

        messages = _run(window, page, project, out, "csv", ["x"])
        conflicts = _payloads(messages, "conflict")
        harness.eq(len(conflicts), 1, f"messages: {messages}")
        harness.check("already exists" in conflicts[0][0], conflicts[0])
        harness.eq(target.read_text(encoding="utf-8"), "already here",
                   "the existing file is not overwritten")
    finally:
        _close(window)


@harness.case("mod.export.naming", "L4", "export", tags=("fast",))
def export_naming() -> None:
    window, _project, page = _open("export_naming")
    try:
        ctx = window._ctx
        harness.eq(_export_page._export_file_name(ctx, "a/b:c*", ".csv"), "a_b_c_.csv")
        harness.eq(_export_page._export_file_name(ctx, "   ", ".csv"), "export.csv")
        harness.eq(_export_page._export_file_name(ctx, "", ".fcs"), "export.fcs")
        harness.eq(_export_page._export_file_name(ctx, "data.parquet", ".csv"), "data.csv")
    finally:
        _close(window)


@harness.case("mod.export.image", "L4", "export", tags=("fast",))
def export_image() -> None:
    task = harness.task_def("export", task_id="e-1", name="Export")
    project, _parquet = e2e.image_project("export_image", tasks=[task])
    window = e2e.open_window(project)
    try:
        page = e2e.open_page(window, "e-1", "export", "Export")
        out = harness.RUN_DIR / "cases" / "export_image" / "out_image"
        q = queue.Queue()
        _export_page._export_job_image(window._ctx, page, q, {
            "files": [str(project.data_dir / "nuclei.parquet")],
            "channels": ["Channel 1"],
            "dir": str(out),
        })
        messages = _drain(q)
        harness.check(bool(_payloads(messages, "done")), str(messages))
        target = out / "nuclei.tif"
        harness.check(target.is_file())
        image = tifffile.imread(target)
        harness.eq(image.shape, (32, 32))
        harness.check(float(image.max()) > 0)
    finally:
        e2e.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
