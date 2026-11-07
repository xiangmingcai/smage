"""Subset page: file and column visibility filters."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from smage.core.constants import MODULE_STATE_MODIFIED
from smage.widgets.resources import _lock_pixmap
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


def _build_subset_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Subset page: summary | data files (1) | columns (1)."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    bar = QFrame()
    bar.setObjectName("moduleBar")
    tl = QHBoxLayout(bar)
    tl.setContentsMargins(12, 6, 12, 6)
    tl.setSpacing(4)
    tl.addWidget(QLabel(f"<b>{_data_files._module_display_title(ctx, task_def.get('id'), name)}</b>"))
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
    close_btn.clicked.connect(lambda _=False: _subset_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("files", [])
    params.setdefault("columns", [])
    page._subset_task_def = task_def
    page._subset_task_id = task_def.get("id")
    page._subset_params = params
    page._subset_locked = locked
    page._subset_save_btn = save_btn
    page._subset_dirty = False
    # Edits stay in a draft until Save, so the filter changes downstream
    # only when the user commits it (the document's "Save to take effect").
    page._subset_draft_files = list(params.get("files") or [])
    page._subset_draft_columns = list(params.get("columns") or [])
    save_btn.clicked.connect(lambda _=False: _subset_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    summary = QLabel("")
    summary.setStyleSheet("color: #3c4043; font-weight: bold;")
    row.addWidget(summary)
    page._subset_summary = summary

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_subset_files_panel(ctx, page))
    splitter.addWidget(_subset_columns_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([440, 440])
    row.addWidget(splitter, 1)
    layout.addWidget(content, 1)

    _subset_refresh(ctx, page)
    return page


def _subset_files_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Data files</b>"))
    hint = QLabel(
        "Only the files kept here stay visible to downstream modules. "
        "An empty selection keeps every file."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    btn = QPushButton("Select files…")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setEnabled(not page._subset_locked)
    lay.addWidget(btn)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(box, 1)
    page._subset_files_box = box
    btn.clicked.connect(lambda _=False: _subset_select_files(ctx, page))
    return panel


def _subset_columns_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Columns</b>"))
    hint = QLabel(
        "Only the columns kept here stay visible to downstream modules. "
        "The list is the union of the kept files' columns. "
        "An empty selection keeps every column."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    btn = QPushButton("Select columns…")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setEnabled(not page._subset_locked)
    lay.addWidget(btn)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(box, 1)
    page._subset_columns_box = box
    btn.clicked.connect(lambda _=False: _subset_select_columns(ctx, page))
    return panel


def _subset_column_options(ctx, page) -> list:
    """Columns available for selection: union over the kept (draft) files."""
    paths = list(getattr(page, "_subset_draft_files", []) or [])
    if not paths:
        # No file filter yet, so every visible file counts as kept.
        paths = [
            rec.get("parquet")
            for rec in _data_files._available_files(ctx, page._subset_task_id)
        ]
    columns = []
    for path in paths:
        for name in _data_columns._columns_for(ctx, path, page._subset_task_id):
            if name not in columns:
                columns.append(name)
    return columns


def _subset_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._subset_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [_data_naming._comp_file_name(ctx, p, page._subset_task_id) for p in page._subset_draft_files]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._subset_draft_files = [by_name[n] for n in chosen if n in by_name]
    # The column list follows the kept files, so drop selections that are
    # no longer offered by any of them.
    options = set(_subset_column_options(ctx, page))
    page._subset_draft_columns = [
        c for c in page._subset_draft_columns if c in options
    ]
    _subset_refresh(ctx, page)
    _subset_mark_dirty(ctx, page)


def _subset_select_columns(ctx, page) -> None:
    options = _subset_column_options(ctx, page)
    if not options:
        QMessageBox.information(
            ctx.host, "Select columns", "There are no columns to choose from."
        )
        return
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select columns", options, page._subset_draft_columns, "Filter columns..."
    )
    if chosen is None:
        return
    page._subset_draft_columns = list(chosen)
    _subset_refresh(ctx, page)
    _subset_mark_dirty(ctx, page)


def _subset_refresh(ctx, page) -> None:
    files_box = getattr(page, "_subset_files_box", None)
    cols_box = getattr(page, "_subset_columns_box", None)
    files = list(getattr(page, "_subset_draft_files", []) or [])
    columns = list(getattr(page, "_subset_draft_columns", []) or [])
    if files_box is not None:
        files_box.clear()
        for path in files:
            files_box.addItem(_data_naming._comp_file_name(ctx, path, page._subset_task_id))
    if cols_box is not None:
        cols_box.clear()
        for name in columns:
            cols_box.addItem(str(name))
    summary = getattr(page, "_subset_summary", None)
    if summary is not None:
        file_text = (
            f"{len(files)} file(s) selected" if files else "All files visible"
        )
        col_text = (
            f"{len(columns)} column(s) selected"
            if columns else "All columns visible"
        )
        summary.setText(f"{file_text} · {col_text}")
    _subset_update_save_btn(ctx, page)


def _subset_mark_dirty(ctx, page) -> None:
    page._subset_dirty = True
    _subset_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _subset_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_subset_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._subset_dirty and not page._subset_locked)


def _subset_save_now(ctx, page) -> None:
    params = page._subset_params
    params["files"] = list(page._subset_draft_files)
    params["columns"] = list(page._subset_draft_columns)
    page._subset_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._subset_dirty = False
    _subset_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Subset settings saved")
    ctx._module_saved(page)


def _subset_close(ctx, page) -> None:
    if getattr(page, "_subset_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Subset",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    ctx._close_module_page(page)
