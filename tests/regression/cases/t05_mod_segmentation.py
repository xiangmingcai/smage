"""L4: Segmentation — Nuclear Expansion, mask persistence and downstream masks."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.data.image_index import _build_image_index_files  # noqa: E402
from smage.importer import import_file  # noqa: E402
from smage.modules.segmentation import page as seg_page  # noqa: E402


def _open(case_id: str):
    project = harness.new_project(case_id, rows=1)
    base = project.path.parent
    image = base / "nuclei.tif"
    stack = np.zeros((1, 32, 32), dtype=np.uint8)
    stack[0, 5:10, 5:10] = 200
    stack[0, 20:25, 20:25] = 200
    tifffile.imwrite(image, stack, metadata={"axes": "CYX"})

    parquet = project.data_dir / "nuclei.parquet"
    params = [{"name": "Channel 1", "extract": True, "rename": "Channel 1"}]
    result = import_file(str(image), params, "all", 100, parquet)
    project.save_columns({"nuclei.parquet": result["columns"]})
    project.workflow["tasks"][0]["params"]["processed"] = [{
        "name": "nuclei.tif",
        "source": str(image),
        "parquet": str(parquet),
        "rows": result["rows"],
    }]
    task = harness.task_def("segmentation", task_id="seg-1", name="Segmentation",
                            children=[harness.task_def("gating", task_id="g-1")])
    task["output"] = {"saved": False}
    project.workflow["tasks"][0]["children"].append(task)
    project.save()
    _build_image_index_files(str(parquet))

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("seg-1", "segmentation", "Segmentation")
    harness.pump(450)
    return window, project, window.tabs.currentWidget(), str(parquet)


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.segmentation.nuclear", "L4", "segmentation", tags=("fast",))
def nuclear_expansion() -> None:
    window, _project, _page, parquet = _open("seg_nuclear")
    try:
        ctx = window._ctx
        params = {
            "channel": "Channel 1",
            "threshold_mode": "manual",
            "threshold": 100.0,
            "min_area": 4,
            "expand": 1,
        }
        mask, error = ctx._seg_nuclear_expansion(parquet, params)
        harness.eq(error, None)
        harness.check(mask is not None)
        harness.eq(mask.shape, (32, 32))
        labels = sorted(set(np.unique(mask).tolist()) - {0})
        harness.eq(labels, [1, 2], "two nuclei are labelled")
        harness.check(
            int(np.count_nonzero(mask)) > 25 * 2,
            "expansion grows both nuclei",
        )

    finally:
        _close(window)


@harness.case("mod.segmentation.mask_io", "L4", "segmentation", tags=("fast",))
def mask_io() -> None:
    window, project, _page, parquet = _open("seg_mask_io")
    try:
        ctx = window._ctx
        mask = np.zeros((32, 32), dtype=np.int32)
        mask[5:10, 5:10] = 1
        mask[20:25, 20:25] = 2

        rel = ctx._seg_write_mask("seg-1", parquet, mask)
        path = project.path / rel
        harness.check(path.is_file(), "the mask parquet is written")
        harness.check(
            (Path(str(path) + ".imgidx") / "manifest.json").is_file(),
            "the mask label pyramid is built",
        )

        loaded = ctx._seg_load_mask(str(path))
        harness.check(loaded is not None)
        harness.eq(loaded.shape, (32, 32))
        harness.eq(sorted(set(np.unique(loaded).tolist()) - {0}), [1, 2])
        harness.eq(int(np.count_nonzero(loaded)), int(np.count_nonzero(mask)))
    finally:
        _close(window)


@harness.case("mod.segmentation.errors", "L4", "segmentation", tags=("fast",))
def errors() -> None:
    window, _project, _page, parquet = _open("seg_errors")
    try:
        ctx = window._ctx
        mask, error = ctx._seg_nuclear_expansion(parquet, {"channel": ""})
        harness.eq(mask, None)
        harness.check("nucleus channel" in error, error)

        mask, error = ctx._seg_nuclear_expansion(
            parquet,
            {"channel": "Channel 1", "threshold_mode": "manual",
             "threshold": 255.0, "min_area": 4, "expand": 0},
        )
        harness.eq(mask, None)
        harness.check("empty mask" in error, error)
    finally:
        _close(window)


@harness.case("mod.segmentation.import_mask", "L4", "segmentation",
              tags=("fast",))
def import_mask() -> None:
    window, project, page, parquet = _open("seg_import_mask")
    try:
        ctx = window._ctx
        page._seg_params["files"] = [parquet]
        page._seg_params["method"] = "mask_input"
        ctx._seg_rebuild_method_panel(page)
        mask = np.zeros((32, 32), dtype=np.uint16)
        mask[5:10, 5:10] = 1
        mask[20:25, 20:25] = 2
        source = harness.RUN_DIR / "cases" / "seg_import_mask" / "mask.tif"
        source.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(source, mask)

        record = ctx._seg_mask_record_live(page, parquet, create=True)
        record["input"] = str(source)
        ctx._seg_import_masks(page)

        harness.eq(record["state"], seg_page.SEG_IMPORT_DONE)
        harness.check("Imported 1 mask" in page._seg_import_status.text(),
                      page._seg_import_status.text())
        path = project.path / record["path"]
        harness.check(path.is_file(), "the imported mask is written")

        loaded = ctx._seg_load_mask(str(path))
        harness.eq(sorted(set(np.unique(loaded).tolist()) - {0}), [1, 2])
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
