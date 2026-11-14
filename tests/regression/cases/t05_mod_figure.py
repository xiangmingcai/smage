"""L4: Figure — add/duplicate/delete plots, breaks and saved plot definitions."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMenu,
    QTableWidget,
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
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        harness.pump(150)
        xr, _yr = card.plot_widget().getPlotItem().getViewBox().viewRange()
        harness.check(abs(xr[0] - 10.0) < 1e-6, f"x min from Scale: {xr}")
        harness.check(abs(xr[1] - 90.0) < 1e-6, f"x max from Scale: {xr}")

        # A saved manual range wins over the Scale default.
        plot["xmin"], plot["xmax"] = 20.0, 40.0
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        harness.pump(150)
        xr, _yr = card.plot_widget().getPlotItem().getViewBox().viewRange()
        harness.check(abs(xr[0] - 20.0) < 1e-6, f"saved x min kept: {xr}")
        harness.check(abs(xr[1] - 40.0) < 1e-6, f"saved x max kept: {xr}")

        # One configured side uses Scale, the other falls back to the data.
        plot["x"] = "FL2-A"
        plot["xmin"] = plot["xmax"] = None
        _figure_page._figure_apply_change(ctx, page, 0, plot)
        harness.pump(150)
        xr, _yr = card.plot_widget().getPlotItem().getViewBox().viewRange()
        data_max = getattr(card.plot_widget(), "_data_xmax", None)
        harness.check(abs(xr[0] - 5.0) < 1e-6, f"one-sided min: {xr}")
        harness.check(
            data_max is not None and abs(xr[1] - data_max) < 1e-6,
            f"one-sided other side uses data: {xr} vs {data_max}",
        )
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


if __name__ == "__main__":
    raise SystemExit(harness.main())
