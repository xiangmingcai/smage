"""Region Size module: pixel area of every Region in every image file."""
from __future__ import annotations

import copy
import queue
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from smage.core.constants import (
    MODULE_STATE_MODIFIED,
    MODULE_STATE_UNMODIFIED,
    _FEATURE_LOG_LIMIT,
)
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.data import regions as _data_regions
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.resources import _drag_handle_pixmap, _lock_pixmap


def _rs_image_records(ctx, task_id):
    return [
        rec for rec in _data_files._available_files(ctx, task_id)
        if rec.get("parquet")
        and _data_outputs._seg_is_image_file(ctx, rec)
    ]


def _rs_file_name(ctx, path, task_id) -> str:
    try:
        return _data_naming._comp_file_name(ctx, path, task_id)
    except Exception:  # noqa: BLE001
        return Path(str(path)).name


def _rs_region_key(task_id, region_id) -> str:
    return f"{task_id}|{region_id}"


def _rs_region_options(ctx, task_id) -> list:
    """Converted, downstream-visible Region channels upstream of this module.

    A Region that was not converted, or whose Downstream visible box is off,
    is not listed: downstream modules no longer see Regions as gates.
    """
    raw = _data_regions._region_channel_options(ctx, task_id)
    counts = {}
    for item in raw:
        name = str(item.get("name") or "")
        counts[name] = counts.get(name, 0) + 1
    out = []
    for item in raw:
        name = str(item.get("name") or item.get("region_id"))
        label = name
        if counts.get(name, 0) > 1:
            label = f"{item['module_name']}/{name}"
        out.append({
            "key": item["key"],
            "label": label,
            "task_id": item["task_id"],
            "module_name": item["module_name"],
            "region": item["region"],
            "files": list((item.get("files") or {}).keys()),
            "channels": dict(item.get("files") or {}),
        })
    return out


def _rs_mark_dirty(ctx, page) -> None:
    page._rs_dirty = True
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)
    _rs_update_save_btn(ctx, page)


def _rs_update_save_btn(ctx, page) -> None:
    """Save stores calculated results; without pending results it is off."""
    button = getattr(page, "_rs_save_btn", None)
    if button is None:
        return
    button.setEnabled(
        not page._rs_locked and getattr(page, "_rs_pending", None) is not None
    )


def _rs_log(ctx, page, text) -> None:
    """Append a timestamped line to the Region Size log (saved with params)."""
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_rs_log_lines", None)
    if lines is None:
        lines = []
        page._rs_log_lines = lines
    for line in str(text).splitlines():
        if not line.strip():
            continue
        entry = f"[{ts}] {line}"
        lines.append(entry)
        box = getattr(page, "_rs_log", None)
        if box is not None:
            box.appendPlainText(entry)
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    page._rs_params["log"] = list(lines)


