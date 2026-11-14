"""Single entry point for the Smage regression suite.

Examples::

    python tests/regression/run_all.py --smoke
    python tests/regression/run_all.py --fast
    python tests/regression/run_all.py --module gating
    python tests/regression/run_all.py --case mod.gating.boolean.and_or_not
    python tests/regression/run_all.py --full --retries 1

Exit codes: 0 = all passed, 1 = failures, 2 = infrastructure problem.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REGRESSION_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = REGRESSION_DIR / "cases"
WORKSPACE_DEFAULT = Path(r"E:\smage_test\samge_batch_test")
LATEST_DIRNAME = "report"

TIMEOUT_DEFAULT = 900


# ---------------------------------------------------------------------------
# Script selection
# ---------------------------------------------------------------------------

def _all_scripts() -> list[Path]:
    return sorted(CASES_DIR.glob("t*.py"))


def _t01_scripts() -> list[Path]:
    return sorted(CASES_DIR.glob("t01_*.py"))


def _t04_scripts() -> list[Path]:
    return sorted(CASES_DIR.glob("t04_*.py"))


def _t06_scripts() -> list[Path]:
    return sorted(CASES_DIR.glob("t06_*.py"))


def _module_script(module: str) -> Path:
    if module in ("import", "import_"):
        # Data Import is covered by the import/project script, not t05.
        return CASES_DIR / "t03_import.py"
    return CASES_DIR / f"t05_mod_{module}.py"


def _changed_files() -> list[str]:
    """Files changed relative to HEAD plus untracked files."""
    files: list[str] = []
    for command in (
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "HEAD"],
        ["git", "-C", str(REPO_ROOT), "ls-files", "--others",
         "--exclude-standard"],
    ):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            files.extend(line.strip() for line in result.stdout.splitlines()
                         if line.strip())
    return sorted(set(files))


def _scripts_for_changes() -> list[Path]:
    """Pick the scripts most likely affected by the working-tree changes."""
    changed = _changed_files()
    if not changed:
        return [CASES_DIR / "t00_static.py",
                CASES_DIR / "t04_page_smoke.py"]

    selected = {CASES_DIR / "t00_static.py"}
    for relative in changed:
        path = relative.replace("\\", "/")
        if path.startswith("tests/regression/"):
            name = Path(path).name
            if name.startswith("t") and name.endswith(".py"):
                selected.add(CASES_DIR / name)
            continue
        if path.startswith("src/smage/modules/"):
            module = path.split("/")[3]
            script = _module_script(module)
            if script.is_file():
                selected.add(script)
            continue
        if path.startswith("src/smage/core/"):
            selected.update(_t01_scripts())
            continue
        if path.startswith("src/smage/data/"):
            selected.update(sorted(CASES_DIR.glob("t02_*.py")))
            continue
        if path.startswith(("src/smage/app/", "src/smage/widgets/",
                            "src/smage/main.py")):
            selected.update(sorted(CASES_DIR.glob("t04_*.py")))
            continue
        if path.startswith(("src/smage/project.py", "src/smage/importer.py",
                            "src/smage/dimensions.py")):
            selected.add(CASES_DIR / "t03_project.py")
            selected.add(CASES_DIR / "t03_import.py")
            continue
        selected.add(CASES_DIR / "t04_page_smoke.py")
    return [path for path in sorted(selected) if path.is_file()]


def scripts_for(args) -> list[Path]:
    """Return the scripts to run for the requested tier."""
    if args.changed:
        return _scripts_for_changes()
    if args.case:
        case_id = args.case
        if case_id.startswith("mod."):
            parts = case_id.split(".")
            if len(parts) < 2:
                raise SystemExit(f"invalid case id: {case_id}")
            script = _module_script(parts[1])
        elif case_id.startswith("pages."):
            script = CASES_DIR / "t04_page_smoke.py"
        elif case_id.startswith("static."):
            script = CASES_DIR / "t00_static.py"
        elif case_id.startswith("sys."):
            script = CASES_DIR / "t99_teardown.py"
        elif case_id.startswith("core."):
            script = None
            return _t01_scripts()
        elif case_id.startswith("shell."):
            script = None
            return _t04_scripts()
        elif case_id.startswith("e2e."):
            script = None
            return _t06_scripts()
        elif case_id.startswith("calc."):
            script = CASES_DIR / "t05_mod_calculation.py"
        elif case_id.startswith("import."):
            script = CASES_DIR / "t03_import.py"
        elif case_id.startswith("project."):
            script = CASES_DIR / "t03_project.py"
        elif case_id.startswith("data."):
            script = None
            return sorted(CASES_DIR.glob("t02_*.py"))
        else:
            return _all_scripts()
        if script is None or not script.is_file():
            raise SystemExit(f"no test script for case {case_id}")
        return [script]

    if args.module:
        script = _module_script(args.module)
        if not script.is_file():
            available = sorted(
                path.stem.removeprefix("t05_mod_") for path in CASES_DIR.glob("t05_mod_*.py")
            )
            raise SystemExit(
                f"no script for module {args.module!r}; available: {', '.join(available) or '(none)'}"
            )
        return ([CASES_DIR / "t00_static.py"] if not args.no_preflight else []) + [script]

    if args.tag:
        # Tags apply to cases, so every script must be considered; the
        # harness drops the scripts that contain none of the requested tags.
        selected = _all_scripts()
    elif args.smoke:
        selected = [CASES_DIR / "t00_static.py", *_t01_scripts()]
        selected.append(CASES_DIR / "t04_page_smoke.py")
        if (CASES_DIR / "t99_teardown.py").is_file():
            selected.append(CASES_DIR / "t99_teardown.py")
    elif args.full:
        selected = _all_scripts()
    else:  # --fast is the default
        selected = [
            path for path in _all_scripts()
            if not path.name.startswith("t06_")
        ]

    if args.no_preflight:
        selected = [path for path in selected if path.name != "t00_static.py"]
    return [path for path in selected if path.is_file()]


# ---------------------------------------------------------------------------
# Running one script
# ---------------------------------------------------------------------------

def _base_env(args, run_dir: Path, workspace: Path) -> dict:
    env = os.environ.copy()
    env["SMAGE_REPO"] = str(REPO_ROOT)
    env["SMAGE_TEST_ROOT"] = str(workspace)
    env["SMAGE_RUN_ID"] = run_dir.name
    env["APPDATA"] = str(run_dir / "_appdata")
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    # Child scripts write Unicode (em dashes, superscripts); force UTF-8 so the
    # parent can decode their output regardless of the Windows code page.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    if args.show:
        env["SMAGE_SHOW"] = "1"
        env.pop("QT_QPA_PLATFORM", None)
    else:
        env.pop("SMAGE_SHOW", None)
        env["QT_QPA_PLATFORM"] = "offscreen"
    if args.update_baseline:
        env["SMAGE_UPDATE_GOLDEN"] = "1"
    return env


def _run_script(script: Path, env: dict, run_dir: Path,
                timeout: int, only_cases=None) -> tuple:
    """Run one test script as a subprocess; return (code, stdout, stderr)."""
    script_env = env.copy()
    script_env.pop("SMAGE_ONLY_CASES", None)
    if only_cases:
        script_env.pop("SMAGE_ONLY_CASE", None)
        script_env["SMAGE_ONLY_CASES"] = ",".join(only_cases)
    # Isolate global settings/error logs per script so parallel runs cannot
    # race on settings.json.
    app_data = run_dir / "_appdata" / script.stem
    app_data.mkdir(parents=True, exist_ok=True)
    script_env["APPDATA"] = str(app_data)
    script_env["SMAGE_APPDATA"] = str(app_data)

    part = run_dir / "parts" / f"{script.stem}.json"
    if part.is_file():
        part.unlink()

    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{script.stem}.log"

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO_ROOT),
            env=script_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        errors = exc.stderr or ""
        if isinstance(errors, bytes):
            errors = errors.decode("utf-8", "replace")
        code = "timeout"
        stdout = output
        stderr = errors + f"\nTIMEOUT after {timeout}s"
    duration = time.perf_counter() - started
    log_path.write_text(
        f"$ {sys.executable} {script}\n"
        f"exit={code} duration={duration:.1f}s\n\n"
        f"--- stdout ---\n{stdout}\n\n--- stderr ---\n{stderr}\n",
        encoding="utf-8",
    )
    return code, stdout, stderr


def _load_part(run_dir: Path, script: Path) -> list[dict]:
    part = run_dir / "parts" / f"{script.stem}.json"
    if not part.is_file():
        return []
    try:
        return list(json.loads(part.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def _synthetic_record(case_id: str, status: str, message: str, script: str) -> dict:
    return {
        "case_id": case_id,
        "layer": "L0",
        "module": "infra",
        "tags": ["smoke"],
        "status": status,
        "duration_ms": 0,
        "message": message,
        "notes": [],
        "log_errors": [],
        "artifacts": [],
        "traceback": "",
        "script": script,
        "repro": f"python tests/regression/cases/{script}.py",
    }


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------

def _load_quarantine() -> set[str]:
    path = REGRESSION_DIR / "quarantine.txt"
    if not path.is_file():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            ids.add(text)
    return ids


# ---------------------------------------------------------------------------
# Reports and cleanup
# ---------------------------------------------------------------------------

def _write_reports(run_dir: Path, workspace: Path, records: list[dict],
                   meta: dict) -> None:
    counts = Counter(record["status"] for record in records)
    failures = [record for record in records if record["status"] in ("fail", "error")]
    quarantined = [record for record in records if record["status"] == "quarantined"]

    by_layer: dict[str, Counter] = defaultdict(Counter)
    by_module: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        by_layer[record["layer"]][record["status"]] += 1
        by_module[record["module"]][record["status"]] += 1

    lines = [
        f"# Smage regression — {meta['run_id']}",
        "",
        f"- Started: {meta['started']}",
        f"- Duration: {meta['duration']:.1f}s",
        f"- Python: {sys.version.split()[0]}",
        f"- Selected: {len(records)} cases",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status in ("pass", "fail", "skip", "quarantined"):
        if counts.get(status):
            lines.append(f"| {status} | {counts[status]} |")
    if counts.get("error"):
        lines.append(f"| error | {counts['error']} |")
    lines.append("")

    lines.extend(["## By layer", "", "| Layer | Pass | Fail | Skip |", "|---|---|---|---|"])
    for layer in sorted(by_layer):
        counter = by_layer[layer]
        lines.append(
            f"| {layer} | {counter.get('pass', 0)} | "
            f"{counter.get('fail', 0) + counter.get('error', 0)} | "
            f"{counter.get('skip', 0) + counter.get('quarantined', 0)} |"
        )
    lines.append("")

    lines.extend(["## By module", "", "| Module | Pass | Fail | Skip |", "|---|---|---|---|"])
    for module in sorted(by_module):
        counter = by_module[module]
        lines.append(
            f"| {module} | {counter.get('pass', 0)} | "
            f"{counter.get('fail', 0) + counter.get('error', 0)} | "
            f"{counter.get('skip', 0) + counter.get('quarantined', 0)} |"
        )
    lines.append("")

    if failures:
        lines.extend(["## Failures", ""])
        for record in failures:
            lines.append(f"### {record['case_id']}")
            lines.append("")
            lines.append(f"- Module: `{record['module']}`  Layer: `{record['layer']}`")
            lines.append(f"- Message: {record['message']}")
            if record.get("log_errors"):
                lines.append("- Log errors:")
                for error in record["log_errors"][:3]:
                    lines.append(f"  - `{error}`")
            lines.append(f"- Repro: `{record['repro']}`")
            lines.append("")
    else:
        lines.extend(["## Failures", "", "None.", ""])

    if quarantined:
        lines.extend(["## Quarantined", ""])
        for record in quarantined:
            lines.append(f"- `{record['case_id']}` — {record['message']}")
        lines.append("")

    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    failure_lines = [f"# Failures — {meta['run_id']}", ""]
    if not failures:
        failure_lines.append("None.")
    for record in failures:
        failure_lines.extend(
            [
                f"## {record['case_id']}",
                "",
                f"- status: **{record['status']}**",
                f"- module: `{record['module']}`",
                f"- message: {record['message']}",
                f"- log: `{run_dir / 'logs' / (record['script'] + '.log')}`",
                f"- repro: `{record['repro']}`",
                "",
            ]
        )
        if record.get("log_errors"):
            failure_lines.append("Log errors:")
            for error in record["log_errors"][:5]:
                failure_lines.append(f"- `{error}`")
            failure_lines.append("")
        if record.get("traceback"):
            failure_lines.append("```")
            failure_lines.append(record["traceback"].rstrip()[-6000:])
            failure_lines.append("```")
            failure_lines.append("")
    (run_dir / "failures.md").write_text("\n".join(failure_lines), encoding="utf-8")

    results = {
        "meta": meta,
        "counts": dict(counts),
        "records": records,
    }
    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    latest = workspace / LATEST_DIRNAME
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_dir / "results.json", latest / "latest.json")
    shutil.copy2(run_dir / "report.md", latest / "latest.md")
    shutil.copy2(run_dir / "failures.md", latest / "failures.md")


def _cleanup_cases(run_dir: Path, records: list[dict]) -> None:
    """Delete successful case projects; keep failed ones for investigation."""
    cases_root = run_dir / "cases"
    if not cases_root.is_dir():
        return
    failed = {
        record["case_id"] for record in records
        if record["status"] in ("fail", "error")
    }
    passed = {
        record["case_id"] for record in records
        if record["status"] in ("pass", "skip", "quarantined")
    }
    evidence = run_dir / "failures"
    evidence.mkdir(parents=True, exist_ok=True)
    for entry in list(cases_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in failed:
            target = evidence / entry.name
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(entry), str(target))
        elif entry.name in passed:
            shutil.rmtree(entry, ignore_errors=True)
        # Unknown folders are shared fixtures used by several cases: keep them.


def _prune_runs(workspace: Path, keep: int, current: Path) -> None:
    runs_root = workspace / "runs"
    if not runs_root.is_dir():
        return
    keep = max(0, int(keep))
    runs = sorted(
        path for path in runs_root.iterdir()
        if path.is_dir() and path != current
    )
    for old in runs[:-keep] if keep > 0 else runs:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # A Windows console may use a legacy code page; never let a status line
    # with a Unicode character abort the whole run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument("--smoke", action="store_true", help="fastest checks (<= 2 min)")
    tier.add_argument("--fast", action="store_true", help="daily iteration")
    tier.add_argument("--full", action="store_true", help="everything, including slow tests")
    tier.add_argument("--changed", action="store_true",
                      help="run the scripts affected by the current git changes")
    parser.add_argument("--module", help="run one module's t05 script")
    parser.add_argument("--case", help="run one case id")
    parser.add_argument("--tag", action="append", default=[], help="only cases with a tag")
    parser.add_argument("--retries", type=int, default=1, help="retries for failed cases")
    parser.add_argument("--keep-runs", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=TIMEOUT_DEFAULT)
    parser.add_argument("--jobs", type=int, default=0,
                        help="parallel scripts (0 = auto: 4 for fast/full, 1 otherwise)")
    parser.add_argument("--root", default=os.environ.get("SMAGE_TEST_ROOT") or str(WORKSPACE_DEFAULT))
    parser.add_argument("--show", action="store_true", help="run with a real window")
    parser.add_argument("--no-preflight", action="store_true", help="skip t00_static")
    parser.add_argument("--no-clean", action="store_true", help="keep successful case folders")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.root).resolve()
    if workspace == REPO_ROOT or REPO_ROOT in workspace.parents:
        print(
            "The test workspace must be outside the source repository.",
            file=sys.stderr,
        )
        return 2
    workspace.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = workspace / "runs" / run_id
    (run_dir / "parts").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "_appdata").mkdir(parents=True, exist_ok=True)

    env = _base_env(args, run_dir, workspace)
    if args.case:
        env["SMAGE_ONLY_CASE"] = args.case
    default_fast = not (
        args.smoke or args.full or args.module or args.case or args.tag
        or args.changed
    )
    if not args.tag and (args.fast or default_fast):
        env["SMAGE_EXCLUDE_TAGS"] = "slow,needs-extension,needs-real-data,e2e"
    else:
        env.pop("SMAGE_EXCLUDE_TAGS", None)
    if args.tag:
        env["SMAGE_TAGS"] = ",".join(args.tag)

    meta = {
        "run_id": run_id,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": 0.0,
        "repo": str(REPO_ROOT),
        "workspace": str(workspace),
        "python": sys.version.split()[0],
        "args": vars(args),
    }

    started = time.perf_counter()
    scripts = scripts_for(args)
    if not scripts:
        print("No test scripts selected.", file=sys.stderr)
        return 2

    if args.update_baseline:
        subprocess.run([sys.executable, str(REGRESSION_DIR / "inventory.py"), "--write"],
                       cwd=str(REPO_ROOT), check=False)

    # Fixtures are shared by every script; build them once before parallel runs.
    subprocess.run(
        [sys.executable, str(REGRESSION_DIR / "fixtures_gen.py"),
         "--root", str(workspace)],
        cwd=str(REPO_ROOT), check=False,
        capture_output=True, text=True,
    )

    jobs = args.jobs
    if jobs <= 0:
        jobs = 1 if (args.smoke or args.case or args.module) else min(4, os.cpu_count() or 1)
    jobs = max(1, min(jobs, len(scripts)))

    print(f"Smage regression: {len(scripts)} script(s), {jobs} job(s), run {run_id}")
    records: dict[str, dict] = {}

    def ingest(script, code, stdout, stderr) -> None:
        part_records = _load_part(run_dir, script)
        for record in part_records:
            records[record["case_id"]] = record

        if code == "timeout":
            records[f"script.timeout.{script.stem}"] = _synthetic_record(
                f"script.timeout.{script.stem}", "error",
                f"script timed out after {args.timeout}s", script.stem,
            )
        elif not part_records and code != 0:
            tail = (stderr or stdout).strip().splitlines()[-12:]
            records[f"script.crash.{script.stem}"] = _synthetic_record(
                f"script.crash.{script.stem}", "error",
                f"script exited {code} without results:\n" + "\n".join(tail),
                script.stem,
            )
        elif code not in (0, 1):
            records[f"script.exit.{script.stem}"] = _synthetic_record(
                f"script.exit.{script.stem}", "fail",
                f"script exited with code {code} after writing results", script.stem,
            )

        if args.verbose:
            print(f"\n===== {script.name} =====")
            print(stdout)
            if stderr:
                print(stderr, file=sys.stderr)
        else:
            for line in stdout.splitlines():
                if line.startswith(("[FAIL]", "SUMMARY", "[SKIP]")):
                    print(f"  {line}")

    if jobs == 1:
        for script in scripts:
            print(f"- {script.name}")
            code, stdout, stderr = _run_script(script, env, run_dir, args.timeout)
            ingest(script, code, stdout, stderr)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(_run_script, script, env, run_dir, args.timeout): script
                for script in scripts
            }
            for future in as_completed(futures):
                script = futures[future]
                try:
                    code, stdout, stderr = future.result()
                except Exception as exc:  # noqa: BLE001 - infrastructure failure
                    code, stdout, stderr = "crash", "", f"{type(exc).__name__}: {exc}"
                print(f"- {script.name}")
                ingest(script, code, stdout, stderr)

    # --- retries ---------------------------------------------------------
    if max(0, args.retries) > 0:
        failed = [record for record in records.values() if record["status"] in ("fail", "error")]
        by_script: dict[str, list[str]] = defaultdict(list)
        for record in failed:
            by_script[record.get("script", "")].append(record["case_id"])
        for stem, case_ids in by_script.items():
            script = CASES_DIR / f"{stem}.py"
            if not script.is_file():
                continue
            print(f"retry {stem}: {', '.join(case_ids)}")
            _run_script(script, env, run_dir, args.timeout, only_cases=case_ids)
            for record in _load_part(run_dir, script):
                if record["case_id"] not in case_ids:
                    continue
                if record["status"] == "pass":
                    first = records[record["case_id"]]
                    merged = dict(record)
                    merged["tags"] = sorted(set(first.get("tags", [])) | {"flaky"})
                    merged["message"] = "passed on retry (flaky)"
                    records[record["case_id"]] = merged
                elif records[record["case_id"]]["status"] not in ("fail", "error"):
                    records[record["case_id"]] = record

    # --- quarantine ------------------------------------------------------
    quarantine = _load_quarantine()
    for record in records.values():
        if record["case_id"] in quarantine and record["status"] in ("fail", "error"):
            record["message"] = "quarantined: " + record["message"]
            record["status"] = "quarantined"

    meta["duration"] = round(time.perf_counter() - started, 2)
    ordered = sorted(records.values(), key=lambda item: item["case_id"])
    _write_reports(run_dir, workspace, ordered, meta)
    if not args.no_clean:
        _cleanup_cases(run_dir, ordered)
    _prune_runs(workspace, args.keep_runs, run_dir)

    counts = Counter(record["status"] for record in ordered)
    failed = counts.get("fail", 0) + counts.get("error", 0)
    print("")
    print(
        f"RESULT: {counts.get('pass', 0)} passed, {failed} failed, "
        f"{counts.get('skip', 0)} skipped, {counts.get('quarantined', 0)} quarantined"
    )
    print(f"REPORT: {run_dir / 'report.md'}")
    if failed:
        print("FAILURES:")
        for record in ordered:
            if record["status"] in ("fail", "error"):
                print(f"  - {record['case_id']}: {record['message']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
