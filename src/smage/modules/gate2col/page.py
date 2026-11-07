"""Gate2Col module: turn gates into 0/1 columns."""
from __future__ import annotations

from PySide6.QtCore import (
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from pathlib import Path
from smage.core.constants import (
    MODULE_STATE_MODIFIED,
    _FEATURE_LOG_LIMIT,
)
from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_chain,
    _gate_needed_columns,
)
from smage.data.image_index import _build_image_index_files
from smage.widgets.controls import _NoWheelComboBox
from smage.widgets.resources import _lock_pixmap
import copy


def _build_gate2col_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Gate2Col page: files (1) | gates table (2) | log."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    bar = QFrame()
    bar.setObjectName("moduleBar")
    tl = QHBoxLayout(bar)
    tl.setContentsMargins(12, 6, 12, 6)
    tl.setSpacing(4)
    tl.addWidget(QLabel(f"<b>{ctx._module_display_title(task_def.get('id'), name)}</b>"))
    if locked:
        lock_icon = QLabel()
        lock_icon.setPixmap(_lock_pixmap())
        lock_icon.setFixedSize(14, 14)
        lock_icon.setToolTip("Locked — a downstream generate module has saved data")
        tl.addWidget(lock_icon)
        draft = QLabel("Draft (locked)")
        draft.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft)
    tl.addStretch()
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setEnabled(not locked)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(lambda _=False: ctx._g2c_close(page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("files", [])
    params.setdefault("rules", [])
    params.setdefault("outputs", {})
    params.setdefault("log", [])
    page._g2c_task_def = task_def
    page._g2c_task_id = task_def.get("id")
    page._g2c_params = params
    page._g2c_locked = locked
    page._g2c_save_btn = save_btn
    page._g2c_dirty = False
    page._g2c_snapshot = copy.deepcopy(params)
    page._g2c_log_lines = list(params.get("log") or [])
    page._g2c_running = False
    page._g2c_cancel = False
    save_btn.clicked.connect(lambda _=False: ctx._g2c_save_now(page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(ctx._g2c_files_panel(page))
    splitter.addWidget(ctx._g2c_rules_panel(page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 2)
    splitter.setSizes([300, 620])
    row.addWidget(splitter, 1)

    row.addWidget(ctx._g2c_log_panel(page))
    layout.addWidget(content, 1)

    ctx._g2c_refresh_files(page)
    ctx._g2c_update_save_btn(page)
    return page


def _g2c_log_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Log</b>"))
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Conversion output appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    log.setFixedHeight(120)
    for line in getattr(page, "_g2c_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._g2c_log = log
    return panel


def _g2c_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_g2c_log_lines", None)
    if lines is None:
        lines = []
        page._g2c_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    if getattr(page, "_g2c_params", None) is not None:
        page._g2c_params["log"] = list(lines)
    box = getattr(page, "_g2c_log", None)
    if box is not None:
        for line in str(text).splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")


def _g2c_gate_options(ctx, page):
    """Enumerate every gate of every upstream Gating module."""
    opts = []
    gating_tasks = [
        t for t in ctx._task_chain(page._g2c_task_id)
        if t.get("id") != page._g2c_task_id and t.get("type") == "gating"
    ]
    multi = len(gating_tasks) > 1
    for task in gating_tasks:
        gates = ctx._gating_gates_for(task)
        by_id = {g["id"]: g for g in gates}
        for gate in gates:
            chain = _gate_chain(gates, gate["id"])
            needed = set()
            for g in chain:
                needed |= _gate_needed_columns(g, by_id)
            # Show at most three levels: grandparent -> parent -> this gate.
            label = " -> ".join(g.get("name", g["id"]) for g in chain[-3:])
            if multi:
                label += f" ({task.get('name', '')})"
            opts.append({
                "gate_task": task["id"],
                "task_name": task.get("name", ""),
                "gate_id": gate["id"],
                "gates": gates,
                "name": gate.get("name", gate["id"]),
                "label": label,
                "needed": needed,
            })
    return opts


def _g2c_gate_mask(ctx, gate_task_id, gates, gate_id, parquet_path):
    """Row-aligned int8 0/1 mask of a gate and its ancestors, or None."""
    import numpy as np

    by_id = {g["id"]: g for g in gates}
    needed = set()
    for g in _gate_chain(gates, gate_id):
        needed |= _gate_needed_columns(g, by_id)
    if not needed:
        return None
    values = ctx._column_data(gate_task_id, parquet_path, list(needed))
    if not values:
        return None
    try:
        mask = _compute_gate_filter_mask(
            gates, gate_id, parquet_path, {},
            existing=set(values), extra=values,
        )
    except Exception:  # noqa: BLE001
        return None
    if mask is None:
        return None
    return mask.astype("int8")


def _g2c_files_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Data files</b>"))
    select_btn = QPushButton("Select files…")
    select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    select_btn.setEnabled(not page._g2c_locked)
    lay.addWidget(select_btn)

    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)

    table = QTableWidget()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["File", "Status"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    page._g2c_files_table = table
    page._g2c_count_label = count
    select_btn.clicked.connect(lambda _=False: ctx._g2c_select_files(page))
    return panel


def _g2c_select_files(ctx, page) -> None:
    records = ctx._available_files(page._g2c_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [ctx._comp_file_name(p, page._g2c_task_id) for p in page._g2c_params.get("files") or []]
    chosen = ctx._pick_items_dialog(
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    # Reject a file that lacks the columns needed by an already-selected gate.
    rules = page._g2c_params.get("rules") or []
    selected_opts = [
        o for o in ctx._g2c_gate_options(page)
        if any(r.get("gate_task") == o["gate_task"] and r.get("gate_id") == o["gate_id"]
               for r in rules)
    ]
    rejected = []
    accepted = []
    for name in chosen:
        path = by_name.get(name)
        if path is None:
            continue
        cols = set(ctx._columns_for(path, page._g2c_task_id))
        missing = [
            o["label"] for o in selected_opts if not (o["needed"] <= cols)
        ]
        if missing:
            rejected.append(f"{name} (missing {', '.join(missing)})")
        else:
            accepted.append(path)
    if rejected:
        QMessageBox.warning(
            ctx.host, "Select files",
            "These files were not added because they lack the columns "
            "needed by the selected gates:\n" + "\n".join(rejected),
        )
    page._g2c_params["files"] = accepted
    ctx._g2c_refresh_files(page)
    ctx._g2c_mark_dirty(page)


def _g2c_file_status(ctx, page, parquet_path) -> str:
    params = page._g2c_params
    rec = (params.get("outputs") or {}).get(parquet_path)
    if not isinstance(rec, dict) or not rec.get("path"):
        return "Not converted"
    if ctx.project is None:
        return "Not converted"
    if not (ctx.project.path / rec["path"]).is_file():
        return "Not converted"
    if rec.get("signature") != ctx._gate2col_signature(
        params, parquet_path, page._g2c_task_id
    ):
        return "Not converted"
    return "Converted"


def _g2c_refresh_files(ctx, page) -> None:
    table = getattr(page, "_g2c_files_table", None)
    files = list(page._g2c_params.get("files") or [])
    if table is not None:
        table.blockSignals(True)
        table.setRowCount(len(files))
        for i, path in enumerate(files):
            missing = not Path(str(path)).is_file()
            label = ctx._comp_file_name(path, page._g2c_task_id)
            if missing:
                label += "  (missing)"
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if missing:
                item.setForeground(QColor("#c0392b"))
                item.setToolTip(
                    "This file is no longer on disk — re-run the module that "
                    "created it, or select files again."
                )
            table.setItem(i, 0, item)
            status = "File missing" if missing else ctx._g2c_file_status(page, path)
            sitem = QTableWidgetItem(status)
            sitem.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if missing:
                sitem.setForeground(QColor("#c0392b"))
            else:
                sitem.setForeground(
                    QColor("#1a7f37") if status == "Converted" else QColor("#9aa0a6")
                )
            table.setItem(i, 1, sitem)
        table.blockSignals(False)
    count = getattr(page, "_g2c_count_label", None)
    if count is not None:
        count.setText(f"{len(files)} file(s) selected")
    ctx._g2c_rebuild_rules(page)


def _g2c_rules_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Convert gates</b>"))

    table = QTableWidget()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["Gate", "New column name"])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    page._g2c_rules_table = table

    btns = QHBoxLayout()
    add_btn = QPushButton("Add")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_btn.setEnabled(not page._g2c_locked)
    del_btn = QPushButton("Delete")
    del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    del_btn.setEnabled(not page._g2c_locked)
    btns.addWidget(add_btn)
    btns.addWidget(del_btn)
    btns.addStretch()
    lay.addLayout(btns)

    calc_btn = QPushButton("Convert")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(not page._g2c_locked)
    lay.addWidget(calc_btn)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._g2c_calc_btn = calc_btn
    page._g2c_calc_status = status

    add_btn.clicked.connect(lambda _=False: ctx._g2c_add_rule(page))
    del_btn.clicked.connect(lambda _=False: ctx._g2c_delete_rule(page))
    calc_btn.clicked.connect(lambda _=False: ctx._g2c_calculate(page))
    return panel


def _g2c_add_rule(ctx, page) -> None:
    page._g2c_params["rules"].append({"gate_task": None, "gate_id": None, "name": ""})
    ctx._g2c_refresh_rules(page)
    ctx._g2c_mark_dirty(page)


def _g2c_delete_rule(ctx, page) -> None:
    table = page._g2c_rules_table
    row = table.currentRow()
    rules = page._g2c_params["rules"]
    if 0 <= row < len(rules):
        del rules[row]
        ctx._g2c_refresh_rules(page)
        ctx._g2c_mark_dirty(page)


def _g2c_existing_columns(ctx, page) -> set:
    cols = set()
    for path in page._g2c_params.get("files") or []:
        cols |= set(ctx._columns_for(path, page._g2c_task_id))
    return cols


def _g2c_duplicate_names(ctx, page) -> set:
    """Set of new-column names that are duplicated or clash with a file column."""
    existing = ctx._g2c_existing_columns(page)
    dup = set()
    seen = set()
    for rule in page._g2c_params.get("rules") or []:
        name = (rule.get("name") or "").strip()
        if not name:
            continue
        if name in existing or name in seen:
            dup.add(name)
        seen.add(name)
    return dup


def _g2c_rebuild_rules(ctx, page) -> None:
    table = getattr(page, "_g2c_rules_table", None)
    if table is None:
        return
    params = page._g2c_params
    rules = params.get("rules") or []
    opts = ctx._g2c_gate_options(page)
    enabled = not page._g2c_locked
    dup_names = ctx._g2c_duplicate_names(page)

    table.blockSignals(True)
    table.setRowCount(0)
    table.setRowCount(len(rules))

    def mark():
        ctx._g2c_mark_dirty(page)
        ctx._g2c_refresh_statuses(page)

    def rebuild_soon():
        QTimer.singleShot(0, lambda: ctx._g2c_rebuild_rules(page))

    for i, rule in enumerate(rules):
        combo = _NoWheelComboBox()
        combo.addItem("", None)
        for o in opts:
            combo.addItem(o["label"], f"{o['gate_task']}|{o['gate_id']}")
        cur = f"{rule.get('gate_task')}|{rule.get('gate_id')}"
        idx = combo.findData(cur)
        combo.setCurrentIndex(max(0, idx))
        combo.setEnabled(enabled)

        def on_gate(value, r=rule):
            if value:
                task, _, gid = str(value).partition("|")
                r["gate_task"], r["gate_id"] = task, gid
                opt = next((o for o in opts
                            if o["gate_task"] == task and o["gate_id"] == gid),
                           None)
                if opt is not None:
                    old = (r.get("name") or "").strip()
                    if not old or old == r.get("_default"):
                        r["name"] = opt["name"]
                    r["_default"] = opt["name"]
            else:
                r["gate_task"], r["gate_id"] = None, None
            rebuild_soon()
            mark()

        combo.currentIndexChanged.connect(
            lambda _=0, c=combo, cb=on_gate: cb(c.currentData())
        )
        table.setCellWidget(i, 0, combo)

        name_box = QWidget()
        nh = QHBoxLayout(name_box)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(4)
        name_edit = QLineEdit(str(rule.get("name") or ""))
        name_edit.setEnabled(enabled)
        warn = QLabel("⚠")
        warn.setToolTip("Duplicate column name")
        warn.setStyleSheet("color: #b58900; font-weight: bold;")

        def on_name(text, r=rule, warn_label=warn):
            r["name"] = text.strip()
            mark()
            warn_label.setVisible(
                (r.get("name") or "").strip() in ctx._g2c_duplicate_names(page)
            )

        name_edit.textEdited.connect(lambda t, cb=on_name: cb(t))
        nh.addWidget(name_edit, 1)
        nh.addWidget(warn)
        table.setCellWidget(i, 1, name_box)
        warn.setVisible((rule.get("name") or "").strip() in dup_names)

    table.blockSignals(False)


def _g2c_refresh_rules(ctx, page) -> None:
    ctx._g2c_rebuild_rules(page)


def _g2c_refresh_statuses(ctx, page) -> None:
    table = getattr(page, "_g2c_files_table", None)
    if table is None:
        return
    files = list(page._g2c_params.get("files") or [])
    for i, path in enumerate(files):
        if i >= table.rowCount():
            break
        status = ctx._g2c_file_status(page, path)
        item = table.item(i, 1)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(i, 1, item)
        item.setText(status)
        item.setForeground(
            QColor("#1a7f37") if status == "Converted" else QColor("#9aa0a6")
        )


def _g2c_mark_dirty(ctx, page) -> None:
    page._g2c_dirty = True
    ctx._g2c_update_save_btn(page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _g2c_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_g2c_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._g2c_dirty and not page._g2c_locked)


def _g2c_save_now(ctx, page) -> None:
    params = page._g2c_params
    page._g2c_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._g2c_snapshot = copy.deepcopy(params)
    page._g2c_dirty = False
    ctx._g2c_update_save_btn(page)
    ctx._g2c_log(page, "Settings saved")
    ctx.statusBar().showMessage("Gate2Col settings saved")
    ctx._module_saved(page)


def _g2c_validate(ctx, page):
    params = page._g2c_params
    if not params.get("files"):
        return False, "Select at least one data file."
    rules = params.get("rules") or []
    if not rules:
        return False, "Add at least one gate to convert."
    existing = ctx._g2c_existing_columns(page)
    names = []
    for i, rule in enumerate(rules):
        if not rule.get("gate_task") or not rule.get("gate_id"):
            return False, f"Row {i + 1}: choose a gate."
        name = (rule.get("name") or "").strip()
        if not name:
            return False, f"Row {i + 1}: give the new column a name."
        names.append(name)
    if len(names) != len(set(names)):
        return False, "New column names must be unique."
    if any(name in existing for name in names):
        return False, "A new column name collides with an existing column."
    return True, ""


def _g2c_calculate(ctx, page) -> None:
    ok, message = ctx._g2c_validate(page)
    if not ok:
        QMessageBox.warning(ctx.host, "Convert", message)
        return
    params = page._g2c_params
    outputs = params.get("outputs") or {}
    selected = params.get("files") or []
    existing = [p for p in selected if isinstance(outputs.get(p), dict)]
    if existing:
        reply = QMessageBox.question(
            ctx.host, "Convert",
            f"{len(existing)} file(s) already have results.\n"
            "Recompute and overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    status = getattr(page, "_g2c_calc_status", None)
    if status is not None:
        status.setText("Converting…")
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    done = 0
    failed = []
    page._g2c_running = True
    page._g2c_cancel = False
    try:
        for path in selected:
            if getattr(page, "_g2c_cancel", False):
                ctx._g2c_log(page, "Conversion cancelled.")
                break
            name = ctx._comp_file_name(path, page._g2c_task_id)
            if status is not None:
                status.setText(f"Converting… {name}")
            QApplication.processEvents()
            if not Path(str(path)).is_file():
                outputs.pop(path, None)
                failed.append(f"{name}: file not found")
                ctx._g2c_log(page, f"✗ {name}: file not found — skipped")
                continue
            try:
                rel, columns, kind = ctx._g2c_compute_file(page, path)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                ctx._g2c_log(page, f"✗ {name}: {exc}")
                continue
            outputs[path] = {
                "path": rel,
                "columns": columns,
                "kind": kind,
                "signature": ctx._gate2col_signature(
                    params, path, page._g2c_task_id
                ),
            }
            done += 1
            ctx._g2c_log(
                page, f"✓ {name} → {Path(rel).name} ({len(columns)} column(s))"
            )
    finally:
        QApplication.restoreOverrideCursor()
        page._g2c_running = False

    for path in list(outputs.keys()):
        if path not in selected:
            outputs.pop(path, None)
    params["outputs"] = outputs
    page._g2c_task_def["output"] = {"saved": bool(outputs)}
    page._g2c_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._g2c_snapshot = copy.deepcopy(params)
    page._g2c_dirty = False
    ctx._g2c_update_save_btn(page)
    ctx._g2c_refresh_files(page)

    summary = f"Converted {done} file(s)."
    if failed:
        summary += f" {len(failed)} failed — see the log."
    if status is not None:
        status.setText(summary)
    ctx._g2c_log(page, summary)
    ctx.statusBar().showMessage(summary)
    ctx._module_saved(page)


def _g2c_row_count(ctx, parquet_path) -> int:
    return ctx._data_row_count(parquet_path)


def _g2c_compute_file(ctx, page, parquet_path):
    """Convert every rule into a 0/1 column and save them; return (rel, columns, kind)."""
    import numpy as np
    import pandas as pd

    params = page._g2c_params
    rules = params.get("rules") or []
    opts = ctx._g2c_gate_options(page)
    by_key = {(o["gate_task"], o["gate_id"]): o for o in opts}

    # A gate is evaluated row by row, so restricting the work to the rows a
    # Subsample module keeps gives the same values. The output file stays
    # row-aligned to the whole source; rows hidden by sampling hold 0 and
    # are never read below the Subsample module.
    n_full = ctx._data_row_count(parquet_path)
    index = ctx._subsample_row_index(page._g2c_task_id, parquet_path)
    cols = []
    for rule in rules:
        opt = by_key.get((rule.get("gate_task"), rule.get("gate_id")))
        if opt is None:
            raise ValueError(f"gate not found for '{rule.get('name')}'")
        gating_task = ctx._find_task_by_id(opt["gate_task"])
        gates = (
            ctx._gating_gates_for(gating_task, parquet_path)
            if gating_task is not None else opt["gates"]
        )
        mask = ctx._g2c_gate_mask(
            opt["gate_task"], gates, opt["gate_id"], parquet_path
        )
        if mask is None:
            mask = np.zeros(n_full, dtype="int8")
        if index is not None:
            full = np.zeros(n_full, dtype="int8")
            if len(mask) == n_full:
                full[index] = mask[index]
            elif len(mask) == len(index):
                full[index] = mask
            mask = full
        cols.append((rule["name"], mask))

    ent = ctx._ensure_image_index(parquet_path)
    is_image = ent is not None and ent.get("manifest") is not None
    stem = Path(str(parquet_path)).stem
    rel = ctx._module_rel(page._g2c_task_id, f"{stem}_gates.parquet")
    path = ctx.project.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)

    if is_image:
        man = ent["manifest"]
        xmin = int(round(float(man["xmin"])))
        ymin = int(round(float(man["ymin"])))
        width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
        height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
        src = pd.read_parquet(parquet_path, columns=["x", "y"])
        xi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        yi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        ok = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        flat = yi[ok] * width + xi[ok]
        xx = np.tile(np.arange(width, dtype=np.int32), height) + xmin
        yy = np.repeat(np.arange(height, dtype=np.int32), width) + ymin
        data = {"x": xx, "y": yy}
        for name, mask in cols:
            grid = np.zeros(height * width, dtype=np.int8)
            grid[flat] = mask[ok]
            data[name] = grid
        pd.DataFrame(data).to_parquet(path, index=False)
        _build_image_index_files(str(path))
        kind = "image"
    else:
        pd.DataFrame(
            {name: np.asarray(mask, dtype=np.int8) for name, mask in cols}
        ).to_parquet(path, index=False)
        kind = "table"
    return rel, [name for name, _ in cols], kind


def _g2c_close(ctx, page) -> None:
    if getattr(page, "_g2c_running", False):
        reply = QMessageBox.question(
            ctx.host, "Close Gate2Col",
            "A conversion is still running.\nStop it and close the module?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._g2c_cancel = True
        ctx._g2c_log(page, "Stopping after the current file…")
        return
    if getattr(page, "_g2c_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Gate2Col",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log_lines = list(getattr(page, "_g2c_log_lines", []) or [])
        page._g2c_task_def["params"] = page._g2c_snapshot
        page._g2c_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)
