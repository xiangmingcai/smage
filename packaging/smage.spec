# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a Windows onedir build.

Run from the repository root:

    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean packaging/smage.spec

The dynamic imports below are the ones PyInstaller cannot see by itself:
``smage`` because ``data/registry.py`` loads modules through importlib, and
the scientific stack because several packages import their backends lazily.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
# The project uses a src layout and is not installed into site-packages, so
# collect_submodules can only see it after this path is added.
sys.path.insert(0, os.path.join(ROOT, "src"))

datas = [
    (os.path.join(ROOT, "src", "smage", "icons"), "smage/icons"),
    (os.path.join(ROOT, "src", "smage", "runners"), "smage/runners"),
]

# Ship the regression suite so a frozen build can test itself
# (`Smage.exe --run-tests`).  Bytecode caches are not needed.
_test_root = os.path.join(ROOT, "tests", "regression")
for _dir, _dirs, _files in os.walk(_test_root):
    _dirs[:] = [name for name in _dirs if name != "__pycache__"]
    for _name in _files:
        if _name.endswith((".pyc", ".pyo")):
            continue
        _rel = os.path.relpath(_dir, _test_root)
        _dest = "tests/regression" if _rel == "." else os.path.join("tests/regression", _rel)
        datas.append((os.path.join(_dir, _name), _dest))

# Several scientific packages call importlib.metadata.version() at import
# time (scikit-learn is the first one that fails without this).
for _dist in (
    "scikit-learn", "scanpy", "anndata", "umap-learn", "numba", "llvmlite",
    "pynndescent", "igraph", "leidenalg", "louvain", "peacoqc-rs", "polars",
    "numpy", "pandas", "scipy", "pyarrow", "tifffile", "FlowIO",
    "pyqtgraph", "matplotlib", "openTSNE",
):
    try:
        datas += copy_metadata(_dist)
    except Exception:
        pass

# Static analysis cannot follow importlib-based loading.
hiddenimports = []
for package in ("smage", "numba", "llvmlite", "pynndescent", "umap", "scanpy"):
    hiddenimports += collect_submodules(package)
hiddenimports += [
    "peacoqc",
    # The bundled regression suite drives a right-click through QTest; it is
    # only imported by test code, which PyInstaller does not analyse.
    "PySide6.QtTest",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_svg",
    "matplotlib.backends.backend_pdf",
]

# Smage uses only QtCore/QtGui/QtWidgets/QtSvg, plus QtTest for the bundled
# regression suite (see hiddenimports).  Dropping the rest keeps the bundle
# several hundred MB smaller; verify with ``Smage.exe --self-test`` and the
# smoke regression after changing this list.
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtWebSockets",
    "tkinter",
]

a = Analysis(
    [os.path.join(ROOT, "src", "smage", "main.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Smage",
    icon=os.path.join(ROOT, "src", "smage", "icons", "logo.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Smage",
)
