"""L3: file dimensions, the importer, and the Data Import Peek/Import flow."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QPushButton,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.dimensions import parameter_names, read_dimensions  # noqa: E402
from smage.importer import _select_and_rename, import_file  # noqa: E402
from smage.modules.import_ import page as _import__page

FIXTURES = harness.FIXTURES_DIR
FCS = FIXTURES / "fcs" / "synthetic_8ch.fcs"
FCS3 = FIXTURES / "fcs" / "synthetic_3ch.fcs"
CSV = FIXTURES / "tables" / "base.csv"
PARQUET = FIXTURES / "tables" / "base.parquet"
IMAGE = FIXTURES / "images" / "multichannel.tif"
SINGLE = FIXTURES / "images" / "single.tif"


def _work(case_id: str) -> Path:
    path = harness.RUN_DIR / "cases" / case_id / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _params(info, extract=True):
    names = info.get("columns") or parameter_names(info)
    return [
        {"name": name, "extract": extract, "rename": name}
        for name in names
    ]


@harness.case("import.dimensions", "L3", "import", tags=("smoke",))
def dimensions() -> None:
    info = read_dimensions(FCS)
    harness.eq(info["kind"], "fcs")
    harness.eq(info["rows"], 2000)
    harness.eq(len(info["columns"]), 8)

    info = read_dimensions(CSV)
    harness.eq(info["kind"], "table")
    harness.eq(info["rows"], 5000)
    harness.eq(len(info["columns"]), 12)

    info = read_dimensions(PARQUET)
    harness.eq(info["rows"], 5000)

    info = read_dimensions(IMAGE)
    harness.eq(info["kind"], "image")
    harness.eq(info["channels"], 3)
    harness.eq(info["width"], 64)
    harness.eq(info["height"], 64)
    harness.eq(parameter_names(info), ["Channel 1", "Channel 2", "Channel 3"])

    info = read_dimensions(SINGLE)
    harness.eq(info["channels"], 1)
    harness.eq(info["width"], 64)
    harness.eq(info["height"], 64)
    harness.eq(parameter_names(info), ["Channel 1"])

    with harness.raises(ValueError, match="Unsupported"):
        read_dimensions(FIXTURES / "broken" / "table.txt")
    with harness.raises(ValueError):
        read_dimensions(FIXTURES / "broken" / "empty.csv")
    with harness.raises(Exception):
        read_dimensions(FIXTURES / "broken" / "bad.parquet")


@harness.case("import.formats", "L3", "import", tags=("smoke",))
def import_formats() -> None:
    work = _work("import_formats")

    info = read_dimensions(FCS)
    out = work / "fcs.parquet"
    result = import_file(str(FCS), _params(info), "all", 100, out)
    harness.eq(result["rows"], 2000)
    harness.eq(result["columns"], info["columns"])
    harness.check(out.is_file())

    info = read_dimensions(CSV)
    out = work / "csv.parquet"
    result = import_file(str(CSV), _params(info), "all", 100, out)
    harness.eq(result["rows"], 5000)
    harness.eq(result["columns"], info["columns"])

    info = read_dimensions(PARQUET)
    out = work / "parquet.parquet"
    result = import_file(str(PARQUET), _params(info), "all", 100, out)
    harness.eq(result["rows"], 5000)

    info = read_dimensions(IMAGE)
    out = work / "image.parquet"
    result = import_file(str(IMAGE), _params(info), "all", 100, out)
    harness.eq(result["rows"], 4096)
    harness.eq(result["columns"][:2], ["x", "y"])
    harness.eq(result["columns"][2:], ["Channel 1", "Channel 2", "Channel 3"])


@harness.case("import.options", "L3", "import", tags=("smoke",))
def import_options() -> None:
    work = _work("import_options")
    info = read_dimensions(FCS)

    result = import_file(
        str(FCS), _params(info), "partial", 50, work / "half.parquet"
    )
    harness.eq(result["rows"], 1000, "partial sampling uses 50% of the rows")

    params = _params(info)
    params[0]["extract"] = False
    params[1]["rename"] = "Renamed"
    result = import_file(str(FCS), params, "all", 100, work / "renamed.parquet")
    harness.eq(len(result["columns"]), 7)
    harness.check("Renamed" in result["columns"])
    harness.check(info["columns"][0] not in result["columns"])

    frame = pd.DataFrame({"A": [1, 2], "B": [3, 4], "C": [5, 6]})
    selected = _select_and_rename(
        frame,
        [{"name": "A", "extract": False},
         {"name": "B", "extract": True, "rename": "Bee"},
         {"name": "C", "extract": True}],
    )
    harness.eq(list(selected.columns), ["Bee", "C"])
    harness.eq(selected["Bee"].tolist(), [3, 4])

    with harness.raises(ValueError, match="peeked"):
        import_file(str(FCS), [], "all", 100, work / "nope.parquet")
    with harness.raises(ValueError, match="Unsupported"):
        import_file(str(FIXTURES / "broken" / "table.txt"),
                    [{"name": "x"}], "all", 100, work / "nope.parquet")


# ---------------------------------------------------------------------------
# Data Import page
# ---------------------------------------------------------------------------

def _import_page(case_id: str):
    project = harness.new_project(case_id, rows=10)
    project.workflow["tasks"][0]["params"]["processed"] = []
    project.save_columns({})
    project.save()
    window = harness.new_window()
    harness.open_project(window, project)
    _import__page._load_import_files(window._ctx)
    window._open_module("data-import", "import", "Data Import")
    harness.pump(300)
    return window, project


def _set_files(window, paths, sampling="full", percent=10) -> None:
    window._import_files = [
        {
            "id": uuid4().hex,
            "path": str(path),
            "checked": True,
            "status": "pending",
            "sampling": sampling,
            "sampling_percent": percent,
        }
        for path in paths
    ]
    _import__page._refresh_import_list(window._ctx)


def _close(window) -> None:
    harness.close_window(window)


@harness.case("import.image.resize", "L3", "import", tags=("fast",))
def image_resize_core() -> None:
    """The Qt-free plane resizer: shapes, dtype, nearest values, NaN-safe."""
    import numpy as np

    from smage.core.image_ops import _resize_plane

    plane = np.arange(16, dtype=np.uint16).reshape(4, 4)
    smooth = _resize_plane(plane, 2, 2, "smooth")
    harness.eq(smooth.shape, (2, 2))
    harness.eq(smooth.dtype, plane.dtype)
    harness.check(bool(np.isfinite(smooth).all()), "smooth output is finite")
    harness.check(
        abs(float(smooth.mean()) - float(plane.mean())) < 1.0,
        "downscaling keeps the average level",
    )

    nearest = _resize_plane(plane, 2, 2, "nearest")
    harness.eq(nearest.shape, (2, 2))
    harness.check(
        set(int(v) for v in nearest.ravel())
        <= set(int(v) for v in plane.ravel()),
        "nearest keeps original pixel values",
    )

    big = _resize_plane(plane, 8, 8, "smooth")
    harness.eq(big.shape, (8, 8))
    harness.eq(int(big[0, 0]), int(plane[0, 0]))
    harness.eq(int(big[-1, -1]), int(plane[-1, -1]))

    nan_plane = plane.astype("float32")
    nan_plane[0, 0] = np.nan
    nan_out = _resize_plane(nan_plane, 2, 2, "smooth")
    harness.eq(nan_out.shape, (2, 2))
    harness.check(
        bool(np.isfinite(nan_out).any()),
        "a NaN corner does not blank the whole resized plane",
    )

    # A checkerboard separates the two interpolation modes: Smooth averages,
    # Nearest keeps the original levels.
    checker = np.array([
        [0, 255, 0, 255],
        [255, 0, 255, 0],
        [0, 255, 0, 255],
        [255, 0, 255, 0],
    ], dtype=np.uint8)
    smooth_checker = _resize_plane(checker, 2, 2, "smooth")
    nearest_checker = _resize_plane(checker, 2, 2, "nearest")
    harness.check(
        any(int(value) not in (0, 255) for value in smooth_checker.ravel()),
        "smooth interpolation averages the checkerboard",
    )
    harness.check(
        set(int(value) for value in nearest_checker.ravel()) <= {0, 255},
        "nearest interpolation only keeps source levels",
    )


@harness.case("import.image.resize_import", "L3", "import", tags=("fast",))
def image_resize_import() -> None:
    """import_file resizes images, records the sizes, and ignores sampling."""
    work = _work("import_image_resize")
    info = read_dimensions(IMAGE)
    out = work / "resized.parquet"
    result = import_file(
        str(IMAGE), _params(info), "partial", 50, out,
        image_resize={
            "enabled": True, "width": 32, "height": 20,
            "keep_aspect": False, "method": "nearest",
        },
    )
    harness.eq(result["rows"], 32 * 20)
    harness.eq(result["original_size"], [64, 64])
    harness.eq(result["imported_size"], [32, 20])
    frame = pd.read_parquet(out)
    harness.eq(len(frame), 32 * 20)
    harness.eq(int(frame["x"].max()), 31)
    harness.eq(int(frame["y"].max()), 19)
    harness.eq(
        list(frame.columns),
        ["x", "y", "Channel 1", "Channel 2", "Channel 3"],
    )

    # A single-channel 2-D TIFF imports (and resizes) too.
    single = read_dimensions(SINGLE)
    result = import_file(
        str(SINGLE), _params(single), "all", 100, work / "single.parquet",
        image_resize={"enabled": True, "width": 32, "height": 16},
    )
    harness.eq(result["rows"], 32 * 16)
    harness.eq(result["imported_size"], [32, 16])
    harness.eq(result["columns"], ["x", "y", "Channel 1"])

    # Enlarging is allowed too.
    result = import_file(
        str(SINGLE), _params(single), "all", 100, work / "up.parquet",
        image_resize={"enabled": True, "width": 128, "height": 128},
    )
    harness.eq(result["rows"], 128 * 128)
    harness.eq(result["imported_size"], [128, 128])


@harness.case("import.page.peek", "L3", "import", tags=("smoke",))
def page_peek() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_peek")
    try:
        _set_files(window, [FCS])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)

        entry = window._import_files[0]
        harness.eq(entry["status"], "peeked")
        harness.eq(entry["dimensions"]["rows"], 2000)
        harness.eq(
            [param["name"] for param in entry["parameters"]],
            read_dimensions(FCS)["columns"],
        )
        harness.eq(window.import_list.count(), 1)
        harness.eq(window.import_list.item(0).checkState(), Qt.CheckState.Checked)
    finally:
        _close(window)


@harness.case("import.page.peek_error", "L3", "import", tags=("smoke",))
def page_peek_error() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_peek_error")
    try:
        _set_files(window, [FIXTURES / "broken" / "table.txt"])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)

        entry = window._import_files[0]
        harness.eq(entry["status"], "error")
        harness.check("Unsupported" in (entry.get("error") or ""),
                      f"error: {entry.get('error')}")
    finally:
        _close(window)


@harness.case("import.page.run", "L3", "import", tags=("smoke",))
def page_run() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_run")
    try:
        _set_files(window, [FCS])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)
            _import__page._run_import(window._ctx)
        harness.check(getattr(window, "_import_running", False), "import started")
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=30,
            message="import to finish",
        )

        entry = window._import_files[0]
        harness.eq(entry["status"], "processed")
        harness.eq(len(window._processed_files), 1)
        record = window._processed_files[0]
        harness.eq(record["rows"], 2000)
        harness.check(Path(record["parquet"]).is_file())
        harness.eq(window._column_index[Path(record["parquet"]).name],
                   read_dimensions(FCS)["columns"])
    finally:
        _close(window)


@harness.case("import.page.not_peeked", "L3", "import", tags=("smoke",))
def page_not_peeked() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_not_peeked")
    try:
        _set_files(window, [FCS])
        with harness.patch_dialogs(
            import_page,
            question=QMessageBox.StandardButton.Yes,
            warning=QMessageBox.StandardButton.Ok,
        ):
            _import__page._run_import(window._ctx)

        harness.check(
            not getattr(window, "_import_running", False),
            "the import never starts without Peek",
        )
        harness.eq(window._processed_files, [])
    finally:
        _close(window)


@harness.case("import.page.sampling", "L3", "import", tags=("smoke",))
def page_sampling() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_sampling")
    try:
        _set_files(window, [FCS], sampling="partial", percent=50)
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)
            _import__page._run_import(window._ctx)
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=30,
            message="sampled import to finish",
        )
        harness.eq(window._processed_files[0]["rows"], 1000)
    finally:
        _close(window)


@harness.case("import.page.rename", "L3", "import", tags=("smoke",))
def page_rename() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_rename")
    try:
        _set_files(window, [FCS])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)
            params = window._import_files[0]["parameters"]
            params[0]["extract"] = False
            params[1]["rename"] = "Renamed"
            _import__page._run_import(window._ctx)
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=30,
            message="renamed import to finish",
        )
        columns = window._processed_files[0]
        harness.eq(columns["rows"], 2000)
        stored = window._column_index[Path(columns["parquet"]).name]
        harness.eq(len(stored), 7)
        harness.check("Renamed" in stored)
    finally:
        _close(window)


@harness.case("import.page.image", "L3", "import", tags=("smoke",))
def page_image() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_image")
    try:
        _set_files(window, [IMAGE])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)
            _import__page._run_import(window._ctx)
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=30,
            message="image import to finish",
        )
        record = window._processed_files[0]
        harness.eq(record["rows"], 4096)
        parquet = Path(record["parquet"])
        index_dir = Path(str(parquet) + ".imgidx")
        harness.check(
            (index_dir / "manifest.json").is_file(),
            "the image level-of-detail index is built during import",
        )
    finally:
        _close(window)


@harness.case("import.page.image_resize", "L3", "import", tags=("fast",))
def page_image_resize() -> None:
    """The image entry exposes resize controls; import writes the new size."""
    import json

    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_image_resize")
    try:
        _set_files(window, [IMAGE, FCS])
        ctx = window._ctx
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(ctx)

        ctx.import_list.setCurrentRow(0)  # the image
        harness.pump(30)
        harness.check(ctx._resize_enabled is not None,
                      "image entries show the resize controls")
        harness.check(not ctx._resize_enabled.isChecked(),
                      "resize is off by default")
        harness.eq(ctx._resize_width.value(), 64)
        harness.eq(ctx._resize_height.value(), 64)
        harness.check(ctx._resize_keep.isChecked(),
                      "the aspect ratio is kept by default")
        harness.check(not ctx.sample_combo.isEnabled(),
                      "sampling is disabled for images")

        ctx._resize_enabled.setChecked(True)
        ctx._resize_width.setValue(128)
        harness.eq(ctx._resize_width.value(), 128,
                   "enlarging is allowed")
        harness.eq(ctx._resize_height.value(), 128,
                   "the height follows the width")
        ctx._resize_width.setValue(32)
        harness.eq(ctx._resize_height.value(), 32,
                   "the height follows the width")
        settings = ctx._import_files[0]["image_resize"]
        harness.eq(settings["width"], 32)
        harness.eq(settings["height"], 32)

        # Import just the image.
        window._import_files[1]["checked"] = False
        _import__page._refresh_import_list(ctx)
        ctx.import_list.setCurrentRow(0)
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._run_import(ctx)
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=60,
            message="resized image import to finish",
        )
        record = window._processed_files[0]
        harness.eq(record["rows"], 32 * 32)
        harness.eq(record["original_size"], [64, 64])
        harness.eq(record["imported_size"], [32, 32])
        parquet = Path(record["parquet"])
        manifest = json.loads(
            (Path(str(parquet) + ".imgidx") / "manifest.json").read_text()
        )
        harness.eq(manifest["xmax"], 31.0)
        harness.eq(manifest["ymax"], 31.0)

        # Aspect lock can be turned off, then on again.
        ctx._resize_keep.setChecked(False)
        ctx._resize_width.setValue(48)
        ctx._resize_height.setValue(24)
        settings = ctx._import_files[0]["image_resize"]
        harness.eq([settings["width"], settings["height"]], [48, 24])
        harness.check(settings["keep_aspect"] is False)
        ctx._resize_method.setCurrentIndex(
            ctx._resize_method.findData("nearest")
        )
        settings = ctx._import_files[0]["image_resize"]
        harness.eq(settings["method"], "nearest")
        ctx._resize_keep.setChecked(True)
        ctx._resize_width.setValue(40)
        harness.eq(ctx._resize_height.value(), 40,
                   "the aspect lock restores the original ratio")
        harness.eq(
            ctx._import_files[0]["image_resize"]["height"], 40
        )

        ctx.import_list.setCurrentRow(1)  # the FCS file
        harness.pump(30)
        harness.check(ctx._resize_enabled is None,
                      "tables do not show resize controls")
        harness.check(ctx.sample_combo.isEnabled(),
                      "sampling is available for tables")
    finally:
        _close(window)


@harness.case("import.page.delete", "L3", "import", tags=("smoke",))
def page_delete() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_delete")
    try:
        _set_files(window, [FCS3])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)
            _import__page._run_import(window._ctx)
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=30,
            message="import to finish",
        )
        parquet = Path(window._processed_files[0]["parquet"])
        harness.check(parquet.is_file())

        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._delete_selected_files(window._ctx)

        harness.eq(window._import_files, [])
        harness.eq(window._processed_files, [])
        harness.check(not parquet.exists(), "the imported parquet is deleted")
        harness.eq(window._column_index, {})
    finally:
        _close(window)


@harness.case("import.page.apply_to", "L3", "import", tags=("fast",))
def page_apply_to() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_apply")
    try:
        _set_files(window, [FCS, FCS])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)
        first, second = window._import_files
        first["parameters"][0]["extract"] = False
        first["parameters"][1]["rename"] = "Applied"
        window._current_entry = first

        def confirm() -> None:
            for widget in QApplication.topLevelWidgets():
                if not isinstance(widget, QDialog) or not widget.isVisible():
                    continue
                if widget.windowTitle() != "Apply to":
                    continue
                for button in widget.findChildren(QPushButton):
                    if button.text() == "Confirm":
                        button.click()
                        return
                widget.accept()
                return

        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(confirm)
        timer.start()
        try:
            with harness.patch_dialogs(
                import_page,
                information=QMessageBox.StandardButton.Ok,
                warning=QMessageBox.StandardButton.Ok,
                question=QMessageBox.StandardButton.Yes,
            ):
                _import__page._apply_to_files(window._ctx)
        finally:
            timer.stop()

        harness.eq(second["parameters"][0]["extract"], False)
        harness.eq(second["parameters"][1]["rename"], "Applied")
    finally:
        _close(window)


@harness.case("import.page.cancel", "L3", "import", tags=("fast",))
def page_cancel() -> None:
    import time as _time

    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_cancel")
    original = import_page.import_file
    try:
        _set_files(window, [FCS, FCS3])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._peek_files(window._ctx)

        def slow_import(*args, **kwargs):
            _time.sleep(0.3)
            return original(*args, **kwargs)

        import_page.import_file = slow_import
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            _import__page._run_import(window._ctx)
        harness.pump(60)
        _import__page._cancel_import(window._ctx)
        harness.wait_for(
            lambda: not getattr(window, "_import_running", False),
            timeout=30,
            message="cancelled import to stop",
        )
        harness.check(window._import_cancel, "the cancel flag is set")
        harness.check(
            len(window._processed_files) <= 1,
            "the second file was not imported after cancellation",
        )
    finally:
        import_page.import_file = original
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
