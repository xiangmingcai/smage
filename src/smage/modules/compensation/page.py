"""Compensation page: file selection, matrix editing and saving."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smage.core.constants import MODULE_STATE_MODIFIED
from smage.widgets.gate_editor.canvas import _ReleaseFilter
from smage.widgets.resources import _drag_handle_pixmap, _lock_pixmap


def _comp_cell_color(value, max_positive, min_negative) -> QColor:
    """Compensation-matrix cell colour: positive red, negative blue, 0 white."""
    if value > 0 and max_positive > 0:
        frac = min(1.0, float(value) / float(max_positive))
        shade = int(255 - 190 * frac)
        return QColor(255, shade, shade)
    if value < 0 and min_negative < 0:
        frac = min(1.0, float(value) / float(min_negative))
        shade = int(255 - 190 * frac)
        return QColor(shade, shade, 255)
    return QColor(255, 255, 255)


def _build_compensation_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Compensation page: matrix (5) | files (1)."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Toolbar with Save/Close.
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
    close_btn.clicked.connect(lambda _=False: ctx._comp_close(page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("files", [])
    params.setdefault("params", [])
    params.setdefault("matrix", [])
    page._comp_task_def = task_def
    page._comp_task_id = task_def.get("id")
    page._comp_params = params
    page._comp_files = params["files"]
    page._comp_names = params["params"]
    page._comp_matrix = params["matrix"]
    page._comp_locked = locked
    page._comp_save_btn = save_btn
    page._comp_dirty = False
    page._comp_snapshot = copy.deepcopy(params)
    page._comp_order_timer = QTimer(ctx.host)
    page._comp_order_timer.setSingleShot(True)
    page._comp_order_timer.setInterval(400)
    page._comp_order_timer.timeout.connect(
        lambda: ctx._comp_reordered(page)
    )
    save_btn.clicked.connect(lambda _=False: ctx._comp_save_now(page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QHBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(12)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    # Matrix on the left; the file list (with "Set files") on the right.
    splitter.addWidget(ctx._comp_matrix_panel(page))
    splitter.addWidget(ctx._comp_files_panel(page))
    splitter.setStretchFactor(0, 5)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([1100, 220])
    row.addWidget(splitter, 1)
    layout.addWidget(content, 1)

    ctx._comp_refresh(page)
    ctx._comp_update_save_btn(page)
    return page


def _comp_files_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    set_btn = QPushButton("Set Files")
    set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_btn.setEnabled(not page._comp_locked and bool(page._comp_names))
    set_btn.clicked.connect(lambda _=False: ctx._comp_set_files(page))
    lay.addWidget(set_btn)
    files_list = QListWidget()
    files_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: none; }"
    )
    lay.addWidget(files_list, 1)
    page._comp_files_list = files_list
    page._comp_setfiles_btn = set_btn
    return panel


def _comp_matrix_panel(ctx, page) -> QWidget:
    panel = ctx._build_panel_frame()
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    top = QHBoxLayout()
    set_btn = QPushButton("Set Compensation Matrix")
    set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_btn.setEnabled(not page._comp_locked)
    set_btn.clicked.connect(lambda _=False: ctx._comp_set_params(page))
    top.addWidget(set_btn)
    top.addStretch()
    copy_btn = QPushButton("Copy Matrix")
    paste_btn = QPushButton("Paste Matrix")
    for btn in (copy_btn, paste_btn):
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.clicked.connect(lambda _=False: ctx._comp_copy_matrix(page))
    paste_btn.clicked.connect(lambda _=False: ctx._comp_paste_matrix(page))
    top.addWidget(copy_btn)
    top.addWidget(paste_btn)
    lay.addLayout(top)

    table = QTableWidget()
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    table.verticalHeader().setDefaultSectionSize(26)
    table.verticalHeader().setSectionsMovable(True)
    table.verticalHeader().sectionMoved.connect(
        lambda *_: page._comp_order_timer.start()
    )
    # Apply the new order as soon as the drag is released (the timer above
    # is only a fallback), so a mid-drag rebuild cannot misplace rows.
    release_filter = _ReleaseFilter(lambda: ctx._comp_reordered(page))
    table.verticalHeader().viewport().installEventFilter(release_filter)
    page._comp_release_filter = release_filter
    table.horizontalHeader().setDefaultSectionSize(66)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
        | QAbstractItemView.EditTrigger.AnyKeyPressed
    )
    table.itemChanged.connect(lambda item: ctx._comp_item_changed(page, item))
    lay.addWidget(table, 1)

    hint = QLabel(
        "No parameters selected.\n"
        'Click "Set Compensation Matrix" to choose the parameters.'
    )
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hint.setStyleSheet("color: #888888;")
    hint.setWordWrap(True)
    lay.addWidget(hint, 1)

    page._comp_table = table
    page._comp_hint = hint
    page._comp_setparams_btn = set_btn
    page._comp_copy_btn = copy_btn
    page._comp_paste_btn = paste_btn
    return panel


def _comp_columns_for(ctx, page, path) -> list:
    return ctx._columns_for(path, page._comp_task_id)


def _comp_all_columns(ctx, page) -> list:
    columns = []
    for rec in ctx._available_files(page._comp_task_id):
        for name in ctx._comp_columns_for(page, rec.get("parquet")):
            if name not in columns:
                columns.append(name)
    return columns


def _comp_refresh(ctx, page) -> None:
    files_list = page._comp_files_list
    files_list.blockSignals(True)
    files_list.clear()
    for path in page._comp_files:
        item = QListWidgetItem(ctx._comp_file_name(path, page._comp_task_id))
        item.setData(Qt.ItemDataRole.UserRole, path)
        files_list.addItem(item)
    files_list.blockSignals(False)
    editable = not page._comp_locked
    page._comp_setfiles_btn.setEnabled(editable and bool(page._comp_names))
    page._comp_setfiles_btn.setToolTip(
        "" if page._comp_names
        else "Set the compensation matrix parameters first"
    )
    page._comp_copy_btn.setEnabled(bool(page._comp_names))
    page._comp_paste_btn.setEnabled(editable and bool(page._comp_names))
    ctx._comp_rebuild_table(page)


def _comp_rebuild_table(ctx, page) -> None:
    table = page._comp_table
    names = page._comp_names
    matrix = page._comp_matrix
    table.blockSignals(True)
    table.clear()
    # Resetting the count clears the header's visual section order too;
    # otherwise the moved rows stay offset after a reorder.
    table.setRowCount(0)
    table.setColumnCount(0)
    table.setRowCount(len(names))
    table.setColumnCount(len(names))
    table.setHorizontalHeaderLabels(names)
    grip = QIcon(_drag_handle_pixmap())
    for i, name in enumerate(names):
        header_item = QTableWidgetItem(name)
        header_item.setIcon(grip)
        header_item.setToolTip("Drag to reorder rows (columns follow)")
        table.setVerticalHeaderItem(i, header_item)
    for i in range(len(names)):
        for j in range(len(names)):
            if i == j:
                item = QTableWidgetItem("100")
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setBackground(QColor("#e4e7ec"))
                item.setForeground(QColor("#5b6b82"))
            else:
                value = (
                    matrix[i][j]
                    if i < len(matrix) and j < len(matrix[i]) else 0
                )
                item = QTableWidgetItem(str(int(value)))
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEditable
                )
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(i, j, item)
    table.blockSignals(False)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
        if page._comp_locked
        else (
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
    )
    ctx._comp_refresh_colors(page)
    has_names = bool(names)
    table.setVisible(has_names)
    page._comp_hint.setVisible(not has_names)
    table.resizeColumnsToContents()
    for j in range(table.columnCount()):
        if table.columnWidth(j) < 56:
            table.setColumnWidth(j, 56)


def _comp_refresh_colors(ctx, page) -> None:
    table = page._comp_table
    matrix = page._comp_matrix
    positives = [
        v for i, row in enumerate(matrix)
        for j, v in enumerate(row) if i != j and v > 0
    ]
    negatives = [
        v for i, row in enumerate(matrix)
        for j, v in enumerate(row) if i != j and v < 0
    ]
    max_pos = max(positives) if positives else 0
    min_neg = min(negatives) if negatives else 0
    table.blockSignals(True)
    for i in range(min(table.rowCount(), len(matrix))):
        for j in range(min(table.columnCount(), len(matrix[i]))):
            if i == j:
                continue
            item = table.item(i, j)
            if item is None:
                continue
            item.setBackground(_comp_cell_color(matrix[i][j], max_pos, min_neg))
    table.blockSignals(False)


def _comp_item_changed(ctx, page, item) -> None:
    table = page._comp_table
    i, j = table.row(item), table.column(item)
    if page._comp_locked or i < 0 or j < 0 or i == j:
        return
    if i >= len(page._comp_matrix) or j >= len(page._comp_matrix[i]):
        return
    text = item.text().strip()
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        table.blockSignals(True)
        item.setText(str(int(page._comp_matrix[i][j])))
        table.blockSignals(False)
        return
    if value == page._comp_matrix[i][j]:
        return
    page._comp_matrix[i][j] = value
    ctx._comp_refresh_colors(page)
    ctx._comp_mark_dirty(page)


def _comp_set_files(ctx, page) -> None:
    if not page._comp_names:
        QMessageBox.information(
            ctx.host,
            "Set Files",
            "Set the compensation matrix parameters first.",
        )
        return
    records = ctx._available_files(page._comp_task_id)
    names = [rec.get("name", "") for rec in records]
    current = [ctx._comp_file_name(path, page._comp_task_id) for path in page._comp_files]
    chosen = ctx._pick_items_dialog(
        "Select files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    paths = [
        rec.get("parquet") for rec in records
        if rec.get("name", "") in chosen_set
    ]
    problems = []
    for path in paths:
        columns = set(ctx._comp_columns_for(page, path))
        missing = [name for name in page._comp_names if name not in columns]
        if missing:
            problems.append((ctx._comp_file_name(path, page._comp_task_id), missing))
    if problems:
        ctx._comp_show_missing(problems)
        return
    page._comp_files[:] = paths
    ctx._comp_refresh(page)
    ctx._comp_mark_dirty(page)


def _comp_set_params(ctx, page) -> None:
    all_columns = ctx._comp_all_columns(page)
    current = list(page._comp_names)
    chosen = ctx._pick_items_dialog(
        "Compensation parameters", all_columns, current,
        "Filter parameters...",
    )
    if chosen is None:
        return
    chosen_set = set(chosen)
    new_names = [name for name in all_columns if name in chosen_set]
    added = [name for name in new_names if name not in current]
    problems = []
    for path in page._comp_files:
        columns = set(ctx._comp_columns_for(page, path))
        missing = [name for name in added if name not in columns]
        if missing:
            problems.append((ctx._comp_file_name(path, page._comp_task_id), missing))
    if problems:
        ctx._comp_show_missing(problems)
        return
    ctx._comp_apply_params(page, new_names)
    ctx._comp_mark_dirty(page)


def _comp_show_missing(ctx, problems) -> None:
    lines = ["These parameters are missing from the selected files:", ""]
    for name, missing in problems:
        lines.append(f"{name}: {', '.join(missing)}")
    QMessageBox.warning(ctx.host, "Compensation", "\n".join(lines))


def _comp_apply_params(ctx, page, new_names) -> None:
    """Add/remove/reorder parameters, keeping the numbers by name."""
    old_names = list(page._comp_names)
    old_matrix = page._comp_matrix
    by_name = {}
    for i, row_name in enumerate(old_names):
        for j, col_name in enumerate(old_names):
            if i < len(old_matrix) and j < len(old_matrix[i]):
                by_name[(row_name, col_name)] = old_matrix[i][j]
    matrix = []
    for i, row_name in enumerate(new_names):
        row = []
        for j, col_name in enumerate(new_names):
            if i == j:
                row.append(100)
            else:
                row.append(by_name.get((row_name, col_name), 0))
        matrix.append(row)
    page._comp_names[:] = list(new_names)
    page._comp_matrix[:] = matrix
    ctx._comp_refresh(page)


def _comp_reordered(ctx, page) -> None:
    table = page._comp_table
    header = table.verticalHeader()
    count = len(page._comp_names)
    order = sorted(range(count), key=lambda i: header.visualIndex(i))
    if order == list(range(count)):
        return
    page._comp_names[:] = [page._comp_names[i] for i in order]
    page._comp_matrix[:] = [
        [page._comp_matrix[i][j] for j in order] for i in order
    ]
    ctx._comp_rebuild_table(page)
    ctx._comp_mark_dirty(page)


def _comp_copy_matrix(ctx, page) -> None:
    names = list(page._comp_names)
    if not names:
        return
    lines = ["\t".join([""] + names)]
    for i, name in enumerate(names):
        values = [
            str(int(page._comp_matrix[i][j]))
            for j in range(len(names))
        ]
        lines.append("\t".join([name] + values))
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Compensation matrix copied")


def _comp_paste_matrix(ctx, page) -> None:
    names = list(page._comp_names)
    if not names:
        return
    text = QApplication.clipboard().text()
    raw_lines = [
        line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    if not raw_lines:
        QMessageBox.warning(
            ctx.host, "Paste Matrix", "The clipboard does not contain a matrix."
        )
        return
    header = [cell.strip() for cell in raw_lines[0].split("\t")]
    if header and not header[0]:
        header = header[1:]
    values = {}
    row_names = []
    for line in raw_lines[1:]:
        cells = [cell.strip() for cell in line.split("\t")]
        if not cells or not cells[0]:
            continue
        row_name = cells[0]
        row_names.append(row_name)
        numbers = cells[1:]
        if len(numbers) != len(header):
            QMessageBox.warning(
                ctx.host,
                "Paste Matrix",
                "The pasted matrix has the wrong number of columns.",
            )
            return
        for col_name, raw in zip(header, numbers):
            try:
                values[(row_name, col_name)] = int(float(raw))
            except (TypeError, ValueError):
                QMessageBox.warning(
                    ctx.host,
                    "Paste Matrix",
                    f'"{raw}" is not an integer percentage.',
                )
                return
    if set(header) != set(names) or set(row_names) != set(names):
        QMessageBox.warning(
            ctx.host,
            "Paste Matrix",
            "The pasted matrix uses different parameter names.\n"
            "It was not applied.",
        )
        return
    matrix = []
    for i, row_name in enumerate(names):
        row = []
        for j, col_name in enumerate(names):
            if i == j:
                row.append(100)
            else:
                row.append(values.get((row_name, col_name), 0))
        matrix.append(row)
    page._comp_matrix[:] = matrix
    ctx._comp_rebuild_table(page)
    ctx._comp_mark_dirty(page)


def _comp_mark_dirty(ctx, page) -> None:
    page._comp_dirty = True
    ctx._comp_update_save_btn(page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _comp_save_now(ctx, page) -> None:
    page._comp_task_def["params"] = page._comp_params
    if ctx.project is not None:
        ctx.project.save()
    page._comp_snapshot = copy.deepcopy(page._comp_params)
    page._comp_dirty = False
    ctx._comp_update_save_btn(page)
    ctx.statusBar().showMessage("Compensation saved")
    ctx._module_saved(page)


def _comp_update_save_btn(ctx, page) -> None:
    page._comp_save_btn.setEnabled(page._comp_dirty and not page._comp_locked)


def _comp_close(ctx, page) -> None:
    if page._comp_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Compensation",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._comp_task_def["params"] = page._comp_snapshot
    timer = getattr(page, "_comp_order_timer", None)
    if timer is not None:
        timer.stop()
    ctx._close_module_page(page)
