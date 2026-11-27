"""L4: Illumination — submodules, masks and the per-chip correction stage."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.illumination import page as illum_page  # noqa: E402
from smage.data import columns as _data_columns
from smage.modules.illumination import page as illum_page


def _open(case_id: str):
    task = harness.task_def(
        "illumination", task_id="i-1", name="Illumination",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project(case_id, rows=100, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("i-1", "illumination", "Illumination")
    harness.pump(400)
    return window, project, window.tabs.currentWidget(), str(project.data_dir / "case.parquet")


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.illumination.sync_views", "L4", "illumination", tags=("fast",))
def sync_views() -> None:
    """The one-shot button copies the active preview's range to the other."""
    window, _project, page, _parquet = _open("illum_sync")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
        harness.pump(200)
        before = page._illum_before_plot
        after = page._illum_after_plot
        harness.check(before is not None, "the Before preview exists")
        harness.check(after is not None, "the After preview exists")

        before.getPlotItem().getViewBox().setRange(
            xRange=(10.0, 20.0), yRange=(30.0, 40.0), padding=0
        )
        page._illum_sync_source = before
        illum_page._illum_sync_views(ctx, page)
        xr, yr = after.getPlotItem().getViewBox().viewRange()
        harness.check(abs(xr[0] - 10.0) < 1e-6, f"x min: {xr}")
        harness.check(abs(xr[1] - 20.0) < 1e-6, f"x max: {xr}")
        harness.check(abs(yr[0] - 30.0) < 1e-6, f"y min: {yr}")
        harness.check(abs(yr[1] - 40.0) < 1e-6, f"y max: {yr}")
    finally:
        _close(window)


@harness.case("mod.illumination.submodules", "L4", "illumination", tags=("fast",))
def submodules() -> None:
    window, _project, page, _parquet = _open("illum_submodules")
    try:
        ctx = window._ctx
        harness.eq(page._illum_submodules, [])
        illum_page._illum_add_submodule(ctx, page)
        illum_page._illum_add_submodule(ctx, page)
        harness.eq(len(page._illum_submodules), 2)
        names = [sub["name"] for sub in page._illum_submodules]
        harness.eq(names, ["Channel1", "Channel2"])

        sub = page._illum_submodules[0]
        illum_page._illum_duplicate_submodule(ctx, page, sub)
        harness.eq(len(page._illum_submodules), 3)
        harness.eq(page._illum_submodules[-1]["name"], "Channel1 copy")
        harness.eq(page._illum_submodules[-1]["mask"], None)

        with harness.patch_dialogs(illum_page,
                                   question=QMessageBox.StandardButton.Yes):
            illum_page._illum_delete_submodule(ctx, page, page._illum_submodules[-1])
        harness.eq(len(page._illum_submodules), 2)
    finally:
        _close(window)


@harness.case("mod.illumination.correction", "L4", "illumination", tags=("fast",))
def correction() -> None:
    window, project, page, parquet = _open("illum_correction")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
        sub = page._illum_submodules[0]
        sub["files"] = [parquet]
        sub["channel"] = "FL1-A"
        sub["x"] = "x"
        sub["y"] = "y"
        sub["tile_w"] = 2
        sub["tile_h"] = 2
        sub["method"] = "median"
        sub["mask"] = [[0.5, 0.25], [0.5, 0.5]]
        sub["mask_source"] = "manual"

        stage = illum_page._illum_sub_stage(ctx, sub)
        harness.check(stage is not None)
        harness.eq(stage["T"], 0.5, "median is the threshold")
        harness.eq(stage["channel"], "FL1-A")

        illum_page._illum_mark_dirty(ctx, page)
        illum_page._illum_save_now(ctx, page)
        stages = _data_columns._map_stages(ctx, "g-1")
        harness.eq([kind for kind, _stage in stages], ["illumination"])

        raw = pd.read_parquet(parquet)
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        mask = np.array([[0.5, 0.25], [0.5, 0.5]], dtype=float)
        xi = raw["x"].to_numpy(dtype=int) % 2
        yi = raw["y"].to_numpy(dtype=int) % 2
        factors = np.where(mask[yi, xi] > 0, 0.5 / mask[yi, xi], 1.0)
        expected = raw["FL1-A"].to_numpy(dtype=float) * factors
        harness.check(np.allclose(values["FL1-A"], expected))
    finally:
        _close(window)