def _rs_current_outputs(page) -> dict:
    """Results shown in the table: pending (unsaved) first, then saved."""
    pending = getattr(page, "_rs_pending", None)
    if pending is not None:
        return pending
    return page._rs_params.get("outputs") or {}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _build_region_size_page(ctx, name: str, task_def: dict,
                            locked: bool) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    bar = QFrame()
    bar.setObjectName("moduleBar")
    tl = QHBoxLayout(bar)
    tl.setContentsMargins(12, 6, 12, 6)
    tl.setSpacing(4)
    tl.addWidget(QLabel(
        f"<b>{_data_files._module_display_title(ctx, task_def.get('id'), name)}</b>"
    ))
    if locked:
        icon = QLabel()
        icon.setPixmap(_lock_pixmap())
        icon.setFixedSize(14, 14)
        icon.setToolTip("Locked — a downstream generate module saved data")
        tl.addWidget(icon)
    tl.addStretch()
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setEnabled(False)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    if "outputs" not in params:
        params["outputs"] = {}
    params.setdefault("log", [])
    page._rs_task_def = task_def
    page._rs_task_id = task_def.get("id")
    page._rs_params = params
    page._rs_locked = locked
    page._rs_dirty = False
    page._rs_options = _rs_region_options(ctx, page._rs_task_id)
    stored_files = params.get("files")
    if stored_files is None:
        # A new module starts empty; the user chooses the image files.
        stored_files = []
        params["files"] = stored_files
    page._rs_files = [str(p) for p in stored_files or []]
    stored_regions = params.get("regions")
    if stored_regions is None:
        # A new module starts with no Regions selected; the user chooses them.
        stored_regions = []
        params["regions"] = stored_regions
    page._rs_selected = [str(key) for key in stored_regions or []]
    page._rs_pending = None
    page._rs_log_lines = list(params.get("log") or [])
    page._rs_token = 0
    page._rs_state = None
    page._rs_worker = _RegionSizeWorker()
    page._rs_timer = QTimer(page)
    page._rs_timer.setInterval(120)
    page._rs_timer.timeout.connect(lambda: _rs_poll(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)
    left = _rs_files_panel(ctx, page)
    middle = _rs_regions_panel(ctx, page)
    right = _rs_table_panel(ctx, page)
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.addWidget(left)
    split.addWidget(middle)
    split.addWidget(right)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 1)
    split.setStretchFactor(2, 3)
    split.setSizes([260, 300, 900])
    cl.addWidget(split, 1)
    layout.addWidget(content, 1)

    save_btn.clicked.connect(lambda _=False: _rs_save(ctx, page))
    close_btn.clicked.connect(lambda _=False: _rs_close(ctx, page))
    page._rs_save_btn = save_btn
    _rs_rebuild_panels(ctx, page)
    _rs_rebuild_table(ctx, page)
    _rs_update_save_btn(ctx, page)
    return page


def _rs_files_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Data files</b>"))
    hint = QLabel("Only image files are listed.")
    hint.setStyleSheet("color: #6b7785; font-size: 8pt;")
    lay.addWidget(hint)
    select = QPushButton("Select files…")
    select.setCursor(Qt.CursorShape.PointingHandCursor)
    select.setEnabled(not page._rs_locked)
    select.clicked.connect(lambda _=False: _rs_select_files(ctx, page))
    lay.addWidget(select)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    lay.addWidget(box, 1)
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    page._rs_files_list = box
    page._rs_files_count = count
    return panel


def _rs_regions_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Regions</b>"))
    hint = QLabel("Regions from every upstream Region module.")
    hint.setStyleSheet("color: #6b7785; font-size: 8pt;")
    lay.addWidget(hint)
    select = QPushButton("Select regions…")
    select.setCursor(Qt.CursorShape.PointingHandCursor)
    select.setEnabled(not page._rs_locked)
    select.clicked.connect(lambda _=False: _rs_select_regions(ctx, page))
    lay.addWidget(select)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    lay.addWidget(box, 1)
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    lay.addStretch()
    calculate = QPushButton("Calculate")
    calculate.setCursor(Qt.CursorShape.PointingHandCursor)
    calculate.setEnabled(not page._rs_locked)
    calculate.clicked.connect(lambda _=False: _rs_start_calculate(ctx, page))
    lay.addWidget(calculate)
    status = QLabel("")
    status.setWordWrap(True)
    status.setStyleSheet("color: #6b7785;")
    lay.addWidget(status)
    progress = QProgressBar()
    progress.setRange(0, 100)
    progress.setValue(0)
    progress.hide()
    lay.addWidget(progress)
    page._rs_regions_list = box
    page._rs_regions_count = count
    page._rs_calculate_btn = calculate
    page._rs_status = status
    page._rs_progress = progress
    return panel


