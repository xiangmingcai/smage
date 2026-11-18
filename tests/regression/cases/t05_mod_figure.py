"""L4: Figure — add/duplicate/delete plots, breaks and saved plot definitions."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QToolTip,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.modules.figure.page import _FigurePermuteDialog  # noqa: E402
from smage.modules.figure import page as _figure_page
from smage.widgets import picker as _widgets_picker  # noqa: E402
from smage.widgets.plot import export as _widgets_plot_export  # noqa: E402
from smage.widgets.plot import renderer as _widgets_plot_renderer  # noqa: E402


def _open(case_id: str):
    task = harness.task_def("figure", task_id="fig-1", name="Figure")
    project = harness.new_project(case_id, rows=60, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fig-1", "figure", "Figure")
    harness.pump(450)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


def _assert_axis_margin(test, view_range, lo, hi, label) -> None:
    """The configured min/max sit at 5 % and 95 % of the visible axis."""
    span = view_range[1] - view_range[0]
    test.check(span > 0, f"{label}: empty view range {view_range}")
    pos_lo = (lo - view_range[0]) / span
    pos_hi = (hi - view_range[0]) / span
    test.check(
        abs(pos_lo - 0.05) < 1e-6,
        f"{label}: min at 5 % of the axis, got {pos_lo:.4f} ({view_range})",
    )
    test.check(
        abs(pos_hi - 0.95) < 1e-6,
        f"{label}: max at 95 % of the axis, got {pos_hi:.4f} ({view_range})",
    )


@harness.case("mod.figure.add", "L4", "figure", tags=("fast",))
def add_plots() -> None:
    window, _project, page = _open("figure_add")
    try:
        ctx = window._ctx
        harness.eq(page._fig_plots, [])
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(300)
        harness.eq(len(page._fig_plots), 1)
        harness.eq(len(page._fig_cards), 1)
        plot = page._fig_plots[0]
        harness.eq(plot["type"], "scatter")
        harness.check(plot["file"] is not None)
        harness.check(bool(plot["x"]) and bool(plot["y"]))
        harness.eq(page._fig_selected, [0])

        _figure_page._figure_add_plot(ctx, page, "histogram")
        harness.pump(250)
        harness.eq(len(page._fig_plots), 2)
        harness.eq(page._fig_plots[1]["type"], "histogram")
        harness.eq(page._fig_plots[1]["y"], None)
    finally:
        _close(window)


@harness.case(
    "mod.figure.range_fallback", "L4", "figure", tags=("fast",)
)
def range_fallback() -> None:
    """Without a fully configured Scale, From scale falls back to Auto."""
    window, _project, page = _open("figure_range_fallback")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(300)
        plot = page._fig_plots[0]
        harness.eq(plot.get("x_range_mode"), "auto")
        harness.eq(plot.get("y_range_mode"), "auto")

        combos = [
            combo for combo in page._fig_settings_layout.parentWidget()
            .findChildren(QComboBox)
            if combo.findData("auto") >= 0
            and combo.findData("manual") >= 0
        ]
        harness.eq(len(combos), 2)
        harness.check(
            all(combo.findData("scale") < 0 for combo in combos),
            "From scale is hidden when no Scale provides both sides",
        )
        harness.check(
            all(combo.currentData() == "auto" for combo in combos),
            "the fallback is Auto",
        )
    finally:
        _close(window)


@harness.case("mod.figure.range_modes", "L4", "figure", tags=("fast",))
def range_modes() -> None:
    """New scatters use From scale, and Manual pre-fills the current range."""
    project = harness.new_project("figure_range_modes", rows=80)
    parquet = str(project.data_dir / "case.parquet")
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={
            "files": [parquet],
            "columns": [
                {"name": "x", "method": "linear", "param": None,
                 "min": 1.0, "max": 9.0},
                {"name": "y", "method": "linear", "param": None,
                 "min": 0.0, "max": 2.0},
            ],
        },
        children=[figure],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fig-1", "figure", "Figure")
    harness.pump(400)
    try:
        ctx = window._ctx
        page = window.tabs.currentWidget()
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(300)
        plot = page._fig_plots[0]
        harness.eq(plot.get("x_range_mode"), "scale")
        harness.eq(plot.get("y_range_mode"), "scale")
        widget = page._fig_cards[0].plot_widget()
        xr, yr = widget.getPlotItem().getViewBox().viewRange()
        _assert_axis_margin(harness, xr, 1.0, 9.0, "from-scale x")
        _assert_axis_margin(harness, yr, 0.0, 2.0, "from-scale y")

        combos = [
            combo for combo in page._fig_settings_layout.parentWidget()
            .findChildren(QComboBox)
            if combo.findData("scale") >= 0
            and combo.findData("manual") >= 0
        ]
        harness.eq(len(combos), 2)
        harness.check(
            all(combo.currentData() == "scale" for combo in combos),
            "new scatter defaults to From scale",
        )

        x_combo = combos[0]
        x_combo.setCurrentIndex(x_combo.findData("manual"))
        harness.pump(120)
        harness.eq(plot.get("x_range_mode"), "manual")
        harness.check(plot.get("xmin") is not None)
        harness.check(plot.get("xmax") is not None)
        harness.check(abs(plot["xmin"] - 1.0) < 1e-6)
        harness.check(abs(plot["xmax"] - 9.0) < 1e-6)

        x_combo.setCurrentIndex(x_combo.findData("auto"))
        harness.pump(120)
        harness.eq(plot.get("x_range_mode"), "auto")
        xr, _yr = widget.getPlotItem().getViewBox().viewRange()
        data_min = getattr(widget, "_data_xmin", None)
        data_max = getattr(widget, "_data_xmax", None)
        harness.check(
            data_min is not None and data_max is not None,
            "Auto has a data extent",
        )
        _assert_axis_margin(
            harness, xr, data_min, data_max, "auto x"
        )
    finally:
        _close(window)


@harness.case("mod.figure.channel_ranges", "L4", "figure", tags=("fast",))
def channel_range_records() -> None:
    """Each channel keeps its own mode and manual range."""
    from smage.widgets.plot import renderer

    plot = {
        "type": "scatter", "file": "f", "x": "Channel 1",
        "x_range_mode": "manual", "xmin": 1.0, "xmax": 9.0,
    }
    renderer._figure_sync_range_record(plot, "x")
    harness.eq(plot["x_ranges"]["Channel 1"]["mode"], "manual")

    plot["x"] = "Channel 2"
    renderer._figure_sync_range_record(plot, "x")
    harness.eq(plot["x_range_mode"], "scale")
    harness.check(plot["xmin"] is None and plot["xmax"] is None)

    plot["x_range_mode"] = "auto"
    renderer._figure_save_range_record(plot, "x", "auto")
    plot["x"] = "Channel 1"
    renderer._figure_sync_range_record(plot, "x")
    harness.eq(plot["x_range_mode"], "manual")
    harness.eq(plot["xmin"], 1.0)
    harness.eq(plot["xmax"], 9.0)

    plot["x"] = "Channel 2"
    renderer._figure_sync_range_record(plot, "x")
    harness.eq(plot["x_range_mode"], "auto")


@harness.case("mod.figure.scale_defaults", "L4", "figure", tags=("fast",))
def scale_axis_defaults() -> None:
    """Auto axes take their default range from the nearest upstream Scale."""
    project = harness.new_project("figure_scale_defaults", rows=80)
    parquet = str(project.data_dir / "case.parquet")
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={
            "files": [parquet],
            "columns": [
                {"name": "FL1-A", "method": "linear", "param": None,
                 "min": 10.0, "max": 90.0},
                # Only the lower side is configured on this channel.
                {"name": "FL2-A", "method": "linear", "param": None,
                 "min": 5.0, "max": None},
            ],
        },
        children=[figure],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fig-1", "figure", "Figure")
    harness.pump(400)
    try:
        ctx = window._ctx
        page = window.tabs.currentWidget()
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(250)
        plot = page._fig_plots[0]
        card = page._fig_cards[0]

        plot["x"], plot["y"] = "FL1-A", "FL2-A"
        plot["xmin"] = plot["xmax"] = None
        plot["ymin"] = plot["ymax"] = None
        # Changing the axis rebuilds the settings; the automatic fallback is
        # then replaced by From scale for FL1-A (which has both sides).
        _figure_page._figure_rebuild_settings(ctx, page)
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        harness.pump(150)
        xr, _yr = card.plot_widget().getPlotItem().getViewBox().viewRange()
        _assert_axis_margin(harness, xr, 10.0, 90.0, "FL1-A x")

        # The range inputs show the same Scale default as the view.
        _figure_page._figure_rebuild_settings(ctx, page)
        harness.pump(100)
        texts = {
            edit.text()
            for edit in page._fig_settings_layout.parentWidget()
            .findChildren(QLineEdit)
        }
        harness.check(
            "10.0" in texts and "90.0" in texts,
            f"range fields show the Scale bounds: {texts}",
        )

        # Manual mode wins over the Scale default.
        plot["x_range_mode"] = "manual"
        plot["xmin"], plot["xmax"] = 20.0, 40.0
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        harness.pump(150)
        xr, _yr = card.plot_widget().getPlotItem().getViewBox().viewRange()
        _assert_axis_margin(harness, xr, 20.0, 40.0, "manual x")

        # A Scale with only one side does not qualify: From scale falls back
        # to the data extent on both sides.
        plot["x"] = "FL2-A"
        plot["x_range_mode"] = "scale"
        plot["xmin"] = plot["xmax"] = None
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        harness.pump(150)
        xr, _yr = card.plot_widget().getPlotItem().getViewBox().viewRange()
        data_min = getattr(card.plot_widget(), "_data_xmin", None)
        data_max = getattr(card.plot_widget(), "_data_xmax", None)
        harness.check(
            data_min is not None and data_max is not None,
            "the fallback has a data extent",
        )
        _assert_axis_margin(
            harness, xr, data_min, data_max, "one-sided Scale fallback"
        )
    finally:
        _close(window)


@harness.case("mod.figure.axis_margin", "L4", "figure", tags=("fast",))
def axis_margin() -> None:
    """Data plots keep a 5 % border; the saved range values stay unpadded."""
    window, _project, page = _open("figure_axis_margin")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(300)
        _figure_page._figure_add_plot(ctx, page, "histogram")
        harness.pump(300)

        scatter = page._fig_cards[0].plot_widget()
        xr, yr = scatter.getPlotItem().getViewBox().viewRange()
        _assert_axis_margin(
            harness, xr, scatter._data_xmin, scatter._data_xmax, "scatter x"
        )
        _assert_axis_margin(
            harness, yr, scatter._data_ymin, scatter._data_ymax, "scatter y"
        )

        hist = page._fig_cards[1].plot_widget()
        hx, hy = hist.getPlotItem().getViewBox().viewRange()
        _assert_axis_margin(
            harness, hx, hist._data_xmin, hist._data_xmax, "histogram x"
        )
        # Counts follow the strict rule, so 0 sits just above the bottom edge.
        _assert_axis_margin(
            harness, hy, hist._data_ymin, hist._data_ymax, "histogram y"
        )

        # Empty or missing ranges are left to auto-range.
        harness.check(
            _widgets_plot_renderer._figure_pad_range(5.0, 5.0) is None,
            "an empty range is not padded",
        )
        harness.check(
            _widgets_plot_renderer._figure_pad_range(None, 1.0) is None,
            "a missing bound is not padded",
        )
    finally:
        _close(window)


@harness.case(
    "mod.figure.multi_manual_ranges", "L4", "figure", tags=("fast",)
)
def multi_manual_ranges() -> None:
    """Multi-plot Manual ranges convert original units on each channel."""
    import math

    from smage.widgets.controls import _LogStepSpinBox

    project = harness.new_project("figure_multi_manual", rows=80)
    parquet = str(project.data_dir / "case.parquet")
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={
            "files": [parquet],
            "columns": [
                {"name": "FL1-A", "method": "arcsinh", "param": 1000.0,
                 "min": 0.0, "max": 65535.0},
                {"name": "FL2-A", "method": "arcsinh", "param": 1000.0,
                 "min": 0.0, "max": 65535.0},
                {"name": "FL3-A", "method": "linear", "param": None,
                 "min": 0.0, "max": 65535.0},
            ],
        },
        children=[figure],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fig-1", "figure", "Figure")
    harness.pump(400)
    try:
        ctx = window._ctx
        page = window.tabs.currentWidget()
        for _ in range(2):
            _figure_page._figure_add_plot(ctx, page, "scatter")
            harness.pump(200)
        plots = page._fig_plots
        plots[0]["x"], plots[0]["y"] = "FL1-A", "FL2-A"
        plots[1]["x"], plots[1]["y"] = "FL3-A", "FL2-A"
        for i, plot in enumerate(plots):
            _widgets_plot_renderer._figure_sync_range_record(plot, "x")
            _widgets_plot_renderer._figure_sync_range_record(plot, "y")
            _figure_page._figure_apply_change(ctx, page, i, plot)
        _figure_page._figure_set_selection(ctx, page, [0, 1])
        harness.pump(200)
        host = page._fig_settings_layout.parentWidget()

        # Put both the X and Y rows into Manual (each change rebuilds).
        for _ in range(2):
            modes = [
                combo for combo in host.findChildren(QComboBox)
                if combo.findData("manual") >= 0
                and combo.findData("auto") >= 0
            ]
            target = next(
                (combo for combo in modes if combo.currentData() != "manual"),
                None,
            )
            if target is None:
                break
            target.setCurrentIndex(target.findData("manual"))
            harness.pump(150)

        spins = host.findChildren(_LogStepSpinBox)
        harness.check(len(spins) >= 4, f"range spins: {len(spins)}")
        # The point-size/bins fields come first; the two range rows are last.
        for spin, value in zip(spins[-4:], (0, 10000, 0, 10000)):
            spin.edit.setText(str(value))
            spin.edit.editingFinished.emit()
        harness.pump(200)

        # FL1-A is arcsinh: 10000 original becomes asinh(10) plot units.
        arc = math.asinh(10000 / 1000.0)
        harness.check(
            abs(plots[0]["xmax"] - arc) < 1e-6,
            f"arcsinh x max: {plots[0]['xmax']} vs {arc}",
        )
        harness.check(abs(plots[0]["xmin"]) < 1e-9)
        # FL3-A is linear: the value is stored unchanged.
        harness.check(abs(plots[1]["xmax"] - 10000.0) < 1e-6)
        # Both Y axes use FL2-A (arcsinh).
        harness.check(abs(plots[0]["ymax"] - arc) < 1e-6)
        harness.check(abs(plots[1]["ymax"] - arc) < 1e-6)

        # The single-plot display converts back without a NaN.
        configs = _widgets_plot_renderer._figure_axis_scale_configs(
            ctx, page._fig_task_id, parquet, "FL1-A",
            page._fig_column_transforms,
        )
        shown = _widgets_plot_renderer._figure_display_range_value(
            plots[0]["xmax"], configs
        )
        harness.check(math.isfinite(shown))
        harness.check(abs(shown - 10000.0) < 1e-6)
    finally:
        _close(window)


@harness.case("mod.figure.image_readout", "L4", "figure", tags=("fast",))
def image_cursor_readout() -> None:
    """An image plot reports x/y and every visible layer's value."""
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    project, _parquet = e2e.image_project("figure_readout", tasks=[figure])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("fig-1", "figure", "Figure")
    harness.pump(350)
    try:
        ctx = window._ctx
        page = window.tabs.currentWidget()
        _figure_page._figure_add_plot(ctx, page, "image")
        harness.pump(400)
        widget = page._fig_cards[0].plot_widget()
        probe = getattr(widget, "_image_probe", None)
        harness.check(probe is not None, "the image probe is retained")
        harness.check(bool(probe.get("layers")), "one layer is probeable")
        harness.eq(
            widget.getPlotItem().getAxis("bottom").labelText, "x",
            "the image X axis is the pixel x coordinate",
        )
        harness.eq(
            widget.getPlotItem().getAxis("left").labelText, "y",
            "the image Y axis is the pixel y coordinate",
        )

        viewbox = widget.getPlotItem().getViewBox()
        scene_pos = viewbox.mapViewToScene(QPointF(7.0, 7.0))
        _widgets_plot_renderer._figure_update_cursor_readout(
            widget, scene_pos
        )
        text = widget.getPlotItem().readoutText.text()
        harness.check("x: 7" in text and "y: 7" in text, text)
        harness.check("Channel 1:" in text, text)
        harness.check("200.0" in text, "one-decimal intensity: " + text)
        harness.check("—" not in text, text)
    finally:
        _close(window)