@harness.case("mod.illumination.names", "L4", "illumination", tags=("fast",))
def names() -> None:
    window, _project, page, _parquet = _open("illum_names")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
        harness.eq(illum_page._illum_next_name(ctx, page), "Channel2")
        harness.eq(illum_page._illum_unique_name(ctx, page, "Channel1"), "Channel1 2")
        harness.eq(illum_page._illum_unique_name(ctx, page, "Other"), "Other")
    finally:
        _close(window)


def _image_preview(case_id: str):
    """Open the module on an image project and show the Image preview."""
    from smage.data.image_index import _build_image_index_files

    task = harness.task_def("illumination", task_id="i-1", name="Illumination")
    project = harness.new_project(case_id, rows=2500, tasks=[task])
    parquet = str(project.data_dir / "case.parquet")
    # The import path builds a display pyramid; do it here since the test
    # project is written directly.
    _build_image_index_files(parquet)
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("i-1", "illumination", "Illumination")
    harness.pump(300)
    page = window.tabs.currentWidget()
    ctx = window._ctx
    illum_page._illum_add_submodule(ctx, page)
    sub = page._illum_submodules[0]
    sub.update(files=[parquet], channel="FL1-A", x="x", y="y",
               tile_w=10, tile_h=10, plot_type="image",
               method="median", intensity=1.0)
    sub["mask"] = np.tile(
        np.linspace(0.5, 2.0, 10)[None, :], (10, 1)
    ).tolist()
    sub["mask_source"] = {
        "tile_w": 10, "tile_h": 10, "bin": 5, "resolution": 10,
        "files": [parquet], "channel": "FL1-A", "x": "x", "y": "y",
    }
    illum_page._illum_rebuild_right(ctx, page)
    harness.pump(900)
    illum_page._illum_render_preview(ctx, page)
    harness.pump(200)
    return window, page, ctx, sub


@harness.case("mod.illumination.preview", "L4", "illumination", tags=("fast",))
def preview() -> None:
    """After applies the mask, intensity 0 is the identity, levels are shared."""
    window, page, ctx, sub = _image_preview("illum_preview")
    try:
        before = page._illum_before_plot._img_data
        after = page._illum_after_plot._img_data
        harness.check(before is not None, "the Before image rendered")
        harness.check(after is not None, "the After image rendered")
        harness.check(
            not np.array_equal(before, after),
            "the After image applies the correction",
        )
        harness.check(
            page._illum_shared_levels is not None,
            "the sampled histograms set one display range",
        )
        sub["intensity"] = 0.0
        illum_page._illum_render_preview(ctx, page)
        harness.pump(200)
        harness.check(
            np.array_equal(
                page._illum_after_plot._img_data,
                page._illum_before_plot._img_data,
            ),
            "intensity 0 leaves the image unchanged",
        )
    finally:
        _close(window)


@harness.case("mod.illumination.preview_update", "L4", "illumination", tags=("fast",))
def preview_update() -> None:
    """A method change through Update redraws the After image."""
    from PySide6.QtWidgets import QComboBox, QPushButton

    window, page, ctx, sub = _image_preview("illum_preview_update")
    try:
        combo = next(
            c for c in page.findChildren(QComboBox) if c.findData("down") >= 0
        )
        combo.setCurrentIndex(combo.findData("down"))
        harness.pump(100)
        update = next(
            b for b in page.findChildren(QPushButton) if b.text() == "Update"
        )
        before_pixels = page._illum_after_plot._img_data.copy()
        update.click()
        harness.pump(900)
        after_pixels = page._illum_after_plot._img_data
        harness.eq(sub.get("method"), "down")
        harness.check(
            not np.array_equal(before_pixels, after_pixels),
            "the After image reflects the new method",
        )
    finally:
        _close(window)