def _rs_table_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    split = QSplitter(Qt.Orientation.Vertical)
    split.setChildrenCollapsible(False)
    top = QWidget()
    top_lay = QVBoxLayout(top)
    top_lay.setContentsMargins(0, 0, 0, 0)
    top_lay.setSpacing(6)
    header = QHBoxLayout()
    header.addWidget(QLabel("<b>Area (pixels)</b>"))
    header.addStretch()
    copy = QPushButton("Copy table")
    copy.setCursor(Qt.CursorShape.PointingHandCursor)
    copy.clicked.connect(lambda _=False: _rs_copy(ctx, page))
    header.addWidget(copy)
    top_lay.addLayout(header)
    table = _RegionSizeTable()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["", "File name"])
    table.horizontalHeader().setStretchLastSection(False)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    table.rowOrderRequested.connect(
        lambda source, target: _rs_reorder_rows(ctx, page, source, target)
    )
    table.horizontalHeader().setSectionsMovable(True)
    table.horizontalHeader().sectionMoved.connect(
        lambda logical, old, new: _rs_column_moved(ctx, page, logical, old, new)
    )
    top_lay.addWidget(table, 1)

    bottom = QWidget()
    bottom_lay = QVBoxLayout(bottom)
    bottom_lay.setContentsMargins(0, 0, 0, 0)
    bottom_lay.setSpacing(4)
    bottom_lay.addWidget(QLabel("<b>Log</b>"))
    log = _widgets_panels._log_widget()
    for line in getattr(page, "_rs_log_lines", []):
        log.appendPlainText(line)
    bottom_lay.addWidget(log, 1)

    split.addWidget(top)
    split.addWidget(bottom)
    split.setStretchFactor(0, 4)
    split.setStretchFactor(1, 1)
    split.setSizes([520, 130])
    lay.addWidget(split, 1)
    page._rs_table = table
    page._rs_copy_btn = copy
    page._rs_log = log
    page._rs_split = split
    return panel


# ---------------------------------------------------------------------------
# Selection panels
# ---------------------------------------------------------------------------

def _rs_rebuild_panels(ctx, page) -> None:
    page._rs_files_list.clear()
    for path in page._rs_files:
        from PySide6.QtWidgets import QListWidgetItem
        item = QListWidgetItem(f"{_rs_file_name(ctx, path, page._rs_task_id)}")
        page._rs_files_list.addItem(item)
    page._rs_files_count.setText(f"{len(page._rs_files)} file(s)")

    by_key = {item["key"]: item for item in page._rs_options}
    page._rs_selected = [
        key for key in page._rs_selected if key in by_key
    ]
    page._rs_regions_list.clear()
    from PySide6.QtWidgets import QListWidgetItem
    for key in page._rs_selected:
        item = by_key.get(key)
        if item is not None:
            page._rs_regions_list.addItem(QListWidgetItem(item["label"]))
    page._rs_regions_count.setText(
        f"{len(page._rs_selected)} region(s)"
    )
    page._rs_calculate_btn.setEnabled(
        not page._rs_locked and bool(page._rs_files)
        and bool(page._rs_selected)
    )


def _rs_select_files(ctx, page) -> None:
    records = _rs_image_records(ctx, page._rs_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _rs_file_name(ctx, path, page._rs_task_id) for path in page._rs_files
    ]
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Select image files", names, current, "Filter files…",
    )
    if chosen is None:
        return
    page._rs_files = [str(by_name[name]) for name in chosen if by_name.get(name)]
    page._rs_params["files"] = list(page._rs_files)
    page._rs_pending = None
    _rs_mark_dirty(ctx, page)
    _rs_rebuild_panels(ctx, page)
    _rs_stale_hint(page)


def _rs_select_regions(ctx, page) -> None:
    page._rs_options = _rs_region_options(ctx, page._rs_task_id)
    labels = [item["label"] for item in page._rs_options]
    by_label = {item["label"]: item["key"] for item in page._rs_options}
    current = [
        item["label"] for item in page._rs_options
        if item["key"] in page._rs_selected
    ]
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Select regions", labels, current, "Filter regions…",
    )
    if chosen is None:
        return
    page._rs_selected = [by_label[name] for name in chosen if by_label.get(name)]
    page._rs_params["regions"] = list(page._rs_selected)
    page._rs_pending = None
    _rs_mark_dirty(ctx, page)
    _rs_rebuild_panels(ctx, page)
    _rs_stale_hint(page)


def _rs_stale_hint(page) -> None:
    page._rs_status.setText(
        "Selections changed. Press Calculate to update the table."
    )


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

