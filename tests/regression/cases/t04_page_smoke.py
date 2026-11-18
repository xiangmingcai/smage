"""L4 smoke: open and close every module page once.

This is the fastest way to catch import errors, Qt-parent mistakes and pages
that fail to build after a refactor.  Deep per-module behaviour lives in the
``t05_mod_*.py`` scripts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

TYPES = [
    "gating",
    "figure",
    "heatmap",
    "scale",
    "calculation",
    "rename",
    "unmix",
    "segmentation",
    "compensation",
    "illumination",
    "feature",
    "feature_up",
    "gate2col",
    "subset",
    "subsample",
    "dimred",
    "cluster",
    "region",
    "region_size",
    "data_clean",
    "batch_align",
    "export",
]

_STATE: dict = {}


def _setup() -> dict:
    """Create one shared project and window for all page cases."""
    if "window" in _STATE:
        return _STATE

    tasks = []
    ids = {}
    for index, task_type in enumerate(TYPES):
        task_id = f"{task_type}-smoke{index:03d}"
        ids[task_type] = task_id
        tasks.append(harness.task_def(task_type, task_id=task_id, name=task_type.title()))

    project = harness.new_project(
        "pages_smoke",
        name="pages_smoke",
        rows=200,
        tasks=tasks,
        shared=True,
    )
    window = harness.new_window()
    harness.open_project(window, project)
    _STATE.update(window=window, project=project, ids=ids)
    return _STATE


def _open_page(task_type: str) -> None:
    state = _setup()
    window = state["window"]
    task_id = state["ids"][task_type]

    window._open_module(task_id, task_type, task_type.title())
    harness.pump(500)
    page = window.tabs.currentWidget()
    harness.eq(
        page.property("task_id"),
        task_id,
        f"{task_type} page did not become the current tab",
    )
    harness.screenshot(page, f"page_{task_type}")
    window._close_module_page(page)
    harness.pump(150)
    harness.eq(window.tabs.count(), 1, f"{task_type} page did not close")


for _type in TYPES:
    harness.case(
        f"pages.open.{_type}",
        "L4",
        _type,
        tags=("smoke",),
    )(lambda task_type=_type: _open_page(task_type))


@harness.case("pages.open.import", "L4", "import", tags=("smoke",))
def open_import_page() -> None:
    state = _setup()
    window = state["window"]
    import_task = state["project"].workflow["tasks"][0]

    window._open_module(import_task["id"], "import", "Data Import")
    harness.pump(500)
    page = window.tabs.currentWidget()
    harness.eq(
        page.property("task_id"),
        import_task["id"],
        "Data Import page did not become the current tab",
    )
    harness.screenshot(page, "page_import")
    window._close_module_page(page)
    harness.pump(150)
    harness.eq(window.tabs.count(), 1, "Data Import page did not close")


@harness.case("pages.tooltips_light", "L4", "shell", tags=("fast",))
def tooltips_keep_light_palette() -> None:
    """Every visible tooltip in the module pages uses a light background."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QToolTip, QWidget

    state = _setup()
    window = state["window"]
    checked = 0
    for task_type in TYPES:
        task_id = state["ids"][task_type]
        window._open_module(task_id, task_type, task_type.title())
        harness.pump(350)
        page = window.tabs.currentWidget()
        try:
            widgets = [
                widget for widget in page.findChildren(QWidget)
                if widget.toolTip() and widget.isVisible()
            ]
            for widget in widgets:
                QToolTip.showText(
                    QPoint(10, 10), widget.toolTip(), widget
                )
                QApplication.processEvents()
                tip = next(
                    (
                        child for child in QApplication.allWidgets()
                        if child.metaObject().className() == "QTipLabel"
                    ),
                    None,
                )
                harness.check(
                    tip is not None,
                    f"{task_type}: tooltip not shown for "
                    f"{widget.toolTip()!r}",
                )
                if tip is not None:
                    color = QColor(
                        tip.palette().color(QPalette.ColorRole.Window)
                    )
                    harness.check(
                        color.lightness() >= 200,
                        f"{task_type}: dark tooltip {color.name()} for "
                        f"{widget.toolTip()!r}",
                    )
                checked += 1
                QToolTip.hideText()
                QApplication.processEvents()
        finally:
            window._close_module_page(page)
            harness.pump(100)
    harness.check(
        checked >= 20, f"only {checked} tooltips were checked"
    )


if __name__ == "__main__":
    raise SystemExit(harness.main())
