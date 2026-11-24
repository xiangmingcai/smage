"""L4: Unmix — signature table, unique names and saved configuration."""
from __future__ import annotations

import sys
from pathlib import Path

import copy

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QImage
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTabWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402
from smage.data import columns as _data_columns
from smage.modules.unmix import page as _unmix_page
from smage.widgets import canvas_export as _widgets_canvas_export
from smage.widgets.gate_editor.canvas import _GatePolygonROI
from smage.widgets.plot import export as _widgets_plot_export


def _open(case_id: str):
    task = harness.task_def("unmix", task_id="u-1", name="Unmix")
    project = harness.new_project(case_id, rows=30, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("u-1", "unmix", "Unmix")
    harness.pump(400)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.unmix.signatures", "L4", "unmix", tags=("fast",))
def signatures() -> None:
    window, project, page = _open("unmix_signatures")
    try:
        ctx = window._ctx
        page._unmix_spectra.append({"id": "sp-1", "name": "Signature 1"})
        _unmix_page._unmix_rebuild_spectrum_list(ctx, page)

        _unmix_page._unmix_add_signature(ctx, page)
        harness.eq(len(page._unmix_matrix), 1)
        row = page._unmix_matrix[0]
        harness.eq(row["positive"], "sp-1")
        harness.eq(row["name"], "Signature 1")

        _unmix_page._unmix_add_signature(ctx, page)
        harness.eq(page._unmix_matrix[1]["name"], "Signature 1 2",
                   "signature names are made unique")

        _unmix_page._unmix_remove_signature(ctx, page, 0)
        harness.eq(len(page._unmix_matrix), 1)

        _unmix_page._unmix_save_now(ctx, page)
        task = window.project.workflow["tasks"][0]["children"][0]
        harness.eq(len(task["params"]["spectra"]), 1)
        harness.eq(len(task["params"]["matrix"]), 1)
    finally:
        _close(window)


@harness.case("mod.unmix.unique_name", "L4", "unmix", tags=("fast",))
def unique_name() -> None:
    window, _project, page = _open("unmix_unique")
    try:
        ctx = window._ctx
        page._unmix_matrix.append({"id": "s1", "name": "A"})
        harness.eq(_unmix_page._unmix_unique_signature_name(ctx, page, "A"), "A 2")
        page._unmix_matrix.append({"id": "s2", "name": "A 2"})
        harness.eq(_unmix_page._unmix_unique_signature_name(ctx, page, "A"), "A 3")
        harness.eq(_unmix_page._unmix_unique_signature_name(ctx, page, "B"), "B")
    finally:
        _close(window)


@harness.case("mod.unmix.flow_config", "L4", "unmix", tags=("fast",))
def flow_config() -> None:
    window, _project, page = _open("unmix_flow")
    try:
        ctx = window._ctx
        harness.eq(_unmix_page._unmix_flow_config(ctx, page._unmix_task_def), None,
                   "an empty configuration cannot unmix")
        harness.eq(_unmix_page._unmix_output_names(ctx, "g-1", "x"), [])
    finally:
        _close(window)


@harness.case("mod.unmix.duplicate_ellipse", "L4", "unmix", tags=("fast",))
def duplicate_ellipse() -> None:
    """A duplicated spectrum reloads its ellipse gate as an ellipse."""
    from smage.core.gates import _gate_mask
    from smage.modules.gating import page as _gating_page
    from smage.modules.gating.page import _gating_ellipse_points

    project = harness.new_project("unmix_duplicate_ellipse", rows=80)
    parquet = str(project.data_dir / "case.parquet")
    points = _gating_ellipse_points((10.0, 10.0), (20.0, 12.0))
    gate = {
        "id": "g1", "name": "Ellipse", "type": "ellipse",
        "x": "FL1-A", "y": "FL2-A", "parent": None,
        "pos": [10.0, 10.0], "size": [20.0, 12.0], "points": points,
    }
    task = harness.task_def(
        "unmix", task_id="u-1", name="Unmix",
        params={
            "spectra": [{
                "id": "sp-1", "name": "Signature 1",
                "tree_id": "tree-1", "gate_id": "g1",
            }],
            "trees": [{
                "id": "tree-1", "spectrum_id": "sp-1",
                "params": {
                    "gates": [gate],
                    "preview": {
                        "file": parquet, "type": "scatter",
                        "x": "FL1-A", "y": "FL2-A",
                    },
                },
            }],
            "matrix": [],
            "samples": [parquet],
        },
    )
    project.workflow["tasks"][0].setdefault("children", []).append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("u-1", "unmix", "Unmix")
    harness.pump(450)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        original = page._unmix_spectra[0]
        _unmix_page._unmix_copy_spectrum(ctx, page, original)
        harness.eq(len(page._unmix_spectra), 2)
        copied = page._unmix_spectra[1]
        tree = _unmix_page._unmix_tree_by_id(
            ctx, page, copied.get("tree_id")
        )
        harness.check(tree is not None)
        _unmix_page._unmix_activate_tree(ctx, page, tree)
        harness.pump(300)
        holder = tree["holder"]
        _gating_page._gating_load_rois(ctx, holder)
        harness.eq(len(holder._gating_gates), 1)
        harness.check(holder._gating_gates[0].get("points"),
                      "the mask outline is kept")

        roi = holder._gating_rois[0][0]
        harness.check(isinstance(roi, pg.EllipseROI),
                      "the duplicated ellipse is not a polygon")
        harness.check(not isinstance(roi, _GatePolygonROI))
        harness.check(
            np.allclose([roi.pos().x(), roi.pos().y()], [10.0, 10.0])
        )
        harness.check(
            np.allclose([roi.size().x(), roi.size().y()], [20.0, 12.0])
        )

        gate_copy = holder._gating_gates[0]
        inside = _gate_mask(
            np.array([20.0]), np.array([16.0]), gate_copy
        )
        outside = _gate_mask(
            np.array([31.0]), np.array([16.0]), gate_copy
        )
        harness.check(bool(inside[0]) and not bool(outside[0]),
                      "the ellipse mask still selects the drawn shape")
    finally:
        _close(window)


@harness.case("mod.unmix.values", "L4", "unmix", tags=("fast",))
def unmix_values() -> None:
    project = harness.new_project("unmix_values", rows=100)
    parquet = str(project.data_dir / "case.parquet")
    gates = [
        {"id": "neg", "name": "Negative", "type": "range", "x": "FL1-A",
         "y": None, "x1": 0.0, "x2": 50.0, "parent": None},
        {"id": "pos", "name": "Positive", "type": "range", "x": "FL1-A",
         "y": None, "x1": 50.0, "x2": 1e12, "parent": None},
    ]
    params = {
        "spectra": [
            {"id": "sp-neg", "name": "Negative", "tree_id": "tree-1",
             "gate_id": "neg"},
            {"id": "sp-pos", "name": "Positive", "tree_id": "tree-1",
             "gate_id": "pos"},
        ],
        "trees": [{
            "id": "tree-1",
            "params": {
                "gates": gates,
                "preview": {"file": parquet, "type": "scatter",
                            "x": "FL1-A", "y": "FL2-A"},
            },
        }],
        "spectrum_settings": {
            "params": ["FL1-A", "FL2-A"],
            "enabled": ["FL1-A", "FL2-A"],
            "stat": "median",
        },
        "matrix": [{"positive": "sp-pos", "negative": "sp-neg",
                    "normalize": False, "name": "Signal"}],
        "samples": [parquet],
        "method": "ols",
    }
    task = harness.task_def("unmix", task_id="u-1", name="Unmix",
                            params=params,
                            children=[harness.task_def("gating", task_id="g-1")])
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        ctx._unmix_flow_cache = {}
        config = _unmix_page._unmix_flow_config(ctx, window._find_task_by_id("u-1"))
        harness.check(config is not None, "a complete configuration is valid")
        harness.eq(config["names"], ["Signal"])
        harness.eq(config["columns"], ["FL1-A", "FL2-A"])
        harness.eq(config["matrix"].shape, (1, 2))

        raw = pd.read_parquet(parquet)
        negative = raw[(raw["FL1-A"] >= 0) & (raw["FL1-A"] <= 50)]
        positive = raw[raw["FL1-A"] > 50]
        expected = np.array([[
            positive["FL1-A"].median() - negative["FL1-A"].median(),
            positive["FL2-A"].median() - negative["FL2-A"].median(),
        ]])
        harness.check(np.allclose(config["matrix"], expected),
                      f"{config['matrix']} vs {expected}")

        # Strict generate: nothing is exposed before the output exists.
        harness.eq(
            _unmix_page._unmix_output_names(ctx, "g-1", parquet), []
        )
        record = _unmix_page._unmix_write_output(
            ctx, "u-1", parquet, config
        )
        harness.eq(record["kind"], "table")
        output_frame = pd.read_parquet(project.path / record["path"])
        harness.eq(
            list(output_frame.columns), ["Signal"],
            "table outputs hold only the unmixed columns",
        )
        task = window._find_task_by_id("u-1")
        task["params"]["outputs"] = {parquet: record}
        task["output"] = {"saved": True}
        project.save()
        _data_columns._bump_stages_revision(ctx)

        harness.eq(
            _unmix_page._unmix_output_names(ctx, "g-1", parquet),
            ["Signal"],
        )
        values = _data_columns._column_data(
            ctx, "g-1", parquet, ["Signal"]
        )["Signal"]
        weights = np.linalg.pinv(config["matrix"]).T
        expected_values = (
            raw[["FL1-A", "FL2-A"]].to_numpy(dtype=float) @ weights[0]
        )
        harness.check(np.allclose(values, expected_values))
    finally:
        harness.close_window(window)


@harness.case("mod.unmix.export_heatmap", "L4", "unmix", tags=("fast",))
def export_heatmap() -> None:
    """The matrix heatmap exports as PNG/JPEG or a vector PDF."""
    window, project, page = _open("unmix_export_heatmap")
    try:
        cfg = {
            "cell_width": 60, "cell_height": 28, "colormap": "viridis",
            "show_numbers": True, "auto_text_color": True,
            "text_color": "#5b6b82",
        }
        rows = ["Signature 1", "Signature 2"]
        cols = ["CD3", "CD8"]
        values = [[1.0, 0.4], [0.3, 0.8]]
        spec = _unmix_page._unmix_matrix_paint_spec(page, rows, cols, cfg)

        def paint(painter):
            _unmix_page._unmix_paint_matrix(
                painter, rows, cols, values, cfg, 0.0, 1.0, 2, spec
            )

        out = project.data_dir / "unmix_export_heatmap"
        out.mkdir(parents=True, exist_ok=True)
        png = out / "matrix.png"
        harness.check(
            _widgets_canvas_export._painter_export_image(
                paint, spec["width"], spec["height"], png, "png"
            ),
            "the heatmap PNG is written",
        )
        image = QImage(str(png))
        harness.eq(image.width(), spec["width"] * 3)
        harness.eq(image.height(), spec["height"] * 3)

        pdf = out / "matrix.pdf"
        harness.check(
            _widgets_canvas_export._painter_export_pdf(
                paint, spec["width"], spec["height"], pdf
            ),
            "the heatmap PDF is written",
        )
        harness.check(pdf.read_bytes()[:4] == b"%PDF", "PDF header")

        harness.eq(
            page._unmix_matrix_label.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
            "the matrix heatmap has a right-click menu",
        )
        harness.eq(
            page._unmix_similarity_label.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
            "the similarity heatmap has a right-click menu",
        )
    finally:
        _close(window)


@harness.case("mod.unmix.export_plots", "L4", "unmix", tags=("fast",))
def export_plots() -> None:
    """The spectrum and curve plots export, with the signature legend."""
    window, project, page = _open("unmix_export_plots")
    try:
        for plot in (page._unmix_spectrum_plot, page._unmix_curve_plot):
            harness.eq(
                plot.contextMenuPolicy(),
                Qt.ContextMenuPolicy.CustomContextMenu,
                "the plot has a right-click menu",
            )

        # The matrix sub-tabs live on the second outer page.
        outer = next(
            tab for tab in page.findChildren(QTabWidget)
            if tab.parent() is page
        )
        outer.setCurrentIndex(1)
        harness.pump(300)
        page._unmix_matrix_tabs.setCurrentIndex(1)  # Signature curves
        harness.pump(300)
        plot = page._unmix_curve_plot
        legend = page._unmix_curve_legend
        harness.check(plot._export_top_widget is legend,
                      "the signature legend is attached to the export")
        # Without real spectra the legend row is empty; give it one entry so
        # it has a height and is part of the export layout.
        page._unmix_curve_legend_lay.addWidget(QLabel("Signature 1"), 0, 0)
        legend.adjustSize()
        harness.pump(150)
        harness.check(
            _widgets_plot_export._plot_export_top_widget(plot) is legend,
            "the legend is visible on the current tab",
        )
        _scene, _side, _w, height, _gap = (
            _widgets_plot_export._plot_export_layout(plot)
        )
        harness.check(
            height >= plot.sceneRect().height() + legend.height(),
            "the legend row adds height to the export",
        )

        out = project.data_dir / "unmix_export_plots"
        out.mkdir(parents=True, exist_ok=True)
        png = out / "curves.png"
        harness.check(
            _widgets_plot_export._plot_export_image(plot, png, "png"),
            "the curve PNG is written",
        )
        harness.check(QImage(str(png)).width() > plot.width(),
                      "the PNG is rendered above screen size")
        pdf = out / "curves.pdf"
        harness.check(
            _widgets_plot_export._plot_export_pdf(plot, pdf),
            "the curve PDF is written",
        )
        harness.check(pdf.read_bytes()[:4] == b"%PDF", "PDF header")
    finally:
        _close(window)


def _manual_open(case_id: str):
    """An Unmix page whose manual matrix is ready to be pasted into."""
    task = harness.task_def(
        "unmix", task_id="u-1", name="Unmix",
        params={
            "spectrum_settings": {
                "params": ["FL1-A", "FL2-A"],
                "enabled": ["FL1-A", "FL2-A"],
                "stat": "median",
            },
            "signature_source": "manual",
            "manual_matrix": {"columns": [], "rows": []},
            "samples": [],
            "matrix": [],
        },
    )
    project = harness.new_project(case_id, rows=30, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("u-1", "unmix", "Unmix")
    harness.pump(400)
    return window, project, window.tabs.currentWidget()


_MANUAL_TEXT = (
    "Signature\tFL2-A\tFL1-A\n"
    "Sig A\t0.4\t1.0\n"
    "Sig B\t0.8\t0.3"
)


@harness.case("mod.unmix.manual_paste", "L4", "unmix", tags=("fast",))
def manual_paste() -> None:
    """A pasted matrix keeps its column order and drives the unmixing."""
    window, _project, page = _manual_open("unmix_manual_paste")
    try:
        ctx = window._ctx
        harness.eq(
            page._unmix_signature_stack.currentIndex(), 1,
            "manual mode starts on the paste page",
        )
        QApplication.clipboard().setText(_MANUAL_TEXT)
        _unmix_page._unmix_paste_manual_matrix(ctx, page)

        manual = page._unmix_params["manual_matrix"]
        harness.eq(manual["columns"], ["FL2-A", "FL1-A"],
                   "the pasted column order is kept")
        harness.eq([r["name"] for r in manual["rows"]], ["Sig A", "Sig B"])
        harness.check(
            np.allclose([r["values"] for r in manual["rows"]],
                        [[0.4, 1.0], [0.8, 0.3]]),
            "the pasted values are stored",
        )

        table = page._unmix_manual_table
        harness.eq(table.rowCount(), 2)
        harness.eq(table.columnCount(), 3)
        harness.eq(table.item(0, 0).text(), "Sig A")
        harness.eq(table.item(1, 1).text(), "0.8")
        harness.eq(table.horizontalHeaderItem(2).text(), "FL1-A")

        space = QFontMetrics(table.font()).horizontalAdvance(" ")
        harness.eq(table.columnWidth(0), space * 20,
                   "the Signature column is 20 characters wide")
        harness.eq(table.columnWidth(1), space * 5,
                   "value columns are 5 characters wide")
        harness.eq(
            table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.ResizeMode.Interactive,
            "the columns can be resized by dragging",
        )

        # Downstream sees the saved configuration, like Unmix for image does.
        _unmix_page._unmix_save_now(ctx, page)
        ctx._unmix_flow_cache = {}
        config = _unmix_page._unmix_flow_config(
            ctx, page._unmix_task_def
        )
        harness.check(config is not None, "the pasted matrix unmixes")
        harness.eq(config["columns"], ["FL2-A", "FL1-A"])
        harness.eq(config["names"], ["Sig A", "Sig B"])
        harness.check(
            np.allclose(config["matrix"], [[0.4, 1.0], [0.8, 0.3]]),
            "the flow matrix uses the pasted values",
        )

        columns, rows = _unmix_page._unmix_matrix_table_values(ctx, page)
        harness.eq(columns, ["FL2-A", "FL1-A"])
        harness.eq(len(rows), 2)

        # Copy matrix keeps the same TSV shape and enough precision to
        # round-trip every float64 value exactly.
        _unmix_page._unmix_copy_matrix(ctx, page)
        copied = QApplication.clipboard().text().splitlines()
        harness.eq(copied[0], "Signature\tFL2-A\tFL1-A")
        cells = copied[1].split("\t")
        harness.eq(cells[0], "Sig A")
        harness.eq(float(cells[1]), 0.4)
        harness.eq(float(cells[2]), 1.0)
        roundtrip, error = _unmix_page._unmix_parse_matrix_text(
            ctx, page, QApplication.clipboard().text()
        )
        harness.eq(error, "")
        harness.check(
            np.array_equal(
                np.asarray(
                    [row["values"] for row in roundtrip["rows"]],
                    dtype="float64",
                ),
                np.asarray([[0.4, 1.0], [0.8, 0.3]], dtype="float64"),
            ),
            "copy → paste reproduces the matrix exactly",
        )
    finally:
        _close(window)


@harness.case("mod.unmix.manual_validation", "L4", "unmix", tags=("fast",))
def manual_validation() -> None:
    """Invalid pastes are rejected with a reason and change nothing."""
    window, _project, page = _manual_open("unmix_manual_validation")
    try:
        ctx = window._ctx
        QApplication.clipboard().setText(_MANUAL_TEXT)
        _unmix_page._unmix_paste_manual_matrix(ctx, page)
        before = copy.deepcopy(page._unmix_params["manual_matrix"])

        def error(text):
            _data, message = _unmix_page._unmix_parse_matrix_text(
                ctx, page, text
            )
            return message

        harness.check("nothing" in error("").lower(),
                      "an empty clipboard is rejected")
        harness.check(
            "Signature" in error("Foo\tFL1-A\tFL2-A\nA\t1\t2"),
            "the header must start with Signature",
        )
        missing = error("Signature\tFL1-A\nSig A\t1.0")
        harness.check("FL2-A" in missing, f"missing channel: {missing}")
        extra = error("Signature\tFL1-A\tFL2-A\tFL3-A\nSig A\t1\t2\t3")
        harness.check("FL3-A" in extra, f"extra channel: {extra}")
        repeated = error("Signature\tFL1-A\tFL1-A\nSig A\t1\t2")
        harness.check("repeats" in repeated, repeated)
        short = error("Signature\tFL1-A\tFL2-A\nSig A\t1.0")
        harness.check("Row 2" in short, short)
        unnamed = error("Signature\tFL1-A\tFL2-A\n\t1.0\t2.0")
        harness.check("no signature name" in unnamed, unnamed)
        duplicate = error(
            "Signature\tFL1-A\tFL2-A\nSig A\t1.0\t2.0\nSig A\t2.0\t3.0"
        )
        harness.check("appears twice" in duplicate, duplicate)
        empty = error("Signature\tFL1-A\tFL2-A\nSig A\t\t2.0")
        harness.check("is empty" in empty, empty)
        bad_number = error("Signature\tFL1-A\tFL2-A\nSig A\tabc\t2.0")
        harness.check("is not a number" in bad_number, bad_number)

        with harness.patch_dialogs(
            _unmix_page, warning=QMessageBox.StandardButton.Ok
        ):
            QApplication.clipboard().setText("Signature\tFL1-A\nSig A\t1.0")
            _unmix_page._unmix_paste_manual_matrix(ctx, page)
        harness.eq(page._unmix_params["manual_matrix"], before,
                   "a rejected paste leaves the matrix unchanged")

        # A different order is accepted as long as the channel set matches.
        data, message = _unmix_page._unmix_parse_matrix_text(
            ctx, page,
            "Signature\tFL2-A\tFL1-A\nSig A\t0.4\t1.0",
        )
        harness.eq(message, "")
        harness.eq(data["columns"], ["FL2-A", "FL1-A"])
    finally:
        _close(window)


@harness.case("mod.unmix.manual_edit", "L4", "unmix", tags=("fast",))
def manual_edit() -> None:
    """Cells are editable, invalid numbers are flagged and rows reorder."""
    window, _project, page = _manual_open("unmix_manual_edit")
    try:
        ctx = window._ctx
        QApplication.clipboard().setText(_MANUAL_TEXT)
        _unmix_page._unmix_paste_manual_matrix(ctx, page)
        table = page._unmix_manual_table
        manual = page._unmix_params["manual_matrix"]

        table.item(0, 1).setText("2.5")
        harness.eq(manual["rows"][0]["values"][0], 2.5)
        table.item(1, 0).setText("Sig B2")
        harness.eq(manual["rows"][1]["name"], "Sig B2")

        table.item(0, 2).setText("abc")
        harness.check(page._unmix_manual_bad_cells,
                      "a non-numeric cell is flagged")
        harness.check(
            "highlighted" in _unmix_page._unmix_manual_problem(ctx, page)
        )
        table.item(0, 2).setText("1.5")
        harness.check(not page._unmix_manual_bad_cells)
        harness.eq(manual["rows"][0]["values"][1], 1.5)

        table.item(1, 0).setText("Sig A")
        harness.check(
            "unique" in _unmix_page._unmix_manual_problem(ctx, page)
        )
        table.item(1, 0).setText("Sig B2")

        table.verticalHeader().moveSection(0, 1)
        harness.pump(350)
        harness.eq(
            [r["name"] for r in page._unmix_params["manual_matrix"]["rows"]],
            ["Sig B2", "Sig A"],
            "dragging the row header reorders the matrix",
        )
    finally:
        _close(window)


@harness.case("mod.unmix.image_values", "L4", "unmix", tags=("fast",))
def image_values() -> None:
    """Image unmix uses pinv and the same upstream stages as table data."""
    from smage.data import columns as data_columns
    from smage.data.image_index import _build_image_index_files

    unmix = harness.task_def("unmix", task_id="u-1", name="Unmix")
    compensation = harness.task_def(
        "compensation", task_id="c-1", name="Compensation",
        params={
            "params": ["FL1-A", "FL2-A"],
            "matrix": [[100, 10], [0, 100]],
            "files": [],
        },
        children=[unmix],
    )
    project = harness.new_project(
        "unmix_image_values", rows=120, tasks=[compensation]
    )
    parquet = str(project.data_dir / "case.parquet")
    compensation["params"]["files"] = [parquet]
    project.save()
    _build_image_index_files(parquet)

    window = harness.new_window()
    harness.open_project(window, project)
    try:
        ctx = window._ctx
        matrix = np.array([[1.0, 0.5], [0.2, 1.0]])
        config = {
            "task_id": "u-1",
            "names": ["U1", "U2"],
            "columns": ["FL1-A", "FL2-A"],
            "matrix": matrix,
            "method": "ols",
            "samples": {parquet},
        }
        record = _unmix_page._unmix_write_output(
            ctx, "u-1", parquet, config
        )
        output = project.path / record["path"]
        frame = pd.read_parquet(output)
        harness.eq(list(frame.columns), ["x", "y", "U1", "U2"])
        harness.check(
            (Path(str(output) + ".imgidx")).is_dir(),
            "the output pyramid is built",
        )

        source = ctx._column_data("u-1", parquet, ["FL1-A", "FL2-A"])
        expected = (
            np.column_stack([source["FL1-A"], source["FL2-A"]])
            @ np.linalg.pinv(matrix)
        )
        harness.check(
            np.allclose(frame[["U1", "U2"]].to_numpy(), expected),
            "image unmix matches X · pinv(matrix)",
        )

        signature = record["signature"]
        ct = window._find_task_by_id("c-1")
        ct["params"]["matrix"] = [[100, 20], [0, 100]]
        data_columns._bump_stages_revision(ctx)
        changed = _unmix_page._unmix_output_signature(
            ctx, "u-1", parquet, config
        )
        harness.check(
            changed != signature,
            "an upstream stage change invalidates the image output",
        )
    finally:
        _close(window)


@harness.case("mod.unmix.deselected_outputs", "L4", "unmix",
              tags=("fast",))
def deselected_outputs() -> None:
    """Deselecting unmixed files prompts before deleting their outputs."""
    window, project, page = _open("unmix_deselected_outputs")
    try:
        ctx = window._ctx
        folder = project.path / "Module" / "u-1"
        folder.mkdir(parents=True, exist_ok=True)
        first = str(project.data_dir / "first.parquet")
        second = str(project.data_dir / "second.parquet")
        outputs = {}
        for source, name in ((first, "first"), (second, "second")):
            out = folder / f"{name}_unmix.parquet"
            out.write_bytes(b"x")
            index = Path(str(out) + ".imgidx")
            index.mkdir()
            (index / "manifest.json").write_text("{}", encoding="utf-8")
            outputs[source] = {
                "path": f"Module/u-1/{name}_unmix.parquet",
                "columns": [name],
                "kind": "image",
                "signature": "sig",
            }
        page._unmix_params["outputs"] = outputs
        page._unmix_params["samples"] = [first]
        page._unmix_task_def["params"] = page._unmix_params
        page._unmix_task_def["output"] = {"saved": True}

        with harness.patch_dialogs(
            _unmix_page, question=QMessageBox.StandardButton.No
        ):
            result = _unmix_page._unmix_confirm_remove_outputs(
                ctx, page, [first]
            )
        harness.eq(result, None, "No cancels the run")
        harness.eq(len(page._unmix_params["outputs"]), 2)
        removed = page._unmix_params["outputs"][second]
        harness.check(
            (project.path / removed["path"]).is_file(),
            "No keeps the deselected output file",
        )

        with harness.patch_dialogs(
            _unmix_page, question=QMessageBox.StandardButton.Yes
        ):
            result = _unmix_page._unmix_confirm_remove_outputs(
                ctx, page, [first]
            )
        harness.eq(len(result), 1)
        harness.eq(list(page._unmix_params["outputs"]), [first])
        harness.check(
            (project.path / removed["path"]).is_file(),
            "the mapping is removed first; the worker deletes the file",
        )

        ok, error = _unmix_page._unmix_delete_output(
            ctx, result[0]["rec"]
        )
        harness.check(ok, error)
        harness.check(not (project.path / removed["path"]).exists())
        harness.check(
            not Path(str(project.path / removed["path"]) + ".imgidx").exists()
        )

        # Removing the last output unlocks the upstream modules.
        with harness.patch_dialogs(
            _unmix_page, question=QMessageBox.StandardButton.Yes
        ):
            result = _unmix_page._unmix_confirm_remove_outputs(
                ctx, page, []
            )
        harness.eq(len(result), 1)
        harness.eq(page._unmix_params["outputs"], {})
        harness.eq(page._unmix_task_def["output"], {"saved": False})
    finally:
        _close(window)


@harness.case("mod.unmix.manual_channels", "L4", "unmix", tags=("fast",))
def manual_channels() -> None:
    """Changing the spectrum channels keeps the paste but blocks unmixing."""
    window, project, page = _manual_open("unmix_manual_channels")
    try:
        ctx = window._ctx
        QApplication.clipboard().setText(_MANUAL_TEXT)
        _unmix_page._unmix_paste_manual_matrix(ctx, page)
        harness.eq(_unmix_page._unmix_manual_problem(ctx, page), "")

        page._unmix_spectrum_cfg["enabled"] = ["FL1-A"]
        _unmix_page._unmix_update_manual_summary(ctx, page)
        _unmix_page._unmix_save_now(ctx, page)
        problem = _unmix_page._unmix_manual_problem(ctx, page)
        harness.check("FL2-A" in problem, problem)
        harness.eq(
            [r["name"] for r in page._unmix_params["manual_matrix"]["rows"]],
            ["Sig A", "Sig B"],
            "the pasted data is kept",
        )
        ctx._unmix_flow_cache = {}
        harness.eq(
            _unmix_page._unmix_flow_config(ctx, page._unmix_task_def), None,
            "a mismatched matrix has no flow config",
        )

        page._unmix_params["samples"] = [str(project.data_dir / "case.parquet")]
        with harness.patch_dialogs(
            _unmix_page, information=QMessageBox.StandardButton.Ok
        ):
            _unmix_page._unmix_start_run(ctx, page)
        harness.check(
            not getattr(page, "_unmix_running", False),
            "unmixing is refused until the channels match again",
        )

        page._unmix_spectrum_cfg["enabled"] = ["FL1-A", "FL2-A"]
        _unmix_page._unmix_update_manual_summary(ctx, page)
        harness.eq(_unmix_page._unmix_manual_problem(ctx, page), "")
    finally:
        _close(window)


@harness.case("mod.unmix.manual_modes", "L4", "unmix", tags=("fast",))
def manual_modes() -> None:
    """Switching sources keeps both matrices and saves the active mode."""
    window, _project, page = _manual_open("unmix_manual_modes")
    try:
        ctx = window._ctx
        page._unmix_matrix.append({
            "positive": None, "negative": None,
            "normalize": True, "name": "Spectra Sig",
        })
        QApplication.clipboard().setText(_MANUAL_TEXT)
        _unmix_page._unmix_paste_manual_matrix(ctx, page)

        combo = page._unmix_signature_mode_combo
        combo.setCurrentIndex(combo.findData("spectra"))
        harness.pump(150)
        harness.eq(page._unmix_signature_stack.currentIndex(), 0)
        harness.eq(len(page._unmix_matrix), 1)

        combo.setCurrentIndex(combo.findData("manual"))
        harness.pump(150)
        harness.eq(page._unmix_signature_stack.currentIndex(), 1)
        harness.eq(len(page._unmix_params["manual_matrix"]["rows"]), 2,
                   "the pasted matrix survived the switch")

        _unmix_page._unmix_save_now(ctx, page)
        task = window._find_task_by_id("u-1")
        params = task["params"]
        harness.eq(params["signature_source"], "manual")
        harness.eq(len(params["manual_matrix"]["rows"]), 2)
        harness.eq(len(params["matrix"]), 1, "the spectra row is kept too")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
