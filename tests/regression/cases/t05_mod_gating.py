"""L4: Gating — gate creation, groups, boolean NOT, delete and persistence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QMessageBox,
    QPushButton,
    QToolTip,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.modules.gating import page as gating_page  # noqa: E402
from smage.core.gates import _gate_mask  # noqa: E402
from smage.widgets.gate_editor import editor as _gate_editor  # noqa: E402
from smage.widgets.gate_editor.canvas import _GatePolygonROI  # noqa: E402
from smage.widgets.plot_axes import _gate_roi_pens  # noqa: E402
from smage.widgets.plot import renderer as _plot_renderer  # noqa: E402
from smage.data import naming as _data_naming
from smage.modules.gating import page as gating_page


def _select_all_files(ctx, page) -> None:
    """Choose every upstream file, as the Data files picker would."""
    records = ctx._available_files("g-1")
    page._gating_files = [rec.get("parquet") for rec in records]
    gating_page._gating_refresh_files(ctx, page)
    gating_page._gating_update_tools(ctx, page)
    gating_page._gating_reload_rois(ctx, page)
    _gate_editor._gating_render(ctx, page)
    gating_page._gating_update_roi_visibility(ctx, page)
    _gate_editor._gating_rebuild_settings(ctx, page)
    harness.pump(200)


def _open(case_id: str):
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    project = harness.new_project(case_id, rows=120, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    page = window.tabs.currentWidget()
    _select_all_files(window._ctx, page)
    return window, project, page


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.gating.empty_state", "L4", "gating", tags=("fast",))
def empty_state() -> None:
    """A new Gating module waits for Data files before drawing anything."""
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    project = harness.new_project("gating_empty_state", rows=80, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    page = window.tabs.currentWidget()
    try:
        preview = page._gating_preview
        harness.eq(page._gating_files, [])
        harness.eq(preview.get("file"), None)
        harness.eq(preview.get("x"), None)
        harness.eq(preview.get("y"), None)
        harness.check(
            not page._gating_plot_widget.getPlotItem().items,
            "no data is drawn before files are selected",
        )
        harness.check(
            not page._gating_overlay.isHidden(),
            "the select-files hint is shown",
        )
        harness.check(
            "Select data files" in page._gating_overlay_box.text(),
            page._gating_overlay_box.text(),
        )
        harness.check(
            not any(
                btn.isEnabled()
                for btn in page._gating_tool_buttons.values()
            ),
            "gate tools are disabled without a file",
        )
        harness.check(
            not page._gating_col2gate_btn.isEnabled(),
            "Col2Gate is disabled without a file",
        )
    finally:
        _close(window)


@harness.case("mod.gating.plot_export", "L4", "gating", tags=("fast",))
def plot_export() -> None:
    """Right-clicking the gating plot saves it with the Figure exporter."""
    window, _project, page = _open("gating_plot_export")
    calls = []
    original = gating_page._widgets_plot_export._plot_export_save
    try:
        gating_page._widgets_plot_export._plot_export_save = (
            lambda widget, parent, name: calls.append((widget, name))
            or "C:/tmp/gating.pdf"
        )
        plot_widget = page._gating_plot_widget
        expected_name = gating_page._gating_export_name(page._gating_preview)
        harness.check(
            not plot_widget.getPlotItem().menuEnabled(),
            "pyqtgraph's built-in menu is disabled",
        )
        harness.check(
            not plot_widget.getPlotItem().getViewBox().menuEnabled(),
            "the viewbox's built-in menu is disabled",
        )
        gating_page._gating_save_plot_as(window._ctx, page)
        harness.eq(len(calls), 1)
        widget, name = calls[0]
        harness.check(widget is plot_widget, "exports the visible plot")
        harness.check(" vs " in name, name)
        harness.eq(name, expected_name)

        # A single right-click through the canvas must reach the plot menu
        # (forwarding only the left button made the second click necessary).
        opened = []
        original_menu = gating_page._gating_plot_context_menu
        gating_page._gating_plot_context_menu = (
            lambda ctx, target, pos: opened.append(pos)
        )
        try:
            center = plot_widget.mapToGlobal(
                QPoint(plot_widget.width() // 2, plot_widget.height() // 2)
            )
            canvas_pos = page._gating_plot_canvas.viewport().mapFromGlobal(center)
            QTest.mouseClick(
                page._gating_plot_canvas.viewport(),
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
                canvas_pos,
            )
            harness.eq(len(opened), 1, "one right-click opens the menu")
        finally:
            gating_page._gating_plot_context_menu = original_menu
    finally:
        gating_page._widgets_plot_export._plot_export_save = original
        _close(window)


@harness.case("mod.gating.fixed_point_size", "L4", "gating", tags=("fast",))
def fixed_point_size() -> None:
    """Fixed point size is a screen-pixel diameter, so points stay round."""
    window, _project, page = _open("gating_point_size")
    try:
        preview = page._gating_preview
        preview["fixed_point_size"] = True
        preview["point_size"] = 7
        _gate_editor._gating_render(window._ctx, page)
        harness.pump(200)
        scatter = next(
            item for item in page._gating_plot_widget.getPlotItem().items
            if isinstance(item, pg.ScatterPlotItem)
        )
        harness.check(scatter.opts["pxMode"], "points use screen pixels")
        harness.eq(float(scatter.opts["size"]), 7.0)

        # A data-unit size saved by an older project falls back to default.
        preview["point_size"] = 1000
        _gate_editor._gating_render(window._ctx, page)
        harness.pump(200)
        scatter = next(
            item for item in page._gating_plot_widget.getPlotItem().items
            if isinstance(item, pg.ScatterPlotItem)
        )
        harness.eq(float(scatter.opts["size"]), 4.0)
    finally:
        _close(window)


@harness.case("mod.gating.auto_open_first", "L4", "gating", tags=("fast",))
def auto_open_first_file() -> None:
    """Selecting files opens the first one with its first two channels."""
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    project = harness.new_project("gating_auto_open", rows=80, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        _select_all_files(ctx, page)
        preview = page._gating_preview
        records = ctx._available_files("g-1")
        harness.check(bool(records), "the project has an imported file")
        harness.eq(preview.get("file"), records[0].get("parquet"))
        harness.eq(page._gating_files, [records[0].get("parquet")])
        harness.eq(preview.get("x"), "FSC-A")
        harness.eq(preview.get("y"), "SSC-A")
        harness.check(
            bool(page._gating_plot_widget.getPlotItem().items),
            "the selected file is drawn",
        )
        harness.check(
            page._gating_overlay.isHidden(),
            "the empty-state hint is gone",
        )
        harness.check(
            page._gating_tool_buttons["rect"].isEnabled(),
            "gate tools are usable again",
        )
        harness.check(
            page._gating_col2gate_btn.isEnabled(),
            "Col2Gate is usable again",
        )
    finally:
        _close(window)


@harness.case("mod.gating.tooltip_light", "L4", "gating", tags=("fast",))
def gate_colour_tooltip_is_light() -> None:
    """The gate-colour swatch keeps the light tooltip palette."""
    window, _project, page = _open("gating_tooltip")
    try:
        button = page.findChild(QPushButton, "gateColorButton")
        harness.check(button is not None, "the gate colour button exists")
        QToolTip.showText(QPoint(10, 10), button.toolTip(), button)
        QApplication.processEvents()
        tip = next(
            (
                widget for widget in QApplication.allWidgets()
                if widget.metaObject().className() == "QTipLabel"
            ),
            None,
        )
        harness.check(tip is not None, "the tooltip is shown")
        if tip is not None:
            color = QColor(tip.palette().color(QPalette.ColorRole.Window))
            harness.check(
                color.lightness() >= 200,
                f"tooltip background is light: {color.name()}",
            )
        QToolTip.hideText()
        QApplication.processEvents()
    finally:
        _close(window)


@harness.case("mod.gating.switch_file_root_axes", "L4", "gating",
              tags=("fast",))
def switch_file_keeps_configured_axes_at_root() -> None:
    """At the root, changing files keeps the axes from Plot settings."""
    window, project, page = _open("gating_switch_axes")
    try:
        ctx = window._ctx
        first = project.data_dir / "case.parquet"
        second = project.data_dir / "second.parquet"
        second.write_bytes(first.read_bytes())
        columns = project.load_columns()
        columns[second.name] = list(columns.get(first.name) or [])
        project.save_columns(columns)
        ctx._column_index = project.load_columns()
        project.workflow["tasks"][0]["params"]["processed"].append({
            "name": "second.fcs",
            "source": str(first),
            "parquet": str(second),
            "rows": 80,
        })
        project.save()

        page._gating_files = [str(first), str(second)]
        preview = page._gating_preview
        preview["x"], preview["y"] = "FL1-A", "FL2-A"
        page._gating_settings_x = "FL1-A"
        page._gating_settings_y = "FL2-A"
        # Simulate axes left over from an earlier channel choice.
        preview["last_x"], preview["last_y"] = "FSC-A", "SSC-A"
        harness.eq(page._gating_context_gate_id, None)

        gating_page._gating_switch_file(ctx, page, str(second))
        harness.eq(preview["file"], str(second))
        harness.eq(preview["x"], "FL1-A")
        harness.eq(preview["y"], "FL2-A")
    finally:
        _close(window)


@harness.case("mod.gating.real_coordinates", "L4", "gating", tags=("fast",))
def gates_live_in_real_coordinates() -> None:
    """Gate geometry is stored in real units; Scale only moves it on screen."""
    gating = harness.task_def("gating", task_id="g-1", name="Gating")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "FL1-A", "method": "log10"}],
                "files": None},
        children=[gating],
    )
    project = harness.new_project("gating_real_coords", rows=60, tasks=[scale])
    window = harness.new_window()
    harness.open_project(window, project)
    try:
        window._open_module("g-1", "gating", "Gating")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx
        _select_all_files(ctx, page)
        page._gating_preview["x"] = "FL1-A"
        page._gating_preview["y"] = "FL2-A"
        gate = {
            "type": "rect", "x": "FL1-A", "y": "FL2-A",
            "plot_type": "scatter",
        }
        display = {"pos": [2.0, 3.0], "size": [1.0, 5.0]}
        real = gating_page._gating_geometry_to_real(ctx, page, gate, display)
        harness.check(np.isclose(real["pos"][0], 100.0),
                      f"log10 display x=2 is real 100: {real}")
        harness.check(np.isclose(real["size"][0], 900.0),
                      "the width follows the transformed corners")
        harness.check(np.isclose(real["pos"][1], 3.0), "linear Y is unchanged")
        harness.check(np.isclose(real["size"][1], 5.0))

        back = gating_page._gating_geometry_to_display(ctx, page, gate, real)
        harness.check(np.allclose(back["pos"], display["pos"]))
        harness.check(np.allclose(back["size"], display["size"]))
    finally:
        _close(window)


@harness.case("mod.gating.ellipse_scale", "L4", "gating", tags=("fast",))
def ellipse_follows_the_drawn_shape() -> None:
    """An ellipse drawn on a log axis keeps the outline that was drawn."""
    gating = harness.task_def("gating", task_id="g-1", name="Gating")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={"columns": [{"name": "FL1-A", "method": "log10"}],
                "files": None},
        children=[gating],
    )
    project = harness.new_project(
        "gating_ellipse_scale", rows=200, tasks=[scale]
    )
    window = harness.new_window()
    harness.open_project(window, project)
    try:
        window._open_module("g-1", "gating", "Gating")
        harness.pump(400)
        page = window.tabs.currentWidget()
        ctx = window._ctx
        _select_all_files(ctx, page)
        page._gating_preview["x"] = "FL1-A"
        page._gating_preview["y"] = "FL2-A"

        pos, size = (1.0, 2.0), (2.0, 1.0)
        roi = pg.EllipseROI(pos, size, **_gate_roi_pens("#000000"))
        gating_page._gating_add_gate(ctx, page, "ellipse", roi)
        gate = page._gating_gates[-1]
        harness.check(gate.get("points"), "the gate stores its outline")

        display = gating_page._gating_geometry_to_display(ctx, page, gate, gate)
        cx, cy = pos[0] + size[0] / 2.0, pos[1] + size[1] / 2.0
        rx, ry = size[0] / 2.0, size[1] / 2.0
        for px, py in display["points"]:
            value = ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2
            harness.check(
                abs(value - 1.0) < 1e-6,
                "outline vertices return to the drawn ellipse",
            )

        # Reloading must keep an editable ellipse, not turn the stored mask
        # outline into dozens of polygon anchors.
        gating_page._gating_reload_rois(ctx, page)
        loaded = page._gating_rois[-1][0]
        harness.check(isinstance(loaded, pg.EllipseROI))
        harness.check(not isinstance(loaded, _GatePolygonROI))
        harness.check(
            np.allclose([loaded.pos().x(), loaded.pos().y()], pos),
            "the reloaded ellipse keeps the drawn position",
        )
        harness.check(
            np.allclose([loaded.size().x(), loaded.size().y()], size),
            "the reloaded ellipse keeps the drawn size",
        )

        # Log10 is the transform of the x axis; y (FL2-A) stays linear.
        inside_x = 10.0 ** (cx + 0.9 * rx)
        outside_x = 10.0 ** (cx + 1.1 * rx)
        mask = _gate_mask(
            np.array([inside_x, outside_x]), np.array([cy, cy]), gate
        )
        harness.check(bool(mask[0]), "a point inside the drawn ellipse is selected")
        harness.check(not bool(mask[1]), "a point outside it is not")
    finally:
        _close(window)


@harness.case("mod.gating.range", "L4", "gating", tags=("fast",))
def add_range_gate() -> None:
    window, _project, page = _open("gating_range")
    try:
        ctx = window._ctx
        harness.eq(page._gating_gates, [])
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))
        harness.eq(len(page._gating_gates), 1)
        gate = page._gating_gates[0]
        harness.eq(gate["type"], "range")
        harness.eq((gate["x1"], gate["x2"]), (2.0, 8.0))
        harness.check(bool(gate.get("x")), "the gate records its x column")
        harness.check(page._gating_dirty, "creating a gate marks the module dirty")
    finally:
        _close(window)


@harness.case("mod.gating.groups", "L4", "gating", tags=("fast",))
def gate_groups() -> None:
    window, _project, page = _open("gating_groups")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "quadrant", (5.0, 5.0))
        harness.eq(len(page._gating_gates), 4)
        quadrants = {gate.get("quadrant") for gate in page._gating_gates}
        harness.eq(quadrants, {1, 2, 3, 4})
        crosshairs = {gate.get("crosshair_id") for gate in page._gating_gates}
        harness.eq(len(crosshairs), 1)
        harness.check(None not in crosshairs)

        gating_page._gating_add_gate(ctx, page, "split", (5.0, 0.0))
        harness.eq(len(page._gating_gates), 6)
        split_gates = page._gating_gates[4:]
        harness.eq({gate["side"] for gate in split_gates}, {"left", "right"})
        harness.eq(len({gate.get("split_id") for gate in split_gates}), 1)
    finally:
        _close(window)


@harness.case("mod.gating.not", "L4", "gating", tags=("fast",))
def not_gate() -> None:
    window, _project, page = _open("gating_not")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))
        source = page._gating_gates[0]
        gating_page._gating_add_not_gate(ctx, page, 0)
        harness.eq(len(page._gating_gates), 2)
        not_gate = page._gating_gates[1]
        harness.eq(not_gate["type"], "not")
        harness.eq(not_gate["source"], source["id"])
        harness.check(not_gate["name"].startswith("NOT "), not_gate["name"])
    finally:
        _close(window)


@harness.case("mod.gating.image_polygon_not", "L4", "gating", tags=("fast",))
def image_polygon_not() -> None:
    """An image NOT subset is pixel-exact: the polygon interior is hidden."""
    gating = harness.task_def("gating", task_id="g-1", name="Gating")
    project, _parquet = e2e.image_project(
        "gating_image_polygon_not", tasks=[gating]
    )
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        _select_all_files(ctx, page)
        page._gating_preview["type"] = "image"
        page._gating_preview["x"] = "x"
        page._gating_preview["y"] = "y"
        gating_page._gating_update_tools(ctx, page)
        gating_page._gating_reload_rois(ctx, page)
        _gate_editor._gating_render(ctx, page)
        harness.pump(250)

        points = [(8.0, 8.0), (24.0, 8.0), (24.0, 24.0), (8.0, 24.0)]
        roi = _GatePolygonROI(
            points, closed=True, **_gate_roi_pens("#000000")
        )
        gating_page._gating_add_gate(ctx, page, "polygon", roi)
        gating_page._gating_add_not_gate(
            ctx, page, len(page._gating_gates) - 1
        )
        not_gate = page._gating_gates[-1]
        gating_page._gating_open_gate_subset(ctx, page, not_gate["id"])
        harness.pump(400)

        image = page._gating_plot_widget._img_item.image
        harness.check(image is not None, "the subset image is rendered")
        # The raster is binned to screen resolution, so array indices are
        # not data coordinates: map the two probe points through the item's
        # rect and shape.
        rect = page._gating_plot_widget._img_item.mapRectToView(
            page._gating_plot_widget._img_item.boundingRect()
        )
        height, width = image.shape[0], image.shape[1]

        def cell(dx: float, dy: float):
            col = int((dx - rect.x()) / rect.width() * width)
            row = int((dy - rect.y()) / rect.height() * height)
            col = max(0, min(width - 1, col))
            row = max(0, min(height - 1, row))
            return row, col

        inside = cell(16.0, 16.0)
        outside = cell(2.0, 2.0)
        harness.eq(int(image[inside[0], inside[1], 3]), 0,
                   "a pixel inside the polygon is hidden")
        harness.eq(int(image[outside[0], outside[1], 3]), 255,
                   "a pixel outside the polygon is shown")
    finally:
        _close(window)


@harness.case("mod.gating.polygon_not", "L4", "gating", tags=("fast",))
def polygon_not_subset() -> None:
    """A polygon NOT gate selects exactly the complement of the polygon."""
    window, _project, page = _open("gating_polygon_not")
    try:
        ctx = window._ctx
        preview = page._gating_preview
        parquet = preview["file"]
        from smage.core.gates import _point_in_polygon
        from smage.data import columns as _data_columns

        data = _data_columns._column_data(
            ctx, page._gating_task_id, parquet,
            [preview["x"], preview["y"]],
        )
        points = [(0.0, 0.0), (25.0, 0.0), (25.0, 1.0), (0.0, 1.0)]
        roi = _GatePolygonROI(
            points, closed=True, **_gate_roi_pens("#000000")
        )
        gating_page._gating_add_gate(ctx, page, "polygon", roi)
        polygon = page._gating_gates[-1]
        gating_page._gating_add_not_gate(
            ctx, page, len(page._gating_gates) - 1
        )
        not_gate = page._gating_gates[-1]

        direct = _point_in_polygon(
            data[preview["x"]], data[preview["y"]], polygon["points"]
        )
        source_mask = gating_page._gating_compute_mask(
            ctx, page, polygon["id"], parquet
        )
        not_mask = gating_page._gating_compute_mask(
            ctx, page, not_gate["id"], parquet
        )
        harness.eq(int(np.asarray(source_mask).sum()), int(direct.sum()),
                   "the polygon mask matches the drawn outline")
        harness.check(
            np.array_equal(
                np.asarray(not_mask, dtype=bool),
                ~np.asarray(source_mask, dtype=bool),
            ),
            "NOT selects exactly the complement of the polygon",
        )

        gating_page._gating_open_gate_subset(ctx, page, not_gate["id"])
        visible = gating_page._gating_visible_ids(ctx, page)
        harness.check(
            polygon["id"] not in visible,
            "the source polygon is not drawn inside the NOT subset",
        )
        harness.check(
            not visible,
            "an empty NOT subset shows no gate outlines",
        )
    finally:
        _close(window)


@harness.case("mod.gating.polygon_scaled", "L4", "gating", tags=("fast",))
def polygon_scaled_axes() -> None:
    """A polygon drawn on an arcsinh axis selects the drawn shape."""
    project = harness.new_project("gating_polygon_scaled", rows=200)
    parquet = str(project.data_dir / "case.parquet")
    gating = harness.task_def("gating", task_id="g-1", name="Gating")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={
            "files": [parquet],
            "columns": [
                {"name": "FSC-A", "method": "arcsinh", "param": 3.0,
                 "min": None, "max": None},
                {"name": "SSC-A", "method": "arcsinh", "param": 3.0,
                 "min": None, "max": None},
            ],
        },
        children=[gating],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        _select_all_files(ctx, page)
        from smage.core.gates import _point_in_polygon
        from smage.core.scale_math import _apply_scale_transforms
        from smage.data import columns as _data_columns

        x_name, y_name = "FSC-A", "SSC-A"
        page._gating_preview["x"] = x_name
        page._gating_preview["y"] = y_name
        # Display-space (arcsinh) square, as the user would draw it.
        display_points = [(5.0, 5.0), (9.0, 5.0), (9.0, 9.0), (5.0, 9.0)]
        roi = _GatePolygonROI(
            display_points, closed=True, **_gate_roi_pens("#000000")
        )
        gating_page._gating_add_gate(ctx, page, "polygon", roi)
        polygon = page._gating_gates[-1]
        harness.check(
            bool(polygon.get("mask_points")),
            "the polygon stores a sampled mask outline",
        )
        gating_page._gating_add_not_gate(
            ctx, page, len(page._gating_gates) - 1
        )
        not_gate = page._gating_gates[-1]

        data = _data_columns._column_data(
            ctx, "g-1", parquet, [x_name, y_name]
        )
        xcfg = _plot_renderer._figure_axis_scale_configs(
            ctx, "g-1", parquet, x_name
        )
        ycfg = _plot_renderer._figure_axis_scale_configs(
            ctx, "g-1", parquet, y_name
        )
        xd = _apply_scale_transforms(
            np.asarray(data[x_name], dtype="float64"), xcfg
        )
        yd = _apply_scale_transforms(
            np.asarray(data[y_name], dtype="float64"), ycfg
        )
        drawn = _point_in_polygon(xd, yd, display_points)
        source_mask = gating_page._gating_compute_mask(
            ctx, page, polygon["id"], parquet
        )
        not_mask = gating_page._gating_compute_mask(
            ctx, page, not_gate["id"], parquet
        )
        harness.eq(
            int(np.asarray(source_mask).sum()), int(drawn.sum()),
            "the mask selects the points inside the drawn polygon",
        )
        harness.eq(
            int((np.asarray(not_mask, bool) & drawn).sum()), 0,
            "the NOT subset excludes every point inside the drawn polygon",
        )
    finally:
        _close(window)


@harness.case("mod.gating.delete_group", "L4", "gating", tags=("fast",))
def delete_group() -> None:
    window, _project, page = _open("gating_delete")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "quadrant", (5.0, 5.0))
        harness.eq(len(page._gating_gates), 4)
        with harness.patch_dialogs(
            gating_page, question=QMessageBox.StandardButton.Yes
        ):
            gating_page._gating_delete_gate(ctx, page, 0, cascade=True)
        harness.eq(page._gating_gates, [], "deleting one quadrant deletes the group")
        harness.eq(page._gating_rois, [])
    finally:
        _close(window)


@harness.case("mod.gating.save", "L4", "gating", tags=("fast",))
def save_gates() -> None:
    window, project, page = _open("gating_save")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (1.0, 9.0))
        gating_page._gating_add_not_gate(ctx, page, 0)
        gating_page._gating_save_now(ctx, page)

        harness.check(not page._gating_dirty)
        path = project.path / "Module" / "g-1" / "gates.json"
        harness.check(path.is_file(), "gates.json is written")
        payload = json.loads(path.read_text(encoding="utf-8"))
        harness.eq(len(payload["gates"]), 2)
        harness.eq(payload["gates"][0]["type"], "range")
        harness.eq(payload["gates"][1]["type"], "not")
        harness.golden("gating_gates", [
            {"type": gate["type"], "x1": gate.get("x1"),
             "x2": gate.get("x2"), "has_source": bool(gate.get("source"))}
            for gate in payload["gates"]
        ])
        harness.check("files" in payload and "per_file" in payload)

        from smage.project import Project

        reloaded = Project.load(project.path)
        task = reloaded.workflow["tasks"][0]["children"][0]
        harness.check("gates" not in (task.get("params") or {}),
                      "gates live in their store, not project.json")
    finally:
        _close(window)


@harness.case("mod.gating.close_prompt", "L4", "gating", tags=("fast",))
def close_prompt() -> None:
    window, _project, page = _open("gating_close")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (1.0, 9.0))
        harness.check(page._gating_dirty)

        with harness.patch_dialogs(
            gating_page, question=QMessageBox.StandardButton.No
        ):
            gating_page._gating_close(ctx, page)
        harness.eq(window.tabs.count(), 2, "No keeps the page open")

        with harness.patch_dialogs(
            gating_page, question=QMessageBox.StandardButton.Yes
        ):
            gating_page._gating_close(ctx, page)
        harness.eq(window.tabs.count(), 1, "Yes closes without saving")
    finally:
        _close(window)


@harness.case("mod.gating.shapes", "L4", "gating", tags=("fast",))
def shapes() -> None:
    window, _project, page = _open("gating_shapes")
    try:
        ctx = window._ctx
        pens = _gate_roi_pens("#000000")

        rect = pg.RectROI((2, 2), (8, 8), **pens)
        gating_page._gating_add_gate(ctx, page, "rect", rect)
        harness.eq(page._gating_gates[-1]["type"], "rect")
        harness.eq(page._gating_gates[-1]["pos"], [2.0, 2.0])
        harness.eq(page._gating_gates[-1]["size"], [8.0, 8.0])

        ellipse = pg.EllipseROI((10, 10), (4, 6), **pens)
        gating_page._gating_add_gate(ctx, page, "ellipse", ellipse)
        harness.eq(page._gating_gates[-1]["type"], "ellipse")
        harness.eq(page._gating_gates[-1]["size"], [4.0, 6.0])

        polygon = _GatePolygonROI([(0, 0), (5, 0), (5, 5), (0, 5)], **pens)
        gating_page._gating_add_gate(ctx, page, "polygon", polygon)
        harness.eq(page._gating_gates[-1]["type"], "polygon")
        harness.check(len(page._gating_gates[-1]["points"]) >= 3)
        harness.eq(len(page._gating_gates), 3)
    finally:
        _close(window)


@harness.case("mod.gating.live_drag", "L4", "gating", tags=("fast",))
def live_drag_follows_gate() -> None:
    """The label follows a drag; statistics refresh when the gate is released."""
    window, _project, page = _open("gating_live_drag")
    try:
        ctx = window._ctx
        rect = pg.RectROI((2, 2), (8, 8), **_gate_roi_pens("#000000"))
        gating_page._gating_add_gate(ctx, page, "rect", rect)
        harness.wait_for(
            lambda: len(page._gating_labels) == len(page._gating_gates),
            message="gate labels",
        )
        # Let any in-flight statistics settle before measuring the release.
        harness.pump(300)
        previous_stats = page._gating_last_stats
        label = page._gating_labels[-1]
        before = (label.pos().x(), label.pos().y())
        generation = page._gating_stat_generation
        # pyqtgraph emits sigRegionChanged for programmatic and mouse moves.
        rect.setPos(6.0, 6.0)
        after = (label.pos().x(), label.pos().y())
        harness.check(after != before, "the label follows the gate while dragging")
        harness.eq(page._gating_stat_generation, generation,
                   "statistics are not recomputed while the gate is moving")
        # Releasing the mouse emits sigRegionChangeFinished.
        rect.stateChangeFinished()
        harness.eq(page._gating_gates[-1]["pos"], [6.0, 6.0],
                   "the moved geometry is stored on release")
        harness.wait_for(
            lambda: page._gating_last_stats is not previous_stats,
            message="statistics refreshed after the gate is released",
        )
    finally:
        _close(window)


@harness.case("mod.gating.stats", "L4", "gating", tags=("fast",))
def stats() -> None:
    window, _project, page = _open("gating_stats")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 1e12))
        gate_id = page._gating_gates[0]["id"]
        gating_page._gating_request_stats(ctx, page)
        harness.wait_for(
            lambda: gate_id in getattr(page, "_gating_last_stats", {}),
            timeout=30,
            message="gate statistics",
        )
        count, percent = page._gating_last_stats[gate_id]
        harness.eq(count, 120, "all rows are inside the wide range")
        harness.check(abs(percent - 100.0) < 1e-6, percent)
    finally:
        _close(window)


@harness.case("mod.gating.copy_paste", "L4", "gating", tags=("fast",))
def copy_paste() -> None:
    window, _project, page = _open("gating_copy")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        first = page._gating_gates[0]
        gating_page._gating_copy_gate(ctx, page, 0)
        gating_page._gating_paste_gate(ctx, page, None)
        harness.eq(len(page._gating_gates), 2)
        copied = page._gating_gates[1]
        harness.check(copied["id"] != first["id"])
        harness.eq(copied["type"], "range")

        page._gating_gates[1]["parent"] = first["id"]
        gating_page._gating_copy_branch(ctx, page, 0)
        gating_page._gating_paste_branch(ctx, page, None)
        harness.eq(len(page._gating_gates), 4)
        new_root = page._gating_gates[2]
        new_child = page._gating_gates[3]
        harness.eq(new_child["parent"], new_root["id"],
                   "the pasted branch keeps its structure")
    finally:
        _close(window)


@harness.case("mod.gating.per_file", "L4", "gating", tags=("fast",))
def per_file() -> None:
    window, project, page = _open("gating_per_file")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._gating_files = [parquet]
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gate = page._gating_gates[0]
        from smage.core.gates import _gate_group_id

        group = _gate_group_id(gate)
        gating_page._gating_toggle_per_file(ctx, page, 0)
        harness.check(group in page._gating_per_file)

        gating_page._gating_apply_geometry(ctx, page, gate, {"x1": 1.0, "x2": 2.0})
        harness.eq(page._gating_per_file[group][parquet],
                   {"x1": 1.0, "x2": 2.0})
        harness.eq((gate["x1"], gate["x2"]), (0.0, 5.0),
                   "the shared geometry is unchanged")

        name = _data_naming._comp_file_name(ctx, parquet, page._gating_task_id)
        with harness.patch_dialogs(
            gating_page, item=name, item_ok=True
        ):
            gating_page._gating_unify_gate(ctx, page, 0)
        harness.check(group not in page._gating_per_file,
                      "unify drops the per-file overrides")
        harness.eq((gate["x1"], gate["x2"]), (1.0, 2.0))
    finally:
        _close(window)


@harness.case("mod.gating.col2gate", "L4", "gating", tags=("fast",))
def col2gate() -> None:
    project = harness.new_project("gating_col2gate", rows=60)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(400)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        page._gating_files = [str(parquet)]
        counts = gating_page._gating_col2gate_counts(ctx, page, "group")
        harness.eq([value for value, _count in counts], [1, 2, 3])
        harness.eq(sum(count for _value, count in counts), 60)

        gating_page._gating_col2gate_generate(ctx, page, "group", "Group ", counts)
        harness.eq(len(page._gating_gates), 3)
        harness.eq(
            [gate["name"] for gate in page._gating_gates],
            ["Group 1", "Group 2", "Group 3"],
        )
        harness.eq(
            [gate["value"] for gate in page._gating_gates], [1, 2, 3]
        )
        harness.eq({gate["type"] for gate in page._gating_gates},
                   {"categorical"})
    finally:
        _close(window)


@harness.case("mod.gating.and_gate", "L4", "gating", tags=("fast",))
def and_gate() -> None:
    window, _project, page = _open("gating_and")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))

        def accept() -> None:
            for widget in QApplication.topLevelWidgets():
                if not isinstance(widget, QDialog):
                    continue
                for box in widget.findChildren(QCheckBox):
                    box.setChecked(True)
                for button in widget.findChildren(QPushButton):
                    if button.text() == "OK":
                        button.click()
                        return
                widget.accept()
                return

        QTimer.singleShot(0, accept)
        gating_page._gating_add_boolean_gate(ctx, page, 0, "and")
        harness.eq(len(page._gating_gates), 3)
        boolean = page._gating_gates[-1]
        harness.eq(boolean["type"], "and")
        harness.eq(len(boolean["sources"]), 2)
        harness.check(" AND " in boolean["name"], boolean["name"])
    finally:
        _close(window)


@harness.case("mod.gating.boolean_dependents", "L4", "gating", tags=("fast",))
def boolean_dependents() -> None:
    """Moving a gate also refreshes the boolean gates that reference it."""
    window, _project, page = _open("gating_bool_deps")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))

        def accept() -> None:
            for widget in QApplication.topLevelWidgets():
                if not isinstance(widget, QDialog):
                    continue
                for box in widget.findChildren(QCheckBox):
                    box.setChecked(True)
                for button in widget.findChildren(QPushButton):
                    if button.text() == "OK":
                        button.click()
                        return
                widget.accept()
                return

        QTimer.singleShot(0, accept)
        gating_page._gating_add_boolean_gate(ctx, page, 0, "and")
        and_gate = page._gating_gates[-1]
        gating_page._gating_add_not_gate(ctx, page, len(page._gating_gates) - 1)
        not_gate = page._gating_gates[-1]

        moved = page._gating_gates[0]
        affected = gating_page._gating_affected_ids(ctx, page, {moved["id"]})
        harness.check(and_gate["id"] in affected,
                      "an AND gate that references the moved gate is affected")
        harness.check(not_gate["id"] in affected,
                      "a NOT of that AND follows transitively")
        harness.eq(
            affected, {moved["id"], and_gate["id"], not_gate["id"]},
            "the unrelated sibling gate is not recomputed",
        )
    finally:
        _close(window)


@harness.case("mod.gating.subset_view", "L4", "gating", tags=("fast",))
def subset_view() -> None:
    window, _project, page = _open("gating_subset")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gate = page._gating_gates[0]
        gating_page._gating_open_gate_subset(ctx, page, gate["id"])
        harness.eq(page._gating_parent_id, gate["id"])
        harness.eq(page._gating_active_gate_id, gate["id"])
        harness.check(page._gating_tree.selectedItems(),
                      "the gate is selected in the tree")

        gating_page._gating_open_gate_subset(ctx, page, "missing")
        harness.eq(page._gating_parent_id, None)
        harness.eq(page._gating_active_gate_id, None)
    finally:
        _close(window)


@harness.case("mod.gating.channel_ranges", "L4", "gating", tags=("fast",))
def channel_range_records() -> None:
    """Gating axes start From scale and remember each channel's range."""
    project = harness.new_project("gating_channel_ranges", rows=120)
    parquet = str(project.data_dir / "case.parquet")
    gating = harness.task_def("gating", task_id="g-1", name="Gating")
    scale = harness.task_def(
        "scale", task_id="s-1", name="Scale",
        params={
            "files": [parquet],
            "columns": [
                {"name": "FL1-A", "method": "linear", "param": None,
                 "min": 10.0, "max": 90.0},
                {"name": "FL2-A", "method": "linear", "param": None,
                 "min": 5.0, "max": 50.0},
            ],
        },
        children=[gating],
    )
    project.workflow["tasks"][0]["children"].append(scale)
    project.save()
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    try:
        page = window.tabs.currentWidget()
        _select_all_files(window._ctx, page)
        preview = page._gating_preview
        host = page._gating_settings_layout.parentWidget()

        def param_combos():
            return [
                combo for combo in host.findChildren(QComboBox)
                if combo.findText("FL1-A") >= 0
                and combo.findText("FL2-A") >= 0
            ]

        def mode_combos():
            return [
                combo for combo in host.findChildren(QComboBox)
                if combo.findData("manual") >= 0
                and combo.findData("auto") >= 0
            ]

        harness.check(len(param_combos()) >= 2)
        param_combos()[0].setCurrentText("FL1-A")
        harness.pump(150)
        param_combos()[1].setCurrentText("FL2-A")
        harness.pump(150)

        harness.eq(preview.get("x_range_mode"), "scale")
        harness.eq(preview.get("y_range_mode"), "scale")
        modes = mode_combos()
        harness.eq(len(modes), 2)
        harness.check(
            all(combo.currentData() == "scale" for combo in modes),
            "both axes start From scale",
        )

        # Give X=FL1-A a manual range, switch to FL2-A and back.
        modes[0].setCurrentIndex(modes[0].findData("manual"))
        harness.pump(150)
        harness.eq(preview.get("x_range_mode"), "manual")
        manual_min = preview.get("xmin")
        manual_max = preview.get("xmax")
        harness.check(manual_min is not None and manual_max is not None)

        param_combos()[0].setCurrentText("FL2-A")
        harness.pump(150)
        harness.eq(preview.get("x_range_mode"), "scale")

        param_combos()[0].setCurrentText("FL1-A")
        harness.pump(150)
        harness.eq(preview.get("x_range_mode"), "manual")
        harness.check(abs(preview.get("xmin") - manual_min) < 1e-6)
        harness.check(abs(preview.get("xmax") - manual_max) < 1e-6)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