def _rs_start_calculate(ctx, page) -> None:
    if page._rs_locked or page._rs_state is not None:
        return
    if not page._rs_files:
        QMessageBox.information(ctx.host, "Region Size", "Select image files first.")
        return
    page._rs_options = _rs_region_options(ctx, page._rs_task_id)
    by_key = {item["key"]: item for item in page._rs_options}
    descriptors = [
        copy.deepcopy(by_key[key]) for key in page._rs_selected
        if key in by_key
    ]
    if not descriptors:
        QMessageBox.information(
            ctx.host, "Region Size", "Select at least one Region first."
        )
        return
    page._rs_token += 1
    token = page._rs_token
    page._rs_state = {
        "token": token,
        "files": list(page._rs_files),
        "regions": [{
            "key": item["key"],
            "label": item["label"],
        } for item in descriptors],
        "values": {},
        "done": 0,
        "total": len(page._rs_files),
    }
    page._rs_progress.setRange(0, len(page._rs_files))
    page._rs_progress.setValue(0)
    page._rs_progress.show()
    page._rs_status.setText("Reading Region channels…")
    page._rs_calculate_btn.setEnabled(False)
    _rs_log(
        ctx, page,
        f"===== Calculate: {len(page._rs_files)} file(s) × "
        f"{len(descriptors)} Region(s) =====",
    )
    page._rs_worker.request(
        token,
        ctx,
        page._rs_task_id,
        list(page._rs_files),
        descriptors,
    )
    page._rs_timer.start()


def _rs_poll(ctx, page) -> None:
    state = page._rs_state
    if state is None:
        page._rs_timer.stop()
        return
    finished = False
    for message in page._rs_worker.poll():
        if not message:
            continue
        kind = message[0]
        if len(message) < 2 or message[1] != state["token"]:
            continue
        if kind == "progress":
            _, _token, done, total, _text = message
            index = max(0, min(int(done), len(state["files"]) - 1))
            name = _rs_file_name(
                ctx, state["files"][index], page._rs_task_id
            )
            text = f"Reading {name} ({index + 1}/{total})…"
            page._rs_progress.setRange(0, total)
            page._rs_progress.setValue(done)
            page._rs_status.setText(text)
            _rs_log(ctx, page, text)
        elif kind == "file":
            _, _token, path, values, error = message
            state["values"][str(path)] = values
            state["done"] += 1
            page._rs_progress.setValue(state["done"])
            if error:
                page._rs_status.setText(error)
                _rs_log(
                    ctx, page,
                    f"✗ {_rs_file_name(ctx, path, page._rs_task_id)}: {error}",
                )
        elif kind == "done":
            finished = True
    if finished:
        page._rs_timer.stop()
        _rs_finish(ctx, page)


def _rs_finish(ctx, page) -> None:
    state = page._rs_state
    page._rs_state = None
    page._rs_progress.hide()
    if not state:
        _rs_rebuild_panels(ctx, page)
        return
    files = [
        path for path in state.get("files") or []
        if state.get("values", {}).get(path)
    ]
    if not files:
        page._rs_status.setText(
            "Calculation failed. Check that x/y coordinates are available."
        )
        _rs_log(
            ctx, page,
            "✗ Calculation failed. Check that the selected image files "
            "have converted Region channels.",
        )
        _rs_rebuild_panels(ctx, page)
        _rs_update_save_btn(ctx, page)
        return
    files.sort(key=lambda path: _rs_file_name(
        ctx, path, page._rs_task_id
    ).lower())
    regions = list(state.get("regions") or [])
    regions.sort(key=lambda item: item["label"].lower())
    values = state.get("values") or {}
    labels = [
        _rs_file_name(ctx, path, page._rs_task_id) for path in files
    ]
    seen = {}
    for index, label in enumerate(labels):
        count = seen.get(label, 0) + 1
        seen[label] = count
        if count > 1:
            labels[index] = f"{label} ({count})"
    outputs = {
        "files": files,
        "file_labels": labels,
        "regions": regions,
        "values": {
            path: values.get(path, {}) for path in files
        },
    }
    # Keep the result in memory until Save; Calculate no longer writes it.
    page._rs_pending = outputs
    _rs_mark_dirty(ctx, page)
    page._rs_status.setText(
        f"Calculated {len(files)} file(s) × {len(regions)} region(s). "
        "Press Save to store the table and lock upstream modules."
    )
    _rs_log(
        ctx, page,
        f"Calculated {len(files)} file(s) × {len(regions)} Region(s). "
        "Press Save to store the table.",
    )
    _rs_rebuild_panels(ctx, page)
    _rs_rebuild_table(ctx, page)
    _rs_update_save_btn(ctx, page)


