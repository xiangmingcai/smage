"""Shared harness for the Smage regression suite.

Import this module *first* in every test script: it sets up the offscreen Qt
platform and an isolated ``%APPDATA%`` before PySide6 or ``smage`` is imported.

Standalone use::

    python tests/regression/cases/t00_static.py

Normal use goes through the single entry point::

    python tests/regression/run_all.py --smoke
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

REGRESSION_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("SMAGE_REPO") or REGRESSION_DIR.parents[1]).resolve()
SRC_DIR = REPO_ROOT / "src"
CASES_DIR = REGRESSION_DIR / "cases"
BASELINE_DIR = REGRESSION_DIR / "baseline"

WORKSPACE = Path(
    os.environ.get("SMAGE_TEST_ROOT") or r"E:\smage_test\samge_batch_test"
).resolve()
RUN_ID = os.environ.get("SMAGE_RUN_ID") or time.strftime("%Y%m%d-%H%M%S")
RUN_DIR = WORKSPACE / "runs" / RUN_ID
# Each script gets its own APPDATA when the runner provides one (parallel runs).
APP_DATA = Path(os.environ.get("SMAGE_APPDATA") or (RUN_DIR / "_appdata"))
PARTS_DIR = RUN_DIR / "parts"
SCREENSHOT_DIR = RUN_DIR / "screenshots"
FIXTURES_DIR = WORKSPACE / "_fixtures"

# These must be in place before Qt / smage import anything.
if os.environ.get("SMAGE_SHOW") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = str(APP_DATA)
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_FORBIDDEN = [
    (REPO_ROOT / "src").resolve(),
    (REPO_ROOT / "data").resolve(),
    (REPO_ROOT / ".git").resolve(),
    Path(r"E:\smage_test\evos_test2.smage").resolve(),
]


# ---------------------------------------------------------------------------
# Workspace, paths and safety
# ---------------------------------------------------------------------------

def ensure_work_dir() -> None:
    """Create the run directories if they are missing."""
    if WORKSPACE == REPO_ROOT or REPO_ROOT in WORKSPACE.parents:
        raise RuntimeError(
            f"the test workspace must be outside the repository: {WORKSPACE}"
        )
    for path in (RUN_DIR, PARTS_DIR, SCREENSHOT_DIR, APP_DATA, FIXTURES_DIR):
        path.mkdir(parents=True, exist_ok=True)


def guard(path, *, allow_baseline: bool = False) -> Path:
    """Return *path* as a resolved path, or raise if it is outside the sandbox.

    Tests may only write under the workspace.  ``allow_baseline`` additionally
    permits the committed baseline folder (used by the inventory tool).
    """
    resolved = Path(path).resolve()
    inside = resolved == WORKSPACE or WORKSPACE in resolved.parents
    if allow_baseline:
        inside = inside or resolved == BASELINE_DIR or BASELINE_DIR in resolved.parents
    if not inside:
        raise RuntimeError(f"test path outside the workspace: {resolved}")
    for bad in _FORBIDDEN:
        if resolved == bad or bad in resolved.parents:
            raise RuntimeError(f"test attempted to write into a protected path: {bad}")
    if "evos_test2.smage" in str(resolved).lower():
        raise RuntimeError("test attempted to touch the user's real project")
    return resolved


def ensure_fixtures() -> Path:
    """Generate the synthetic fixtures on first use."""
    marker = FIXTURES_DIR / "fcs" / "synthetic_8ch.fcs"
    if not marker.is_file():
        import fixtures_gen

        fixtures_gen.generate(FIXTURES_DIR)
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# Case registry
# ---------------------------------------------------------------------------

@dataclass
class Case:
    id: str
    layer: str
    module: str
    tags: tuple[str, ...]
    fn: object
    notes: list[str] = field(default_factory=list)


CASES: list[Case] = []


def case(case_id: str, layer: str, module: str, tags=()):
    """Register a test function under a stable case id."""

    def decorator(fn):
        CASES.append(Case(case_id, layer, module, tuple(tags), fn))
        return fn

    return decorator


class SkipTest(Exception):
    """Raised by :func:`skip` to mark a case as not applicable."""


def skip(reason: str):
    raise SkipTest(reason)


_CURRENT_NOTES: list[str] = []
_CURRENT_ARTIFACTS: list[str] = []
_CURRENT_CASE = ""
_EXPECTED_LOG_MARKERS: list[tuple[str, ...]] = []


def note(message: str) -> None:
    """Attach an informational note to the running case."""
    _CURRENT_NOTES.append(str(message))


def artifact(path) -> Path:
    """Record a file produced by the running case."""
    resolved = Path(path)
    _CURRENT_ARTIFACTS.append(str(resolved))
    return resolved


def golden(name: str, value) -> None:
    """Compare a deterministic result with its committed JSON baseline.

    ``--update-baseline`` sets ``SMAGE_UPDATE_GOLDEN=1`` and rewrites the
    baseline instead of comparing.
    """
    folder = BASELINE_DIR / "golden"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    if os.environ.get("SMAGE_UPDATE_GOLDEN") == "1" or not path.is_file():
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    if value != expected:
        raise AssertionError(
            f"golden {name} differs:\nexpected {expected!r}\ngot {value!r}"
        )


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def check(condition, message: str = "assertion failed") -> None:
    if not condition:
        raise AssertionError(message)


def eq(actual, expected, message: str = "") -> None:
    if actual != expected:
        prefix = f"{message}: " if message else ""
        raise AssertionError(f"{prefix}expected {expected!r}, got {actual!r}")


def contains(haystack, needle, message: str = "") -> None:
    if needle not in haystack:
        prefix = f"{message}: " if message else ""
        raise AssertionError(f"{prefix}{needle!r} not found in {haystack!r}")


@contextlib.contextmanager
def raises(exc_type, match: str | None = None):
    """Assert that the block raises ``exc_type``."""
    try:
        yield
    except exc_type as exc:  # noqa: PERF203 - intentional
        if match is not None and match not in str(exc):
            raise AssertionError(
                f"expected {exc_type.__name__} containing {match!r}, got {exc!r}"
            ) from exc
    else:
        raise AssertionError(f"expected {exc_type.__name__}, nothing was raised")


@contextlib.contextmanager
def expect_log_errors(*markers: str):
    """Suppress log-error detection for chunks containing one of ``markers``.

    Used by cases that deliberately exercise an error path.
    """
    _EXPECTED_LOG_MARKERS.append(tuple(markers))
    try:
        yield
    finally:
        _EXPECTED_LOG_MARKERS.pop()


# ---------------------------------------------------------------------------
# Qt helpers
# ---------------------------------------------------------------------------

_APP = None


def app():
    """Return the shared QApplication, creating it on first use."""
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])
    return _APP


def pump(milliseconds: int = 50) -> None:
    """Process Qt events for roughly ``milliseconds``."""
    application = app()
    deadline = time.monotonic() + max(0, milliseconds) / 1000.0
    while True:
        application.processEvents()
        if time.monotonic() >= deadline:
            break
        time.sleep(0.004)
    application.processEvents()


def wait_for(predicate, timeout: float = 10.0, message: str = "condition") -> None:
    """Pump events until ``predicate()`` is true or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:  # noqa: BLE001 - a transient failure is retried
            pass
        app().processEvents()
        time.sleep(0.02)
    raise AssertionError(f"timeout after {timeout:.1f}s waiting for {message}")


