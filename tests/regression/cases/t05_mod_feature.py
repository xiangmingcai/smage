"""L4: Feature — aggregate rules, output files and background-ignore filtering."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.modules.feature import page as feature_page  # noqa: E402
from smage.modules.feature import page as feature_page


def _open(case_id: str, ignore=None):
    project = harness.new_project(case_id, rows=30)
    parquet = project.data_dir / "case.parquet"
    frame = pd.read_parquet(parquet)
    frame["group"] = (np.arange(len(frame)) % 3 + 1).astype("int32")
    frame["v"] = np.arange(len(frame), dtype="float64")
    frame.to_parquet(parquet, index=False)
    columns = project.load_columns()
    columns["case.parquet"] = list(frame.columns)
    project.save_columns(columns)

    task = harness.task_def(
        "feature", task_id="f-1", name="Feature",
        params={
            "feature": "group",
            "files": [str(parquet)],
            "rules": [
                {"method": "count", "name": "n"},
                {"source": "v", "method": "mean", "name": "mean_v"},
            ],
            "ignore_values": ignore,
        },
    )
    task["output"] = {"saved": False}
    project.workflow["tasks"][0]["children"].append(task)
    project.save()

    window = harness.new_window()
    harness.open_project(window, project)
    window._open_module("f-1", "feature", "Feature")
    harness.pump(350)
    return window, project, window.tabs.currentWidget(), str(parquet)


def _close(window) -> None:
    harness.close_window(window)


@harness.case("mod.feature.calculate", "L4", "feature", tags=("fast",))
def calculate() -> None:
    window, project, page, parquet = _open("feature_calc", ignore=[])
    try:
        ctx = window._ctx
        feature_page._feat_calculate(ctx, page)
        outputs = page._feat_params.get("outputs") or {}
        harness.check(parquet in outputs, f"outputs: {outputs}")
        record = outputs[parquet]
        harness.eq(record["columns"], ["group", "n", "mean_v"])

        output = project.path / record["path"]
        harness.check(output.is_file(), "the aggregated parquet is written")
        frame = pd.read_parquet(output)
        harness.eq(sorted(frame["group"].tolist()), [1, 2, 3])
        for group in (1, 2, 3):
            expected = int((pd.read_parquet(parquet)["group"] == group).sum())
            harness.eq(int(frame.loc[frame["group"] == group, "n"].iloc[0]),
                       expected)
        harness.check(
            page._feat_task_def["output"] == {"saved": True},
            "the generate module records its saved output",
        )
    finally:
        _close(window)


@harness.case("mod.feature.mode", "L3", "feature", tags=("fast",))
def mode_rule() -> None:
    """Mode returns the most frequent value; ties use the smallest value."""
    from smage.core.feature import (
        _feature_aggregate_frame,
        _feature_auto_name,
    )

    frame = pd.DataFrame({
        "group": [1, 1, 1, 2, 2, 2, 2, 3, 3],
        "v": [5.0, 5.0, 7.0, 3.0, 4.0, 4.0, 3.0, 9.0, 9.0],
    })
    rules = [{
        "source": "v", "method": "mode", "name": "v_mode",
        "lo": 0.0, "hi": 100.0,
    }]
    out, columns, _warnings = _feature_aggregate_frame(
        frame, "group", rules, "n", key_name="group",
    )
    harness.eq(columns, ["group", "n", "v_mode"])
    values = dict(zip(out["group"], out["v_mode"]))
    harness.eq(float(values[1]), 5.0)
    harness.eq(
        float(values[2]), 3.0,
        "a tie between 3 and 4 takes the smaller value",
    )
    harness.eq(float(values[3]), 9.0)
    harness.eq(
        _feature_auto_name({"source": "v", "method": "mode"}), "v_mode"
    )
    harness.check(("Mode", "mode") in feature_page.FEATURE_METHODS)

    # The per-class range filter is applied before the mode.
    ranged = [{
        "source": "v", "method": "mode", "name": "v_mode",
        "lo": 50.0, "hi": 100.0,
    }]
    out, _columns, _warnings = _feature_aggregate_frame(
        frame, "group", ranged, "n", key_name="group",
    )
    values = dict(zip(out["group"], out["v_mode"]))
    harness.eq(float(values[2]), 4.0,
               "the upper half of group 2 is 4, 4")


@harness.case("mod.feature.batch", "L3", "feature", tags=("fast",))
def batch_rules() -> None:
    """Add batch: pick channels, share method/range/suffix, unique names."""
    from unittest.mock import patch

    window, _project, page, _parquet = _open("feature_batch", ignore=[])
    try:
        ctx = window._ctx
        dialog = feature_page._FeatureBatchDialog(
            None, ["v", "group"], {"v_mode"}
        )
        harness.eq(dialog.method.currentData(), "mean")
        harness.eq(dialog.suffix.text(), "mean")
        dialog.method.setCurrentIndex(dialog.method.findData("mode"))
        harness.eq(dialog.suffix.text(), "mode",
                   "the default suffix follows the method")
        dialog.method.setCurrentIndex(
            dialog.method.findData("percentile")
        )
        harness.check(dialog.perc.isEnabled())
        harness.eq(dialog.suffix.text(), "p50")
        dialog.perc.setValue(90)
        harness.eq(dialog.suffix.text(), "p90")
        dialog.suffix.setText("custom")
        dialog._suffix_edited("custom")
        dialog.method.setCurrentIndex(dialog.method.findData("mean"))
        harness.eq(dialog.suffix.text(), "custom",
                   "a user suffix is not overwritten")
        dialog.lo.setValue(10)
        dialog.hi.setValue(90)
        values = dialog.values()
        harness.eq(values["method"], "mean")
        harness.eq(values["lo"], 10)
        harness.eq(values["hi"], 90)
        harness.eq(values["suffix"], "custom")
        harness.check("v_custom" in dialog.preview.text())
        dialog.reject()

        settings = {
            "method": "mode", "lo": 10, "hi": 90,
            "perc": 50, "suffix": "mm",
        }
        with patch.object(
            feature_page._widgets_picker, "_pick_items_dialog",
            return_value=["v", "group"],
        ), patch.object(
            feature_page._FeatureBatchDialog, "exec",
            return_value=QDialog.DialogCode.Accepted,
        ), patch.object(
            feature_page._FeatureBatchDialog, "values",
            return_value=settings,
        ):
            feature_page._feat_add_batch_rules(ctx, page)
        rules = page._feat_params["rules"]
        added = rules[-2:]
        harness.eq([r["source"] for r in added], ["v", "group"])
        harness.eq([r["method"] for r in added], ["mode", "mode"])
        harness.eq([r["name"] for r in added], ["v_mm", "group_mm"])
        harness.eq([r["lo"] for r in added], [10, 10])
        harness.eq([r["hi"] for r in added], [90, 90])
        harness.check(all(r.get("perc") is None for r in added))

        # Running the same batch again gets unique column names.
        with patch.object(
            feature_page._widgets_picker, "_pick_items_dialog",
            return_value=["v"],
        ), patch.object(
            feature_page._FeatureBatchDialog, "exec",
            return_value=QDialog.DialogCode.Accepted,
        ), patch.object(
            feature_page._FeatureBatchDialog, "values",
            return_value=settings,
        ):
            feature_page._feat_add_batch_rules(ctx, page)
        harness.eq(page._feat_params["rules"][-1]["name"], "v_mm_1")
    finally:
        _close(window)


@harness.case("mod.feature.feature_row", "L4", "feature", tags=("fast",))
def feature_row() -> None:
    """The feature variable has its own (deletable) row in the rules table."""
    window, _project, page, parquet = _open("feature_row", ignore=[])
    try:
        ctx = window._ctx
        texts = " ".join(
            label.text() for label in page.findChildren(QLabel)
        )
        harness.check(
            "Mode:" in texts and "most frequent" in texts,
            "the rules panel explains Mode next to Range",
        )
        rules = page._feat_params["rules"]
        harness.eq([r.get("method") for r in rules], ["count", "feature", "mean"],
                   "the group-key row is added by default")
        harness.eq(rules[1].get("name"), "group")

        # Deleting the row (the Delete button sets the same flag) leaves the
        # identifier column out of the aggregated file.
        del rules[1]
        page._feat_params["feature_rule"] = False
        feature_page._feat_sync_feature_rule(ctx, page)
        harness.check(
            not any(r.get("method") == "feature" for r in rules),
            "a deleted group-key row is not re-created",
        )
        feature_page._feat_calculate(ctx, page)
        record = page._feat_params["outputs"][str(parquet)]
        harness.eq(record["columns"], ["n", "mean_v"])
    finally:
        _close(window)


@harness.case("mod.feature.ignore", "L4", "feature", tags=("fast",))
def ignore_background() -> None:
    window, project, page, parquet = _open("feature_ignore", ignore=[3])
    try:
        ctx = window._ctx
        with harness.patch_dialogs(feature_page,
                                   question=QMessageBox.StandardButton.Yes):
            feature_page._feat_calculate(ctx, page)
        output = project.path / page._feat_params["outputs"][parquet]["path"]
        frame = pd.read_parquet(output)
        harness.eq(sorted(frame["group"].tolist()), [1, 2],
                   "the ignored background value is dropped")
    finally:
        _close(window)


@harness.case("mod.feature.validation", "L4", "feature", tags=("fast",))
def validation() -> None:
    window, _project, page, _parquet = _open("feature_validation", ignore=[])
    try:
        ctx = window._ctx
        page._feat_params["feature"] = ""
        with harness.patch_dialogs(feature_page,
                                   warning=QMessageBox.StandardButton.Ok):
            feature_page._feat_calculate(ctx, page)
        harness.check(not page._feat_params.get("outputs"))

        page._feat_params["feature"] = "group"
        page._feat_params["rules"] = [{"source": "v", "method": "mean"}]
        with harness.patch_dialogs(feature_page,
                                   warning=QMessageBox.StandardButton.Ok):
            feature_page._feat_calculate(ctx, page)
        harness.check(not page._feat_params.get("outputs"),
                      "Count must be the first rule")
    finally:
        _close(window)


if __name__ == "__main__":
    raise SystemExit(harness.main())
