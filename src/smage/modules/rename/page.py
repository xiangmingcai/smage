"""Rename page: file-mode and column-mode renames."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListWidget, QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.pipeline import _rename_applies
from smage.widgets.controls import _NoWheelComboBox
from smage.widgets.resources import _lock_pixmap


def _rename_task(ctx, task_def: dict) -> None:
    """Rename a task."""
    name, ok = QInputDialog.getText(
        ctx.host, "Rename", "New name:", text=task_def.get("name", "")
    )
    name = name.strip()
    if not ok or not name:
        return
    task_def["name"] = name
    if ctx.project is not None:
        ctx.project.save()
    ctx._refresh_workflow_tree()


def _rename_columns_forward(ctx, task_id, parquet_path, names) -> list:
    """Map a list of column names through upstream renames (for UI names)."""
    out = list(names)
    for stage in ctx._column_rename_stages(task_id):
        if not _rename_applies(stage, parquet_path):
            continue
        out = [stage["map"].get(n, n) for n in out]
    return out


def _build_rename_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Rename page: mode toggle | file/column rename table."""
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
    close_btn.clicked.connect(lambda _=False: ctx._rename_close(page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("mode", "file")
    params.setdefault("file", {"files": [], "rows": [], "rename": {}})
    params.setdefault("column", {"files": [], "rows": [], "rename": {}})
    page._rename_task_def = task_def
    page._rename_task_id = task_def.get("id")
    page._rename_params = params
    page._rename_locked = locked
    page._rename_save_btn = save_btn
    page._rename_dirty = False
    save_btn.clicked.connect(lambda _=False: ctx._rename_save_now(page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(8)

    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("<b>Rename</b>"))
    mode = _NoWheelComboBox()
    mode.addItem("Data files", "file")
    mode.addItem("Variables", "column")
    mode.setEnabled(not locked)
    mode_row.addWidget(mode)
    mode_row.addStretch()
    row.addLayout(mode_row)
    page._rename_mode = mode

    stack = QStackedWidget()
    stack.addWidget(ctx._rename_file_panel(page))
    stack.addWidget(ctx._rename_column_panel(page))
    row.addWidget(stack, 1)
    page._rename_stack = stack
    layout.addWidget(content, 1)

    mode.currentIndexChanged.connect(lambda _=0: ctx._rename_on_mode(page))
    ctx._rename_refresh(page)
    return page


def _rename_file_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    top = QHBoxLayout()
    sel = QPushButton("Select files…")
    sel.setCursor(Qt.CursorShape.PointingHandCursor)
    sel.clicked.connect(lambda _=False: ctx._rename_select_files(page))
    top.addWidget(sel)
    top.addStretch()
    lay.addLayout(top)
    hint = QLabel(
        "Rename the display name of selected files. The internal file "
        "identity is unchanged; only downstream modules see the new name."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    lay.addWidget(ctx._rename_table(page, "file"), 1)
    lay.addLayout(ctx._rename_copy_paste(page))
    return panel


def _rename_column_panel(ctx, page) -> QWidget:
    panel = QWidget()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)

    left = ctx._build_panel_frame()
    ll = QVBoxLayout(left)
    ll.setContentsMargins(10, 10, 10, 10)
    ll.setSpacing(6)
    ll.addWidget(QLabel("<b>Data files</b>"))
    hint_files = QLabel(
        "Renaming applies only to the selected files; the same variable in "
        "an unselected file keeps its old name."
    )
    hint_files.setWordWrap(True)
    hint_files.setStyleSheet("color: #6b7785;")
    ll.addWidget(hint_files)
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.clicked.connect(lambda _=False: ctx._rename_select_files(page))
    ll.addWidget(files_btn)
    files_box = QListWidget()
    files_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    ll.addWidget(files_box, 1)
    page._rename_column_files_box = files_box

    right = ctx._build_panel_frame()
    rl = QVBoxLayout(right)
    rl.setContentsMargins(10, 10, 10, 10)
    rl.setSpacing(6)
    rl.addWidget(QLabel("<b>Variables</b>"))
    hint_vars = QLabel(
        "Variables shared by the selected files (their union)."
    )
    hint_vars.setWordWrap(True)
    hint_vars.setStyleSheet("color: #6b7785;")
    rl.addWidget(hint_vars)
    vars_btn = QPushButton("Select variables…")
    vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    vars_btn.clicked.connect(lambda _=False: ctx._rename_select_vars(page))
    rl.addWidget(vars_btn)
    vars_box = QListWidget()
    vars_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    rl.addWidget(vars_box, 1)
    page._rename_column_vars_box = vars_box

    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    lay.addWidget(splitter, 3)
    lay.addWidget(ctx._rename_table(page, "column"), 4)
    lay.addLayout(ctx._rename_copy_paste(page))
    return panel


def _rename_table(ctx, page, mode) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    table = QTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["original name", "new name", ""])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(30)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    attr = "_rename_file_table" if mode == "file" else "_rename_column_table"
    setattr(page, attr, table)
    table.itemChanged.connect(
        lambda item, m=mode: ctx._rename_on_cell(page, item, m)
    )
    return panel


def _rename_copy_paste(ctx, page) -> QHBoxLayout:
    btns = QHBoxLayout()
    copy_btn = QPushButton("Copy")
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    paste_btn = QPushButton("Paste")
    paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.clicked.connect(lambda _=False: ctx._rename_copy(page))
    paste_btn.clicked.connect(lambda _=False: ctx._rename_paste(page))
    btns.addWidget(copy_btn)
    btns.addWidget(paste_btn)
    btns.addStretch()
    return btns


def _rename_on_mode(ctx, page) -> None:
    if getattr(page, "_rename_updating", False):
        return
    mode = page._rename_mode.currentData() or "file"
    if mode != page._rename_params.get("mode"):
        page._rename_params["mode"] = mode
        ctx._rename_mark_dirty(page)
    page._rename_stack.setCurrentIndex(0 if mode == "file" else 1)
    ctx._rename_refresh(page)


def _rename_cfg(ctx, page, mode=None):
    params = page._rename_params
    mode = mode or params.get("mode", "file")
    return params.setdefault(mode, {"files": [], "rows": [], "rename": {}})


def _rename_select_files(ctx, page) -> None:
    records = ctx._available_files(page._rename_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    mode = page._rename_params.get("mode", "file")
    cfg = ctx._rename_cfg(page, mode)
    current = [
        ctx._comp_file_name(p, page._rename_task_id) for p in cfg.get("files") or []
    ]
    chosen = ctx._pick_items_dialog(
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    paths = [by_name[n] for n in chosen if n in by_name]
    cfg["files"] = paths
    if mode == "file":
        # File mode: the table is one row per selected file.
        existing = {r["original"]: r for r in cfg.get("rows") or []}
        rows = []
        for path in paths:
            if path in existing:
                rows.append(existing[path])
            else:
                rows.append({
                    "original": path,
                    "new": ctx._comp_file_name(path, page._rename_task_id),
                })
        cfg["rows"] = rows
    else:
        # Column mode: files only set the scope; the variable rows are
        # picked separately. Drop rows whose column is no longer offered.
        union = set(ctx._rename_column_union(page))
        cfg["rows"] = [
            r for r in (cfg.get("rows") or [])
            if r.get("original") in union
        ]
    ctx._rename_refresh(page)
    ctx._rename_mark_dirty(page)


def _rename_column_union(ctx, page) -> list:
    cfg = ctx._rename_cfg(page, "column")
    paths = list(cfg.get("files") or [])
    columns = []
    for path in paths:
        for name in ctx._columns_for(path, page._rename_task_id):
            if name not in columns:
                columns.append(name)
    return columns


def _rename_select_vars(ctx, page) -> None:
    options = ctx._rename_column_union(page)
    if not options:
        QMessageBox.information(
            ctx.host, "Select variables", "Select data files first."
        )
        return
    cfg = ctx._rename_cfg(page, "column")
    existing = [r["original"] for r in cfg.get("rows") or []]
    chosen = ctx._pick_items_dialog(
        "Select variables", options, existing, "Filter variables..."
    )
    if chosen is None:
        return
    kept = {r["original"]: r for r in cfg.get("rows") or []}
    rows = []
    for name in chosen:
        if name in kept:
            rows.append(kept[name])
        else:
            rows.append({"original": name, "new": name})
    cfg["rows"] = rows
    ctx._rename_refresh(page)
    ctx._rename_mark_dirty(page)


def _rename_file_status(ctx, page, path, new_name, taken) -> tuple:
    new_name = (new_name or "").strip()
    if not new_name:
        return "Empty name", "error"
    if any(c in '\\/:*?"<>|' for c in new_name):
        return "Invalid characters", "error"
    if new_name in taken:
        return "Duplicate name", "error"
    return "", "ok"


def _rename_column_status(ctx, page, old, new_name, existing) -> tuple:
    new_name = (new_name or "").strip()
    if not new_name:
        return "Empty name", "error"
    if any(c in '\\/:*?"<>|' for c in new_name):
        return "Invalid characters", "error"
    others = {c for c in existing if c != old}
    if new_name in others:
        return "Duplicate name", "warning"
    return "", "ok"


def _rename_refresh(ctx, page) -> None:
    page._rename_updating = True
    try:
        mode = page._rename_params.get("mode", "file")
        page._rename_mode.blockSignals(True)
        page._rename_mode.setCurrentIndex(
            max(0, page._rename_mode.findData(mode))
        )
        page._rename_mode.blockSignals(False)
        page._rename_stack.setCurrentIndex(0 if mode == "file" else 1)
        if mode == "file":
            ctx._rename_refresh_file(page)
        else:
            ctx._rename_refresh_column(page)
    finally:
        page._rename_updating = False
    ctx._rename_update_save_btn(page)


def _rename_refresh_file(ctx, page) -> None:
    cfg = ctx._rename_cfg(page, "file")
    rows = cfg.get("rows") or []

    table = page._rename_file_table
    table.blockSignals(True)
    table.setRowCount(len(rows))
    for i, r in enumerate(rows):
        orig_item = QTableWidgetItem(
            ctx._comp_file_name(r["original"], page._rename_task_id)
        )
        orig_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(i, 0, orig_item)
        new_item = QTableWidgetItem(r.get("new", ""))
        table.setItem(i, 1, new_item)
    table.blockSignals(False)
    ctx._rename_statuses_file(page)


def _rename_statuses_file(ctx, page) -> None:
    cfg = ctx._rename_cfg(page, "file")
    records = ctx._available_files(page._rename_task_id)
    selected = set(cfg.get("files") or [])
    rows = cfg.get("rows") or []
    taken = set()
    for r in rows:
        n = (r.get("new") or "").strip()
        if n:
            taken.add(n)
    for rec in records:
        if rec.get("parquet") not in selected:
            taken.add(rec.get("name", ""))
    table = page._rename_file_table
    table.blockSignals(True)
    for i, r in enumerate(rows):
        status, sev = ctx._rename_file_status(
            page, r["original"], r.get("new"),
            taken - {(r.get("new") or "").strip()},
        )
        table.setItem(i, 2, ctx._rename_status_item(status, sev))
    table.blockSignals(False)


def _rename_refresh_column(ctx, page) -> None:
    cfg = ctx._rename_cfg(page, "column")
    rows = cfg.get("rows") or []

    files_box = page._rename_column_files_box
    files_box.clear()
    for path in cfg.get("files") or []:
        files_box.addItem(ctx._comp_file_name(path, page._rename_task_id))
    vars_box = page._rename_column_vars_box
    vars_box.clear()
    for r in rows:
        vars_box.addItem(str(r.get("original", "")))

    table = page._rename_column_table
    table.blockSignals(True)
    table.setRowCount(len(rows))
    for i, r in enumerate(rows):
        orig_item = QTableWidgetItem(str(r.get("original", "")))
        orig_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(i, 0, orig_item)
        new_item = QTableWidgetItem(r.get("new", ""))
        table.setItem(i, 1, new_item)
    table.blockSignals(False)
    ctx._rename_statuses_column(page)


def _rename_statuses_column(ctx, page) -> None:
    cfg = ctx._rename_cfg(page, "column")
    union = set(ctx._rename_column_union(page))
    rows = cfg.get("rows") or []
    table = page._rename_column_table
    table.blockSignals(True)
    for i, r in enumerate(rows):
        status, sev = ctx._rename_column_status(
            page, r["original"], r.get("new"), union
        )
        table.setItem(i, 2, ctx._rename_status_item(status, sev))
    table.blockSignals(False)


def _rename_status_item(ctx, status, severity):
    item = QTableWidgetItem(status)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    if severity == "error":
        item.setForeground(QColor("#c0392b"))
    elif severity == "warning":
        item.setForeground(QColor("#b58900"))
    return item


def _rename_on_cell(ctx, page, item, mode) -> None:
    if getattr(page, "_rename_updating", False) or page._rename_locked:
        return
    if item.column() != 1:
        return
    cfg = ctx._rename_cfg(page, mode)
    rows = cfg.get("rows") or []
    if item.row() >= len(rows):
        return
    rows[item.row()]["new"] = item.text()
    ctx._rename_mark_dirty(page)
    # Only the validation column changes; rebuild just that, so the editor
    # keeps focus while the user types.
    if mode == "file":
        ctx._rename_statuses_file(page)
    else:
        ctx._rename_statuses_column(page)


def _rename_table_rows(ctx, page, mode) -> list:
    cfg = ctx._rename_cfg(page, mode)
    table = (
        page._rename_file_table if mode == "file" else page._rename_column_table
    )
    rows = cfg.get("rows") or []
    out = []
    for i, r in enumerate(rows):
        item = table.item(i, 1)
        new = item.text().strip() if item is not None else r.get("new", "")
        out.append({"original": r["original"], "new": new})
    return out


def _rename_copy(ctx, page) -> None:
    mode = page._rename_params.get("mode", "file")
    rows = ctx._rename_table_rows(page, mode)
    lines = ["original name\tnew name"]
    for r in rows:
        original = (
            ctx._comp_file_name(r["original"], page._rename_task_id)
            if mode == "file" else str(r["original"])
        )
        lines.append(f"{original}\t{r['new']}")
    QApplication.clipboard().setText("\n".join(lines))


def _rename_paste(ctx, page) -> None:
    mode = page._rename_params.get("mode", "file")
    text = QApplication.clipboard().text()
    lines = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()]
    if not lines:
        return
    header = lines[0].split("\t")
    if header != ["original name", "new name"]:
        QMessageBox.warning(
            ctx.host, "Paste",
            "The pasted table must start with the header "
            "'original name\\tnew name' (copy the table first).",
        )
        return
    pasted = [ln.split("\t") for ln in lines[1:]]
    cfg = ctx._rename_cfg(page, mode)
    rows = cfg.get("rows") or []
    current_originals = [
        ctx._comp_file_name(r["original"], page._rename_task_id)
        if mode == "file" else str(r["original"])
        for r in rows
    ]
    if len(pasted) != len(current_originals):
        QMessageBox.warning(
            ctx.host, "Paste",
            "The pasted row count does not match the current table.",
        )
        return
    for idx, (expected, row) in enumerate(zip(current_originals, pasted)):
        if len(row) < 1 or row[0] != expected:
            QMessageBox.warning(
                ctx.host, "Paste",
                f"Row {idx + 1}: the original name does not match.",
            )
            return
    for r, row in zip(rows, pasted):
        r["new"] = (row[1] if len(row) > 1 else "").strip()
    ctx._rename_refresh(page)
    ctx._rename_mark_dirty(page)


def _rename_apply(ctx, page) -> None:
    """Store the valid-only rename map for the current mode."""
    mode = page._rename_params.get("mode", "file")
    cfg = ctx._rename_cfg(page, mode)
    rows = cfg.get("rows") or []
    applied = {}
    for r in rows:
        original = r["original"]
        new = (r.get("new") or "").strip()
        if not new:
            continue
        if mode == "file":
            taken = set()
            for other in rows:
                if other is r:
                    continue
                n = (other.get("new") or "").strip()
                if n:
                    taken.add(n)
            for rec in ctx._available_files(page._rename_task_id):
                if rec.get("parquet") not in (cfg.get("files") or []):
                    taken.add(rec.get("name", ""))
            status, _sev = ctx._rename_file_status(page, original, new, taken)
            if status:
                continue
        else:
            status, _sev = ctx._rename_column_status(
                page, original, new, set(ctx._rename_column_union(page))
            )
            if status == "Empty name" or status == "Invalid characters":
                continue
        applied[str(original)] = new
    cfg["rename"] = applied


def _rename_mark_dirty(ctx, page) -> None:
    page._rename_dirty = True
    ctx._rename_update_save_btn(page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _rename_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_rename_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._rename_dirty and not page._rename_locked)


def _rename_save_now(ctx, page) -> None:
    ctx._rename_apply(page)
    page._rename_task_def["params"] = page._rename_params
    if ctx.project is not None:
        ctx.project.save()
    page._rename_dirty = False
    ctx._rename_update_save_btn(page)
    ctx.statusBar().showMessage("Rename settings saved")
    ctx._module_saved(page)


def _rename_close(ctx, page) -> None:
    if getattr(page, "_rename_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Rename",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    ctx._close_module_page(page)
