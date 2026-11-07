"""Extract an inventory of operable controls from the Smage source.

The inventory is a coverage guard: when a new button, menu entry or other
interactive control appears without a test, the suite reports it instead of
silently missing it.

The key is stable across line moves: it uses the source file, the enclosing
function/class, the control kind, its label and an occurrence counter.

Usage::

    python tests/regression/inventory.py            # print the inventory
    python tests/regression/inventory.py --write    # update the baseline
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path

REGRESSION_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("SMAGE_REPO") or REGRESSION_DIR.parents[1]).resolve()
SRC = REPO_ROOT / "src" / "smage"
BASELINE = REGRESSION_DIR / "baseline" / "inventory.json"

CONTROL_CLASSES = {
    "QPushButton": "button",
    "QToolButton": "button",
    "QCheckBox": "checkbox",
    "QRadioButton": "radio",
    "QComboBox": "combo",
    "QSpinBox": "spin",
    "QDoubleSpinBox": "spin",
    "QLineEdit": "lineedit",
    "QTextEdit": "text",
    "QPlainTextEdit": "text",
    "QListWidget": "list",
    "QTableWidget": "table",
    "QTreeWidget": "tree",
    "QTabWidget": "tabs",
    "QSlider": "slider",
    "QSplitter": "splitter",
}

CONTROL_METHODS = {
    "addAction": "action",
    "addMenu": "menu",
    "addTab": "tab",
}


def scan_files() -> list[Path]:
    """Every source file whose controls belong to the user-visible surface."""
    files: list[Path] = []
    files.extend(sorted((SRC / "modules").glob("*/page.py")))
    files.extend(sorted((SRC / "app").glob("*.py")))
    files.append(SRC / "main.py")
    files.extend(sorted((SRC / "widgets").glob("*.py")))
    return [path for path in files if path.is_file()]


def _first_string(node: ast.Call) -> str | None:
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            value = argument.value.strip()
            return value or None
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, relative: str):
        self.relative = relative
        self.stack: list[str] = []
        self.counts: dict[str, int] = {}
        self.items: list[dict] = []

    def visit_FunctionDef(self, node):  # noqa: N802 - ast API
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):  # noqa: N802 - ast API
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node):  # noqa: N802 - ast API
        function = node.func
        kind = None
        if isinstance(function, ast.Name) and function.id in CONTROL_CLASSES:
            kind = CONTROL_CLASSES[function.id]
        elif isinstance(function, ast.Attribute) and function.attr in CONTROL_METHODS:
            kind = CONTROL_METHODS[function.attr]
        if kind is not None:
            qualifier = ".".join(self.stack) or "<module>"
            label = _first_string(node) or "<dynamic>"
            base = f"{self.relative}|{qualifier}|{kind}|{label}"
            occurrence = self.counts.get(base, 0)
            self.counts[base] = occurrence + 1
            self.items.append(
                {
                    "key": f"{base}|{occurrence}",
                    "file": self.relative,
                    "function": qualifier,
                    "kind": kind,
                    "label": label,
                    "line": node.lineno,
                }
            )
        self.generic_visit(node)


def collect() -> list[dict]:
    """Return every control found in the source, sorted by key."""
    items: list[dict] = []
    for path in scan_files():
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError):
            continue
        visitor = _Visitor(path.relative_to(REPO_ROOT).as_posix())
        visitor.visit(tree)
        items.extend(visitor.items)
    return sorted(items, key=lambda item: item["key"])


def load(path: Path = BASELINE) -> list[dict]:
    if not Path(path).is_file():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("items", []))


def write(items: list[dict], path: Path = BASELINE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "items": items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def diff(current: list[dict], baseline: list[dict]) -> tuple[list[str], list[str]]:
    """Return (added, removed) keys."""
    current_keys = {item["key"] for item in current}
    baseline_keys = {item["key"] for item in baseline}
    return sorted(current_keys - baseline_keys), sorted(baseline_keys - current_keys)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the baseline")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    items = collect()
    if args.write:
        write(items)
        print(f"wrote {len(items)} controls to {BASELINE}")
        return 0
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
    else:
        for item in items:
            print(f"{item['key']}")
        print(f"{len(items)} controls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
