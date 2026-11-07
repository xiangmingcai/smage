"""Runtime self-check for packaged builds (``Smage.exe --self-test``).

PyInstaller only follows static ``import`` statements.  Modules that are
reached through ``importlib`` (``data/registry.py``), and optional backends
imported by third-party packages at runtime, can therefore be missing from
a bundle even though the source tree runs fine.  This check imports every
module the application relies on and verifies the bundled data files, so a
broken build fails in CI instead of on a user's machine.
"""
from __future__ import annotations

import importlib
from datetime import datetime

# Imported by the frozen application.  Keep this list in sync with
# ``requirements.txt``; optional packages live in the second list.
REQUIRED_MODULES = (
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "pyqtgraph",
    "numpy",
    "pandas",
    "scipy",
    "pyarrow",
    "tifffile",
    "flowio",
    "sklearn",
    "umap",
    "scanpy",
    "igraph",
    "leidenalg",
    "louvain",
    "peacoqc",
    "polars",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_svg",
    "matplotlib.backends.backend_pdf",
)

OPTIONAL_MODULES = (
    "openTSNE",
)


def _check_import(name: str, optional: bool, problems: list[str]) -> None:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        if optional:
            return
        problems.append(f"import {name}: {type(exc).__name__}: {exc}")


def run_self_check() -> list[str]:
    """Return a list of problems; an empty list means the build is usable."""
    problems: list[str] = []

    for name in REQUIRED_MODULES:
        _check_import(name, optional=False, problems=problems)
    for name in OPTIONAL_MODULES:
        _check_import(name, optional=True, problems=problems)

    # The application package graph: this pulls in all 21 module pages.
    _check_import("smage.app.module_host", optional=False, problems=problems)

    # Lazy targets used by data/registry.py are invisible to PyInstaller's
    # static analysis, so import each one explicitly.
    try:
        from smage.data.registry import target_modules

        for name in target_modules():
            _check_import(name, optional=False, problems=problems)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"registry: {type(exc).__name__}: {exc}")

    # Bundled data files.
    from smage.extensions_registry import EXTENSION_DEFS
    from smage.frozen import package_root

    package = package_root()
    icon = package / "icons" / "import.svg"
    if not icon.is_file():
        problems.append(f"missing icon: {icon}")
    for entry in EXTENSION_DEFS:
        runner = entry.get("runner")
        if runner and not (package / "runners" / runner).is_file():
            problems.append(f"missing runner for {entry['id']}: {runner}")

    return problems


def write_report(problems: list[str]) -> str:
    """Write the self-check result to ``%APPDATA%\\Smage\\selfcheck.log``."""
    from smage.core.settings import _settings_dir

    folder = _settings_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "selfcheck.log"
    lines = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] self-check"]
    if problems:
        lines.extend(f"FAIL {problem}" for problem in problems)
    else:
        lines.append("OK")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