@harness.case("mod.illumination.raster_coords", "L4", "illumination", tags=("fast",))
def raster_coords() -> None:
    """Generated-channel rasters keep the pattern's absolute position."""
    from PySide6.QtCore import QRectF

    from smage.core.pipeline import _evaluate_stages
    from smage.widgets.image_raster import _image_cells

    rect = QRectF(0.0, 0.0, 4.0, 2.0)
    channel = np.full((2, 4), 10.0)
    stage = ("illumination", {
        "task_id": "t",
        "submodules": [{
            "channel": "U1", "x": "x", "y": "y",
            "files": {"/data.parquet"},
            "tile_w": 2, "tile_h": 2,
            "mask": np.array([[1.0, 2.0], [4.0, 4.0]]),
            "T": 2.0, "intensity": 1.0,
        }],
    })

    def corrected(x_start):
        cells = _image_cells(
            QRectF(x_start, 0.0, rect.width(), rect.height()), channel.shape
        )
        values = _evaluate_stages(
            [stage], {"U1": channel}, ["U1"], parquet_path="/data.parquet",
            origin=(0.0, 0.0), cells=cells,
        )
        return values["U1"]

    values = corrected(0.0)
    harness.check(
        not np.allclose(values, channel),
        "the illumination stage ran on the raster",
    )
    # A crop that starts at a different x applies the same pattern column to
    # the same absolute pixel: the phase comes from the image origin.
    shifted = corrected(1.0)
    harness.check(
        np.allclose(shifted[:, :2], values[:, 1:3]),
        "the pattern keeps its position when the view crop moves",
    )


@harness.case("mod.illumination.preview_cache", "L4", "illumination", tags=("fast",))
def preview_cache() -> None:
    """Update re-applies the correction to the cached base, no re-read."""
    from PySide6.QtWidgets import QComboBox, QPushButton

    import smage.widgets.image_raster as image_raster

    window, page, ctx, sub = _image_preview("illum_preview_cache")
    try:
        before_pixels = page._illum_after_plot._img_data.copy()
        harness.check(
            bool(page._illum_base_cache),
            "the preview keeps its pre-correction raster",
        )
        calls = []
        original = image_raster._image_raster

        def counter(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)

        image_raster._image_raster = counter
        try:
            combo = next(
                c for c in page.findChildren(QComboBox)
                if c.findData("down") >= 0
            )
            combo.setCurrentIndex(combo.findData("down"))
            harness.pump(100)
            update = next(
                b for b in page.findChildren(QPushButton)
                if b.text() == "Update"
            )
            calls.clear()
            update.click()
            harness.pump(700)
        finally:
            image_raster._image_raster = original
        harness.eq(len(calls), 0, "Update does not read the raster again")
        harness.check(
            not np.array_equal(before_pixels, page._illum_after_plot._img_data),
            "Update still redraws the corrected image",
        )
    finally:
        _close(window)


