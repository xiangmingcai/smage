"""Capture the documentation screenshots from synthetic test projects.

Usage::

    .venv\\Scripts\\python.exe docs\\tools\\make_screenshots.py

The images are written to ``docs/assets/screenshots/`` and are used by the
MkDocs site. They are built from the regression fixtures (synthetic data),
not from real experiments; replace them with production screenshots whenever
better ones are available (see TODO.md).

The script uses the regression harness, which sets the Qt platform to
"offscreen" and redirects ``%APPDATA%`` into a temporary folder, so running it
does not touch the user's settings or projects.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "regression"))
sys.path.insert(0, str(ROOT / "tests" / "regression" / "cases"))

import harness  # noqa: E402  (must come first: sets up Qt and APPDATA)

OUT = ROOT / "docs" / "assets" / "screenshots"


def save(widget, name: str) -> None:
    harness.pump(600)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    if widget.grab().save(str(path)):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KiB)")
    else:
        print(f"FAILED to save {path}")


def _tabular_project():
    """A small cytometry project with compensation, gating, feature and figure."""
    import numpy as np
    import pandas as pd

    project = harness.new_project("docs_shots", name="case", rows=1200)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    # A class column for Feature Down (a mask label would look like this).
    frame["group"] = (np.arange(len(frame)) % 4 + 1).astype("int32")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns[parquet.name] = list(frame.columns)
    project.save_columns(columns)
    path = str(parquet)

    x_lo, x_hi = float(frame["FL1-A"].quantile(0.01)), float(frame["FL1-A"].quantile(0.99))
    y_lo, y_hi = float(frame["FL2-A"].quantile(0.01)), float(frame["FL2-A"].quantile(0.99))
    span_x, span_y = max(x_hi - x_lo, 1.0), max(y_hi - y_lo, 1.0)
    limits = {
        "xmin": x_lo - 0.05 * span_x, "xmax": x_hi + 0.05 * span_x,
        "ymin": y_lo - 0.05 * span_y, "ymax": y_hi + 0.05 * span_y,
    }

    figure = harness.task_def("figure", task_id="figure-docs", name="Figure")
    feature = harness.task_def(
        "feature", task_id="feature-docs", name="Feature Down",
        params={
            "feature": "group", "files": [path], "ignore_values": [],
            "rules": [
                {"method": "count", "name": "Count"},
                {"source": "FL1-A", "method": "mean", "name": "FL1_mean"},
                {"source": "FL2-A", "method": "mean", "name": "FL2_mean"},
            ],
        },
    )
    gating = harness.task_def(
        "gating", task_id="gating-docs", name="Gating",
        params={
            "preview": {
                "type": "scatter", "file": path, "file_name": "case.fcs",
                "x": "FL1-A", "y": "FL2-A",
                "color_mode": "density", "palette": "viridis",
                "point_size": 4, "fixed_point_size": False,
                "show_gate_labels": True, "label_show_percent": True,
            },
        },
        children=[feature, figure],
    )
    compensation = harness.task_def(
        "compensation", task_id="comp-docs", name="Compensation",
        params={
            "files": [path], "params": ["FL1-A", "FL2-A"],
            "matrix": [[100, 3], [0, 100]],
        },
        children=[gating],
    )
    import_task = project.workflow["tasks"][0]
    import_task["children"] = [compensation]
    project.save()

    # Gate tree: a parent rectangle and a child gate, in real data units.
    gates = [
        {
            "id": "gatingparent", "name": "Lymphocytes", "type": "rect",
            "x": "FL1-A", "y": "FL2-A", "parent": None, "plot_type": "scatter",
            "pos": [x_lo + 0.15 * span_x, y_lo + 0.10 * span_y],
            "size": [0.55 * span_x, 0.65 * span_y], **limits,
        },
        {
            "id": "gatingchild", "name": "FL1-high", "type": "rect",
            "x": "FL1-A", "y": "FL2-A", "parent": "gatingparent",
            "plot_type": "scatter",
            "pos": [x_lo + 0.45 * span_x, y_lo + 0.25 * span_y],
            "size": [0.22 * span_x, 0.35 * span_y], **limits,
        },
    ]
    store = project.path / "Module" / "gating-docs"
    store.mkdir(parents=True, exist_ok=True)
    (store / "gates.json").write_text(
        json.dumps({"files": [path], "gates": gates, "per_file": {}}),
        encoding="utf-8",
    )
    return project, import_task


def _image_project():
    """A synthetic single-channel image for the segmentation screenshot."""
    import _e2e_helpers as e2e  # noqa: E402

    task = harness.task_def("segmentation", task_id="seg-docs", name="Segmentation")
    project, parquet = e2e.image_project(
        "docs_image", tasks=[task], size=384, squares=((40, 120), (150, 260)),
    )
    task["params"]["files"] = [parquet]
    project.save()
    return project, task


def main() -> int:
    from smage.modules.figure import page as figure_page

    window = harness.new_window()
    try:
        project, import_task = _tabular_project()
        harness.open_project(window, project)
        save(window, "workflow")

        window._open_module(import_task["id"], "import", "Data Import")
        save(window.tabs.currentWidget(), "import")
        window._close_module_page(window.tabs.currentWidget())
        harness.pump(150)

        window._open_module("gating-docs", "gating", "Gating")
        save(window.tabs.currentWidget(), "gating")
        window._close_module_page(window.tabs.currentWidget())
        harness.pump(150)

        window._open_module("feature-docs", "feature", "Feature Down")
        save(window.tabs.currentWidget(), "feature-down")
        window._close_module_page(window.tabs.currentWidget())
        harness.pump(150)

        window._open_module("figure-docs", "figure", "Figure")
        harness.pump(400)
        page = window.tabs.currentWidget()
        figure_page._figure_add_plot(window._ctx, page, "scatter")
        save(page, "figure")
        window._close_module_page(page)
    finally:
        harness.close_window(window)

    # Segmentation lives in an image project; best effort, keep the rest if
    # it fails (for example when the image index needs extra fixtures).
    try:
        window = harness.new_window()
        try:
            project, task = _image_project()
            harness.open_project(window, project)
            window._open_module(task["id"], "segmentation", "Segmentation")
            harness.pump(1200)
            save(window.tabs.currentWidget(), "segmentation")
        finally:
            harness.close_window(window)
    except Exception as error:  # noqa: BLE001 - screenshots must not fail the run
        print(f"segmentation screenshot skipped: {error!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