def _rs_save(ctx, page) -> None:
    """Store the calculated table and lock the upstream modules."""
    if page._rs_locked:
        return
    outputs = getattr(page, "_rs_pending", None)
    if not outputs:
        return
    page._rs_params["outputs"] = outputs
    page._rs_params["files"] = list(page._rs_files)
    page._rs_params["regions"] = list(page._rs_selected)
    page._rs_task_def["output"] = {"saved": True}
    page._rs_pending = None
    page._rs_dirty = False
    if ctx.project is not None:
        ctx.project.save()
    ctx._refresh_workflow_tree()
    ctx._module_set_state(page, MODULE_STATE_UNMODIFIED)
    files = len(outputs.get("files") or [])
    regions = len(outputs.get("regions") or [])
    page._rs_status.setText(
        f"Saved {files} file(s) × {regions} Region(s). "
        "Upstream modules are now locked."
    )
    _rs_log(
        ctx, page,
        f"===== Saved {files} file(s) × {regions} Region(s); "
        "upstream modules are locked =====",
    )
    _rs_update_save_btn(ctx, page)
    _rs_rebuild_panels(ctx, page)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

class _RSRowHandle(QLabel):
    def __init__(self, row: int) -> None:
        super().__init__()
        self._row = row
        self._start = None
        self.setPixmap(_drag_handle_pixmap())
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder rows")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._start is None:
            return
        if (event.position().toPoint() - self._start).manhattanLength() < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"rs-row:{self._row}")
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._start = None

    def mouseReleaseEvent(self, event) -> None:
        self._start = None
        super().mouseReleaseEvent(event)


class _RegionSizeTable(QTableWidget):
    rowOrderRequested = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self._drop_index = -1
        self._indicator = QFrame(self.viewport())
        self._indicator.setStyleSheet("background: #4285f4;")
        self._indicator.setFixedHeight(2)
        self._indicator.hide()

    @staticmethod
    def _source(event) -> int:
        text = event.mimeData().text() if event.mimeData().hasText() else ""
        if not text.startswith("rs-row:"):
            return -1
        try:
            return int(text.split(":", 1)[1])
        except ValueError:
            return -1

    def _target_row(self, y: int) -> int:
        row = self.rowAt(y)
        if row < 0:
            return self.rowCount()
        rect = self.visualRect(self.model().index(row, 1))
        return row + (1 if y > rect.center().y() else 0)

    def dragEnterEvent(self, event) -> None:
        if self._source(event) >= 0:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        source = self._source(event)
        if source < 0:
            event.ignore()
            return
        y = event.position().toPoint().y()
        target = self._target_row(y)
        if target < self.rowCount():
            top = self.visualRect(self.model().index(target, 1)).top()
        elif self.rowCount():
            top = self.visualRect(
                self.model().index(self.rowCount() - 1, 1)
            ).bottom()
        else:
            top = 0
        self._indicator.setGeometry(0, max(0, top - 1), self.viewport().width(), 2)
        self._indicator.show()
        self._indicator.raise_()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._indicator.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._indicator.hide()
        source = self._source(event)
        target = self._target_row(event.position().toPoint().y())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if source >= 0:
            self.rowOrderRequested.emit(source, target)


def _rs_rebuild_table(ctx, page) -> None:
    page._rs_updating = True
    try:
        _rs_rebuild_table_inner(ctx, page)
    finally:
        page._rs_updating = False


