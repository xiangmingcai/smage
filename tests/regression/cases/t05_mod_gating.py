"""L4: Gating — gate creation, groups, boolean NOT, delete and persistence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QMessageBox,
    QPushButton,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.gating import page as gating_page  # noqa: E402
from smage.widgets.gate_editor.canvas import _GatePolygonROI  # noqa: E402
from smage.widgets.plot_axes import _gate_roi_pens  # noqa: E402
from smage.data import naming as _data_naming
from smage.modules.gating import page as gating_page


def _open(case_id: str):
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    project = harness.new_project(case_id, rows=120, tasks=[task])
    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(450)
    return window, project, window.tabs.currentWidget()


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.gating.range", "L4", "gating", tags=("fast",))
def add_range_gate() -> None:
    window, _project, page = _open("gating_range")
    try:
        ctx = window._ctx
        harness.eq(page._gating_gates, [])
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))
        harness.eq(len(page._gating_gates), 1)
        gate = page._gating_gates[0]
        harness.eq(gate["type"], "range")
        harness.eq((gate["x1"], gate["x2"]), (2.0, 8.0))
        harness.check(bool(gate.get("x")), "the gate records its x column")
        harness.check(page._gating_dirty, "creating a gate marks the module dirty")
    finally:
        _close(window)


@harness.case("mod.gating.groups", "L4", "gating", tags=("fast",))
def gate_groups() -> None:
    window, _project, page = _open("gating_groups")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "quadrant", (5.0, 5.0))
        harness.eq(len(page._gating_gates), 4)
        quadrants = {gate.get("quadrant") for gate in page._gating_gates}
        harness.eq(quadrants, {1, 2, 3, 4})
        crosshairs = {gate.get("crosshair_id") for gate in page._gating_gates}
        harness.eq(len(crosshairs), 1)
        harness.check(None not in crosshairs)

        gating_page._gating_add_gate(ctx, page, "split", (5.0, 0.0))
        harness.eq(len(page._gating_gates), 6)
        split_gates = page._gating_gates[4:]
        harness.eq({gate["side"] for gate in split_gates}, {"left", "right"})
        harness.eq(len({gate.get("split_id") for gate in split_gates}), 1)
    finally:
        _close(window)


@harness.case("mod.gating.not", "L4", "gating", tags=("fast",))
def not_gate() -> None:
    window, _project, page = _open("gating_not")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))
        source = page._gating_gates[0]
        gating_page._gating_add_not_gate(ctx, page, 0)
        harness.eq(len(page._gating_gates), 2)
        not_gate = page._gating_gates[1]
        harness.eq(not_gate["type"], "not")
        harness.eq(not_gate["source"], source["id"])
        harness.check(not_gate["name"].startswith("NOT "), not_gate["name"])
    finally:
        _close(window)


@harness.case("mod.gating.delete_group", "L4", "gating", tags=("fast",))
def delete_group() -> None:
    window, _project, page = _open("gating_delete")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "quadrant", (5.0, 5.0))
        harness.eq(len(page._gating_gates), 4)
        with harness.patch_dialogs(
            gating_page, question=QMessageBox.StandardButton.Yes
        ):
            gating_page._gating_delete_gate(ctx, page, 0, cascade=True)
        harness.eq(page._gating_gates, [], "deleting one quadrant deletes the group")
        harness.eq(page._gating_rois, [])
    finally:
        _close(window)


@harness.case("mod.gating.save", "L4", "gating", tags=("fast",))
def save_gates() -> None:
    window, project, page = _open("gating_save")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (1.0, 9.0))
        gating_page._gating_add_not_gate(ctx, page, 0)
        gating_page._gating_save_now(ctx, page)

        harness.check(not page._gating_dirty)
        path = project.path / "Module" / "g-1" / "gates.json"
        harness.check(path.is_file(), "gates.json is written")
        payload = json.loads(path.read_text(encoding="utf-8"))
        harness.eq(len(payload["gates"]), 2)
        harness.eq(payload["gates"][0]["type"], "range")
        harness.eq(payload["gates"][1]["type"], "not")
        harness.golden("gating_gates", [
            {"type": gate["type"], "x1": gate.get("x1"),
             "x2": gate.get("x2"), "has_source": bool(gate.get("source"))}
            for gate in payload["gates"]
        ])
        harness.check("files" in payload and "per_file" in payload)

        from smage.project import Project

        reloaded = Project.load(project.path)
        task = reloaded.workflow["tasks"][0]["children"][0]
        harness.check("gates" not in (task.get("params") or {}),
                      "gates live in their store, not project.json")
    finally:
        _close(window)


@harness.case("mod.gating.close_prompt", "L4", "gating", tags=("fast",))
def close_prompt() -> None:
    window, _project, page = _open("gating_close")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (1.0, 9.0))
        harness.check(page._gating_dirty)

        with harness.patch_dialogs(
            gating_page, question=QMessageBox.StandardButton.No
        ):
            gating_page._gating_close(ctx, page)
        harness.eq(window.tabs.count(), 2, "No keeps the page open")

        with harness.patch_dialogs(
            gating_page, question=QMessageBox.StandardButton.Yes
        ):
            gating_page._gating_close(ctx, page)
        harness.eq(window.tabs.count(), 1, "Yes closes without saving")
    finally:
        _close(window)


@harness.case("mod.gating.shapes", "L4", "gating", tags=("fast",))
def shapes() -> None:
    window, _project, page = _open("gating_shapes")
    try:
        ctx = window._ctx
        pens = _gate_roi_pens("#000000")

        rect = pg.RectROI((2, 2), (8, 8), **pens)
        gating_page._gating_add_gate(ctx, page, "rect", rect)
        harness.eq(page._gating_gates[-1]["type"], "rect")
        harness.eq(page._gating_gates[-1]["pos"], [2.0, 2.0])
        harness.eq(page._gating_gates[-1]["size"], [8.0, 8.0])

        ellipse = pg.EllipseROI((10, 10), (4, 6), **pens)
        gating_page._gating_add_gate(ctx, page, "ellipse", ellipse)
        harness.eq(page._gating_gates[-1]["type"], "ellipse")
        harness.eq(page._gating_gates[-1]["size"], [4.0, 6.0])

        polygon = _GatePolygonROI([(0, 0), (5, 0), (5, 5), (0, 5)], **pens)
        gating_page._gating_add_gate(ctx, page, "polygon", polygon)
        harness.eq(page._gating_gates[-1]["type"], "polygon")
        harness.check(len(page._gating_gates[-1]["points"]) >= 3)
        harness.eq(len(page._gating_gates), 3)
    finally:
        _close(window)


@harness.case("mod.gating.stats", "L4", "gating", tags=("fast",))
def stats() -> None:
    window, _project, page = _open("gating_stats")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 1e12))
        gate_id = page._gating_gates[0]["id"]
        gating_page._gating_request_stats(ctx, page)
        harness.wait_for(
            lambda: gate_id in getattr(page, "_gating_last_stats", {}),
            timeout=30,
            message="gate statistics",
        )
        count, percent = page._gating_last_stats[gate_id]
        harness.eq(count, 120, "all rows are inside the wide range")
        harness.check(abs(percent - 100.0) < 1e-6, percent)
    finally:
        _close(window)


@harness.case("mod.gating.copy_paste", "L4", "gating", tags=("fast",))
def copy_paste() -> None:
    window, _project, page = _open("gating_copy")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        first = page._gating_gates[0]
        gating_page._gating_copy_gate(ctx, page, 0)
        gating_page._gating_paste_gate(ctx, page, None)
        harness.eq(len(page._gating_gates), 2)
        copied = page._gating_gates[1]
        harness.check(copied["id"] != first["id"])
        harness.eq(copied["type"], "range")

        page._gating_gates[1]["parent"] = first["id"]
        gating_page._gating_copy_branch(ctx, page, 0)
        gating_page._gating_paste_branch(ctx, page, None)
        harness.eq(len(page._gating_gates), 4)
        new_root = page._gating_gates[2]
        new_child = page._gating_gates[3]
        harness.eq(new_child["parent"], new_root["id"],
                   "the pasted branch keeps its structure")
    finally:
        _close(window)


@harness.case("mod.gating.per_file", "L4", "gating", tags=("fast",))
def per_file() -> None:
    window, project, page = _open("gating_per_file")
    try:
        ctx = window._ctx
        parquet = str(project.data_dir / "case.parquet")
        page._gating_files = [parquet]
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gate = page._gating_gates[0]
        from smage.core.gates import _gate_group_id

        group = _gate_group_id(gate)
        gating_page._gating_toggle_per_file(ctx, page, 0)
        harness.check(group in page._gating_per_file)

        gating_page._gating_apply_geometry(ctx, page, gate, {"x1": 1.0, "x2": 2.0})
        harness.eq(page._gating_per_file[group][parquet],
                   {"x1": 1.0, "x2": 2.0})
        harness.eq((gate["x1"], gate["x2"]), (0.0, 5.0),
                   "the shared geometry is unchanged")

        name = _data_naming._comp_file_name(ctx, parquet, page._gating_task_id)
        with harness.patch_dialogs(
            gating_page, item=name, item_ok=True
        ):
            gating_page._gating_unify_gate(ctx, page, 0)
        harness.check(group not in page._gating_per_file,
                      "unify drops the per-file overrides")
        harness.eq((gate["x1"], gate["x2"]), (1.0, 2.0))
    finally:
        _close(window)


@harness.case("mod.gating.col2gate", "L4", "gating", tags=("fast",))
def col2gate() -> None:
    project = harness.new_project("gating_col2gate", rows=60)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)
    task = harness.task_def("gating", task_id="g-1", name="Gating")
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("g-1", "gating", "Gating")
    harness.pump(400)
    page = window.tabs.currentWidget()
    try:
        ctx = window._ctx
        page._gating_files = [str(parquet)]
        counts = gating_page._gating_col2gate_counts(ctx, page, "group")
        harness.eq([value for value, _count in counts], [1, 2, 3])
        harness.eq(sum(count for _value, count in counts), 60)

        gating_page._gating_col2gate_generate(ctx, page, "group", "Group ", counts)
        harness.eq(len(page._gating_gates), 3)
        harness.eq(
            [gate["name"] for gate in page._gating_gates],
            ["Group 1", "Group 2", "Group 3"],
        )
        harness.eq(
            [gate["value"] for gate in page._gating_gates], [1, 2, 3]
        )
        harness.eq({gate["type"] for gate in page._gating_gates},
                   {"categorical"})
    finally:
        _close(window)


@harness.case("mod.gating.and_gate", "L4", "gating", tags=("fast",))
def and_gate() -> None:
    window, _project, page = _open("gating_and")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gating_page._gating_add_gate(ctx, page, "range", (2.0, 8.0))

        def accept() -> None:
            for widget in QApplication.topLevelWidgets():
                if not isinstance(widget, QDialog):
                    continue
                for box in widget.findChildren(QCheckBox):
                    box.setChecked(True)
                for button in widget.findChildren(QPushButton):
                    if button.text() == "OK":
                        button.click()
                        return
                widget.accept()
                return

        QTimer.singleShot(0, accept)
        gating_page._gating_add_boolean_gate(ctx, page, 0, "and")
        harness.eq(len(page._gating_gates), 3)
        boolean = page._gating_gates[-1]
        harness.eq(boolean["type"], "and")
        harness.eq(len(boolean["sources"]), 2)
        harness.check(" AND " in boolean["name"], boolean["name"])
    finally:
        _close(window)


@harness.case("mod.gating.subset_view", "L4", "gating", tags=("fast",))
def subset_view() -> None:
    window, _project, page = _open("gating_subset")
    try:
        ctx = window._ctx
        gating_page._gating_add_gate(ctx, page, "range", (0.0, 5.0))
        gate = page._gating_gates[0]
        gating_page._gating_open_gate_subset(ctx, page, gate["id"])
        harness.eq(page._gating_parent_id, gate["id"])
        harness.eq(page._gating_active_gate_id, gate["id"])
        harness.check(page._gating_tree.selectedItems(),
                      "the gate is selected in the tree")

        gating_page._gating_open_gate_subset(ctx, page, "missing")
        harness.eq(page._gating_parent_id, None)
        harness.eq(page._gating_active_gate_id, None)
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
