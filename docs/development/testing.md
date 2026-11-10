# Regression tests

The regression suite lives in `tests/regression` and is the safety net for
every change. It never writes to your own projects: it generates fixtures in
a test workspace (`E:\smage_test\samge_batch_test` by default, configurable
with `SMAGE_TEST_ROOT`).

## Tiers

```bat
.venv\Scripts\python.exe tests\regression\run_all.py --smoke
.venv\Scripts\python.exe tests\regression\run_all.py --fast
.venv\Scripts\python.exe tests\regression\run_all.py --full
```

| Tier | What it covers |
|---|---|
| `--smoke` | Static checks and the smallest end-to-end paths |
| `--fast` | All module-level cases that do not need generated data |
| `--full` | Everything, including e2e and packaging checks |

## Selecting cases

```bat
run_all.py --module gating          :: one module
run_all.py --case mod.gating.not    :: one case
run_all.py --tag e2e                :: one tag
run_all.py --changed                :: cases related to changed files
```

Reports (Markdown + JSON) are written under the test workspace in
`runs\<timestamp>\`.

## Adding a case

Cases live in `tests/regression/cases/tXX_*.py` and are registered with the
`@harness.case(id, tier, module)` decorator. Helpers in
`tests/regression/harness.py` create projects, open windows and pump the Qt
event loop. Keep cases deterministic and small; use the golden-file helper for
anything whose exact output matters.

## Frozen build

The suite is bundled into the packaged application:

```bat
Smage.exe --run-tests --tag e2e
```

Results go to `%TEMP%\SmageTests\frozen-latest.md`. `packaging/build_win.py`
runs this automatically after building.