@harness.case("mod.figure.duplicate_delete", "L4", "figure", tags=("fast",))
def duplicate_delete() -> None:
    window, _project, page = _open("figure_dup")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(250)
        _figure_page._figure_duplicate_selected(ctx, page)
        harness.pump(250)
        harness.eq(len(page._fig_plots), 2)
        harness.eq(len(page._fig_cards), 2)
        harness.eq(page._fig_plots[0]["type"], page._fig_plots[1]["type"])

        page._fig_selected = [0]
        _figure_page._figure_delete_selected(ctx, page)
        harness.pump(250)
        harness.eq(len(page._fig_plots), 1)
        harness.eq(len(page._fig_cards), 1)
    finally:
        _close(window)


@harness.case("mod.figure.break_and_save", "L4", "figure", tags=("fast",))
def break_and_save() -> None:
    window, _project, page = _open("figure_save")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(250)
        plot = page._fig_plots[0]
        harness.check(not plot.get("break_before"))
        _figure_page._figure_toggle_break(ctx, page, [0])
        harness.check(page._fig_plots[0].get("break_before"))

        _figure_page._figure_save_now(ctx, page)
        task = window.project.workflow["tasks"][0]["children"][0]
        harness.eq(len(task["params"]["plots"]), 1)
        harness.check(task["params"]["plots"][0]["break_before"])

        from smage.project import Project

        saved = Project.load(window.project.path)
        child = saved.workflow["tasks"][0]["children"][0]
        harness.eq(len(child["params"]["plots"]), 1)
    finally:
        _close(window)


