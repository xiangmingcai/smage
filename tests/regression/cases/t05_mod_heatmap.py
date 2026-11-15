"""L4: Heatmap — gate/file/variable selection, calculation and persistence."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.heatmap import page as heatmap_page  # noqa: E402
from smage.modules.heatmap import page as heatmap_page


def _open(case_id: str):
    project = harness.new_project(case_id, rows=100)
    parquet = str(project.data_dir / "case.parquet")
    gates = [{
        "id": "in", "name": "In", "type": "range", "x": "FL1-A", "y": None,
        "x1": 0.0, "x2": 50.0, "parent": None,
    }]
    folder = project.path / "Module" / "g-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gates.json").write_text(
        json.dumps({"files": [parquet], "gates": gates, "per_file": {}}),
        encoding="utf-8",
    )
    heatmap = harness.task_def(
        "heatmap", task_id="h-1", name="Heatmap",
        params={
            "files": [parquet],
            "gates": [{"task": "g-1", "id": "in", "label": "In"}],
            "variables": ["x"],
            "sampling": {"mode": "full"},
            "plot": {},
        },
    )
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[heatmap])
    project.workflow["tasks"][0]["children"].append(gating)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("h-1", "heatmap", "Heatmap")
    harness.pump(400)
    return window, project, window.tabs.currentWidget(), parquet


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.heatmap.calculate", "L4", "heatmap", tags=("fast",))
def calculate() -> None:
    window, project, page, parquet = _open("heatmap_calc")
    try:
        ctx = window._ctx
        heatmap_page._heatmap_calculate(ctx, page)
        harness.check(page._heatmap_running, "calculation starts")
        harness.wait_for(
            lambda: not page._heatmap_running, timeout=30,
            message="heatmap calculation",
        )

        store = project.path / "Module" / "h-1" / "heatmap.json"
        harness.check(store.is_file(), "the result store is written")
        payload = json.loads(store.read_text(encoding="utf-8"))
        harness.eq(payload["variables"], ["x"])
        harness.eq(len(payload["values"]), 1)
        harness.eq(len(payload["values"][0]), 1)

        frame = pd.read_parquet(parquet)
        selected = frame[(frame["FL1-A"] >= 0) & (frame["FL1-A"] <= 50)]
        expected = float(selected["x"].mean())
        harness.check(
            abs(float(payload["values"][0][0]) - expected) < 1e-9,
            f"mean {payload['values'][0][0]} vs {expected}",
        )
        harness.check("clustering" in payload and payload["clustering"])
        harness.golden("heatmap_values", payload["values"])
    finally:
        _close(window)


@harness.case("mod.heatmap.validation", "L4", "heatmap", tags=("fast",))
def validation() -> None:
    window, _project, page, _parquet = _open("heatmap_validation")
    try:
        ctx = window._ctx
        page._heatmap_params["files"] = []
        with harness.patch_dialogs(heatmap_page,
                                   warning=QMessageBox.StandardButton.Ok):
            heatmap_page._heatmap_calculate(ctx, page)
        harness.check(not page._heatmap_running)

        page._heatmap_params["files"] = ["x"]
        page._heatmap_params["gates"] = []
        with harness.patch_dialogs(heatmap_page,
                                   warning=QMessageBox.StandardButton.Ok):
            heatmap_page._heatmap_calculate(ctx, page)
        harness.check(not page._heatmap_running)
    finally:
        _close(window)


@harness.case("mod.heatmap.files_required", "L4", "heatmap", tags=("fast",))
def files_required() -> None:
    """A new Heatmap module starts with no data files selected."""
    project = harness.new_project("heatmap_files_required", rows=100)
    heatmap = harness.task_def(
        "heatmap", task_id="h-1", name="Heatmap", params={"plot": {}},
    )
    gating = harness.task_def("gating", task_id="g-1", name="Gating",
                              children=[heatmap])
    project.workflow["tasks"][0]["children"].append(gating)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("h-1", "heatmap", "Heatmap")
    harness.pump(400)
    page = window.tabs.currentWidget()
    ctx = window._ctx
    try:
        harness.eq(page._heatmap_params.get("files"), [])
        harness.eq(page._heatmap_files_count.text(), "0 file(s)")

        with harness.patch_dialogs(
            heatmap_page, warning=QMessageBox.StandardButton.Ok
        ):
            heatmap_page._heatmap_calculate(ctx, page)
        harness.check(not page._heatmap_running,
                      "Calculate refuses an empty file selection")
        harness.check(
            not (project.path / "Module" / "h-1" / "heatmap.json").is_file(),
            "nothing is saved without files",
        )
    finally:
        _close(window)


@harness.case("mod.heatmap.legend_title", "L4", "heatmap", tags=("fast",))
def legend_title() -> None:
    """The colour-legend title follows Normalize until the user edits it."""
    window, _project, page, _parquet = _open("heatmap_legend_title")
    try:
        title_edit = page._heatmap_title_edit
        combo = page._heatmap_norm_combo
        cfg = page._heatmap_params["plot"]
        harness.eq(title_edit.text(), "Raw value", "the automatic title")
        harness.eq(cfg.get("legend_title"), "Raw value")

        combo.setCurrentIndex(combo.findData("column"))
        harness.eq(cfg.get("normalize"), "column")
        harness.eq(title_edit.text(), "Column z-score",
                   "the title follows Normalize")
        combo.setCurrentIndex(combo.findData("row"))
        harness.eq(title_edit.text(), "Row z-score")

        title_edit.setText("Mean intensity")
        harness.eq(cfg.get("legend_title"), "Mean intensity")
        combo.setCurrentIndex(combo.findData("raw"))
        harness.eq(title_edit.text(), "Mean intensity",
                   "a custom title is kept")

        title_edit.setText("")
        harness.eq(cfg.get("legend_title"), "")
        combo.setCurrentIndex(combo.findData("column"))
        harness.eq(title_edit.text(), "", "a cleared title stays hidden")

        # The canvas reserves room for the title, and an empty title uses
        # the original width.
        canvas = heatmap_page._HeatmapCanvas()
        canvas.set_content(
            ["A"], ["B"], np.array([[1.0]]), None, None,
            {"normalize": "raw", "legend_title": "Raw value"},
        )
        with_title = canvas._layout()
        harness.eq(with_title["legend_title"], "Raw value")
        canvas.set_content(
            ["A"], ["B"], np.array([[1.0]]), None, None,
            {"normalize": "raw", "legend_title": ""},
        )
        harness.check(
            canvas._layout()["width"] < with_title["width"],
            "hiding the title narrows the canvas",
        )
    finally:
        _close(window)


@harness.case("mod.heatmap.gate_labels", "L4", "heatmap", tags=("fast",))
def gate_labels() -> None:
    """Unique gate names stay short; collisions add their module name."""
    project = harness.new_project("heatmap_gate_labels", rows=60)
    parquet = str(project.data_dir / "case.parquet")

    def gate(gate_id, name):
        return {
            "id": gate_id, "name": name, "type": "range", "x": "FL1-A",
            "y": None, "x1": 0.0, "x2": 50.0, "parent": None,
        }

    def write_gates(task_id, gates):
        folder = project.path / "Module" / task_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "gates.json").write_text(
            json.dumps({"files": [parquet], "gates": gates, "per_file": {}}),
            encoding="utf-8",
        )

    write_gates("g-1", [gate("live-1", "Live"), gate("cd3", "CD3+")])
    write_gates("g-2", [gate("live-2", "Live"), gate("other", "Other")])
    heatmap = harness.task_def(
        "heatmap", task_id="h-1", name="Heatmap",
        params={"files": [parquet], "plot": {}},
    )
    gating2 = harness.task_def(
        "gating", task_id="g-2", name="Gating 2", children=[heatmap]
    )
    gating1 = harness.task_def(
        "gating", task_id="g-1", name="Gating 1", children=[gating2]
    )
    project.workflow["tasks"][0].setdefault("children", []).append(gating1)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("h-1", "heatmap", "Heatmap")
    harness.pump(400)
    page = window.tabs.currentWidget()
    try:
        labels = [
            opt["label"]
            for opt in heatmap_page._heatmap_gate_options(window._ctx, page)
        ]
        harness.eq(len(labels), 4)
        harness.check(
            "Live (Gating 1)" in labels,
            "a duplicated name shows its module",
        )
        harness.check(
            "Live (Gating 2)" in labels,
            "each duplicated name shows its module",
        )
        harness.check("CD3+" in labels, "a unique name needs no module")
        harness.check("Other" in labels, "a unique name needs no module")
    finally:
        _close(window)


@harness.case("mod.heatmap.export", "L4", "heatmap", tags=("fast",))
def canvas_export() -> None:
    """The heatmap canvas saves as PNG, JPEG and a vector PDF."""
    from smage.widgets import canvas_export as exporter

    harness.app()  # a QWidget needs QApplication even when no window is open
    canvas = heatmap_page._HeatmapCanvas()
    canvas.set_content(
        ["A", "B"], ["C1", "C2"],
        np.array([[1.0, 2.0], [3.0, 4.0]]), None, None,
        {"normalize": "raw", "legend_title": "Raw value"},
    )
    canvas.adjustSize()
    tmp = Path(tempfile.mkdtemp(prefix="heatmap_export_"))
    try:
        png = tmp / "heatmap.png"
        harness.check(
            exporter._canvas_export_image(canvas, png, "png"),
            "the PNG is written",
        )
        image = QImage(str(png))
        harness.eq(image.width(), canvas.width() * 3)
        harness.eq(image.height(), canvas.height() * 3)

        jpg = tmp / "heatmap.jpg"
        harness.check(
            exporter._canvas_export_image(canvas, jpg, "jpg"),
            "the JPEG is written",
        )
        harness.check(jpg.stat().st_size > 0)

        pdf = tmp / "heatmap.pdf"
        harness.check(
            exporter._canvas_export_pdf(canvas, pdf),
            "the PDF is written",
        )
        data = pdf.read_bytes()
        harness.check(data.startswith(b"%PDF"), "the PDF header is written")
        harness.check(len(data) > 1000, "the PDF has content")
        harness.check(
            getattr(canvas, "_export_vector_text", False) is False,
            "the vector-text flag is restored after exporting",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@harness.case("mod.heatmap.store", "L4", "heatmap", tags=("fast",))
def store_roundtrip() -> None:
    window, project, page, _parquet = _open("heatmap_store")
    try:
        ctx = window._ctx
        payload = {"version": 1, "values": [[1.0]], "files": []}
        heatmap_page._heatmap_store_save(ctx, "h-1", payload)
        loaded = heatmap_page._heatmap_store_load(ctx, "h-1")
        harness.eq(loaded["values"], [[1.0]])

        path = project.path / "Module" / "h-1" / "heatmap.json"
        harness.check(path.is_file())
        harness.eq(heatmap_page._heatmap_store_load(ctx, "missing"), None)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