def _rs_rebuild_table_inner(ctx, page) -> None:
    table = page._rs_table
    outputs = _rs_current_outputs(page)
    files = list(outputs.get("files") or [])
    file_labels = list(outputs.get("file_labels") or [])
    regions = list(outputs.get("regions") or [])
    values = outputs.get("values") or {}
    has_results = bool(files)
    if not files:
        # No results yet: show the current selection with blank values.
        files = list(page._rs_files)
        file_labels = [
            _rs_file_name(ctx, path, page._rs_task_id) for path in files
        ]
        by_key = {item["key"]: item for item in page._rs_options}
        regions = [
            {"key": key, "label": by_key[key]["label"]}
            for key in page._rs_selected if key in by_key
        ]
        values = {}
    table.blockSignals(True)
    table.clear()
    table.setColumnCount(2 + len(regions))
    table.setHorizontalHeaderLabels(
        ["", "File name"] + [str(item.get("label") or "") for item in regions]
    )
    table.setRowCount(len(files))
    table.setColumnWidth(0, 22)
    table.setColumnWidth(1, 210)
    for column in range(2, table.columnCount()):
        table.setColumnWidth(column, 100)
    for row, path in enumerate(files):
        table.setCellWidget(row, 0, _RSRowHandle(row))
        name_item = QTableWidgetItem(
            file_labels[row] if row < len(file_labels) else Path(path).name
        )
        name_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        table.setItem(row, 1, name_item)
        row_values = values.get(path) or {}
        for index, region in enumerate(regions):
            value = row_values.get(region.get("key"))
            if not has_results:
                text = ""
            else:
                text = "NA" if value is None else str(int(value))
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            table.setItem(row, 2 + index, item)
    table.blockSignals(False)
    page._rs_file_order = files
    page._rs_region_order = [item.get("key") for item in regions]


def _rs_persist(ctx, page) -> None:
    """Save a reordered result table; the orders are part of the output."""
    if not (page._rs_params.get("outputs") or {}).get("files"):
        return
    page._rs_params["files"] = list(page._rs_files)
    page._rs_params["regions"] = list(page._rs_selected)
    if ctx.project is not None:
        ctx.project.save()
    page._rs_dirty = False
    ctx._module_set_state(page, MODULE_STATE_UNMODIFIED)


def _rs_reorder_rows(ctx, page, source: int, target: int) -> None:
    order = list(page._rs_file_order)
    if not (0 <= source < len(order)):
        return
    value = order.pop(source)
    if target > source:
        target -= 1
    target = max(0, min(target, len(order)))
    order.insert(target, value)
    page._rs_file_order = order
    pending = getattr(page, "_rs_pending", None)
    if pending is not None:
        labels = {
            path: name for path, name in zip(
                pending.get("files") or [],
                pending.get("file_labels") or [],
            )
        }
        pending["files"] = order
        pending["file_labels"] = [
            labels.get(path, Path(path).name) for path in order
        ]
        _rs_mark_dirty(ctx, page)
        _rs_rebuild_table(ctx, page)
        return
    outputs = page._rs_params.get("outputs") or {}
    if outputs:
        labels = {
            path: name for path, name in zip(
                outputs.get("files") or [],
                outputs.get("file_labels") or [],
            )
        }
        outputs["files"] = order
        outputs["file_labels"] = [
            labels.get(path, Path(path).name) for path in order
        ]
        page._rs_params["outputs"] = outputs
        _rs_mark_dirty(ctx, page)
    _rs_rebuild_table(ctx, page)
    _rs_persist(ctx, page)


def _rs_column_moved(ctx, page, logical, old, new) -> None:
    if getattr(page, "_rs_updating", False):
        return
    if logical in (0, 1):
        header = page._rs_table.horizontalHeader()
        header.blockSignals(True)
        header.moveSection(new, old)
        header.blockSignals(False)
        return
    header = page._rs_table.horizontalHeader()
    order = [
        header.logicalIndex(visual)
        for visual in range(2, header.count())
    ]
    pending = getattr(page, "_rs_pending", None)
    if pending is not None:
        regions = list(pending.get("regions") or [])
        if regions and len(order) == len(regions):
            pending["regions"] = [regions[index - 2] for index in order]
            _rs_mark_dirty(ctx, page)
        _rs_rebuild_table(ctx, page)
        return
    regions = list((page._rs_params.get("outputs") or {}).get("regions") or [])
    if regions and len(order) == len(regions):
        outputs = page._rs_params.get("outputs") or {}
        outputs["regions"] = [regions[index - 2] for index in order]
        page._rs_params["outputs"] = outputs
        _rs_mark_dirty(ctx, page)
    _rs_rebuild_table(ctx, page)
    _rs_persist(ctx, page)


