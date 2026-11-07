"""Build the Windows onedir bundle, then run its runtime self-check.

Usage::

    .venv\\Scripts\\python.exe packaging/build_win.py

The freeze is only considered successful when ``Smage.exe --self-test``
exits 0.  Its report is written to ``%APPDATA%\\Smage\\selfcheck.log``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = Path(__file__).with_name("smage.spec")
EXE = ROOT / "dist" / "Smage" / "Smage.exe"
REPORT = Path(os.environ.get("APPDATA") or Path.home()) / "Smage" / "selfcheck.log"


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=ROOT,
        check=True,
    )
    if not EXE.is_file():
        print(f"BUILD FAILED: {EXE} was not produced")
        return 1
    print(f"built: {EXE} ({EXE.stat().st_size / 1_048_576:.1f} MB)")
    print("running self-test…")
    result = subprocess.run([str(EXE), "--self-test"], timeout=900)
    if result.returncode != 0:
        print(f"SELF-TEST FAILED (exit {result.returncode})")
        if REPORT.is_file():
            print(REPORT.read_text(encoding="utf-8", errors="replace"))
        return result.returncode
    print("SELF-TEST OK")

    print("running frozen e2e suite…")
    result = subprocess.run(
        [str(EXE), "--run-tests", "--tag", "e2e"], timeout=3600
    )
    latest = Path(os.environ.get("TEMP") or Path.home()) / "SmageTests" / "frozen-latest.md"
    if result.returncode != 0:
        print(f"FROZEN E2E FAILED (exit {result.returncode})")
        if latest.is_file():
            print(latest.read_text(encoding="utf-8", errors="replace"))
        return result.returncode
    print("FROZEN E2E OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