@harness.case("mod.illumination.pattern_floor", "L4", "illumination", tags=("fast",))
def pattern_floor() -> None:
    """A negative pattern is raised so its minimum is the floor."""
    from smage.data.columns import _illumination_stage_item

    sub = {
        "channel": "CD68", "x": "x", "y": "y", "files": [],
        "tile_w": 2, "tile_h": 2, "method": "median",
        "intensity": 1.0, "pattern_floor": 100.0,
        "mask": [[-10.0, 0.0], [10.0, 30.0]],
    }
    stage = _illumination_stage_item(sub)
    mask = np.asarray(stage["mask"], dtype=float)
    harness.check(
        abs(float(mask.min()) - 100.0) < 1e-9,
        f"the minimum is the floor: {mask.min()}",
    )
    harness.eq(stage["T"], 115.0)

    # A positive pattern is left untouched.
    positive = dict(sub, mask=[[10.0, 20.0], [30.0, 40.0]])
    stage2 = _illumination_stage_item(positive)
    harness.check(
        np.allclose(np.asarray(stage2["mask"]), np.asarray(positive["mask"])),
        "a positive pattern is unchanged",
    )
    harness.eq(stage2["T"], 25.0)

    # An invalid floor falls back to the default.
    invalid = dict(sub, pattern_floor=0.0)
    stage3 = _illumination_stage_item(invalid)
    harness.check(
        abs(float(np.asarray(stage3["mask"]).min()) - 100.0) < 1e-9,
        "an invalid floor uses the default",
    )
    # The correction factor at the darkest cell is finite and positive.
    factor = stage["T"] / float(np.asarray(stage["mask"]).min())
    harness.check(np.isfinite(factor) and factor > 0, "the factor is usable")


@harness.case("mod.illumination.paste_copy", "L4", "illumination", tags=("fast",))
def paste_copy() -> None:
    """Paste validates the tile shape; Copy round-trips the pattern."""
    from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QPushButton

    from smage.widgets.controls import _NoWheelSpinBox

    window, _project, page, _parquet = _open("illum_paste_copy")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
        sub = page._illum_submodules[0]
        sub["channel"] = "FL1-A"
        sub["x"] = "x"
        sub["y"] = "y"
        sub["tile_w"] = 2
        sub["tile_h"] = 2
        illum_page._illum_rebuild_right(ctx, page)
        harness.pump(200)

        QApplication.clipboard().setText("1\t2\n3\t4")
        illum_page._illum_paste_pattern(ctx, page, sub)
        harness.pump(100)
        harness.check(
            np.allclose(np.asarray(sub["mask"]), np.array([[1, 2], [3, 4]])
        ), "the pasted matrix is stored")
        harness.eq(illum_page._illum_mask_source(sub), "pasted")
        harness.check(
            "pasted" in page._illum_calc_status.text().lower(),
            "the status says the pattern was pasted",
        )

        # A mismatched shape is rejected and the pattern is kept.
        QApplication.clipboard().setText("1\t2\n3\t4\n5\t6")
        warnings = []

        class _Dialogs:
            StandardButton = None

            @staticmethod
            def warning(*args, **_kwargs):
                warnings.append(args)
                return None

            @staticmethod
            def information(*_args, **_kwargs):
                return None

        original = illum_page.QMessageBox
        illum_page.QMessageBox = _Dialogs
        try:
            illum_page._illum_paste_pattern(ctx, page, sub)
        finally:
            illum_page.QMessageBox = original
        harness.check(bool(warnings), "a mismatched shape is rejected")
        harness.check(
            np.allclose(np.asarray(sub["mask"]), np.array([[1, 2], [3, 4]])),
            "the previous pattern is kept",
        )

        # Copy writes a TSV matrix that parses back exactly.
        illum_page._illum_copy_pattern(ctx, page, sub)
        text = QApplication.clipboard().text()
        parsed = np.array([
            [float(value) for value in line.split("\t")]
            for line in text.split("\n")
        ])
        harness.check(
            np.array_equal(parsed, np.array([[1.0, 2.0], [3.0, 4.0]])),
            "Copy and Paste round-trip",
        )

        # bin/resolution changes keep a pasted pattern; tile changes clear it.
        bin_spin = next(
            spin for spin in page.findChildren(_NoWheelSpinBox)
            if spin.value() == int(sub.get("bin", 5))
        )
        bin_spin.setValue(int(bin_spin.value()) + 1)
        harness.pump(80)
        harness.check(sub.get("mask") is not None,
                      "a pasted pattern survives a bin change")
        tile_spin = next(
            spin for spin in page.findChildren(_NoWheelSpinBox)
            if spin.value() == 2
        )
        tile_spin.setValue(3)
        harness.pump(80)
        harness.check(sub.get("mask") is None,
                      "a pasted pattern is cleared by a tile change")

        # The floor is applied through Update.
        sub["tile_w"] = 2
        sub["tile_h"] = 2
        QApplication.clipboard().setText("-10\t0\n10\t30")
        illum_page._illum_paste_pattern(ctx, page, sub)
        harness.pump(150)
        floor_spin = next(
            spin for spin in page.findChildren(QDoubleSpinBox)
            if abs(spin.value() - 100.0) < 1e-9
        )
        floor_spin.setValue(50.0)
        update = next(
            b for b in page.findChildren(QPushButton) if b.text() == "Update"
        )
        update.click()
        harness.pump(150)
        harness.eq(sub.get("pattern_floor"), 50.0)
        stage = _data_columns._illumination_stage_item(sub)
        harness.check(
            abs(float(np.asarray(stage["mask"]).min()) - 50.0) < 1e-9,
            "Update applies the new floor",
        )
    finally:
        _close(window)


