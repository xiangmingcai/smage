"""L1: the shared background subprocess worker."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402


@harness.case("core.workers.run_chain", "L1", "core", tags=("fast",))
def run_chain_runs_commands_in_order() -> None:
    """A chain runs every command and stops at the first failure."""
    from smage.data.workers import _ProcessWorker

    worker = _ProcessWorker()

    def collect(key):
        lines = []
        code = None
        for _ in range(240):
            for event in worker.poll():
                if event[0] != key:
                    continue
                if event[1] == "line":
                    lines.append(event[2])
                elif event[1] == "done":
                    code = event[2]
            if code is not None:
                break
            harness.pump(25)
        return lines, code

    try:
        worker.run_chain(("ok",), [
            [sys.executable, "-c", "print('first')"],
            [sys.executable, "-c", "print('second')"],
        ])
        lines, code = collect(("ok",))
        harness.eq(code, 0, "the chain succeeds")
        harness.eq(lines, ["first", "second"])

        worker.run_chain(("fail",), [
            [sys.executable, "-c", "print('one'); raise SystemExit(3)"],
            [sys.executable, "-c", "print('two')"],
        ])
        lines, code = collect(("fail",))
        harness.eq(code, 3, "the failing command's exit code is returned")
        harness.check("one" in lines, f"output before the failure: {lines}")
        harness.check(
            "two" not in lines,
            f"the chain stops after the failure: {lines}",
        )
    finally:
        worker.stop()


if __name__ == "__main__":
    raise SystemExit(harness.main())