def screenshot(widget, name: str) -> Path:
    """Grab a widget into the run's screenshot folder."""
    ensure_work_dir()
    safe_case = _CURRENT_CASE.replace("/", "_") or "misc"
    path = SCREENSHOT_DIR / f"{safe_case}__{name}.png"
    widget.grab().save(str(path))
    return artifact(path)


# ---------------------------------------------------------------------------
# Window / project helpers
# ---------------------------------------------------------------------------

def close_window(window) -> None:
    """Close a test window after discarding open module pages.

    Module pages left open in a test may be dirty; closing the window would
    otherwise raise the real unsaved-changes prompt and block the suite.
    """
    try:
        while window.tabs.count() > 1:
            window._close_module_page(window.tabs.widget(window.tabs.count() - 1))
    except Exception:  # noqa: BLE001 - best-effort test cleanup
        pass
    window.close()
    pump(100)


def new_window():
    """Create a MainWindow, already shown on the offscreen platform."""
    app()
    from smage.main import MainWindow

    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    pump(100)
    return window


def task_def(task_type: str, task_id: str | None = None, name: str | None = None,
             params: dict | None = None, children=None) -> dict:
    """Build one workflow task definition."""
    return {
        "id": task_id or f"{task_type}-task",
        "type": task_type,
        "name": name or task_type.replace("_", " ").title(),
        "params": dict(params or {}),
        "output": {"saved": False},
        "children": list(children or []),
    }


