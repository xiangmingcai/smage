"""L0: environment preflight, compilation, imports and coverage guards."""
from __future__ import annotations

import hashlib
import importlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


@harness.case("static.python", "L0", "infra", tags=("smoke",))
def check_python() -> None:
    harness.eq(
        sys.version_info[:2],
        (3, 11),
        "Smage requires Python 3.11",
    )


@harness.case("static.pyside", "L0", "infra", tags=("smoke",))
def check_pyside() -> None:
    import PySide6

    version = getattr(PySide6, "__version__", "")
    harness.check(
        version.startswith("6.8.3"),
        f"PySide6 must stay pinned to 6.8.3 (got {version!r})",
    )


@harness.case("static.requirements", "L0", "infra", tags=("smoke",))
def check_requirements() -> None:
    required = [
        "numpy",
        "pandas",
        "pyarrow",
        "tifffile",
        "flowio",
        "pyqtgraph",
        "scipy",
    ]
    missing = []
    for name in required:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - report all missing packages
            missing.append(name)
    harness.check(not missing, f"missing required packages: {', '.join(missing)}")


@harness.case("static.compile", "L0", "infra", tags=("smoke",))
def check_compile() -> None:
    import py_compile

    output = harness.RUN_DIR / "pycache"
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    for path in sorted((harness.SRC_DIR / "smage").rglob("*.py")):
        relative = path.relative_to(harness.REPO_ROOT).as_posix()
        digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()
        try:
            py_compile.compile(
                str(path),
                cfile=str(output / f"{digest}.pyc"),
                doraise=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{relative}: {exc}")
    harness.check(not errors, "compile errors:\n" + "\n".join(errors[:20]))


@harness.case("static.import", "L0", "infra", tags=("smoke",))
def check_imports() -> None:
    harness.app()
    root = harness.SRC_DIR / "smage"
    names: set[str] = set()
    for init in sorted(root.rglob("__init__.py")):
        relative = init.parent.relative_to(harness.SRC_DIR)
        names.add(".".join(relative.parts))
    for page in sorted((root / "modules").glob("*/page.py")):
        names.add(".".join(page.relative_to(harness.SRC_DIR).with_suffix("").parts))
    names.add("smage.main")

    errors = []
    for name in sorted(names):
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    harness.check(not errors, "import errors:\n" + "\n".join(errors[:20]))


@harness.case("static.runners", "L0", "infra", tags=("smoke",))
def check_runners() -> None:
    """The external runner scripts exist where the extension registry expects."""
    from smage.extensions_registry import EXTENSION_DEFS

    errors = []
    for entry in EXTENSION_DEFS:
        runner = entry.get("runner")
        if not runner:
            continue
        path = harness.SRC_DIR / "smage" / "runners" / runner
        if not path.is_file():
            errors.append(
                f"{entry.get('id', '?')}: "
                f"{path.relative_to(harness.REPO_ROOT)} is missing"
            )
    harness.check(not errors, "; ".join(errors))


@harness.case("static.frozen", "L0", "infra", tags=("smoke",))
def check_frozen_helpers() -> None:
    """bundle_root/package_root switch from the source tree to _MEIPASS."""
    import sys as _sys

    from smage import frozen

    harness.eq(frozen.is_frozen(), False)
    harness.eq(frozen.bundle_root(), harness.SRC_DIR)
    harness.check(
        (frozen.package_root() / "runners" / "cellpose_runner.py").is_file(),
        "runner scripts resolve under package_root()",
    )

    had_frozen = hasattr(_sys, "frozen")
    had_meipass = hasattr(_sys, "_MEIPASS")
    old_frozen = getattr(_sys, "frozen", None)
    old_meipass = getattr(_sys, "_MEIPASS", None)
    try:
        _sys.frozen = True
        _sys._MEIPASS = str(harness.RUN_DIR)
        harness.check(frozen.is_frozen())
        harness.eq(frozen.bundle_root(), harness.RUN_DIR)
        harness.eq(frozen.package_root(), harness.RUN_DIR / "smage")
    finally:
        if had_frozen:
            _sys.frozen = old_frozen
        else:
            del _sys.frozen
        if had_meipass:
            _sys._MEIPASS = old_meipass
        else:
            del _sys._MEIPASS


@harness.case("static.selfcheck", "L0", "infra", tags=("fast",))
def check_self_check() -> None:
    """Every module imported at runtime (incl. lazy ones) resolves here."""
    from smage.selfcheck import run_self_check

    harness.eq(run_self_check(), [], "self-check reported problems")


@harness.case("static.case_ids", "L0", "infra", tags=("smoke",))
def check_case_ids() -> None:
    ids = []
    for path in sorted(harness.CASES_DIR.glob("t*.py")):
        text = path.read_text(encoding="utf-8")
        ids.extend(re.findall(r'@harness\.case\(\s*"([^"]+)"', text))
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    harness.check(not duplicates, f"duplicate case ids: {', '.join(duplicates)}")


@harness.case("static.module_coverage", "L0", "infra", tags=("smoke",))
def check_module_coverage() -> None:
    """Every module page must eventually have its own t05 script."""
    modules_dir = harness.SRC_DIR / "smage" / "modules"
    missing = []
    for folder in sorted(modules_dir.iterdir()):
        if not (folder / "page.py").is_file():
            continue
        if folder.name == "import_":
            # Data Import is covered by the import/project script.
            required = harness.CASES_DIR / "t03_import.py"
        else:
            required = harness.CASES_DIR / f"t05_mod_{folder.name}.py"
        if not required.is_file():
            missing.append(folder.name)
    harness.check(
        not missing,
        "module page(s) without a t05 script: " + ", ".join(missing),
    )


@harness.case("static.inventory", "L0", "infra", tags=("smoke",))
def check_inventory() -> None:
    """Create or compare the control inventory baseline."""
    import inventory

    current = inventory.collect()
    baseline_path = harness.BASELINE_DIR / "inventory.json"
    if not baseline_path.is_file():
        inventory.write(current, baseline_path)
        harness.note(f"created inventory baseline with {len(current)} controls")
        return

    baseline = inventory.load(baseline_path)
    added, removed = inventory.diff(current, baseline)
    if added:
        harness.note(
            f"{len(added)} new control(s) without a baseline entry: "
            + ", ".join(added[:8])
        )
    if removed:
        harness.note(
            f"{len(removed)} control(s) disappeared (update the baseline): "
            + ", ".join(removed[:8])
        )


@harness.case("static.path_guard", "L0", "infra", tags=("smoke",))
def check_path_guard() -> None:
    with harness.raises(RuntimeError):
        harness.guard(harness.REPO_ROOT / "src" / "smage" / "main.py")
    with harness.raises(RuntimeError):
        harness.guard(harness.REPO_ROOT / "data" / "scratch.txt")
    probe = harness.guard(harness.RUN_DIR / "guard_probe.txt")
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


@harness.case("static.workspace", "L0", "infra", tags=("smoke",))
def check_workspace() -> None:
    harness.ensure_work_dir()
    probe = harness.RUN_DIR / "workspace_probe.txt"
    probe.write_text("ok", encoding="utf-8")
    harness.eq(probe.read_text(encoding="utf-8"), "ok", "workspace is writable")
    probe.unlink()


@harness.case("static.fixtures", "L0", "infra", tags=("smoke",))
def check_fixtures() -> None:
    import fixtures_gen

    fixtures_gen.generate(harness.FIXTURES_DIR)
    required = [
        "fcs/synthetic_8ch.fcs",
        "fcs/synthetic_3ch.fcs",
        "tables/base.parquet",
        "tables/base.csv",
        "tables/edge.parquet",
        "images/multichannel.tif",
        "images/single.tif",
        "images/mask.tif",
        "broken/truncated.fcs",
        "broken/empty.csv",
        "broken/bad.parquet",
        "broken/table.txt",
    ]
    missing = [name for name in required if not (harness.FIXTURES_DIR / name).is_file()]
    harness.check(not missing, f"missing fixtures: {', '.join(missing)}")


if __name__ == "__main__":
    raise SystemExit(harness.main())
