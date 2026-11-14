"""L4: Calculation — map-stage rows, pipeline values and the module page."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.core.calculation import (  # noqa: E402
    _apply_calculation,
    _calculation_dependencies,
    _calculation_outputs,
    _calculation_stage,
    _curve_lut,
)
from smage.core.pipeline import _pipeline_dependencies  # noqa: E402
from smage.data import columns as _data_columns  # noqa: E402
from smage.modules.calculation import page as _calc_page  # noqa: E402
from smage.modules.figure import page as _figure_page  # noqa: E402


def _project_with_calculation(case_id: str, rows: list, children=None):
    project = harness.new_project(case_id, rows=200)
    parquet = str(project.data_dir / "case.parquet")
    task = harness.task_def(
        "calculation", task_id="calc-1", name="Calculation",
        params={"files": [parquet], "rows": rows},
        children=list(children or []),
    )
    project.workflow["tasks"][0].setdefault("children", []).append(task)
    project.save()
    return project, parquet


def _open(project):
    window = harness.new_window()
    harness.open_project(window, project)
    return window


def _close(window) -> None:
    harness.close_window(window)


@harness.case("calc.core", "L3", "calculation", tags=("fast",))
def core_rows() -> None:
    rows = [
        {
            "id": "r1", "type": "clip", "files": ["f"], "output": "c1",
            "visible": True, "source": {"column": "x"}, "min": 1, "max": 3,
        },
        {
            "id": "r2", "type": "add", "files": ["f"], "output": "c2",
            "visible": True,
            "terms": [
                {"weight": 2.0, "source": {"row": "r1"}},
                {"weight": 1.0, "source": {"column": "y"}},
            ],
        },
        {
            "id": "r3", "type": "contrast", "files": ["f"], "output": "c3",
            "visible": False, "source": {"row": "r2"},
            "domain": {"auto": False, "min": 0.0, "max": 20.0},
            "anchors": [[0.0, 1.0], [1.0, 0.0]],
        },
    ]
    stage = _calculation_stage({"rows": rows})
    harness.check(stage is not None)
    harness.eq([row["output"] for row in stage["rows"]], ["c1", "c2", "c3"])
    harness.eq(_calculation_dependencies(stage, {"c2"}), {"x", "y"})
    harness.eq(_calculation_dependencies(stage, {"c3"}), {"x", "y"})

    values = {
        "x": np.array([0.0, 2.0, 4.0]),
        "y": np.array([10.0, 10.0, 10.0]),
    }
    _apply_calculation(stage, values, "f")
    harness.check(np.allclose(values["c1"], [1.0, 2.0, 3.0]))
    harness.check(np.allclose(values["c2"], [12.0, 14.0, 16.0]))
    # The reverse curve maps the normalized input to its complement.
    harness.check(np.allclose(values["c3"], [8.0, 6.0, 4.0]))

    # A row hidden downstream still feeds the rows below it.
    hidden_feed = _calculation_stage({"rows": [
        {**rows[0]},
        {**rows[1], "visible": False},
        {**rows[2], "visible": True},
    ]})
    values = {
        "x": np.array([0.0, 2.0, 4.0]),
        "y": np.array([10.0, 10.0, 10.0]),
    }
    _apply_calculation(hidden_feed, values, "f")
    harness.check(np.allclose(values["c3"], [8.0, 6.0, 4.0]))
    harness.eq(
        _calculation_outputs(hidden_feed, visible_only=True), ["c1", "c3"],
        "only the visible rows are exposed downstream",
    )

    identity = _curve_lut([[0.0, 0.0], [1.0, 1.0]])
    harness.check(abs(float(identity[0])) < 1e-6)
    harness.check(abs(float(identity[-1]) - 1.0) < 1e-6)
    reverse = _curve_lut([[0.0, 1.0], [1.0, 0.0]])
    harness.check(abs(float(reverse[0]) - 1.0) < 1e-6)
    harness.check(abs(float(reverse[-1])) < 1e-6)


@harness.case("calc.pipeline", "L3", "calculation", tags=("fast",))
def pipeline_values() -> None:
    rows = [
        {
            "id": "r1", "type": "clip", "files": [], "output": "clipped",
            "visible": True, "source": {"column": "FL1-A"},
            "min": 40.0, "max": 100.0,
        },
        {
            "id": "r2", "type": "add", "files": [], "output": "hidden_sum",
            "visible": False,
            "terms": [
                {"weight": 1.0, "source": {"row": "r1"}},
                {"weight": 1.0, "source": {"column": "FL2-A"}},
            ],
        },
    ]
    project, parquet = _project_with_calculation("calc_pipeline", [])
    rows[0]["files"] = [parquet]
    rows[1]["files"] = [parquet]
    task = project.workflow["tasks"][0]["children"][0]
    task["params"]["rows"] = rows
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    task.setdefault("children", []).append(figure)
    project.save()

    window = _open(project)
    try:
        ctx = window._ctx
        columns = _data_columns._columns_for(ctx, parquet, "fig-1")
        harness.check("clipped" in columns,
                      f"downstream columns: {columns}")
        harness.check("hidden_sum" not in columns,
                      "hidden rows stay out of downstream channel lists")

        values = ctx._column_data("fig-1", parquet, ["clipped"])
        base = harness.base_frame(200)
        expected = np.clip(
            base["FL1-A"].to_numpy(dtype="float64"), 40.0, 100.0
        )
        harness.check(
            np.allclose(values["clipped"], expected),
            "the saved stage produces the clipped values",
        )
        stages = _data_columns._map_stages(
            ctx, "fig-1", parquet_path=parquet
        )
        harness.check(any(kind == "calculation" for kind, _ in stages))
        dependencies = _pipeline_dependencies(stages, {"clipped"}, parquet)
        harness.check("FL1-A" in dependencies)
        harness.check(
            "clipped" not in dependencies,
            "a calculation output is not listed as a source column",
        )
    finally:
        _close(window)


@harness.case("calc.figure", "L4", "calculation", tags=("fast",))
def figure_downstream() -> None:
    rows = [{
        "id": "r1", "type": "clip", "files": [], "output": "clipped",
        "visible": True, "source": {"column": "FL1-A"},
        "min": 40.0, "max": 100.0,
    }]
    project, parquet = _project_with_calculation("calc_figure", [])
    rows[0]["files"] = [parquet]
    task = project.workflow["tasks"][0]["children"][0]
    task["params"]["rows"] = rows
    task.setdefault("children", []).append(
        harness.task_def("figure", task_id="fig-1", name="Figure")
    )
    project.save()

    window = _open(project)
    try:
        window._open_module("fig-1", "figure", "Figure")
        harness.pump(400)
        ctx = window._ctx
        page = window.tabs.currentWidget()
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(300)
        plot = page._fig_plots[0]
        plot["x"], plot["y"] = "clipped", "FL2-A"
        plot["xmin"] = plot["xmax"] = None
        plot["ymin"] = plot["ymax"] = None
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        widget = page._fig_cards[0].plot_widget()
        harness.wait_for(
            lambda: (
                getattr(widget, "_data_xmin", None) is not None
                and widget._data_xmin >= 40.0 - 1e-6
            ),
            timeout=15,
            message="the Figure scatter on the calculated channel",
        )
        harness.check(widget._data_xmax <= 100.0 + 1e-6)
    finally:
        _close(window)


@harness.case("calc.page", "L4", "calculation", tags=("fast",))
def page_flow() -> None:
    rows = [{
        "id": "r1", "type": "clip", "files": [], "output": "channel_1",
        "visible": True, "source": {"column": "FL1-A"},
        "min": None, "max": None, "auto_range": True,
    }]
    project, parquet = _project_with_calculation("calc_page", [])
    rows[0]["files"] = [parquet]
    task = project.workflow["tasks"][0]["children"][0]
    task["params"]["rows"] = rows
    task.setdefault("children", []).append(
        harness.task_def("figure", task_id="fig-1", name="Figure")
    )
    project.save()

    window = _open(project)
    try:
        window._open_module("calc-1", "calculation", "Calculation")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx
        harness.eq(page._calc_files, [parquet])
        harness.eq(len(page._calc_rows), 1)
        harness.eq(page._calc_selected_id, "r1")

        # The initial row has no thresholds: the worker fills them from the
        # channel data range.
        harness.wait_for(
            lambda: page._calc_rows[0].get("min") is not None,
            timeout=15,
            message="the data range to be computed",
        )
        row = page._calc_rows[0]
        harness.check(row["min"] <= row["max"])
        harness.eq(page._calc_preview["x"], "channel_1")
        harness.eq(page._calc_preview["type"], "scatter")

        # A conflicting name is flagged and blocks saving.
        row["output"] = "FL1-A"
        _calc_page._calc_rebuild_rows(ctx, page, select_id="r1")
        errors = _calc_page._calc_row_errors(ctx, page)
        harness.check("r1" in errors)
        with harness.patch_dialogs(
            _calc_page, warning=QMessageBox.StandardButton.Ok
        ):
            _calc_page._calc_save_now(ctx, page)
        harness.check(page._calc_dirty, "an invalid page is not saved")

        row["output"] = "channel_1"
        _calc_page._calc_rebuild_rows(ctx, page, select_id="r1")
        with harness.patch_dialogs(
            _calc_page, warning=QMessageBox.StandardButton.Ok
        ):
            _calc_page._calc_save_now(ctx, page)
        harness.check(not page._calc_dirty)
        saved = window.project.workflow["tasks"][0]["children"][0]
        harness.eq(saved["params"]["rows"][0]["output"], "channel_1")
        columns = _data_columns._columns_for(ctx, parquet, "fig-1")
        harness.check("channel_1" in columns)
    finally:
        _close(window)


@harness.case("calc.page.preview", "L4", "calculation", tags=("fast",))
def page_preview() -> None:
    project, parquet = _project_with_calculation("calc_preview", [])
    task = project.workflow["tasks"][0]["children"][0]
    task["params"]["rows"] = []
    project.save()

    window = _open(project)
    try:
        window._open_module("calc-1", "calculation", "Calculation")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx
        _calc_page._calc_add_row(ctx, page)
        row = page._calc_rows[0]
        harness.wait_for(
            lambda: row.get("min") is not None, timeout=15,
            message="the Clip data range",
        )
        harness.wait_for(
            lambda: getattr(page._calc_plot_widget, "_data_xmin", None)
            is not None,
            timeout=15,
            message="the scatter preview",
        )
        harness.check(
            page._calc_plot_widget._data_xmin >= row["min"] - 1e-6
            and page._calc_plot_widget._data_xmax <= row["max"] + 1e-6,
            "the preview plots the clipped channel",
        )

        # Switch the row to Contrast through the type combo.
        widget = page._calc_row_widgets[row["id"]]
        combo = widget._calc_type_combo
        combo.setCurrentIndex(combo.findData("contrast"))
        harness.pump(150)
        harness.eq(row["type"], "contrast")
        harness.wait_for(
            lambda: (row.get("domain") or {}).get("min") is not None,
            timeout=15,
            message="the Contrast input range",
        )
        editor = page._calc_row_widgets[row["id"]]._calc_editor._calc_curve
        editor.set_anchors([[0.0, 1.0], [1.0, 0.0]])
        editor.changed.emit()
        harness.pump(300)

        stage = _calculation_stage({"rows": page._calc_rows})
        base = ctx._column_data(
            "calc-1", parquet, ["FL1-A"]
        )["FL1-A"]
        values = {"FL1-A": base}
        _apply_calculation(stage, values, parquet)
        domain = row["domain"]
        span = domain["max"] - domain["min"]
        expected = domain["min"] + (
            1.0 - np.clip(
                (base - domain["min"]) / span, 0.0, 1.0
            )
        ) * span
        out = values[row["output"]]
        finite = np.isfinite(out)
        harness.check(
            np.allclose(out[finite], expected[finite]),
            "the reverse contrast curve is applied",
        )
    finally:
        _close(window)


@harness.case("calc.page.image", "L4", "calculation", tags=("fast",))
def page_image_preview() -> None:
    calc = harness.task_def(
        "calculation", task_id="calc-img", name="Calculation",
        params={"files": [], "rows": []},
    )
    project, parquet = e2e.image_project("calc_image", tasks=[calc])
    task = project.workflow["tasks"][0]["children"][0]
    task["params"]["files"] = [parquet]
    task["params"]["rows"] = [{
        "id": "r1", "type": "clip", "files": [parquet],
        "output": "channel_1", "visible": True,
        "source": {"column": "Channel 1"}, "min": 0.0, "max": 100.0,
    }]
    project.save()

    window = e2e.open_window(project)
    try:
        page = e2e.open_page(
            window, "calc-img", "calculation", "Calculation"
        )
        ctx = window._ctx
        page._calc_preview["type"] = "image"
        page._calc_preview["file"] = parquet
        page._calc_preview["file_name"] = "nuclei.tif"
        page._calc_preview["image_channel"] = "channel_1"
        _calc_page._calc_rebuild_settings(ctx, page)
        _calc_page._calc_render_preview(ctx, page)
        harness.pump(400)
        plot = page._calc_plot_widget
        harness.check(
            getattr(plot, "_img_item", None) is not None,
            "a calculated channel renders as an image layer",
        )
        harness.check(
            getattr(plot, "_img_extent", None) is not None,
            "the image extent is available",
        )
        harness.check(
            getattr(plot, "_img_data", None) is not None,
            "the calculated channel is actually rastered, not left blank",
        )
        harness.check(
            getattr(plot, "_image_probe", None) is not None,
            "the cursor probe follows the calculated image",
        )
        harness.eq(page._calc_preview.get("x"), "x",
                   "the image preview fixes X to x")
        harness.eq(page._calc_preview.get("y"), "y",
                   "the image preview fixes Y to y")
        harness.eq(
            plot.getPlotItem().getAxis("bottom").labelText, "x",
            "the calculation image uses pixel x as the X axis",
        )
        harness.eq(
            plot.getPlotItem().getAxis("left").labelText, "y",
            "the calculation image uses pixel y as the Y axis",
        )
    finally:
        e2e.close_window(window)


@harness.case("calc.scale", "L3", "calculation", tags=("fast",))
def scale_rows() -> None:
    """Log/Asinh rows dispatch to the scale maths and keep chained inputs."""
    from smage.core.scale_math import _apply_scale_transform

    rows = [
        {
            "id": "r1", "type": "scale", "files": ["f"], "output": "log",
            "visible": True, "source": {"column": "x"},
            "method": "log10",
        },
        {
            "id": "r2", "type": "scale", "files": ["f"], "output": "asinh",
            "visible": True, "source": {"row": "r1"},
            "method": "arcsinh", "cofactor": 5.0,
        },
    ]
    stage = _calculation_stage({"rows": rows})
    harness.eq(_calculation_dependencies(stage, {"log"}), {"x"})
    harness.eq(_calculation_dependencies(stage, {"asinh"}), {"x"})

    base = np.array([-100.0, -10.0, 0.0, 10.0, 100.0])
    values = {"x": base.copy()}
    _apply_calculation(stage, values, "f")
    harness.check(
        np.allclose(
            values["log"],
            _apply_scale_transform(base, {"method": "log10", "param": None}),
        ),
        "the Log row applies log10",
    )
    harness.check(
        np.allclose(
            values["asinh"],
            _apply_scale_transform(
                values["log"], {"method": "arcsinh", "param": 5.0}
            ),
        ),
        "the Asinh row reads the row above",
    )
    harness.check(np.allclose(values["x"], base),
                  "calculation does not modify its inputs")

    fallback = _calculation_stage({"rows": [{
        "id": "r", "type": "scale", "files": ["f"], "output": "s",
        "source": {"column": "x"}, "method": "unknown", "cofactor": -1,
    }]})
    harness.eq(
        fallback["rows"][0]["payload"],
        {"method": "log10", "cofactor": 5.0},
        "invalid scale settings fall back to log10 / 5",
    )


@harness.case("calc.file_scope", "L4", "calculation", tags=("fast",))
def file_scope() -> None:
    """A row applies only to its own files; other files do not see it."""
    project = harness.new_project("calc_scope", rows=60)
    first = str(project.data_dir / "case.parquet")
    second = e2e.add_parquet(
        project, "second", harness.base_frame(40).copy()
    )
    task = harness.task_def(
        "calculation", task_id="calc-1", name="Calculation",
        params={
            "files": [first, second],
            "rows": [
                {
                    "id": "r1", "type": "clip", "files": [first],
                    "output": "first_only", "visible": True,
                    "source": {"column": "FL1-A"},
                    "min": 0.0, "max": 10.0,
                },
                {
                    "id": "r2", "type": "add", "files": [first, second],
                    "output": "both", "visible": True,
                    "terms": [
                        {"weight": 1.0, "source": {"column": "x"}},
                        {"weight": 1.0, "source": {"column": "y"}},
                    ],
                },
                {
                    "id": "r3", "type": "scale", "files": [second],
                    "output": "second_only", "visible": True,
                    "source": {"column": "FL2-A"}, "method": "log10",
                },
            ],
        },
        children=[harness.task_def("figure", task_id="fig-1", name="Figure")],
    )
    project.workflow["tasks"][0].setdefault("children", []).append(task)
    project.save()

    window = _open(project)
    try:
        ctx = window._ctx
        first_columns = _data_columns._columns_for(ctx, first, "fig-1")
        second_columns = _data_columns._columns_for(ctx, second, "fig-1")
        harness.check("first_only" in first_columns)
        harness.check("both" in first_columns)
        harness.check("second_only" not in first_columns)
        harness.check("both" in second_columns)
        harness.check("second_only" in second_columns)
        harness.check("first_only" not in second_columns)

        first_values = ctx._column_data(
            "fig-1", first, ["first_only", "both"]
        )
        second_values = ctx._column_data(
            "fig-1", second, ["first_only", "both", "second_only"]
        )
        harness.check("first_only" in first_values)
        harness.check("first_only" not in second_values,
                      "a row is not evaluated on files outside its scope")
        harness.check("both" in first_values and "both" in second_values)
        harness.check("second_only" in second_values)
    finally:
        _close(window)


@harness.case("calc.page.files_visible", "L4", "calculation",
              tags=("fast",))
def page_files_visible() -> None:
    """The row file picker and visibility checkbox reach the saved stage."""
    from unittest.mock import patch

    project = harness.new_project("calc_files_visible", rows=80)
    first = str(project.data_dir / "case.parquet")
    second = e2e.add_parquet(
        project, "second", harness.base_frame(40).copy()
    )
    task = harness.task_def(
        "calculation", task_id="calc-1", name="Calculation",
        params={"files": [first, second], "rows": []},
        children=[harness.task_def("figure", task_id="fig-1", name="Figure")],
    )
    project.workflow["tasks"][0].setdefault("children", []).append(task)
    project.save()

    window = _open(project)
    try:
        window._open_module("calc-1", "calculation", "Calculation")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx

        _calc_page._calc_add_row(ctx, page)
        first_row = page._calc_rows[0]
        first_name = _calc_page._calc_file_name(
            ctx, first, page._calc_task_id
        )
        with patch.object(
            _calc_page._widgets_picker, "_pick_items_dialog",
            return_value=[first_name],
        ):
            _calc_page._calc_choose_row_files(ctx, page, first_row)
        harness.eq(first_row["files"], [first])
        first_row.update({"min": 0.0, "max": 100.0})
        _calc_page._calc_changed(ctx, page, first_row)

        _calc_page._calc_add_row(ctx, page)
        second_row = page._calc_rows[1]
        second_row["output"] = "hidden_row"
        second_row.update({"min": 0.0, "max": 100.0})
        widget = page._calc_row_widgets[second_row["id"]]
        widget._calc_visible.setChecked(False)
        harness.check(second_row["visible"] is False)

        _calc_page._calc_save_now(ctx, page)
        harness.check(not page._calc_dirty)
        saved = window.project.workflow["tasks"][0]["children"][0]
        rows = saved["params"]["rows"]
        harness.eq(rows[0]["files"], [first])
        harness.check(rows[1]["visible"] is False)

        harness.check(
            "channel_1" in _data_columns._columns_for(ctx, first, "fig-1")
        )
        harness.check(
            "channel_1" not in _data_columns._columns_for(
                ctx, second, "fig-1"
            ),
            "the first row is scoped to its file",
        )
        for path in (first, second):
            harness.check(
                "hidden_row" not in _data_columns._columns_for(
                    ctx, path, "fig-1"
                ),
                "a hidden row is not exposed downstream",
            )
    finally:
        _close(window)


@harness.case("calc.page.files_required", "L4", "calculation",
              tags=("fast",))
def page_files_required() -> None:
    """A new Calculation module starts with no files selected."""
    from unittest.mock import patch

    project = harness.new_project("calc_files_required", rows=40)
    task = harness.task_def(
        "calculation", task_id="calc-1", name="Calculation", params={},
    )
    project.workflow["tasks"][0].setdefault("children", []).append(task)
    project.save()

    window = _open(project)
    try:
        window._open_module("calc-1", "calculation", "Calculation")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx
        harness.eq(page._calc_files, [])
        harness.check(page._calc_preview["file"] is None,
                      "the preview does not pick a file automatically")
        harness.eq(page._calc_files_count.text(), "0 file(s) selected")

        with harness.patch_dialogs(
            _calc_page, information=QMessageBox.StandardButton.Ok
        ):
            _calc_page._calc_add_row(ctx, page)
        harness.eq(page._calc_rows, [],
                   "a row cannot be added before choosing files")

        names = [
            rec.get("name")
            for rec in _calc_page._calc_available_files(ctx, "calc-1")
        ]
        harness.check(bool(names))
        with patch.object(
            _calc_page._widgets_picker, "_pick_items_dialog",
            return_value=names,
        ):
            _calc_page._calc_select_files(ctx, page)
        harness.eq(len(page._calc_files), len(names))
        harness.check(page._calc_preview["file"] in page._calc_files)

        _calc_page._calc_add_row(ctx, page)
        harness.eq(len(page._calc_rows), 1)
        harness.eq(page._calc_rows[0]["files"], page._calc_files)
    finally:
        _close(window)


@harness.case("calc.page.dependencies", "L4", "calculation", tags=("fast",))
def page_dependencies() -> None:
    project, parquet = _project_with_calculation("calc_dependencies", [])
    task = project.workflow["tasks"][0]["children"][0]
    task["params"]["rows"] = []
    project.save()
    window = _open(project)
    try:
        window._open_module("calc-1", "calculation", "Calculation")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx

        _calc_page._calc_add_row(ctx, page)
        harness.pump(100)
        first = page._calc_rows[0]
        _calc_page._calc_add_row(ctx, page)
        harness.pump(100)
        second = page._calc_rows[1]

        # Second row reads the first row's output.
        second["type"] = "scale"
        second["source"] = {"row": first["id"]}
        second["method"] = "log10"
        _calc_page._calc_rebuild_rows(ctx, page, select_id=second["id"])
        errors = _calc_page._calc_row_errors(ctx, page)
        harness.check(second["id"] not in errors,
                      f"valid dependency: {errors}")

        # Moving the first row below the second breaks the dependency and is
        # refused, leaving the order unchanged.
        with harness.patch_dialogs(
            _calc_page, warning=QMessageBox.StandardButton.Ok
        ):
            _calc_page._calc_reorder(
                ctx, page, str(first["id"]), len(page._calc_rows)
            )
        harness.eq(page._calc_rows[0]["id"], first["id"])
        harness.eq(page._calc_rows[1]["id"], second["id"])

        # Deleting the row another row uses is refused.
        page._calc_selected_id = str(first["id"])
        with harness.patch_dialogs(
            _calc_page, warning=QMessageBox.StandardButton.Ok
        ):
            _calc_page._calc_delete_row(ctx, page)
        harness.eq(len(page._calc_rows), 2)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