@harness.case("mod.figure.nxn", "L4", "figure", tags=("fast",))
def nxn() -> None:
    window, _project, page = _open("figure_nxn")
    try:
        ctx = window._ctx
        _widgets_picker._pick_items_dialog = lambda *a, **k: ["x", "y", "FL1-A"]
        _figure_page._figure_add_nxn(ctx, page)
        harness.pump(600)
        harness.eq(len(page._fig_plots), 3, "three variables give three pairs")
        harness.eq(len(page._fig_cards), 3)
        axes = {(plot["x"], plot["y"]) for plot in page._fig_plots}
        harness.eq(axes, {("x", "y"), ("x", "FL1-A"), ("y", "FL1-A")})
    finally:
        _close(window)


@harness.case("mod.figure.permute", "L4", "figure", tags=("fast",))
def permute() -> None:
    window, _project, page = _open("figure_permute")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(250)

        class FakeDialog:
            def exec(self):
                return QDialog.DialogCode.Accepted

            def dimension(self):
                return _FigurePermuteDialog.X_AXIS

            def values(self):
                return ["FL1-A", "FL2-A"]

            def new_row(self):
                return False

        window._figure_permute_dialog = lambda *a, **k: FakeDialog()
        _figure_page._figure_permute(ctx, page, 0)
        harness.pump(500)
        harness.eq(len(page._fig_plots), 3)
        harness.eq(
            [plot["x"] for plot in page._fig_plots],
            ["x", "FL1-A", "FL2-A"],
        )
    finally:
        _close(window)


