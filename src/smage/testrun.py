"""Run the bundled regression suite from a packaged build.

``Smage.exe --run-tests`` launches one child process per case script with
``--run-script``.  Each child writes its results to a JSON part file (a
windowed exe has no stdout), and this module aggregates them into
``frozen-report.json`` / ``frozen-report.md`` next to the run directory.

The suite is shipped inside the bundle (``tests/regression``) so a release
build can verify its own business flows on a machine with no Python.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from smage.frozen import bundle_root, is_frozen

# Static checks inspect the source tree; they are meaningless in a bundle.
_SKIP_SCRIPTS = {"t00_static.py"}


def regression_dir() -> Path:
    """The regression suite: inside the bundle, or in the source checkout."""
    bundled = bundle_root() / "tests" / "regression"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[2] / "tests" / "regression"


def _self_command(*args: str) -> list[str]:
    """Command that re-enters this application with extra arguments."""
    if is_frozen():
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve().with_name("main.py")), *args]


def _default_workspace() -> Path:
    return Path(os.environ.get("TEMP") or Path.home()) / "SmageTests"


def run_tests(tags: tuple[str, ...] = (), case: str | None = None,
              root: str | None = None) -> int:
    """Run selected cases and return 0 only when every one passed."""
    regression = regression_dir()
    cases_dir = regression / "cases"
    if not cases_dir.is_dir():
        print(f"test suite not found: {cases_dir}")
        return 1

    workspace = Path(root or os.environ.get("SMAGE_TEST_ROOT") or _default_workspace())
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = workspace / "runs" / run_id
    parts_dir = run_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "SMAGE_TEST_ROOT": str(workspace),
            "SMAGE_RUN_ID": run_id,
        }
    )
    if tags:
        env["SMAGE_TAGS"] = ",".join(tags)
    if case:
        env["SMAGE_ONLY_CASE"] = case

    scripts = sorted(
        path for path in cases_dir.glob("t*.py") if path.name not in _SKIP_SCRIPTS
    )

    records: list[dict] = []
    for script in scripts:
        env["SMAGE_PART_NAME"] = script.stem
        part = parts_dir / f"{script.stem}.json"
        if part.is_file():
            part.unlink()
        try:
            result = subprocess.run(
                _self_command("--run-script", str(script)),
                env=env,
                timeout=1800,
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            exit_code = -1
        if part.is_file():
            try:
                records.extend(json.loads(part.read_text(encoding="utf-8")))
            except ValueError:
                pass
        elif exit_code != 0:
            records.append(
                {
                    "case_id": f"script.{script.stem}",
                    "layer": "?",
                    "module": script.stem,
                    "status": "fail",
                    "message": f"script exited {exit_code} without results",
                    "script": script.stem,
                }
            )

    passed = sum(1 for item in records if item.get("status") == "pass")
    failed = [item for item in records if item.get("status") in ("fail", "error")]
    skipped = sum(1 for item in records if item.get("status") == "skip")

    report = {
        "run_id": run_id,
        "frozen": is_frozen(),
        "tags": list(tags),
        "case": case,
        "workspace": str(workspace),
        "passed": passed,
        "failed": len(failed),
        "skipped": skipped,
        "records": records,
    }
    (run_dir / "frozen-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        f"# Frozen test run {run_id}",
        "",
        f"- tests: {passed} passed, {len(failed)} failed, {skipped} skipped",
        f"- workspace: `{workspace}`",
        "",
    ]
    for item in failed:
        lines.append(f"- **{item.get('case_id')}**: {item.get('message', '')}")
    report_text = "\n".join(lines) + "\n"
    (run_dir / "frozen-report.md").write_text(report_text, encoding="utf-8")
    # A fixed path so a windowed parent (no stdout) can show the last result.
    (workspace / "frozen-latest.md").write_text(report_text, encoding="utf-8")

    print(f"FROZEN TESTS: {passed} passed, {len(failed)} failed, {skipped} skipped")
    print(f"report: {run_dir / 'frozen-report.md'}")
    for item in failed:
        print(f"  FAIL {item.get('case_id')}: {item.get('message', '')}")
    if not records:
        print("no cases selected")
        return 1
    return 0 if not failed else 1