def base_frame(rows: int = 500, seed: int = 42):
    """A small deterministic numeric table (x/y plus cytometry-like channels)."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    n = int(rows)
    index = np.arange(n)
    frame = pd.DataFrame(
        {
            "x": (index % 50).astype("int32"),
            "y": (index // 50).astype("int32"),
            "FSC-A": rng.normal(50_000, 12_000, n).astype("float32"),
            "SSC-A": rng.normal(30_000, 8_000, n).astype("float32"),
        }
    )
    for channel in range(1, 7):
        frame[f"FL{channel}-A"] = rng.gamma(2.0, 100.0, n).astype("float32")
    return frame


def new_project(case_id: str, name: str = "case", rows: int = 500,
                tasks=(), seed: int = 42, shared: bool = False):
    """Create a clean project with one imported parquet file.

    ``shared=True`` puts the project in ``runs/<id>/work`` instead of the
    per-case folder; used when several cases share one window/project.
    """
    from smage.project import Project

    ensure_work_dir()
    ensure_fixtures()
    parent = RUN_DIR / ("work" if shared else "cases") / case_id
    if parent.exists():
        shutil.rmtree(parent)
    parent.mkdir(parents=True, exist_ok=True)
    guard(parent)

    project = Project.create(parent, name)
    frame = base_frame(rows, seed)
    parquet = project.data_dir / f"{name}.parquet"
    frame.to_parquet(parquet, index=False)
    project.save_columns({parquet.name: list(frame.columns)})

    import_task = project.workflow["tasks"][0]
    import_task.setdefault("params", {})["processed"] = [
        {
            "name": f"{name}.fcs",
            "source": str(FIXTURES_DIR / "fcs" / "synthetic_8ch.fcs"),
            "parquet": str(parquet),
            "rows": int(len(frame)),
        }
    ]
    for task in tasks:
        import_task.setdefault("children", []).append(task)
    project.save()
    return project


def open_project(window, project):
    """Attach *project* to *window* the same way the File > Open path does."""
    from smage.project import Project
    from smage.runlog import runlog

    runlog.stop("test switch")
    window._discard_draft_folders()
    window.project = Project.load(project.path)
    runlog.start(window.project.path, window._log_environment())
    window.setWindowTitle(f"Smage — {window.project.name}")
    window._column_index = window.project.load_columns()
    window._reset_module_tabs()
    window._load_import_files()
    window._refresh_workflow_tree()
    pump(120)
    return window.project


# ---------------------------------------------------------------------------
# Dialog patching
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def patch_dialogs(namespace, **answers):
    """Replace the modal dialog classes inside one module namespace.

    Example::

        with harness.patch_dialogs(
            project_io, directory=str(tmp), text="My Project",
            question=QMessageBox.StandardButton.Yes,
        ):
            window._new_project()
    """
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QMessageBox

    class FakeMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*_a, **_k):
            return answers.get("question", QMessageBox.StandardButton.No)

        @staticmethod
        def warning(*_a, **_k):
            return answers.get("warning", QMessageBox.StandardButton.Ok)

        @staticmethod
        def information(*_a, **_k):
            return answers.get("information", QMessageBox.StandardButton.Ok)

        @staticmethod
        def critical(*_a, **_k):
            return answers.get("critical", QMessageBox.StandardButton.Ok)

    class FakeFileDialog:
        @staticmethod
        def getExistingDirectory(*_a, **_k):
            return answers.get("directory", "")

        @staticmethod
        def getOpenFileNames(*_a, **_k):
            return list(answers.get("open_files", [])), ""

        @staticmethod
        def getOpenFileName(*_a, **_k):
            files = list(answers.get("open_files", []))
            return (files[0] if files else ""), ""

        @staticmethod
        def getSaveFileName(*_a, **_k):
            return answers.get("save_file", ""), ""

    class FakeInputDialog:
        @staticmethod
        def getText(*_a, **_k):
            return answers.get("text", ""), answers.get("text_ok", True)

        @staticmethod
        def getItem(*_a, **_k):
            return answers.get("item", ""), answers.get("item_ok", True)

        @staticmethod
        def getInt(*_a, **_k):
            return int(answers.get("int", 0)), answers.get("int_ok", True)

        @staticmethod
        def getDouble(*_a, **_k):
            return float(answers.get("double", 0.0)), answers.get("double_ok", True)

    class FakeColorDialog:
        @staticmethod
        def getColor(*_a, **_k):
            return answers.get("color", QColor("#ff0000"))

    replacements = {
        "QMessageBox": FakeMessageBox,
        "QFileDialog": FakeFileDialog,
        "QInputDialog": FakeInputDialog,
        "QColorDialog": FakeColorDialog,
    }
    saved = {}
    for name, fake in replacements.items():
        if hasattr(namespace, name):
            saved[name] = getattr(namespace, name)
            setattr(namespace, name, fake)
    try:
        yield
    finally:
        for name, original in saved.items():
            setattr(namespace, name, original)


# ---------------------------------------------------------------------------
# Project-log error detection
# ---------------------------------------------------------------------------

class LogWatcher:
    """Collect new ``[ERROR]``/traceback lines written during one case."""

    def __init__(self, roots=()):
        self.roots = [Path(root) for root in roots]
        self._offsets: dict[Path, int] = {}

    def _files(self) -> list[Path]:
        files: set[Path] = set()
        for root in self.roots:
            if not root.exists():
                continue
            for pattern in ("**/log/*.log", "**/error.log", "**/debug.log"):
                files.update(root.glob(pattern))
        return sorted(files)

    def mark(self) -> None:
        """Remember current file sizes; call at the start of a case."""
        self._offsets = {}
        for path in self._files():
            try:
                self._offsets[path] = path.stat().st_size
            except OSError:
                pass

    def errors(self) -> list[str]:
        # The session log is written by a background thread in ~0.5s batches.
        time.sleep(0.6)
        app().processEvents()
        found: list[str] = []
        for path in self._files():
            start = self._offsets.get(path, 0)
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(start)
                    text = handle.read()
            except OSError:
                continue
            if not text:
                continue
            if any(marker in text for markers in _EXPECTED_LOG_MARKERS for marker in markers):
                continue
            for line in text.splitlines():
                if "[ERROR]" in line or "Traceback (most recent call last)" in line:
                    found.append(f"{path.name}: {line.strip()[:300]}")
        return found


# ---------------------------------------------------------------------------
# Running the cases
# ---------------------------------------------------------------------------

def _selected_cases() -> list[Case]:
    only = os.environ.get("SMAGE_ONLY_CASE", "").strip()
    many = {item.strip() for item in os.environ.get("SMAGE_ONLY_CASES", "").split(",") if item.strip()}
    include = {item.strip() for item in os.environ.get("SMAGE_TAGS", "").split(",") if item.strip()}
    exclude = {item.strip() for item in os.environ.get("SMAGE_EXCLUDE_TAGS", "").split(",") if item.strip()}

    selected = []
    for item in CASES:
        if only and item.id != only:
            continue
        if many and item.id not in many:
            continue
        tags = set(item.tags)
        if include and not (tags & include):
            continue
        if exclude and (tags & exclude):
            continue
        selected.append(item)
    return selected


def _result_record(item: Case, status: str, message: str, duration_ms: int,
                   log_errors, trace: str = "") -> dict:
    return {
        "case_id": item.id,
        "layer": item.layer,
        "module": item.module,
        "tags": list(item.tags),
        "status": status,
        "duration_ms": int(duration_ms),
        "message": message,
        "notes": list(_CURRENT_NOTES),
        "log_errors": list(log_errors),
        "artifacts": list(_CURRENT_ARTIFACTS),
        "traceback": trace,
        "script": Path(sys.argv[0]).stem,
        "repro": f"python tests/regression/run_all.py --case {item.id}",
    }


def run_cases() -> int:
    """Run every selected case and write a JSON part file."""
    global _CURRENT_NOTES, _CURRENT_ARTIFACTS, _CURRENT_CASE
    ensure_work_dir()

    selected = _selected_cases()
    if not selected:
        print("NO CASES SELECTED")
        (PARTS_DIR / f"{Path(sys.argv[0]).stem}.json").write_text("[]", encoding="utf-8")
        return 0

    results: list[dict] = []
    failures = 0
    for item in selected:
        _CURRENT_NOTES = []
        _CURRENT_ARTIFACTS = []
        _CURRENT_CASE = item.id
        watcher = LogWatcher([RUN_DIR])
        watcher.mark()
        started = time.perf_counter()
        status = "pass"
        message = ""
        trace = ""
        try:
            item.fn()
        except SkipTest as exc:
            status = "skip"
            message = str(exc)
        except Exception as exc:  # noqa: BLE001 - a failing case is data, not a crash
            status = "fail"
            message = f"{type(exc).__name__}: {exc}"
            trace = traceback.format_exc()
        duration_ms = int((time.perf_counter() - started) * 1000)

        log_errors: list[str] = []
        if status != "skip":
            log_errors = watcher.errors()
            if log_errors and status == "pass":
                status = "fail"
                message = f"unexpected log error: {log_errors[0]}"

        record = _result_record(item, status, message, duration_ms, log_errors, trace)
        results.append(record)
        if status in ("fail", "error"):
            failures += 1

        marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}.get(status, status.upper())
        suffix = f" - {message}" if message else ""
        print(f"[{marker}] {item.id} ({duration_ms} ms){suffix}")

    part = PARTS_DIR / f"{Path(sys.argv[0]).stem}.json"
    part.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    passed = sum(1 for item in results if item["status"] == "pass")
    skipped = sum(1 for item in results if item["status"] == "skip")
    print(f"SUMMARY {Path(sys.argv[0]).stem}: {passed} passed, {failures} failed, {skipped} skipped")
    return 0 if failures == 0 else 1


def main() -> int:
    """Script entry point: run the cases, then close top-level widgets."""
    code = run_cases()
    try:
        application = app()
        for widget in application.topLevelWidgets():
            try:
                widget.close()
            except RuntimeError:
                pass  # already deleted by a module's own cleanup
        pump(80)
        application.quit()
        application.processEvents()
        import gc

        gc.collect()
        application.processEvents()
    except Exception:  # noqa: BLE001 - teardown must not hide the test result
        traceback.print_exc()
    return code