@harness.case("mod.figure.image_overlay", "L4", "figure", tags=("fast",))
def image_overlay() -> None:
    task = harness.task_def("figure", task_id="fig-img", name="Figure")
    project, _parquet = e2e.image_project("figure_image", tasks=[task])
    window = e2e.open_window(project)
    try:
        page = e2e.open_page(window, "fig-img", "figure", "Figure")
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "image")
        harness.pump(500)
        harness.eq(page._fig_plots[0]["type"], "image")
        harness.check(bool(page._fig_plots[0].get("layers")),
                      "the image plot starts with one color layer")
        harness.eq(len(page._fig_cards), 1)
        harness.check(
            hasattr(page._fig_cards[0]._plot_widget, "_legend_widget"),
            "the image card has a side legend widget",
        )

        # Saving an image plot also writes its side legend.
        exported = project.data_dir / "image_plot.pdf"
        harness.check(
            _widgets_plot_export._plot_export_pdf(
                page._fig_cards[0].plot_widget(), exported
            ),
            "image plot with legend exports to PDF",
        )
        harness.check(exported.exists() and exported.stat().st_size > 1000,
                      "image PDF has content")

        _figure_page._figure_add_plot(ctx, page, "overlay")
        harness.pump(400)
        harness.eq(page._fig_plots[1]["type"], "overlay")
        harness.eq(page._fig_plots[1]["color_mode"], "overlay")
        harness.eq(len(page._fig_cards), 2)
    finally:
        e2e.close_window(window)


