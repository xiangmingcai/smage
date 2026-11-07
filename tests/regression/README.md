# Smage regression suite

Reusable, fast, offscreen regression tests for Smage. Run it after every
change instead of testing the app by hand.

The full design is in [`TEST_PLAN.md`](../../TEST_PLAN.md).

## Quick start

```bash
# fastest check after a small edit (<= 2 minutes)
python tests/regression/run_all.py --smoke

# daily iteration (5-8 minutes)
python tests/regression/run_all.py --fast

# before a release
python tests/regression/run_all.py --full

# parallel workers (auto: 4 for --fast/--full, 1 otherwise)
python tests/regression/run_all.py --full --jobs 8

# one module or one case
python tests/regression/run_all.py --module gating
python tests/regression/run_all.py --case mod.gating.boolean.and_or_not

# only the scripts affected by the current git changes
python tests/regression/run_all.py --changed
```

Requirements: Python 3.11 and the project virtual environment (PySide6 is
pinned to 6.8.3). Tests run with `QT_QPA_PLATFORM=offscreen` and never open a
visible window unless `--show` is passed.

## Where things live

| Path | Purpose |
|---|---|
| `tests/regression/` | test code (committed) |
| `tests/regression/cases/` | one script per layer/module |
| `tests/regression/baseline/` | small text baselines (committed) |
| `E:\smage_test\samge_batch_test\_fixtures\` | generated fixtures (disposable) |
| `E:\smage_test\samge_batch_test\runs\<run-id>\` | results, logs, screenshots |
| `E:\smage_test\samge_batch_test\runs\<run-id>\failures\` | kept projects for failed cases |

Successful case projects are deleted automatically. `--keep-runs N` controls
how many runs are retained (default 10). Use `--root` or `SMAGE_TEST_ROOT` to
move the workspace. Scripts run in parallel (4 workers by default for
`--fast`/`--full`); each script gets its own `APPDATA`, so global settings
cannot race. Use `--jobs 1` when debugging a single failure.

## Results

- `runs/<id>/results.json` — machine-readable result for every case.
- `runs/<id>/report.md` — summary by layer and module.
- `runs/<id>/failures.md` — failure details, log errors and repro commands.
- `report/latest.md` and `report/latest.json` — always the latest run.
- Exit code: `0` passed, `1` failures, `2` infrastructure problem.

Every case also fails when its project log contains `[ERROR]` or a traceback,
even if the functional assertions passed. This catches silent regressions.

## Case ids and tags

Stable ids: `mod.<module>.<area>.<detail>`, `pages.open.<module>`,
`static.<check>`, `sys.<check>`, `e2e.<scenario>`.

Tags select or exclude groups:

```bash
python tests/regression/run_all.py --tag slow
python tests/regression/run_all.py --fast      # excludes slow, needs-extension, needs-real-data
```

Use `harness.skip("reason")` when an optional dependency is missing; the case
is reported as `skip`, not `fail`.

## Golden files

`harness.golden("name", value)` compares a deterministic result with
`baseline/golden/<name>.json`. Rebuild the baselines deliberately with:

```bash
python tests/regression/run_all.py --update-baseline --full
```

The control inventory (`baseline/inventory.json`) is rebuilt by the same flag.

## Adding a test

1. Add a function in the right script (or create `cases/t05_mod_<module>.py`).
2. Register it:

```python
@harness.case("mod.gating.rectangle.draw", "L4", "gating", tags=("fast",))
def draw_rectangle() -> None:
    ...
    harness.eq(page._gating_gates(page), [], "starts empty")
```

3. Use `harness.new_project(...)` for a clean project, `harness.new_window()`
   for a `MainWindow`, and `harness.open_project(window, project)`.
4. Assert both in-memory state and on-disk artifacts. Call
   `harness.screenshot(widget, "name")` for evidence.
5. Prefer `harness.pump(ms)` / `harness.wait_for(...)` over `sleep`.

When a new module page appears, add `cases/t05_mod_<module>.py`; the
`static.module_coverage` check reports modules without a script.

## Coverage guard

`inventory.py` extracts buttons, menus, checkboxes and other operable controls
from the source. `t00_static` compares the live inventory with
`baseline/inventory.json` and reports new or removed controls.

```bash
python tests/regression/inventory.py           # inspect
python tests/regression/inventory.py --write   # update the baseline deliberately
```

## Dialog handling

Modal dialogs are replaced inside the module namespace:

```python
from smage.app import project_io
with harness.patch_dialogs(project_io, directory=str(tmp), text="demo"):
    window._new_project()
```

## Troubleshooting

- **A case hangs** — run it alone with `--case <id>` and `--show`.
- **Screenshots are empty** — the page may not have been laid out; call
  `harness.pump(300)` before `screenshot`.
- **Flaky UI timing** — the runner retries failed cases once by default and
  marks them `flaky`. Known-unstable cases go in `quarantine.txt`.
- **Segfault after results are printed** — reported as `script.exit.<name>`;
  this is a real teardown bug worth fixing, not a test failure to ignore.
