"""L4: Scale — apply settings to variables, save, and downstream value change."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.scale import page as scale_page  # noqa: E402
from smage.data import columns as _data_columns
from smage.widgets.plot import renderer as _widgets_plot_renderer


def _open(case_id: str):
    task = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=80, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("s-1", "scale", "Scale")
    harness.pump(400)
    return window, project, window.tabs.currentWidget()


def _open_two_files(case_id: str):
    """Open Scale on a project with two imported files sharing columns."""
    task = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=80, tasks=[task])
    first = project.data_dir / "case.parquet"
    second = project.data_dir / "second.parquet"
    frame = pd.read_parquet(first)
    frame.to_parquet(second, index=False)
    import_task = project.workflow["tasks"][0]
    import_task["params"]["processed"].append(
        {
            "name": "second.fcs",
            "source": str(harness.FIXTURES_DIR / "fcs" / "synthetic_8ch.fcs"),
            "parquet": str(second),
            "rows": int(len(frame)),
        }
    )
    columns = project.load_columns()
    columns[second.name] = list(frame.columns)
    project.save_columns(columns)
    project.save()
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("s-1", "scale", "Scale")
    harness.pump(400)
    return window, project, window.tabs.currentWidget(), first, second


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.scale.apply", "L4", "scale", tags=("fast",))
def apply_method() -> None:
    window, project, page = _open("scale_apply")
    try:
        ctx = window._ctx
        page._scaling_apply_vars = ["FL1-A"]
        index = page._scaling_apply_method.findData("log10")
        harness.check(index >= 0, "log10 is offered")
        page._scaling_apply_method.setCurrentIndex(index)
        page._scaling_apply_param.setText("")
        page._scaling_apply_min.setText("")
        page._scaling_apply_max.setText("")
        scale_page._scaling_apply_batch(ctx, page)

        config = next(
            item for item in page._scaling_columns if item["name"] == "FL1-A"
        )
        harness.eq(config["method"], "log10")
        harness.check(page._scaling_dirty)

        parquet = str(project.data_dir / "case.parquet")
        page._scaling_files = [parquet]
        scale_page._scaling_save_now(ctx, page)
        task = window.project.workflow["tasks"][0]["children"][0]
        saved = task["params"]
        harness.eq(saved["files"], [parquet])
        saved_config = next(
            item for item in saved["columns"] if item["name"] == "FL1-A"
        )
        harness.eq(saved_config["method"], "log10")

        raw = pd.read_parquet(parquet)["FL1-A"].to_numpy(dtype="float64")
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        harness.check(
            np.allclose(values["FL1-A"], raw),
            "Scale is display-only: downstream data is unchanged",
        )
        configs = _widgets_plot_renderer._figure_axis_scale_configs(
            ctx, "g-1", parquet, "FL1-A"
        )
        harness.eq([c.get("method") for c in configs], ["log10"],
                   "the plot applies the log10 display transform")
    finally:
        _close(window)


@harness.case("mod.scale.arcsinh", "L4", "scale", tags=("fast",))
def apply_arcsinh() -> None:
    window, project, page = _open("scale_arcsinh")
    try:
        ctx = window._ctx
        page._scaling_apply_vars = ["FL2-A"]
        page._scaling_apply_method.setCurrentIndex(
            page._scaling_apply_method.findData("arcsinh")
        )
        page._scaling_apply_param.setText("5")
        scale_page._scaling_apply_batch(ctx, page)
        config = next(
            item for item in page._scaling_columns if item["name"] == "FL2-A"
        )
        harness.eq(config["method"], "arcsinh")
        harness.eq(config["param"], 5.0)

        parquet = str(project.data_dir / "case.parquet")
        page._scaling_files = [parquet]
        scale_page._scaling_save_now(ctx, page)
        raw = pd.read_parquet(parquet)["FL2-A"].to_numpy(dtype="float64")
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL2-A"])
        harness.check(
            np.allclose(values["FL2-A"], raw),
            "Scale is display-only: downstream data is unchanged",
        )
        configs = _widgets_plot_renderer._figure_axis_scale_configs(
            ctx, "g-1", parquet, "FL2-A"
        )
        harness.eq([c.get("method") for c in configs], ["arcsinh"],
                   "the plot applies the arcsinh display transform")
    finally:
        _close(window)


@harness.case("mod.scale.preview_keeps_axes", "L4", "scale", tags=("fast",))
def preview_keeps_axes() -> None:
    """Switching the preview file must not reset X/Y or the image channel."""
    window, _project, page, first, second = _open_two_files("scale_preview_axes")
    try:
        ctx = window._ctx
        # Settings (and the preview file combo) are built once files are
        # selected; without a selection there are no visible columns.
        page._scaling_files = [str(first), str(second)]
        scale_page._scaling_rebuild_visible(ctx, page)
        preview = page._scaling_preview
        preview.update(
            {"x": "FL2-A", "y": "FL3-A", "image_channel": "FL4-A"}
        )
        combo = page._scaling_file_combo
        index = combo.findData(str(second))
        harness.check(index >= 0, "the second file is listed")
        combo.setCurrentIndex(index)
        harness.pump(200)

        harness.eq(preview["file"], str(second))
        harness.eq(preview["x"], "FL2-A", "X parameter survives the file switch")
        harness.eq(preview["y"], "FL3-A", "Y parameter survives the file switch")
        harness.eq(preview["image_channel"], "FL4-A",
                   "image channel survives the file switch")
    finally:
        _close(window)


@harness.case("mod.scale.preview_files", "L4", "scale", tags=("fast",))
def preview_files_follow_selection() -> None:
    """The preview File list only offers the selected Data files."""
    window, _project, page, first, second = _open_two_files("scale_preview_files")
    try:
        ctx = window._ctx
        # Nothing selected: empty preview and an empty File list.
        harness.eq(page._scaling_file_combo.count(), 0)
        harness.check(
            not page._scaling_plot_widget.getPlotItem().items,
            "no data is drawn without a selected file",
        )
        harness.check(
            not page._scaling_overlay.isHidden(),
            "the select-files hint is shown",
        )
        harness.check(
            "Select data files" in page._scaling_overlay_box.text(),
            page._scaling_overlay_box.text(),
        )

        # The selection order drives the list; the first file opens.
        page._scaling_files = [str(second), str(first)]
        scale_page._scaling_rebuild_visible(ctx, page)
        # The first render may wait for the column pool; let it finish.
        harness.pump(400)
        combo = page._scaling_file_combo
        harness.eq(combo.count(), 2)
        harness.eq(combo.itemData(0), str(second))
        harness.eq(combo.itemData(1), str(first))
        harness.eq(page._scaling_preview["file"], str(second))
        harness.check(page._scaling_overlay.isHidden())
        harness.check(
            bool(page._scaling_plot_widget.getPlotItem().items),
            "the selected file is drawn",
        )

        # Removing the file on screen switches to the first remaining one.
        page._scaling_files = [str(first)]
        scale_page._scaling_rebuild_visible(ctx, page)
        harness.eq(page._scaling_file_combo.count(), 1)
        harness.eq(page._scaling_preview["file"], str(first))

        # Clearing the selection returns to the empty state.
        page._scaling_files = []
        scale_page._scaling_rebuild_visible(ctx, page)
        harness.eq(page._scaling_file_combo.count(), 0)
        harness.eq(page._scaling_preview["file"], None)
        harness.check(
            not page._scaling_plot_widget.getPlotItem().items,
            "the plot is cleared again",
        )
    finally:
        _close(window)


@harness.case("mod.scale.validation", "L4", "scale", tags=("fast",))
def apply_validation() -> None:
    window, _project, page = _open("scale_validation")
    try:
        ctx = window._ctx
        before = [dict(item) for item in page._scaling_columns]

        page._scaling_apply_vars = []
        with harness.patch_dialogs(scale_page,
                                   information=QMessageBox.StandardButton.Ok):
            scale_page._scaling_apply_batch(ctx, page)
        harness.eq(page._scaling_columns, before, "no variables means no change")

        page._scaling_apply_vars = ["FL1-A"]
        page._scaling_apply_method.setCurrentIndex(
            page._scaling_apply_method.findData("arcsinh")
        )
        page._scaling_apply_param.setText("not-a-number")
        with harness.patch_dialogs(scale_page,
                                   warning=QMessageBox.StandardButton.Ok):
            scale_page._scaling_apply_batch(ctx, page)
        config = next(
            item for item in page._scaling_columns if item["name"] == "FL1-A"
        )
        harness.eq(config["method"], "linear", "the invalid edit is rejected")

        page._scaling_apply_vars = ["FL1-A"]
        page._scaling_apply_method.setCurrentIndex(0)
        page._scaling_apply_param.setText("")
        with harness.patch_dialogs(scale_page,
                                   information=QMessageBox.StandardButton.Ok):
            scale_page._scaling_apply_batch(ctx, page)
        harness.eq(page._scaling_columns, before, "empty fields do nothing")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