@harness.case("mod.figure.image_layer_color", "L4", "figure",
              tags=("fast",))
def image_layer_color() -> None:
    """Picking an image-layer color updates the settings swatch too."""
    task = harness.task_def("figure", task_id="fig-color", name="Figure")
    project, _parquet = e2e.image_project("figure_layer_color", tasks=[task])
    window = e2e.open_window(project)
    try:
        page = e2e.open_page(window, "fig-color", "figure", "Figure")
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "image")
        harness.pump(500)
        tables = [
            table for table in page.findChildren(QTableWidget)
            if table.columnCount() == 6
            and table.horizontalHeaderItem(2) is not None
            and table.horizontalHeaderItem(2).text() == "Color"
        ]
        harness.check(len(tables) == 1, f"image layer tables: {len(tables)}")
        table = tables[0]
        button = table.cellWidget(0, 2)
        harness.check(button is not None)
        harness.check(
            tuple(page._fig_plots[0]["layers"][0]["color"]) != (3, 200, 50)
        )

        with harness.patch_dialogs(
            _widgets_plot_renderer, color=QColor(3, 200, 50)
        ):
            button.click()
        harness.eq(
            tuple(page._fig_plots[0]["layers"][0]["color"]),
            (3, 200, 50),
            "the layer stores the chosen color",
        )
        harness.check(
            "rgb(3,200,50)" in button.styleSheet().replace(" ", ""),
            f"the settings swatch is refreshed: {button.styleSheet()}",
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.figure.box_select", "L4", "figure", tags=("fast",))
def box_select() -> None:
    window, _project, page = _open("figure_box")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(250)
        _figure_page._figure_set_selection(ctx, page, [1])
        _figure_page._figure_add_plot(ctx, page, "histogram")
        harness.pump(250)

        bounds = page._fig_scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        _figure_page._figure_box_select(ctx, page, bounds, False)
        harness.eq(sorted(page._fig_selected), [0, 1])

        positions = _figure_page._figure_flow_positions(ctx, page._fig_cards, page._fig_plots)
        harness.eq(len(positions), 2)
    finally:
        _close(window)


@harness.case("mod.figure.save_as", "L4", "figure", tags=("fast",))
def save_plot_as() -> None:
    """Save as… writes what is on screen, as image or editable PDF."""
    window, project, page = _open("figure_save_as")
    try:
        ctx = window._ctx
        _figure_page._figure_add_plot(ctx, page, "scatter")
        harness.pump(300)
        plot_widget = page._fig_cards[0].plot_widget()
        out_dir = project.data_dir / "figure_export"
        out_dir.mkdir(parents=True, exist_ok=True)

        png = out_dir / "plot.png"
        harness.check(
            _widgets_plot_export._plot_export_image(plot_widget, png, "png"),
            "PNG export returned success",
        )
        harness.check(png.exists() and png.stat().st_size > 1000,
                      "PNG file has content")
        image = QImage(str(png))
        harness.check(image.width() > plot_widget.width(),
                      f"PNG is rendered above screen size: {image.width()}")

        # The export follows the current view: zooming changes the output.
        def pixels(path):
            picture = QImage(str(path)).convertToFormat(
                QImage.Format.Format_RGBA8888
            )
            frame = np.frombuffer(
                picture.bits(), dtype=np.uint8
            ).reshape(picture.height(), picture.bytesPerLine())
            return frame[:, :picture.width() * 4].copy()

        default_pixels = pixels(png)
        viewbox = plot_widget.getPlotItem().getViewBox()
        viewbox.setRange(xRange=(0.0, 50.0), yRange=(0.0, 50.0), padding=0)
        harness.pump(200)
        zoomed = out_dir / "plot_zoom.png"
        harness.check(
            _widgets_plot_export._plot_export_image(
                plot_widget, zoomed, "png"
            )
        )
        harness.check(
            not np.array_equal(default_pixels, pixels(zoomed)),
            "the PNG export reflects the zoomed view",
        )

        pdf = out_dir / "plot.pdf"
        harness.check(
            _widgets_plot_export._plot_export_pdf(plot_widget, pdf),
            "PDF export returned success",
        )
        harness.check(pdf.exists() and pdf.stat().st_size > 1000,
                      "PDF file has content")
        harness.check(pdf.read_bytes()[:4] == b"%PDF",
                      "PDF has a PDF header")

        # The whole path: right-click the plot, pick "Save as…" from the
        # card menu, and write the file (only the native dialog is stubbed).
        jpg = out_dir / "plot.jpg"
        original_dialog = QFileDialog.getSaveFileName
        original_menu_cls = _figure_page.QMenu
        seen_actions = []

        class _FakeMenu(QMenu):
            def exec(self, *args, **kwargs):
                seen_actions.extend(
                    action.text()
                    for action in self.actions()
                    if not action.isSeparator()
                )
                return next(
                    action for action in self.actions()
                    if action.text() == "Save as…"
                )

        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (str(jpg), "JPEG image (*.jpg *.jpeg)")
        )
        _figure_page.QMenu = _FakeMenu
        try:
            QTest.mouseClick(
                plot_widget.viewport(), Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier, QPoint(60, 60),
            )
            harness.pump(100)
        finally:
            QFileDialog.getSaveFileName = original_dialog
            _figure_page.QMenu = original_menu_cls
        harness.check("Save as…" in seen_actions,
                      f"card menu actions: {seen_actions}")
        harness.check(jpg.exists() and jpg.stat().st_size > 1000,
                      "Save as… wrote the JPEG through the plot menu")

        # Cancelling the dialog writes nothing.
        before = {path.name for path in out_dir.iterdir()}
        original_dialog = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
        try:
            _figure_page._figure_save_plot_as(ctx, page, 0)
        finally:
            QFileDialog.getSaveFileName = original_dialog
        harness.eq(
            {path.name for path in out_dir.iterdir()}, before,
            "cancelling Save as… leaves no file",
        )
    finally:
        _close(window)


