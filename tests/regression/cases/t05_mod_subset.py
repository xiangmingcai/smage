"""L4: Subset — visible files/columns persisted for downstream modules."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.data.files import _subset_visible_columns  # noqa: E402
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.modules.subset import page as _subset_page


def _open(case_id: str):
    task = harness.task_def(
        "subset", task_id="sub-1", name="Subset",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=40, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("sub-1", "subset", "Subset")
    harness.pump(300)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.subset.save", "L4", "subset", tags=("fast",))
def save_filters() -> None:
    window, project, page = _open("subset_save")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._subset_draft_files = [parquet]
        page._subset_draft_columns = ["x", "FL1-A"]
        _subset_page._subset_save_now(ctx, page)

        harness.eq(_data_files._subset_visible_files(ctx, "g-1"), {parquet})
        harness.eq(_subset_visible_columns(ctx, "g-1"), {"x", "FL1-A"})
        harness.eq(_data_columns._columns_for(ctx, parquet, "g-1"), ["x", "FL1-A"])

        from smage.project import Project

        saved = Project.load(project.path)
        task = saved.workflow["tasks"][0]["children"][0]
        harness.eq(task["params"]["files"], [parquet])
        harness.eq(task["params"]["columns"], ["x", "FL1-A"])
    finally:
        _close(window)


@harness.case("mod.subset.clear", "L4", "subset", tags=("fast",))
def clear_filters() -> None:
    window, _project, page = _open("subset_clear")
    try:
        ctx = window._ctx
        page._subset_draft_files = []
        page._subset_draft_columns = []
        _subset_page._subset_save_now(ctx, page)
        harness.eq(_data_files._subset_visible_files(ctx, "g-1"), None)
        harness.eq(_subset_visible_columns(ctx, "g-1"), None)
        harness.eq(_data_files._subset_filters(ctx, "g-1"), [(None, None)],
                   "an empty saved selection is recorded but filters nothing")
    finally:
        _close(window)


@harness.case("mod.subset.summary", "L4", "subset", tags=("fast",))
def summary_text() -> None:
    window, project, page = _open("subset_summary")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._subset_draft_files = [parquet]
        page._subset_draft_columns = ["x"]
        _subset_page._subset_refresh(ctx, page)
        text = page._subset_summary.text()
        harness.check("1 file(s) selected" in text, text)
        harness.check("1 column(s) selected" in text, text)

        page._subset_draft_files = []
        page._subset_draft_columns = []
        _subset_page._subset_refresh(ctx, page)
        text = page._subset_summary.text()
        harness.check("All files visible" in text, text)
        harness.check("All columns visible" in text, text)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
