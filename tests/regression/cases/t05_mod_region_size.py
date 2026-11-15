"""L4: Region Size — NA/0/union semantics, table and upstream locking."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
import _e2e_helpers as e2e  # noqa: E402

from smage.data import regions as _regions  # noqa: E402
from smage.modules.region import page as _region_page  # noqa: E402
from smage.modules.region_size import page as _rs_page  # noqa: E402


def _two_file_project(case_id: str):
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    region = harness.task_def(
        "region", task_id="reg-1", name="Region", params={"files": []},
        children=[figure],
    )
    rsize = harness.task_def(
        "region_size", task_id="rs-1", name="Region Size",
        params={},
    )
    project, first = e2e.image_project(case_id, tasks=[region])
    region_task = project.workflow["tasks"][0]["children"][0]
    region_task.setdefault("children", []).append(rsize)
    source = project.path.parent / "nuclei.tif"
    second, _result = e2e.import_into_project(
        project, "second", source,
        params=[{"name": "Channel 1", "extract": True, "rename": "Channel 1"}],
    )
    region_task["params"]["files"] = [first, second]
    rsize["params"]["files"] = [first, second]
    project.save()
    return project, region_task, rsize, first, second


def _write_channels(ctx, task, store):
    """Write the converted masks that downstream Region Size now reads.

    The project is reloaded when the test window opens, so the task object
    captured before that is stale; always mutate the live task.
    """
    from smage.core.region_detection import _region_parts_raster

    from smage.data.image_index import _build_image_index_files

    live = ctx._find_task_by_id(task.get("id")) or task
    params = live.setdefault("params", {})
    for region in store.get("regions") or []:
        entry = _regions._region_channel_entry(
            params, region.get("id"), create=True
        )
        entry["downstream_visible"] = True
        for path, sample in (region.get("samples") or {}).items():
            man = ctx._image_load_manifest(path)
            if man is None:
                _build_image_index_files(path)
                man = ctx._image_load_manifest(path)
            if man is None:
                continue
            xmin = int(round(float(man["xmin"])))
            ymin = int(round(float(man["ymin"])))
            width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
            height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
            parts = [
                {"in": sub.get("in") or [], "out": sub.get("out") or []}
                for sub in sample.get("sub_regions") or []
            ]
            mask = _region_parts_raster(parts, width, height, xmin, ymin)
            rel, pixels, signature = _regions._region_write_channel(
                ctx, task.get("id"), path, mask, region.get("id")
            )
            entry["files"][str(path)] = {
                "path": rel,
                "signature": signature,
                "region_signature": _regions._region_geometry_signature(
                    region, path
                ),
                "pixels": int(pixels),
            }
    ctx.project.save()
    return live


def _write_store(ctx, task, first, second):
    store = {
        "version": 1,
        "files": [first, second],
        "gates": [],
        "regions": [
            {
                "id": "r1", "name": "A", "visible": True,
                "style": _region_page._region_default_style(0),
                "samples": {
                    first: {
                        "visible": True,
                        "sub_regions": [
                            {"id": "a1", "name": "1", "visible": True,
                             "kind": "region",
                             "in": [[[0, 0], [6, 0], [6, 6], [0, 6]]],
                             "out": []},
                            {"id": "a2", "name": "2", "visible": True,
                             "kind": "region",
                             "in": [[[3, 3], [9, 3], [9, 9], [3, 9]]],
                             "out": []},
                        ],
                    },
                    second: {"visible": True, "sub_regions": []},
                },
            },
            {
                "id": "r2", "name": "B", "visible": True,
                "style": _region_page._region_default_style(1),
                "samples": {
                    first: {"visible": True, "sub_regions": [
                        {"id": "b1", "name": "1", "visible": True,
                         "kind": "region",
                         "in": [[[20, 20], [25, 20], [25, 25], [20, 25]]],
                         "out": []},
                    ]},
                },
            },
        ],
    }
    _regions._region_store_save(ctx, task, store)
    return _write_channels(ctx, task, store)


@harness.case("mod.region_size.visibility", "L4", "region_size",
              tags=("fast",))
def visibility_only() -> None:
    """Region Size lists only converted channels marked downstream-visible."""
    project, region_task, _rs_task, first, second = _two_file_project(
        "region_size_visibility"
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        region_task = _write_store(ctx, region_task, first, second)
        channels = region_task["params"]["channels"]
        channels["r2"]["downstream_visible"] = False
        project.save()
        e2e.open_page(window, "rs-1", "region_size", "Region Size")
        options = _rs_page._rs_region_options(ctx, "rs-1")
        harness.eq([item["label"] for item in options], ["A"],
                   "a Region with Downstream visible off is not listed")
        channels["r2"]["downstream_visible"] = True
        project.save()
        options = _rs_page._rs_region_options(ctx, "rs-1")
        harness.eq(
            sorted(item["label"] for item in options), ["A", "B"]
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region_size.calculate", "L4", "region_size",
              tags=("fast",))
def calculate_table() -> None:
    project, region_task, _rs_task, first, second = _two_file_project(
        "region_size_calc"
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        region_task = _write_store(ctx, region_task, first, second)
        page = e2e.open_page(window, "rs-1", "region_size", "Region Size")
        page._rs_options = _rs_page._rs_region_options(ctx, "rs-1")
        page._rs_selected = [item["key"] for item in page._rs_options]
        _rs_page._rs_rebuild_panels(ctx, page)
        _rs_page._rs_start_calculate(ctx, page)
        harness.wait_for(
            lambda: page._rs_state is None, timeout=30,
            message="Region Size calculation",
        )
        harness.pump(300)
        outputs = page._rs_pending
        harness.check(outputs is not None, "the result is pending save")
        harness.eq(page._rs_params.get("outputs"), {},
                   "Calculate no longer writes the result")
        harness.eq(len(outputs["files"]), 2)
        harness.eq(len(outputs["regions"]), 2)
        values = outputs["values"]
        labels = {
            region["key"]: region["label"]
            for region in outputs["regions"]
        }
        by_label = {label: key for key, label in labels.items()}
        a_key, b_key = by_label["A"], by_label["B"]
        channels = region_task["params"]["channels"]
        a_first = int(channels["r1"]["files"][first]["pixels"])
        b_first = int(channels["r2"]["files"][first]["pixels"])
        harness.check(a_first > 0 and b_first > 0)
        harness.eq(values[first][a_key], a_first,
                   "area comes from the converted mask")
        harness.eq(values[first][b_key], b_first)
        harness.eq(values[second][a_key], 0, "empty Sample is 0")
        harness.check(
            values[second][b_key] is None, "missing Sample is NA"
        )
        live_region = window._find_task_by_id("reg-1")
        harness.check(
            not window._task_locked(live_region),
            "Calculate does not lock upstream modules before Save",
        )
        # The table mirrors the same values.
        table = page._rs_table
        order = list(page._rs_file_order)
        first_row = order.index(first)
        second_row = order.index(second)
        harness.eq(table.item(first_row, 2).text(), str(a_first))
        harness.eq(table.item(second_row, 2).text(), "0")
        harness.eq(table.item(second_row, 3).text(), "NA")
        _rs_page._rs_copy(ctx, page)
        tsv = QApplication.clipboard().text()
        harness.check(tsv.startswith("File name\t"))
        harness.check("NA" in tsv and str(a_first) in tsv)

        # Save stores the pending result and locks upstream modules.
        harness.check(page._rs_save_btn.isEnabled(), "Save is enabled")
        _rs_page._rs_save(ctx, page)
        harness.eq(page._rs_pending, None)
        harness.eq(page._rs_params["outputs"], outputs)
        harness.check(window._task_locked(
            window._find_task_by_id("reg-1")
        ), "Save locks upstream modules")
        harness.check(not page._rs_save_btn.isEnabled(),
                      "Save is disabled after saving")
    finally:
        e2e.close_window(window)


@harness.case("mod.region_size.reorder", "L4", "region_size",
              tags=("fast",))
def reorder_table() -> None:
    project, region_task, _rs_task, first, second = _two_file_project(
        "region_size_order"
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        region_task = _write_store(ctx, region_task, first, second)
        page = e2e.open_page(window, "rs-1", "region_size", "Region Size")
        page._rs_options = _rs_page._rs_region_options(ctx, "rs-1")
        page._rs_selected = [item["key"] for item in page._rs_options]
        _rs_page._rs_rebuild_panels(ctx, page)
        _rs_page._rs_start_calculate(ctx, page)
        harness.wait_for(
            lambda: page._rs_state is None, timeout=30,
            message="Region Size calculation",
        )
        harness.pump(300)
        outputs = page._rs_pending
        harness.check(outputs is not None)
        original = list(page._rs_file_order)
        _rs_page._rs_reorder_rows(ctx, page, 0, len(original))
        harness.eq(
            page._rs_file_order, [original[1], original[0]]
        )
        harness.eq(
            page._rs_pending["files"], [original[1], original[0]],
            "the row order is stored in the pending result",
        )
        _rs_page._rs_save(ctx, page)
        harness.eq(
            page._rs_params["outputs"]["files"],
            [original[1], original[0]],
            "Save stores the reordered result",
        )
    finally:
        e2e.close_window(window)


def _two_region_project(case_id: str):
    """Region 1 → Region 2 → Region Size, both with a Region named A."""
    figure = harness.task_def("figure", task_id="fig-1", name="Figure")
    rsize = harness.task_def(
        "region_size", task_id="rs-1", name="Region Size", params={},
    )
    second_region = harness.task_def(
        "region", task_id="reg-2", name="Region 2", params={"files": []},
        children=[figure, rsize],
    )
    first_region = harness.task_def(
        "region", task_id="reg-1", name="Region 1", params={"files": []},
        children=[second_region],
    )
    project, first = e2e.image_project(case_id, tasks=[first_region])
    region1 = project.workflow["tasks"][0]["children"][0]
    region2 = region1["children"][0]
    rsize = region2["children"][1]
    source = project.path.parent / "nuclei.tif"
    second, _result = e2e.import_into_project(
        project, "second", source,
        params=[{"name": "Channel 1", "extract": True, "rename": "Channel 1"}],
    )
    for task in (region1, region2, rsize):
        task["params"]["files"] = [first, second]
    project.save()
    return project, region1, region2, rsize, first, second


def _write_named_store(ctx, task, first, second, region_id, name, points):
    store = {
        "version": 1,
        "files": [first, second],
        "gates": [],
        "per_file": {},
        "regions": [{
            "id": region_id, "name": name, "visible": True,
            "style": _region_page._region_default_style(0),
            "samples": {
                first: {"visible": True, "sub_regions": [{
                    "id": f"{region_id}-1", "name": "1",
                    "visible": True, "kind": "region",
                    "in": [points], "out": [],
                }]},
            },
        }],
    }
    live = ctx._find_task_by_id(task.get("id")) or task
    _regions._region_store_save(ctx, live, store)
    return _write_channels(ctx, live, store)


@harness.case("mod.region_size.multi_module", "L4", "region_size",
              tags=("fast",))
def multi_module() -> None:
    """Regions from two upstream modules are merged, labelled and reordered."""
    import json

    project, region1, region2, _rsize, first, second = _two_region_project(
        "region_size_multi"
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        region1 = _write_named_store(
            ctx, region1, first, second, "a1", "A",
            [[0, 0], [6, 0], [6, 6], [0, 6]],
        )
        region2 = _write_named_store(
            ctx, region2, first, second, "a2", "A",
            [[10, 10], [16, 10], [16, 16], [10, 16]],
        )
        page = e2e.open_page(window, "rs-1", "region_size", "Region Size")
        options = _rs_page._rs_region_options(ctx, "rs-1")
        labels = sorted(item["label"] for item in options)
        harness.eq(labels, ["Region 1/A", "Region 2/A"])
        page._rs_options = options
        page._rs_selected = [item["key"] for item in options]
        _rs_page._rs_rebuild_panels(ctx, page)
        _rs_page._rs_start_calculate(ctx, page)
        harness.wait_for(
            lambda: page._rs_state is None, timeout=30,
            message="multi-module Region Size",
        )
        harness.pump(300)
        outputs = page._rs_pending
        harness.check(outputs is not None)
        harness.eq(len(outputs["regions"]), 2)
        values = outputs["values"][first]
        expected = sorted([
            int(region1["params"]["channels"]["a1"]["files"][first][
                "pixels"
            ]),
            int(region2["params"]["channels"]["a2"]["files"][first][
                "pixels"
            ]),
        ])
        harness.eq(sorted(values.values()), expected)
        harness.check(
            all(value is None for value in outputs["values"][second].values()),
            "a missing Sample is NA for both modules",
        )

        original = [item["key"] for item in outputs["regions"]]
        page._rs_table.horizontalHeader().moveSection(2, 3)
        harness.pump(200)
        reordered = [
            item["key"]
            for item in page._rs_pending["regions"]
        ]
        harness.eq(reordered, [original[1], original[0]])
        _rs_page._rs_save(ctx, page)
        saved = json.loads((project.path / "project.json").read_text())
        stack = list(saved["workflow"]["tasks"])
        found = None
        while stack:
            task = stack.pop()
            if task.get("id") == "rs-1":
                found = task
                break
            stack.extend(task.get("children") or [])
        harness.check(found is not None, "Region Size task persisted")
        harness.eq(
            [item["key"] for item in found["params"]["outputs"]["regions"]],
            reordered,
            "the column order is saved with the results",
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region_size.files_required", "L4", "region_size",
              tags=("fast",))
def files_required() -> None:
    """A new Region Size module starts with no image files selected."""
    from unittest.mock import patch

    project, _region_task, rsize, _first, _second = _two_file_project(
        "region_size_files_required"
    )
    rsize["params"].pop("files", None)
    project.save()
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "rs-1", "region_size", "Region Size")
        harness.eq(page._rs_files, [])
        harness.eq(page._rs_params.get("files"), [])
        harness.eq(page._rs_selected, [],
                   "a new module does not preselect Regions")
        harness.eq(page._rs_params.get("regions"), [])
        harness.eq(page._rs_save_btn.text(), "Save")
        harness.eq(page._rs_copy_btn.text(), "Copy table")
        with harness.patch_dialogs(
            _rs_page, information=QMessageBox.StandardButton.Ok
        ):
            _rs_page._rs_start_calculate(ctx, page)
        harness.check(page._rs_state is None)
        harness.eq(page._rs_params.get("outputs"), {})
        harness.check(
            not window._task_locked(window._find_task_by_id("reg-1"))
        )

        names = [
            rec.get("name")
            for rec in _rs_page._rs_image_records(ctx, "rs-1")
        ]
        harness.check(len(names) >= 2)
        with patch.object(
            _rs_page._widgets_picker, "_pick_items_dialog",
            return_value=names,
        ):
            _rs_page._rs_select_files(ctx, page)
        harness.eq(len(page._rs_files), len(names))
        harness.eq(page._rs_selected, [],
                   "selecting files does not select Regions")
    finally:
        e2e.close_window(window)


@harness.case("mod.region_size.empty_selection", "L4", "region_size",
              tags=("fast",))
def empty_selection() -> None:
    """Calculate refuses empty file/Region selections and saves nothing."""
    project, region_task, _rs_task, _first, _second = _two_file_project(
        "region_size_empty"
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        page = e2e.open_page(window, "rs-1", "region_size", "Region Size")
        page._rs_selected = []
        _rs_page._rs_rebuild_panels(ctx, page)
        with harness.patch_dialogs(
            _rs_page, information=QMessageBox.StandardButton.Ok
        ):
            _rs_page._rs_start_calculate(ctx, page)
        harness.check(page._rs_state is None)
        harness.eq(page._rs_params.get("outputs"), {})

        page._rs_files = []
        _rs_page._rs_rebuild_panels(ctx, page)
        with harness.patch_dialogs(
            _rs_page, information=QMessageBox.StandardButton.Ok
        ):
            _rs_page._rs_start_calculate(ctx, page)
        harness.check(page._rs_state is None)
        harness.eq(page._rs_params.get("outputs"), {})
        harness.check(
            not window._task_locked(window._find_task_by_id("reg-1")),
            "nothing is locked without results",
        )
    finally:
        e2e.close_window(window)


@harness.case("mod.region_size.save", "L4", "region_size",
              tags=("fast",))
def save_results() -> None:
    """Calculated results stay pending until Save; closing warning works."""
    project, region_task, _rs_task, first, second = _two_file_project(
        "region_size_save"
    )
    window = e2e.open_window(project)
    try:
        ctx = window._ctx
        region_task = _write_store(ctx, region_task, first, second)
        page = e2e.open_page(window, "rs-1", "region_size", "Region Size")
        page._rs_options = _rs_page._rs_region_options(ctx, "rs-1")
        page._rs_selected = [item["key"] for item in page._rs_options]
        _rs_page._rs_rebuild_panels(ctx, page)
        _rs_page._rs_start_calculate(ctx, page)
        harness.wait_for(
            lambda: page._rs_state is None, timeout=30,
            message="Region Size calculation",
        )
        harness.pump(300)
        harness.eq(page._rs_params.get("outputs"), {})
        harness.check(page._rs_pending is not None)
        harness.check(not window._task_locked(
            window._find_task_by_id("reg-1")
        ))

        # Closing with an unsaved table asks first; No keeps the page.
        with harness.patch_dialogs(
            _rs_page, question=QMessageBox.StandardButton.No
        ):
            _rs_page._rs_close(ctx, page)
        harness.check(page._rs_pending is not None,
                      "No keeps the unsaved table")

        outputs = page._rs_pending
        _rs_page._rs_save(ctx, page)
        harness.eq(page._rs_params["outputs"], outputs)
        harness.check(window._task_locked(window._find_task_by_id("reg-1")),
                      "Save locks upstream modules")
        harness.check(not page._rs_save_btn.isEnabled(),
                      "Save is disabled once stored")
    finally:
        e2e.close_window(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