@harness.case(
    "mod.figure.color_tooltip", "L4", "figure", tags=("fast",)
)
def colour_swatch_tooltip_stays_light() -> None:
    """The mixed-colour swatch keeps the light tooltip palette."""
    window, _project, page = _open("figure_colour_tooltip")
    try:
        ctx = window._ctx
        for _ in range(2):
            _figure_page._figure_add_plot(ctx, page, "scatter")
            harness.pump(200)
        plots = page._fig_plots
        for index, colour in enumerate(((255, 0, 0), (0, 0, 255))):
            plots[index]["color_mode"] = "solid"
            plots[index]["solid_color"] = colour
            _figure_page._figure_apply_change(ctx, page, index, plots[index])
        _figure_page._figure_set_selection(ctx, page, [0, 1])
        harness.pump(250)

        host = page._fig_settings_layout.parentWidget()
        buttons = [
            button for button in host.findChildren(QPushButton)
            if button.toolTip() == "Colors differ"
        ]
        harness.check(bool(buttons), "the mixed-colour swatch is shown")
        button = buttons[0]
        QToolTip.showText(QPoint(10, 10), button.toolTip(), button)
        QApplication.processEvents()
        tip = next(
            (
                child for child in QApplication.allWidgets()
                if child.metaObject().className() == "QTipLabel"
            ),
            None,
        )
        harness.check(tip is not None, "the swatch tooltip is shown")
        if tip is not None:
            colour = QColor(tip.palette().color(QPalette.ColorRole.Window))
            harness.check(
                colour.lightness() >= 200,
                f"tooltip background is light: {colour.name()}",
            )
        QToolTip.hideText()
        QApplication.processEvents()
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