def _rs_copy(ctx, page) -> None:
    table = page._rs_table
    if not _rs_current_outputs(page).get("files"):
        QMessageBox.information(
            ctx.host, "Copy table", "Calculate the area table first."
        )
        return
    if table.rowCount() == 0 or table.columnCount() <= 2:
        QMessageBox.information(
            ctx.host, "Copy table", "The area table is empty."
        )
        return
    header = table.horizontalHeader()
    region_columns = [
        header.logicalIndex(visual)
        for visual in range(2, header.count())
    ]
    lines = ["File name\t" + "\t".join(
        table.horizontalHeaderItem(column).text()
        for column in region_columns
    )]
    for row in range(table.rowCount()):
        name = table.item(row, 1)
        values = []
        for column in region_columns:
            cell = table.item(row, column)
            values.append(cell.text() if cell is not None else "")
        lines.append((name.text() if name is not None else "") + "\t"
                     + "\t".join(values))
    QApplication.clipboard().setText("\n".join(lines))
    page._rs_status.setText("Copied the table.")


def _rs_close(ctx, page) -> None:
    if getattr(page, "_rs_pending", None) is not None:
        reply = QMessageBox.question(
            ctx.host, "Close Region Size",
            "The calculated table has not been saved.\n"
            "Close and discard it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    _rs_cleanup(ctx, page)
    ctx._close_module_page(page)


def _rs_cleanup(ctx, page) -> None:
    timer = getattr(page, "_rs_timer", None)
    if timer is not None:
        try:
            timer.stop()
        except RuntimeError:
            pass
    worker = getattr(page, "_rs_worker", None)
    if worker is not None:
        worker.stop()


def _rs_refresh(ctx, page) -> None:
    records = _rs_image_records(ctx, page._rs_task_id)
    available = {rec.get("parquet") for rec in records}
    page._rs_files = [
        path for path in page._rs_files if path in available
    ]
    page._rs_options = _rs_region_options(ctx, page._rs_task_id)
    # Upstream data changed; an unsaved table no longer matches it.
    page._rs_pending = None
    _rs_rebuild_panels(ctx, page)
    _rs_rebuild_table(ctx, page)
    _rs_update_save_btn(ctx, page)


class _RegionSizeWorker:
    """Background pixel-area calculation (one x/y read per file)."""

    def __init__(self) -> None:
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, token, ctx, task_id, files, regions) -> None:
        self._jobs.put((token, ctx, task_id, list(files), list(regions)))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                token, ctx, task_id, files, regions = self._jobs.get(
                    timeout=0.2
                )
            except queue.Empty:
                continue
            total = len(files)
            for index, path in enumerate(files):
                if self._stop.is_set():
                    return
                name = Path(str(path)).name
                self._results.put((
                    "progress", token, index, total,
                    f"Reading {name} ({index + 1}/{total})…",
                ))
                try:
                    values, error = self._file_values(
                        ctx, task_id, path, regions
                    )
                except Exception as exc:  # noqa: BLE001
                    values, error = {}, str(exc)
                self._results.put((
                    "file", token, str(path), values, error,
                ))
            self._results.put(("done", token))

    @staticmethod
    def _file_values(ctx, task_id, path, regions):
        """Area of each selected Region channel on one file.

        The converted channel record stores the number of inside pixels, so
        no coordinates are read.  A file without a conversion record is NA.
        """
        import numpy as np

        values = {}
        for item in regions:
            rec = (item.get("channels") or {}).get(str(path))
            if not isinstance(rec, dict):
                values[item["key"]] = None
                continue
            pixels = rec.get("pixels")
            if pixels is None:
                from smage.modules.segmentation.page import _seg_load_mask

                mask = _seg_load_mask(
                    ctx, rec.get("abs_path") or rec.get("path")
                )
                pixels = (
                    int(np.count_nonzero(mask)) if mask is not None else None
                )
            values[item["key"]] = (
                None if pixels is None else int(pixels)
            )
        return values, None