@harness.case("mod.illumination.histogram_range", "L4", "illumination", tags=("fast",))
def histogram_range() -> None:
    """The level histograms follow the Min/Max display window."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from smage.widgets import image_level

    values = np.linspace(0.0, 100.0, 2000)
    host = QWidget()
    layout = QVBoxLayout(host)
    image_level._image_signal_histogram(
        None, layout, "C", None, "x", configs=[], values=values,
        hist_range=(20.0, 40.0),
    )
    image_level._image_level_controls(
        None, layout, "C", 20.0, 40.0, True, lambda lo, hi: None,
        None, "x", configs=[], values=values,
    )
    plots = host.findChildren(pg.PlotWidget)
    harness.eq(len(plots), 2)
    host.show()
    harness.pump(150)
    for plot in plots:
        plot.getPlotItem().getViewBox().autoRange()
        low, high = plot.getPlotItem().getViewBox().viewRange()[0]
        harness.check(
            low > 15.0 and high < 45.0 and (high - low) < 30.0,
            f"the histogram is limited to the Min/Max window: {low} – {high}",
        )


@harness.case("mod.illumination.intensity_negative", "L4", "illumination", tags=("fast",))
def intensity_negative() -> None:
    """A negative intensity inverts the correction."""
    from smage.core.pipeline import _evaluate_stages
    from smage.data.columns import (
        _illum_intensity_value, _illumination_stage_item,
    )

    harness.eq(_illum_intensity_value({"intensity": -2.0}), -2.0)
    sub = {
        "channel": "C", "x": "x", "y": "y", "files": {"p"},
        "tile_w": 2, "tile_h": 2, "method": "median",
        "intensity": -1.0, "pattern_floor": 100.0,
        "mask": [[1.0, 2.0], [1.0, 1.0]],
    }
    stage = _illumination_stage_item(sub)
    values = {
        "C": np.full((2, 2), 10.0),
        "x": np.tile(np.arange(2.0), (2, 1)),
        "y": np.tile(np.arange(2.0)[:, None], (1, 2)),
    }
    out = _evaluate_stages(
        [("illumination", {"task_id": "t", "submodules": [stage]})],
        values, ["C"], parquet_path="p",
    )
    mask = np.array([[1.0, 2.0], [1.0, 1.0]])
    expected = 10.0 * (2.0 - 1.0 / mask)
    harness.check(np.allclose(out["C"], expected), "the factor is inverted")


@harness.case("mod.illumination.shift", "L4", "illumination", tags=("fast",))
def shift_stage() -> None:
    """The shift is added before the correction and removed downstream."""
    from smage.core.pipeline import _evaluate_stages
    from smage.data.columns import _illumination_stage_item

    base = {
        "channel": "C", "x": "x", "y": "y", "files": {"p"},
        "tile_w": 2, "tile_h": 2, "method": "median",
        "intensity": 1.0, "pattern_floor": 100.0, "shift": 100.0,
        "mask": [[1.0, 2.0], [1.0, 1.0]],
    }
    coords = {
        "x": np.tile(np.arange(2.0), (2, 1)),
        "y": np.tile(np.arange(2.0)[:, None], (1, 2)),
    }
    mask = np.array([[1.0, 2.0], [1.0, 1.0]])
    factors = 1.0 / mask

    data_stage = _illumination_stage_item(base)
    data = _evaluate_stages(
        [("illumination", {"task_id": "t", "submodules": [data_stage]})],
        {"C": np.full((2, 2), 10.0), **coords}, ["C"], parquet_path="p",
    )
    harness.check(
        np.allclose(data["C"], (10.0 + 100.0) * factors - 100.0),
        "downstream data is shifted back",
    )

    display_stage = _illumination_stage_item(base, mode="display")
    display = _evaluate_stages(
        [("illumination", {"task_id": "t", "submodules": [display_stage]})],
        {"C": np.full((2, 2), 110.0), **coords}, ["C"], parquet_path="p",
    )
    harness.check(
        np.allclose(display["C"], 110.0 * factors),
        "the preview keeps the shifted values",
    )

    offset = _evaluate_stages(
        [("offset", {"columns": {"C": 100.0}, "files": {"p"}})],
        {"C": np.full((2, 2), 10.0)}, ["C"], parquet_path="p",
    )
    harness.check(np.allclose(offset["C"], 110.0), "the offset stage adds")


@harness.case("mod.illumination.shift_ui", "L4", "illumination", tags=("fast",))
def shift_ui() -> None:
    """Changing the shift clears the pattern that was built in the old domain."""
    from PySide6.QtWidgets import QDoubleSpinBox

    window, _project, page, _parquet = _open("illum_shift_ui")
    try:
        ctx = window._ctx
        illum_page._illum_add_submodule(ctx, page)
        sub = page._illum_submodules[0]
        sub["channel"] = "FL1-A"
        sub["x"] = "x"
        sub["y"] = "y"
        sub["tile_w"] = 2
        sub["tile_h"] = 2
        sub["mask"] = [[1.0, 2.0], [3.0, 4.0]]
        sub["mask_source"] = {"source": "pasted", "tile_w": 2, "tile_h": 2}
        illum_page._illum_rebuild_right(ctx, page)
        harness.pump(200)
        spins = [
            spin for spin in page.findChildren(QDoubleSpinBox)
            if spin.minimum() <= -1e8
        ]
        harness.check(bool(spins), "the shift field exists")
        shift_spin = spins[0]
        harness.eq(shift_spin.value(), 0.0)
        shift_spin.setValue(50.0)
        harness.pump(150)
        harness.eq(sub.get("shift"), 50.0)
        harness.check(
            sub.get("mask") is None,
            "changing the shift clears the pattern",
        )
        harness.check(
            "shift" in page._illum_calc_status.text().lower(),
            "the status mentions the shift",
        )
    finally:
        _close(window)


@harness.case("mod.illumination.shift_downstream", "L4", "illumination", tags=("fast",))
def shift_downstream() -> None:
    """A downstream module receives the correction with the shift removed."""
    task = harness.task_def(
        "illumination", task_id="i-1", name="Illumination",
        children=[harness.task_def("gating", task_id="g-1", name="Gating")],
    )
    project = harness.new_project("illum_shift_down", rows=100, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("i-1", "illumination", "Illumination")
    harness.pump(300)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        illum_page._illum_add_submodule(ctx, page)
        sub = page._illum_submodules[0]
        sub.update({
            "files": [parquet], "channel": "FL1-A", "x": "x", "y": "y",
            "tile_w": 2, "tile_h": 2, "method": "median",
            "intensity": 1.0, "shift": 100.0,
            "mask": [[0.5, 0.25], [0.5, 0.5]],
            "mask_source": {"source": "pasted", "tile_w": 2, "tile_h": 2},
        })
        illum_page._illum_mark_dirty(ctx, page)
        illum_page._illum_save_now(ctx, page)
        raw = pd.read_parquet(parquet)
        values = _data_columns._column_data(ctx, "g-1", parquet, ["FL1-A"])
        mask = np.array([[0.5, 0.25], [0.5, 0.5]])
        xi = raw["x"].to_numpy(dtype=int) % 2
        yi = raw["y"].to_numpy(dtype=int) % 2
        factors = 0.5 / mask[yi, xi]
        expected = (raw["FL1-A"].to_numpy(dtype=float) + 100.0) * factors - 100.0
        harness.check(
            np.allclose(values["FL1-A"], expected),
            "downstream data has the shift removed",
        )
    finally:
        _close(window)


@harness.case("mod.illumination.pattern_sampling", "L4", "illumination", tags=("fast",))
def pattern_sampling() -> None:
    """The tile estimate samples every block in x and y, not one row band."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    import smage.data.workers as workers
    from smage.data import sampling

    window, project, _page, _parquet = _open("illum_sampling")
    try:
        path = str(project.data_dir / "sampling.parquet")
        rows = np.arange(400_000, dtype=np.int64)
        x = (rows % 200).astype("int32")
        y = (rows // 200).astype("int32")
        base = np.random.default_rng(3).normal(1000, 30, rows.size)
        # The pattern changes inside one 32 px tile.
        pattern = 1.0 + 0.4 * np.cos(2 * np.pi * y / 32) + 0.2 * np.sin(
            2 * np.pi * x / 32
        )
        pq.write_table(
            pa.table({
                "x": x, "y": y,
                "c0": (base * pattern).astype("float32"),
            }),
            path, row_group_size=50_000,
        )
        names = {"x", "y", "c0"}
        original_budget = workers._ILLUM_SAMPLE_ROWS
        try:
            workers._ILLUM_SAMPLE_ROWS = 10_000_000
            worker = workers._IlluminationWorker()
            full = worker._file_stats(
                path, [], {path: names}, "c0", "x", "y", 32, 32, 4, {}
            )
            worker.stop()
            workers._ILLUM_SAMPLE_ROWS = 40_000
            worker = workers._IlluminationWorker()
            sampled = worker._file_stats(
                path, [], {path: names}, "c0", "x", "y", 32, 32, 4, {}
            )
            worker.stop()
        finally:
            workers._ILLUM_SAMPLE_ROWS = original_budget
        harness.check(
            bool((sampled["counts"] > 0).all()),
            "every tile block keeps samples",
        )
        grid_full = full["sums"] / np.maximum(full["counts"], 1)
        grid_sampled = sampled["sums"] / np.maximum(sampled["counts"], 1)
        error = np.abs(grid_sampled - grid_full) / np.abs(grid_full)
        harness.check(
            float(error.max()) < 0.05,
            f"the sampled grid stays close: {error.max():.4f}",
        )

        # The worker must sample the image blocks, not a row band.
        calls = []
        original_blocks = sampling.sample_image_blocks

        def recorder(*args, **kwargs):
            calls.append(True)
            return original_blocks(*args, **kwargs)

        sampling.sample_image_blocks = recorder
        workers._ILLUM_SAMPLE_ROWS = 40_000
        try:
            worker = workers._IlluminationWorker()
            worker._file_stats(
                path, [], {path: names}, "c0", "x", "y", 32, 32, 4, {}
            )
            worker.stop()
        finally:
            sampling.sample_image_blocks = original_blocks
            workers._ILLUM_SAMPLE_ROWS = original_budget
        harness.check(bool(calls), "the tile estimate samples the image blocks")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
