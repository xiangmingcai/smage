"""L4: Region — internal gates, Signal Expansion and downstream spatial gates."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.core.gates import _compute_gate_filter_mask  # noqa: E402
from smage.data import columns as _data_columns  # noqa: E402
from smage.data import regions as _data_regions  # noqa: E402
from smage.data import stores as _data_stores  # noqa: E402
from smage.modules.region import page as _region_page  # noqa: E402
from smage.widgets.gate_editor.canvas import _GatePolygonROI  # noqa: E402


def _region_project(case_id: str):
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    gate2col = harness.task_def(
        "gate2col", task_id="g2c-1", name="Gate2Col",
        params={"rules": []},
    )
    task = harness.task_def(
        "region", task_id="reg-1", name="Region", params={"files": []},
        children=[figure, gate2col],
    )
    project, parquet = e2e.image_project(case_id, tasks=[task])
    region_task = project.workflow["tasks"][0]["children"][0]
    region_task["params"]["files"] = [parquet]
    project.save()
    return project, parquet, region_task, figure


@harness.case("mod.region.detect", "L4", "region", tags=("fast",))
def detect_signal() -> None:
    project, parquet, _task, _figure = _region_project("region_detect")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        harness.eq(page._region_files, [parquet])
        harness.check(
            getattr(page._region_plot_widget, "_img_item", None) is not None,
            "the image plot is rendered",
        )
        page._region_detect["signal"].update({
            "auto_threshold": False, "threshold": 50.0, "smooth": 0.0,
            "expand": 0, "fill_holes": True, "min_area": 5,
            "tolerance": 0.5,
        })
        _region_page._region_rebuild_detect_panel(ctx, page)
        _region_page._region_start_detect(ctx, page)
        harness.wait_for(
            lambda: not _region_page._region_detect_running(page),
            timeout=30, message="Region detection",
        )
        harness.pump(300)
        harness.eq(len(page._region_store["regions"]), 1)
        region = page._region_store["regions"][0]
        harness.eq(region["name"], "Region 1")
        subs = region["samples"][parquet]["sub_regions"]
        harness.eq(len(subs), 2, "two signal squares")
        harness.check(all(len(sub["points"]) >= 3 for sub in subs))

        _region_page._region_save_now(ctx, page)
        store_path = project.path / "Module" / "reg-1" / "regions.json"
        harness.check(store_path.is_file(), "regions.json saved")
        gates = _data_stores._gating_gates_for(ctx, _task, parquet)
        harness.eq(len(gates), 1)
        harness.eq(gates[0]["type"], "region")
        values = _data_columns._column_data(
            ctx, "reg-1", parquet, ["x", "y"]
        )
        mask = _compute_gate_filter_mask(
            gates, gates[0]["id"], parquet, {},
            existing=set(values), extra=values,
        )
        harness.eq(int(np.count_nonzero(mask)), 50,
                   "downstream spatial mask covers both squares")
    finally:
        e2e.close_window(window)


@harness.case("mod.region.tree", "L4", "region", tags=("fast",))
def region_tree() -> None:
    project, parquet, _task, _figure = _region_project("region_tree")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        store = page._region_store
        region = {
            "id": "r1", "name": "Manual", "visible": True,
            "style": _region_page._region_default_style(0),
            "samples": {
                parquet: {
                    "visible": True,
                    "sub_regions": [{
                        "id": "s1", "name": "A", "visible": True,
                        "points": [[2, 2], [6, 2], [6, 6], [2, 6]],
                    }],
                },
            },
        }
        store["regions"] = [region]
        _region_page._region_rebuild_region_tree(ctx, page)
        harness.eq(page._region_tree.topLevelItemCount(), 1)
        top = page._region_tree.topLevelItem(0)
        harness.eq(top.childCount(), 1)
        harness.eq(top.child(0).childCount(), 1)

        # Visibility toggles only the display flag.
        region["samples"][parquet]["visible"] = False
        _region_page._region_render_overlays(ctx, page)
        _region_page._region_mark_dirty(ctx, page)
        _region_page._region_save_now(ctx, page)
        from smage.data import regions as _regions

        saved = _regions._region_store_load(ctx, _task)
        harness.check(
            saved["regions"][0]["samples"][parquet]["visible"] is False
        )
        # A Region is a spatial gate for downstream modules.
        options = _regions._region_options(ctx, "fig-1")
        harness.eq(len(options), 1)
        harness.eq(options[0][1]["kind"], "region")
        from smage.modules.figure.page import _figure_filter_options
        figure_options = _figure_filter_options(ctx, "fig-1")
        harness.eq(len(figure_options), 1)
        harness.eq(figure_options[0][1]["gate_id"], "r1")
        from smage.modules.gate2col import page as _gate2col_page
        from smage.modules.gate2col.page import _g2c_gate_options
        g2c_page = e2e.open_page(window, "g2c-1", "gate2col", "Gate2Col")
        g2c_options = _g2c_gate_options(ctx, g2c_page)
        harness.check(
            any(item["gate_id"] == "r1" for item in g2c_options),
            "Gate2Col lists Region gates",
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region.draw", "L4", "region", tags=("fast",))
def draw_shapes() -> None:
    project, parquet, _task, _figure = _region_project("region_draw")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        page._region_store["regions"] = [{
            "id": "r1", "name": "R", "visible": True,
            "style": _region_page._region_default_style(0),
            "samples": {parquet: {"visible": True, "sub_regions": []}},
        }]
        _region_page._region_rebuild_region_tree(ctx, page)
        from smage.widgets.gate_editor.canvas import _GatePolygonROI
        page._region_subregion_target = {
            "region": "r1", "sample": parquet,
        }
        roi = _GatePolygonROI(
            [(1, 1), (5, 1), (5, 5), (1, 5)], closed=True
        )
        page._region_viewbox.addItem(roi)
        _region_page._region_on_gate_created(ctx, page, "polygon", roi)
        subs = page._region_store["regions"][0]["samples"][parquet][
            "sub_regions"
        ]
        harness.eq(len(subs), 1)
        harness.eq(subs[0]["points"], [[1.0, 1.0], [5.0, 1.0],
                                       [5.0, 5.0], [1.0, 5.0]])

        import pyqtgraph as pg
        page._region_selected_gate_id = None
        rect = pg.RectROI((0, 0), (10, 10))
        page._region_viewbox.addItem(rect)
        _region_page._region_on_gate_created(ctx, page, "rect", rect)
        harness.eq(len(page._region_store["gates"]), 1)
        gate = page._region_store["gates"][0]
        harness.eq(gate["type"], "rect")
        harness.check(gate["size"][0] == 10 and gate["size"][1] == 10)

        page._region_selected_gate_id = None
        ellipse = pg.EllipseROI((2, 3), (10, 6))
        page._region_viewbox.addItem(ellipse)
        _region_page._region_on_gate_created(ctx, page, "ellipse", ellipse)
        gate = page._region_store["gates"][-1]
        harness.eq(gate["type"], "ellipse")
        harness.eq(gate["pos"], [2.0, 3.0])
        harness.eq(gate["size"], [10.0, 6.0])
    finally:
        e2e.close_window(window)


@harness.case("mod.region.signal_params", "L3", "region", tags=("fast",))
def signal_params() -> None:
    """Signal Expansion honours min area, expansion, origins and gate clip."""
    from smage.core.region_detection import signal_expansion

    image = np.zeros((20, 20), dtype="float64")
    image[2:5, 2:5] = 100.0
    image[10:12, 10:12] = 100.0
    polygons, _threshold, binary = signal_expansion(
        image, threshold=50.0, smooth=0.0, expand=0, fill_holes=True,
        min_area=1, tolerance=0.0, x_origin=10.0, y_origin=20.0,
    )
    harness.eq(len(polygons), 2)
    xs = [point[0] for polygon in polygons for point in polygon]
    ys = [point[1] for polygon in polygons for point in polygon]
    harness.check(min(xs) >= 10.0 and max(xs) <= 22.0)
    harness.check(min(ys) >= 20.0 and max(ys) <= 32.0)

    polygons, _threshold, _binary = signal_expansion(
        image, threshold=50.0, smooth=0.0, expand=0, fill_holes=True,
        min_area=10,
    )
    harness.eq(polygons, [], "min area drops the 4-pixel component")

    polygons, _threshold, grown = signal_expansion(
        image, threshold=50.0, smooth=0.0, expand=2, fill_holes=True,
        min_area=1,
    )
    harness.eq(len(polygons), 2)
    harness.check(int(np.count_nonzero(grown)) > 13,
                  "expansion grows the selected area")

    gate = np.zeros((20, 20), dtype=bool)
    gate[:, :10] = True
    polygons, _threshold, _binary = signal_expansion(
        image, gate_mask=gate, threshold=50.0, smooth=0.0, expand=0,
        fill_holes=True, min_area=1,
    )
    harness.eq(len(polygons), 1, "only the square inside the gate remains")


@harness.case("mod.region.sam_mask_polygons", "L3", "region",
              tags=("fast",))
def sam_mask_polygons() -> None:
    """SAM masks become polygons, with origin, gate and min-area handling."""
    from smage.core.region_detection import sam_mask_polygons

    mask = np.zeros((20, 20), dtype=bool)
    mask[2:6, 2:6] = True
    mask[10:14, 10:14] = True
    polygons = sam_mask_polygons(
        mask, x_origin=100.0, y_origin=200.0, tolerance=0.0
    )
    harness.eq(len(polygons), 2)
    xs = [point[0] for polygon in polygons for point in polygon]
    ys = [point[1] for polygon in polygons for point in polygon]
    harness.check(min(xs) >= 100.0 and max(xs) <= 114.0)
    harness.check(min(ys) >= 200.0 and max(ys) <= 214.0)

    gate = np.zeros((20, 20), dtype=bool)
    gate[:, :10] = True
    harness.eq(
        len(sam_mask_polygons(mask, gate_mask=gate)), 1,
        "the second SAM mask is clipped by the gate",
    )
    harness.eq(
        sam_mask_polygons(mask, min_area=20), [],
        "min area removes both 16-pixel masks",
    )
    harness.eq(sam_mask_polygons(np.zeros((8, 8), dtype=bool)), [])


@harness.case("mod.region.sam_runner", "L4", "region", tags=("fast",))
def sam_runner_plumbing() -> None:
    """A configured SAM runner is called, packed masks are decoded."""
    from unittest.mock import patch

    project, parquet, _task, _figure = _region_project("region_sam_runner")
    harness.ensure_work_dir()
    stub = harness.RUN_DIR / "fake_sam_runner.py"
    stub.write_text(
        "import argparse, numpy as np\n"
        "p = argparse.ArgumentParser()\n"
        "sub = p.add_subparsers(dest='mode', required=True)\n"
        "run = sub.add_parser('run')\n"
        "run.add_argument('--input', required=True)\n"
        "run.add_argument('--output', required=True)\n"
        "for name in ('--model', '--device', '--points-per-side',\n"
        "             '--points-per-batch', '--pred-iou', '--stability',\n"
        "             '--crop-layers', '--min-area'):\n"
        "    run.add_argument(name)\n"
        "args = p.parse_args()\n"
        "left = np.zeros((32, 32), dtype=bool)\n"
        "left[5:10, 5:10] = True\n"
        "right = np.zeros((32, 32), dtype=bool)\n"
        "right[20:25, 20:25] = True\n"
        "packed = np.packbits(np.stack([left, right]), axis=-1)\n"
        "np.savez_compressed(args.output, count=np.array(2, dtype=np.int64),\n"
        "                    shape=np.array([32, 32], dtype=np.int64),\n"
        "                    masks=packed,\n"
        "                    scores=np.ones(2, dtype='float32'))\n",
        encoding="utf-8",
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        page._region_store["gates"] = [{
            "id": "g1", "name": "Left", "type": "rect", "parent": None,
            "pos": [0, 0], "size": [16, 32],
        }]
        page._region_selected_gate_id = "g1"
        _region_page._region_rebuild_gate_tree(ctx, page)
        _region_page._region_sync_gate_rois(ctx, page)
        page._region_detect["method"] = "sam"
        page._region_detect_files = [parquet]
        page._region_detect["files"] = [parquet]
        with patch.object(window, "_ext_ready", return_value=True), \
                patch.object(
                    window, "_ext_python", return_value=sys.executable
                ), \
                patch.object(
                    window, "_ext_runner_path", return_value=str(stub)
                ):
            _region_page._region_start_detect(ctx, page)
            harness.wait_for(
                lambda: not _region_page._region_detect_running(page),
                timeout=30, message="SAM detection",
            )
        harness.pump(300)
        harness.eq(len(page._region_store["regions"]), 1)
        region = page._region_store["regions"][0]
        subs = region["samples"][parquet]["sub_regions"]
        harness.eq(len(subs), 1, "the gate clips the second SAM mask")
        xs = [point[0] for point in subs[0]["points"]]
        harness.check(max(xs) <= 16.0 + 1e-6)
    finally:
        e2e.close_window(window)


@harness.case("mod.region.boolean_nested", "L4", "region", tags=("fast",))
def boolean_nested() -> None:
    """AND and NOT-of-OR use the same masks as downstream boolean gates."""
    project, parquet, _task, _figure = _region_project("region_boolean_nested")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        page._region_store["gates"] = [
            {"id": "g1", "name": "Left", "type": "rect", "parent": None,
             "pos": [0, 0], "size": [16, 32]},
            {"id": "g2", "name": "Top", "type": "rect", "parent": None,
             "pos": [0, 0], "size": [32, 16]},
        ]
        _region_page._region_rebuild_gate_tree(ctx, page)
        _region_page._region_add_and_or_gate(ctx, page, "g1", "or", ["g2"])
        union = page._region_store["gates"][-1]
        _region_page._region_add_not_gate(ctx, page, union["id"])
        negated = page._region_store["gates"][-1]
        mask = _data_regions._region_image_gate_mask(
            ctx, "reg-1", parquet, page._region_store["gates"],
            negated["id"],
        )
        harness.eq(int(np.count_nonzero(mask)), 225,
                   "NOT (Left OR Top) covers the outside")

        _region_page._region_add_and_or_gate(ctx, page, "g1", "and", ["g2"])
        intersection = page._region_store["gates"][-1]
        harness.eq(intersection["sources"], ["g1", "g2"])
        mask = _data_regions._region_image_gate_mask(
            ctx, "reg-1", parquet, page._region_store["gates"],
            intersection["id"],
        )
        harness.eq(int(np.count_nonzero(mask)), 289,
                   "AND keeps only the overlapping corner")

        dependents = _region_page._region_boolean_dependents(page, "g1")
        harness.eq(len(dependents), 2)
        harness.check(
            any(gate["type"] == "not" for gate in page._region_store["gates"]),
            "nested boolean gates are stored in the tree",
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region.region_edit", "L4", "region", tags=("fast",))
def region_edit() -> None:
    """Add, rename, sample-add and delete through the Region tree."""
    from unittest.mock import patch

    project, parquet, _task, _figure = _region_project("region_edit")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        _region_page._region_add_region(ctx, page)
        harness.eq(len(page._region_store["regions"]), 1)
        region = page._region_store["regions"][0]
        harness.eq(region["name"], "Region 1")

        name = _region_page._region_file_name(
            ctx, parquet, page._region_task_id
        )
        with patch.object(
            _region_page._widgets_picker, "_pick_items_dialog",
            return_value=[name],
        ):
            _region_page._region_add_sample(ctx, page, region["id"])
        harness.check(parquet in region["samples"])

        item = page._region_tree.topLevelItem(0)
        page._region_tree.blockSignals(True)
        item.setText(0, "Tumour")
        page._region_tree.blockSignals(False)
        _region_page._region_on_region_item_changed(ctx, page, item, 0)
        harness.eq(region["name"], "Tumour")

        page._region_subregion_target = {
            "region": region["id"], "sample": parquet,
        }
        roi = _GatePolygonROI(
            [(2, 2), (6, 2), (6, 6), (2, 6)], closed=True
        )
        page._region_viewbox.addItem(roi)
        _region_page._region_on_gate_created(ctx, page, "polygon", roi)
        sub = region["samples"][parquet]["sub_regions"][0]
        harness.eq(len(region["samples"][parquet]["sub_regions"]), 1)

        _region_page._region_delete(
            ctx, page, ("sub", region["id"], parquet, sub["id"])
        )
        harness.eq(region["samples"][parquet]["sub_regions"], [])
        _region_page._region_delete(
            ctx, page, ("sample", region["id"], parquet, None)
        )
        harness.check(parquet not in region["samples"])
        _region_page._region_delete(
            ctx, page, ("region", region["id"], None, None)
        )
        harness.eq(page._region_store["regions"], [])
    finally:
        e2e.close_window(window)


@harness.case("mod.region.style_visibility", "L4", "region",
              tags=("fast",))
def style_visibility() -> None:
    """Region style drives the overlay; visibility hides it on the image."""
    project, parquet, _task, _figure = _region_project("region_style")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        style = _region_page._region_default_style(0)
        style.update({
            "fill": [10, 20, 30], "fill_opacity": 0.5,
            "edge": [200, 0, 0], "edge_width": 4,
        })
        page._region_store["regions"] = [{
            "id": "r1", "name": "R", "visible": True, "style": style,
            "samples": {parquet: {"visible": True, "sub_regions": [{
                "id": "s1", "name": "A", "visible": True,
                "points": [[2, 2], [8, 2], [8, 8], [2, 8]],
            }]}},
        }]
        page._region_selected_region_id = "r1"
        page._region_selected_sample = parquet
        page._region_selected_sub_id = None
        _region_page._region_render_overlays(ctx, page)
        harness.eq(len(page._region_overlay_items), 1)
        item = page._region_overlay_items[0]
        harness.eq(item.brush().color().alpha(), 128)
        harness.eq(item.pen().widthF(), 4.0)
        harness.check(page._region_edit_roi is None)

        page._region_selected_sub_id = "s1"
        _region_page._region_render_overlays(ctx, page)
        harness.check(page._region_edit_roi is not None,
                      "selecting a sub-region shows its editable outline")
        sub = page._region_store["regions"][0]["samples"][parquet][
            "sub_regions"
        ][0]
        moved = _GatePolygonROI(
            [(3, 3), (9, 3), (9, 9), (3, 9)], closed=True
        )
        page._region_viewbox.addItem(moved)
        _region_page._region_on_subregion_dragged(
            ctx, page, page._region_store["regions"][0], sub, moved
        )
        harness.eq(sub["points"], [[3.0, 3.0], [9.0, 3.0], [9.0, 9.0],
                                   [3.0, 9.0]])

        page._region_store["regions"][0]["visible"] = False
        _region_page._region_render_overlays(ctx, page)
        harness.eq(page._region_overlay_items, [])
    finally:
        e2e.close_window(window)


@harness.case("mod.region.gate_clip", "L4", "region", tags=("fast",))
def gate_clip() -> None:
    project, parquet, _task, _figure = _region_project("region_gate_clip")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        page._region_store["gates"] = [{
            "id": "g1", "name": "Left half", "type": "rect",
            "parent": None, "pos": [0, 0], "size": [16, 32],
        }]
        page._region_selected_gate_id = "g1"
        _region_page._region_rebuild_gate_tree(ctx, page)
        _region_page._region_sync_gate_rois(ctx, page)
        page._region_detect["signal"].update({
            "auto_threshold": False, "threshold": 50.0, "smooth": 0.0,
            "expand": 0, "fill_holes": True, "min_area": 5,
            "tolerance": 0.5,
        })
        _region_page._region_rebuild_detect_panel(ctx, page)
        _region_page._region_start_detect(ctx, page)
        harness.wait_for(
            lambda: not _region_page._region_detect_running(page),
            timeout=30, message="gate-clipped detection",
        )
        harness.pump(200)
        region = page._region_store["regions"][0]
        subs = region["samples"][parquet]["sub_regions"]
        harness.eq(len(subs), 1, "only the signal inside the gate")
        xs = [point[0] for point in subs[0]["points"]]
        harness.check(max(xs) <= 16.0 + 1e-6,
                      f"polygon clipped to the gate: {xs}")
    finally:
        e2e.close_window(window)


@harness.case("mod.region.copy_paste", "L4", "region", tags=("fast",))
def copy_paste() -> None:
    project, parquet, _task, _figure = _region_project("region_copy")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        store = page._region_store
        store["regions"] = [{
            "id": "r1", "name": "Source", "visible": True,
            "style": _region_page._region_default_style(0),
            "samples": {parquet: {"visible": True, "sub_regions": [{
                "id": "s1", "name": "A", "visible": True,
                "points": [[2, 2], [6, 2], [6, 6], [2, 6]],
            }]}},
        }]
        _region_page._region_copy(ctx, page, ("region", "r1", None, None))
        _region_page._region_paste(ctx, page, (None, None, None, None))
        harness.eq(len(store["regions"]), 2)
        harness.check(store["regions"][1]["name"].startswith("Source"))
        harness.check(
            store["regions"][1]["id"] != store["regions"][0]["id"]
        )
        # Sub-region paste is allowed across files.
        sub = store["regions"][0]["samples"][parquet]["sub_regions"][0]
        _region_page._region_copy(
            ctx, page, ("sub", "r1", parquet, sub["id"])
        )
        _region_page._region_paste(
            ctx, page, ("sample", "r1", parquet, None)
        )
        harness.eq(
            len(store["regions"][0]["samples"][parquet]["sub_regions"]), 2
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region.boolean_gates", "L4", "region", tags=("fast",))
def boolean_gates() -> None:
    project, parquet, _task, _figure = _region_project("region_boolean")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        page._region_store["gates"] = [
            {"id": "g1", "name": "Left", "type": "rect", "parent": None,
             "pos": [0, 0], "size": [16, 32]},
            {"id": "g2", "name": "Top", "type": "rect", "parent": None,
             "pos": [0, 0], "size": [32, 16]},
        ]
        _region_page._region_rebuild_gate_tree(ctx, page)
        _region_page._region_add_and_or_gate(ctx, page, "g1", "or", ["g2"])
        gates = page._region_store["gates"]
        harness.eq(len(gates), 3)
        union = gates[-1]
        harness.eq(union["type"], "or")
        harness.eq(union["sources"], ["g1", "g2"])
        mask = _data_regions._region_image_gate_mask(
            ctx, "reg-1", parquet, gates, union["id"]
        )
        harness.eq(int(np.count_nonzero(mask)), 799,
                   "OR covers both rectangles once")

        _region_page._region_add_not_gate(ctx, page, "g1")
        gates = page._region_store["gates"]
        negated = gates[-1]
        harness.eq(negated["type"], "not")
        harness.eq(negated["source"], "g1")
        mask = _data_regions._region_image_gate_mask(
            ctx, "reg-1", parquet, gates, negated["id"]
        )
        harness.eq(int(np.count_nonzero(mask)), 480,
                   "NOT excludes the source rectangle")

        # A gate used by a boolean gate cannot be deleted silently.
        with harness.patch_dialogs(
            _region_page, information=QMessageBox.StandardButton.Ok,
        ):
            _region_page._region_delete_gate(ctx, page, "g1")
        harness.check(
            any(gate["id"] == "g1" for gate in page._region_store["gates"]),
            "the source gate is kept while a boolean gate uses it",
        )
        harness.eq(
            len(_region_page._region_boolean_dependents(page, "g1")), 2
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region.per_file", "L4", "region", tags=("fast",))
def per_file_geometry() -> None:
    import tifffile
    from smage.data import regions as _regions
    from smage.data.image_index import _build_image_index_files

    project, parquet, _task, _figure = _region_project("region_per_file")
    image = project.path.parent / "nuclei_second.tif"
    stack = np.zeros((1, 32, 32), dtype=np.uint8)
    for low, high in ((5, 10), (20, 25)):
        stack[0, low:high, low:high] = 200
    tifffile.imwrite(image, stack, metadata={"axes": "CYX"})
    second, _result = e2e.import_into_project(project, "nuclei_second", image)
    _build_image_index_files(second)
    region_task = project.workflow["tasks"][0]["children"][0]
    region_task["params"]["files"] = [parquet, second]
    project.save()

    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        harness.eq(sorted(page._region_files), sorted([parquet, second]))
        page._region_store["gates"] = [{
            "id": "g1", "name": "Half", "type": "rect", "parent": None,
            "pos": [0, 0], "size": [16, 32],
        }]
        page._region_store["per_file"] = {
            "g1": {str(second): {"pos": [16, 0], "size": [16, 32]}}
        }
        page._region_selected_gate_id = "g1"
        _region_page._region_rebuild_gate_tree(ctx, page)
        _region_page._region_switch_file(ctx, page, parquet)
        harness.eq(
            page._region_gate_rois["g1"].pos().x(), 0.0,
            "the shared shape is shown on a file without an override",
        )
        _region_page._region_switch_file(ctx, page, second)
        harness.eq(
            page._region_gate_rois["g1"].pos().x(), 16.0,
            "the per-file shape is shown on the overridden file",
        )
        # Moving the gate now writes only this file's override.
        _region_page._region_on_gate_roi_changed(
            ctx, page, page._region_store["gates"][0],
            page._region_gate_rois["g1"],
        )
        harness.eq(
            page._region_store["per_file"]["g1"][str(second)]["pos"],
            [16.0, 0.0],
        )

        page._region_detect_files = [parquet, second]
        page._region_detect["files"] = [parquet, second]
        page._region_detect["signal"].update({
            "auto_threshold": False, "threshold": 50.0, "smooth": 0.0,
            "expand": 0, "fill_holes": True, "min_area": 5,
            "tolerance": 0.5,
        })
        _region_page._region_rebuild_detect_panel(ctx, page)
        _region_page._region_start_detect(ctx, page)
        harness.wait_for(
            lambda: not _region_page._region_detect_running(page),
            timeout=30, message="per-file detection",
        )
        harness.pump(300)
        region = page._region_store["regions"][0]
        first_subs = region["samples"][parquet]["sub_regions"]
        second_subs = region["samples"][second]["sub_regions"]
        harness.eq(len(first_subs), 1)
        harness.eq(len(second_subs), 1)
        harness.check(
            max(point[0] for point in first_subs[0]["points"]) <= 16.0 + 1e-6,
            "the first file is detected inside its own gate",
        )
        harness.check(
            min(point[0] for point in second_subs[0]["points"]) >= 16.0 - 1e-6,
            "the second file is detected inside its own gate",
        )

        _region_page._region_save_now(ctx, page)
        saved = _regions._region_store_load(ctx, region_task)
        harness.eq(
            saved.get("per_file", {}).get("g1", {}).get(str(second), {})
            .get("pos"),
            [16.0, 0.0],
        )

        # Apply one file's shape to every file and drop the overrides.
        second_name = _region_page._region_file_name(
            ctx, second, page._region_task_id
        )
        with harness.patch_dialogs(
            _region_page, item=second_name, item_ok=True,
        ):
            _region_page._region_unify_gate(ctx, page, "g1")
        harness.eq(
            page._region_store["gates"][0]["pos"], [16.0, 0.0],
            "the shared shape adopts the chosen file",
        )
        harness.check(
            "g1" not in (page._region_store.get("per_file") or {}),
            "unify turns per-file mode off",
        )
        _region_page._region_save_now(ctx, page)
        saved = _regions._region_store_load(ctx, region_task)
        harness.check(
            "g1" not in (saved.get("per_file") or {}),
            "the cleared override is saved",
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region.gate2col", "L4", "region", tags=("fast",))
def region_gate2col() -> None:
    """Gate2Col turns a Region into a 0/1 column on the pixel table."""
    import pandas as pd
    from smage.core.region_detection import polygon_union_mask
    from smage.modules.gate2col import page as _g2c_page

    project, parquet, task, _figure = _region_project("region_gate2col")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        points = [[2, 2], [10, 2], [10, 10], [2, 10]]
        _data_regions._region_store_save(ctx, task, {
            "version": 1, "files": [parquet], "gates": [], "per_file": {},
            "regions": [{
                "id": "r1", "name": "R", "visible": True,
                "style": _region_page._region_default_style(0),
                "samples": {parquet: {"visible": True, "sub_regions": [{
                    "id": "s1", "name": "1", "visible": True,
                    "points": points,
                }]}},
            }],
        })
        page = e2e.open_page(window, "g2c-1", "gate2col", "Gate2Col")
        page._g2c_params["files"] = [parquet]
        page._g2c_params["rules"] = [{
            "gate_task": "reg-1", "gate_id": "r1", "name": "in_region",
        }]
        _g2c_page._g2c_calculate(ctx, page)
        record = (page._g2c_params.get("outputs") or {}).get(parquet)
        harness.check(record is not None, "Gate2Col produced an output")
        harness.eq(record["columns"], ["in_region"])
        frame = pd.read_parquet(project.path / record["path"])
        base = pd.read_parquet(parquet)
        expected = polygon_union_mask(
            [points],
            base["x"].to_numpy(dtype="float64"),
            base["y"].to_numpy(dtype="float64"),
        ).astype("int8")
        harness.check(
            np.array_equal(frame["in_region"].to_numpy(), expected),
            "the Region mask matches the pixel table",
        )
        harness.check(int(expected.sum()) > 0)
    finally:
        e2e.close_window(window)


@harness.case("mod.region.files_required", "L4", "region", tags=("fast",))
def region_files_required() -> None:
    """A new Region module starts with no image files selected."""
    from unittest.mock import patch

    task = harness.task_def(
        "region", task_id="reg-1", name="Region", params={},
    )
    project, parquet = e2e.image_project("region_files_required", tasks=[task])
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        harness.eq(page._region_files, [])
        harness.check(page._region_preview["file"] is None,
                      "the preview does not pick a file automatically")
        harness.eq(page._region_detect_files, [])

        with harness.patch_dialogs(
            _region_page, information=QMessageBox.StandardButton.Ok
        ):
            _region_page._region_start_detect(ctx, page)
        harness.check(not getattr(page, "_region_detect_state", None))
        harness.eq(page._region_store.get("regions") or [], [])

        name = _region_page._region_file_name(
            ctx, parquet, page._region_task_id
        )
        with patch.object(
            _region_page._widgets_picker, "_pick_items_dialog",
            return_value=[name],
        ):
            _region_page._region_select_files(ctx, page)
        harness.eq(page._region_files, [parquet])
        harness.eq(page._region_preview["file"], parquet)
        harness.eq(page._region_detect_files, [parquet])
    finally:
        e2e.close_window(window)


@harness.case("mod.region.sam_requires_extension", "L4", "region",
              tags=("fast",))
def sam_requires_extension() -> None:
    project, _parquet, _task, _figure = _region_project("region_sam")
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "reg-1", "region", "Region")
        page._region_detect["method"] = "sam"
        with harness.patch_dialogs(
            _region_page, question=QMessageBox.StandardButton.Ok,
            information=QMessageBox.StandardButton.Ok,
        ):
            _region_page._region_start_detect(ctx, page)
        harness.eq(len(page._region_store.get("regions") or []), 0)
        harness.check(
            not getattr(page, "_region_detect_state", None),
            "SAM does not start without an extension environment",
        )
    finally:
        e2e.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
