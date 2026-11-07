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
    harness.eq(parameter_names(info), ["Channel 1", "Channel 2", "Channel 3"])

    info = read_dimensions(SINGLE)
    harness.eq(info["channels"], 1)
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
    window._load_import_files()
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
    window._refresh_import_list()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("import.page.peek", "L3", "import", tags=("smoke",))
def page_peek() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_peek")
    try:
        _set_files(window, [FCS])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            window._peek_files()

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
            window._peek_files()

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
            window._peek_files()
            window._run_import()
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
            window._run_import()

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
            window._peek_files()
            window._run_import()
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
            window._peek_files()
            params = window._import_files[0]["parameters"]
            params[0]["extract"] = False
            params[1]["rename"] = "Renamed"
            window._run_import()
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
            window._peek_files()
            window._run_import()
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


@harness.case("import.page.delete", "L3", "import", tags=("smoke",))
def page_delete() -> None:
    from smage.modules.import_ import page as import_page

    window, _project = _import_page("import_page_delete")
    try:
        _set_files(window, [FCS3])
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            window._peek_files()
            window._run_import()
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
            window._delete_selected_files()

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
            window._peek_files()
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
                window._apply_to_files()
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
            window._peek_files()

        def slow_import(*args, **kwargs):
            _time.sleep(0.3)
            return original(*args, **kwargs)

        import_page.import_file = slow_import
        with harness.patch_dialogs(
            import_page, question=QMessageBox.StandardButton.Yes
        ):
            window._run_import()
        harness.pump(60)
        window._cancel_import()
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
