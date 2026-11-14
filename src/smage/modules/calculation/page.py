"""Calculation module: user-defined rows that produce new channels.

The module is a map stage: saved rows produce real values for every module
below it, but nothing is written to disk.  The page keeps a draft stage for
its live preview, so unsaved edits are visible immediately without changing
what downstream modules read until the module is saved.
"""
from __future__ import annotations

import copy
import queue
import threading
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from smage.core.calculation import _calculation_stage
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.settings import _default_palette
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.widgets import curve_editor as _widgets_curve_editor
from smage.widgets import image_level as _widgets_image_level
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
)
from smage.widgets.plot import renderer as _widgets_plot_renderer
from smage.widgets.resources import _drag_handle_pixmap, _lock_pixmap

_TYPE_LABELS = [
    ("Clip", "clip"),
    ("Scale", "scale"),
    ("Add", "add"),
    ("Contrast", "contrast"),
]

_ROW_STYLE = (
    "QFrame#calcRow { background: #ffffff; border: 1px solid #d9e2ec; "
    "border-radius: 6px; }"
)
_ROW_STYLE_SELECTED = (
    "QFrame#calcRow { background: #ffffff; border: 2px solid #4285f4; "
    "border-radius: 6px; }"
)

_COL_HANDLE = 16
_COL_TYPE = 96
_COL_FILES = 96
_COL_NAME = 136
_COL_VISIBLE = 56


def _calc_available_files(ctx, task_id):
    return [
        rec for rec in _data_files._available_files(ctx, task_id)
        if rec.get("parquet")
    ]


def _calc_file_name(ctx, path, task_id) -> str:
    try:
        return _data_naming._comp_file_name(ctx, path, task_id)
    except Exception:  # noqa: BLE001 - display only
        return Path(str(path)).name


def _calc_mark_dirty(ctx, page) -> None:
    page._calc_dirty = True
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)
    _calc_update_save_btn(ctx, page)


def _calc_update_save_btn(ctx, page) -> None:
    page._calc_save_btn.setEnabled(
        page._calc_dirty and not page._calc_locked
    )


def _calc_source_tuple(source):
    if not isinstance(source, dict):
        return None
    if source.get("row"):
        return ("row", str(source["row"]))
    if source.get("column"):
        return ("column", str(source["column"]))
    return None


def _calc_source_dict(data):
    if not data:
        return None
    kind, value = data
    return {kind: value}


def _calc_source_label(ctx, page, source) -> str:
    if not isinstance(source, dict):
        return ""
    if source.get("column"):
        return str(source["column"])
    row = _calc_row_by_id(page, str(source.get("row") or ""))
    if row is not None:
        return str(row.get("output") or "")
    return ""


def _calc_row_by_id(page, row_id):
    for row in page._calc_rows:
        if row.get("id") == row_id:
            return row
    return None


def _calc_row_index(page, row_id) -> int:
    for index, row in enumerate(page._calc_rows):
        if row.get("id") == row_id:
            return index
    return -1


def _calc_row_files(page, row) -> list:
    return [str(path) for path in (row.get("files") or [])]


def _calc_common_columns(ctx, page, files) -> list:
    """Columns shared by every file in `files` (at this module's task)."""
    files = [str(path) for path in (files or []) if path]
    if not files:
        return []
    common = None
    for path in files:
        columns = set(
            _data_columns._columns_for(ctx, path, page._calc_task_id)
        )
        common = columns if common is None else common & columns
        if not common:
            break
    return sorted(common or ())


def _calc_source_items(ctx, page, index, base_columns):
    """Dropdown entries: shared base columns, then earlier row outputs."""
    row = page._calc_rows[index]
    row_files = set(_calc_row_files(page, row))
    items = [(name, ("column", name)) for name in base_columns]
    for prev in page._calc_rows[:index]:
        output = str(prev.get("output") or "")
        if not output:
            continue
        prev_files = set(_calc_row_files(page, prev))
        if not row_files or not prev_files or not row_files <= prev_files:
            # The subset rule keeps every referenced output defined on every
            # file this row touches.
            continue
        items.append((output, ("row", str(prev.get("id")))))
    return items


def _calc_row_columns(ctx, page, index) -> list:
    """All source names available to one row (base + earlier outputs)."""
    row = page._calc_rows[index]
    files = _calc_row_files(page, row)
    base = _calc_common_columns(ctx, page, files)
    names = list(base)
    for name, _data in _calc_source_items(ctx, page, index, base):
        if name not in names:
            names.append(name)
    return names


def _calc_used_names(ctx, page, exclude_row_id=None) -> set:
    used = set()
    for path in page._calc_files:
        used |= set(
            _data_columns._columns_for(ctx, path, page._calc_task_id)
        )
    for row in page._calc_rows:
        if exclude_row_id and row.get("id") == exclude_row_id:
            continue
        name = str(row.get("output") or "")
        if name:
            used.add(name)
    return used


def _calc_next_name(ctx, page) -> str:
    used = _calc_used_names(ctx, page)
    number = 1
    while f"channel_{number}" in used:
        number += 1
    return f"channel_{number}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _calc_source_errors(ctx, page, index, row):
    errors = []
    base_columns = set(
        _calc_common_columns(ctx, page, _calc_row_files(page, row))
    )
    row_files = set(_calc_row_files(page, row))

    def check(source, label="Channel"):
        if not isinstance(source, dict) or not _calc_source_tuple(source):
            errors.append(f"{label}: select a source.")
            return
        if source.get("column"):
            name = str(source["column"])
            if name not in base_columns:
                errors.append(
                    f"{label}: '{name}' is not present in every file."
                )
            return
        row_id = str(source.get("row") or "")
        prev = _calc_row_by_id(page, row_id)
        if prev is None:
            errors.append(f"{label}: the referenced row no longer exists.")
            return
        prev_index = _calc_row_index(page, row_id)
        if prev_index < 0 or prev_index >= index:
            errors.append(
                f"{label}: '{prev.get('output', '')}' must be on a row above."
            )
        prev_files = set(_calc_row_files(page, prev))
        if row_files and prev_files and not row_files <= prev_files:
            errors.append(
                f"{label}: this row uses files that are not covered by "
                f"'{prev.get('output', '')}'."
            )

    kind = row.get("type")
    if kind == "add":
        terms = row.get("terms") or []
        if not terms:
            errors.append("Add: at least one channel is required.")
        for term in terms:
            check(term.get("source"))
    else:
        check(row.get("source"))

    if kind == "clip":
        low, high = row.get("min"), row.get("max")
        if low is None or high is None:
            errors.append("Clip: waiting for the channel's data range.")
        else:
            try:
                if float(low) >= float(high):
                    errors.append("Clip: the minimum must be below the maximum.")
            except (TypeError, ValueError):
                errors.append("Clip: the thresholds must be numbers.")
    elif kind == "scale":
        if row.get("method") not in ("log10", "arcsinh"):
            errors.append("Scale: choose Log or Asinh.")
        try:
            cofactor = float(row.get("cofactor") or 5.0)
        except (TypeError, ValueError):
            cofactor = 0.0
        if cofactor <= 0:
            errors.append("Scale: the cofactor must be above zero.")
    elif kind == "contrast":
        domain = row.get("domain") or {}
        low, high = domain.get("min"), domain.get("max")
        if low is None or high is None:
            errors.append("Contrast: waiting for the channel's data range.")
        else:
            try:
                if float(high) <= float(low):
                    errors.append(
                        "Contrast: the input range must span more than zero."
                    )
            except (TypeError, ValueError):
                errors.append("Contrast: the input range must be numeric.")
        anchors = row.get("anchors") or []
        if len(anchors) < 2:
            errors.append("Contrast: the curve needs at least two anchors.")
    return errors


def _calc_row_errors(ctx, page) -> dict:
    """Return {row_id: [error, ...]} for every row."""
    result = {}
    if not page._calc_files:
        return result
    for index, row in enumerate(page._calc_rows):
        rid = str(row.get("id") or "")
        errors = []
        row_files = set(_calc_row_files(page, row))
        if not row_files:
            errors.append("Files: select at least one file for this row.")
        elif not row_files <= set(page._calc_files):
            errors.append(
                "Files: this row uses files that are not selected on the left."
            )
        output = str(row.get("output") or "").strip()
        if not output:
            errors.append("New channel name is empty.")
        else:
            others = _calc_used_names(ctx, page, exclude_row_id=rid)
            if output in others:
                errors.append(
                    f"'{output}' is already used by a column or another row."
                )
        errors.extend(_calc_source_errors(ctx, page, index, row))
        if errors:
            result[rid] = errors
    return result


# ---------------------------------------------------------------------------
# Data range worker
# ---------------------------------------------------------------------------

class _CalcRangeWorker:
    """Read a source channel over its files and return the global min/max."""

    def __init__(self) -> None:
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, token, ctx, task_id, files, source, extra_stages) -> None:
        self._jobs.put((
            token, ctx, str(task_id or ""), tuple(files), str(source),
            extra_stages,
        ))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    def _run(self) -> None:
        import numpy as np

        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            token, ctx, task_id, files, source, extra = job
            low = float("inf")
            high = float("-inf")
            errors = []
            try:
                for path in files:
                    values = ctx._column_data(
                        task_id, path, [source], extra_stages=extra
                    )
                    array = values.get(source)
                    if array is None:
                        errors.append(path)
                        continue
                    array = np.asarray(array, dtype="float64")
                    finite = np.isfinite(array)
                    if finite.any():
                        low = min(low, float(array[finite].min()))
                        high = max(high, float(array[finite].max()))
            except Exception as exc:  # noqa: BLE001 - shown in the row
                errors.append(str(exc))
            if not np.isfinite(low) or not np.isfinite(high):
                low = high = None
            self._results.put((token, low, high, errors))


# ---------------------------------------------------------------------------
# Row widgets
# ---------------------------------------------------------------------------

class _CalcDragHandle(QLabel):
    """Starts a row-reorder drag with the row id as its mime data."""

    def __init__(self, row_id: str, parent=None) -> None:
        super().__init__(parent)
        self._row_id = row_id
        self._start = None
        self.setPixmap(_drag_handle_pixmap())
        self.setFixedWidth(_COL_HANDLE)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder this calculation")
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
        moved = (event.position().toPoint() - self._start).manhattanLength()
        if moved < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"calc-row:{self._row_id}")
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._start = None

    def mouseReleaseEvent(self, event) -> None:
        self._start = None
        super().mouseReleaseEvent(event)


class _CalcRowList(QListWidget):
    """Row list that accepts drops from the drag handles."""

    orderRequested = Signal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._indicator = QFrame(self.viewport())
        self._indicator.setStyleSheet("background: #4285f4;")
        self._indicator.setFixedHeight(2)
        self._indicator.hide()

    def _target_index(self, y: int) -> int:
        for index in range(self.count()):
            rect = self.visualItemRect(self.item(index))
            if y < rect.center().y():
                return index
        return self.count()

    @staticmethod
    def _row_id(event) -> str:
        text = event.mimeData().text() if event.mimeData().hasText() else ""
        return text[len("calc-row:"):] if text.startswith("calc-row:") else ""

    def dragEnterEvent(self, event) -> None:
        if self._row_id(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._row_id(event):
            event.ignore()
            return
        y = event.position().toPoint().y()
        index = self._target_index(y)
        if index < self.count():
            top = self.visualItemRect(self.item(index)).top()
        elif self.count():
            top = self.visualItemRect(self.item(self.count() - 1)).bottom()
        else:
            top = 0
        self._indicator.setGeometry(
            0, max(0, top - 1), self.viewport().width(), 2
        )
        self._indicator.show()
        self._indicator.raise_()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._indicator.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._indicator.hide()
        row_id = self._row_id(event)
        target = self._target_index(event.position().toPoint().y())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if row_id:
            self.orderRequested.emit(row_id, target)


class _CalcRowFrame(QFrame):
    """A row card; clicking it selects the calculation it represents."""

    def __init__(self, row_id: str, on_select) -> None:
        super().__init__()
        self.row_id = row_id
        self._on_select = on_select
        self.setObjectName("calcRow")
        self.setStyleSheet(_ROW_STYLE)

    def select(self) -> None:
        self._on_select(self.row_id)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(_ROW_STYLE_SELECTED if selected else _ROW_STYLE)

    def mousePressEvent(self, event) -> None:
        self.select()
        super().mousePressEvent(event)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            _clear_layout(child)


def _calc_param_combo(columns, current, enabled):
    combo = _NoWheelComboBox()
    combo.addItem("", None)
    for column in columns or []:
        combo.addItem(str(column))
    if current is not None:
        selected = combo.findText(str(current))
        if selected < 0:
            combo.addItem(str(current))
            selected = combo.count() - 1
        combo.setCurrentIndex(selected)
    combo.setEnabled(enabled)
    return combo


def _calc_source_combo(ctx, page, index, source, enabled):
    """A combo whose item data identifies a base column or an earlier row."""
    row = page._calc_rows[index]
    base = _calc_common_columns(
        ctx, page, _calc_row_files(page, row)
    )
    combo = _NoWheelComboBox()
    combo.addItem("", None)
    for name, data in _calc_source_items(ctx, page, index, base):
        combo.addItem(name, data)
    target = _calc_source_tuple(source)
    selected = 0
    if target:
        # QComboBox.findData compares arbitrary Python objects by identity,
        # so a tuple rebuilt from the row never matches; compare by value.
        selected = next(
            (
                index for index in range(combo.count())
                if combo.itemData(index) == target
            ),
            -1,
        )
        if selected < 0:
            label = _calc_source_label(ctx, page, source) or "missing"
            combo.addItem(f"{label} (missing)", target)
            selected = combo.count() - 1
    combo.setCurrentIndex(max(0, selected))
    combo.setEnabled(enabled)
    return combo


def _calc_spin(value, default=0.0):
    spin = _NoWheelDoubleSpinBox()
    spin.setDecimals(3)
    spin.setRange(-1e12, 1e12)
    try:
        spin.setValue(float(value))
    except (TypeError, ValueError):
        spin.setValue(default)
    return spin


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _build_calculation_page(ctx, name: str, task_def: dict,
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
        lock_icon = QLabel()
        lock_icon.setPixmap(_lock_pixmap())
        lock_icon.setFixedSize(14, 14)
        lock_icon.setToolTip("Locked — a downstream generate module saved data")
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
    close_btn.clicked.connect(lambda _=False: _calc_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    notice = QLabel(
        "Each row computes a real channel for every module below this one. "
        "Nothing is written to disk; the preview shows unsaved edits."
    )
    notice.setWordWrap(True)
    notice.setStyleSheet(
        "color: #6b7785; padding: 6px 12px 0 12px; font-size: 8.5pt;"
    )
    layout.addWidget(notice)

    page._calc_task_def = task_def
    page._calc_task_id = task_def.get("id")
    page._calc_locked = locked
    page._calc_dirty = False
    params = task_def.setdefault("params", {})
    files = params.get("files")
    if files is None:
        # A new module starts with no files selected; the user must choose
        # the scope explicitly instead of silently processing everything.
        files = []
        params["files"] = files
    page._calc_files = [str(path) for path in files if path]
    rows = params.get("rows")
    if not isinstance(rows, list):
        rows = []
    rows = [row for row in rows if isinstance(row, dict)]
    for row in rows:
        row.setdefault("id", uuid4().hex)
        row.setdefault("type", "clip")
        row.setdefault("visible", True)
    params["rows"] = rows
    page._calc_rows = rows
    page._calc_snapshot = copy.deepcopy(params)
    page._calc_selected_id = (
        str(page._calc_rows[0].get("id")) if page._calc_rows else None
    )
    page._calc_row_widgets = {}
    page._calc_updating = False
    page._calc_rebuilding = False
    page._calc_range_token = 0
    page._calc_range_pending = {}
    page._calc_range_worker = _CalcRangeWorker()
    page._calc_preview_timer = QTimer(page)
    page._calc_preview_timer.setSingleShot(True)
    page._calc_preview_timer.setInterval(150)
    page._calc_preview_timer.timeout.connect(
        lambda: _calc_render_preview(ctx, page)
    )
    page._calc_range_timer = QTimer(page)
    page._calc_range_timer.setInterval(120)
    page._calc_range_timer.timeout.connect(
        lambda: _calc_poll_ranges(ctx, page)
    )

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)

    left_panel = _calc_files_panel(ctx, page)
    middle_panel = _calc_rows_panel(ctx, page)

    # Build the preview config before the plot widget: its A/F buttons close
    # over the same dict and it is mutated in place later.
    first = page._calc_files[0] if page._calc_files else None
    page._calc_preview = {
        "type": "scatter",
        "file": first,
        "file_name": _calc_file_name(ctx, first, page._calc_task_id)
        if first else None,
        "x": None,
        "y": None,
        "color_mode": "density",
        "color_param": None,
        "palette": _default_palette(),
        "xmin": None,
        "xmax": None,
        "ymin": None,
        "ymax": None,
        "point_size": 4,
        "fixed_point_size": False,
        "solid_color": (0, 0, 200),
        "bins": 20,
        "hist_mode": "histogram",
        "hist_color": (0, 0, 200),
        "curve_color": (200, 40, 40),
        "layers": [],
        "image_min": None,
        "image_max": None,
        "show_axes": True,
        "show_axis_names": True,
        "show_indices": True,
        "show_sample_name": True,
    }
    preview_panel = _calc_preview_panel(ctx, page)

    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.addWidget(left_panel)
    split.addWidget(middle_panel)
    split.addWidget(preview_panel)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 3)
    split.setStretchFactor(2, 2)
    split.setSizes([260, 780, 560])
    cl.addWidget(split, 1)
    layout.addWidget(content, 1)

    page._calc_save_btn = save_btn
    save_btn.clicked.connect(lambda _=False: _calc_save_now(ctx, page))
    _calc_update_save_btn(ctx, page)

    page._calc_files_list.blockSignals(True)
    _calc_rebuild_files_list(ctx, page)
    page._calc_files_list.blockSignals(False)
    _calc_rebuild_rows(ctx, page, select_id=page._calc_selected_id)
    _calc_rebuild_settings(ctx, page)
    _calc_select_row(ctx, page, page._calc_selected_id, force=True)
    _calc_render_preview(ctx, page)
    QTimer.singleShot(0, lambda: _calc_schedule_initial_ranges(ctx, page))
    return page


def _calc_files_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Data files</b>"))
    hint = QLabel(
        "Calculation rows only see these files. Each row may pick a subset."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)

    button = QPushButton("Select files…")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setEnabled(not page._calc_locked)
    button.clicked.connect(lambda _=False: _calc_select_files(ctx, page))
    lay.addWidget(button)
    page._calc_files_btn = button

    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    lay.addWidget(box, 1)
    page._calc_files_list = box
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    page._calc_files_count = count
    return panel


def _calc_rebuild_files_list(ctx, page) -> None:
    box = page._calc_files_list
    box.clear()
    for path in page._calc_files:
        box.addItem(_calc_file_name(ctx, path, page._calc_task_id))
    page._calc_files_count.setText(
        f"{len(page._calc_files)} file(s) selected"
    )


def _calc_select_files(ctx, page) -> None:
    records = _calc_available_files(ctx, page._calc_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _calc_file_name(ctx, path, page._calc_task_id)
        for path in page._calc_files
    ]
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Select data files", names, current, "Filter files…",
    )
    if chosen is None:
        return
    paths = [by_name[name] for name in chosen if by_name.get(name)]
    page._calc_files = [str(path) for path in paths if path]
    params = page._calc_task_def.setdefault("params", {})
    params["files"] = list(page._calc_files)
    selected = set(page._calc_files)
    for row in page._calc_rows:
        row["files"] = [
            path for path in (row.get("files") or []) if path in selected
        ]
        # The source/thresholds may no longer be valid for the new files.
        if row.get("type") == "clip":
            row["min"] = None
            row["max"] = None
            row["auto_range"] = True
        elif row.get("type") == "contrast":
            domain = row.setdefault("domain", {})
            domain["auto"] = True
            domain["min"] = None
            domain["max"] = None
    preview = page._calc_preview
    if preview.get("file") not in page._calc_files:
        preview["file"] = page._calc_files[0] if page._calc_files else None
        preview["file_name"] = (
            _calc_file_name(ctx, preview["file"], page._calc_task_id)
            if preview.get("file") else None
        )
    _calc_mark_dirty(ctx, page)
    _calc_rebuild_files_list(ctx, page)
    _calc_rebuild_rows(ctx, page, select_id=page._calc_selected_id)
    _calc_rebuild_settings(ctx, page)
    _calc_select_row(ctx, page, page._calc_selected_id, force=True)
    _calc_schedule_ranges(ctx, page)


def _calc_rows_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Calculation</b>"))

    header = QWidget()
    h = QHBoxLayout(header)
    h.setContentsMargins(6, 0, 6, 0)
    h.setSpacing(6)
    for text, width in (
        ("", _COL_HANDLE), ("Type", _COL_TYPE), ("Files", _COL_FILES),
    ):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; color: #5a6673;")
        label.setFixedWidth(width)
        h.addWidget(label)
    params_label = QLabel("Parameters")
    params_label.setStyleSheet("font-weight: bold; color: #5a6673;")
    h.addWidget(params_label, 1)
    for text, width in (
        ("New channel", _COL_NAME), ("Visible", _COL_VISIBLE)
    ):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; color: #5a6673;")
        label.setFixedWidth(width)
        h.addWidget(label)
    lay.addWidget(header)

    row_list = _CalcRowList()
    row_list.setStyleSheet(
        "QListWidget { background: #f5f8fb; border: none; }"
    )
    row_list.orderRequested.connect(
        lambda row_id, target: _calc_reorder(ctx, page, row_id, target)
    )
    row_list.currentRowChanged.connect(
        lambda index: _calc_on_current_row(ctx, page, index)
    )
    lay.addWidget(row_list, 1)
    page._calc_row_list = row_list

    buttons = QHBoxLayout()
    buttons.setSpacing(6)
    add_btn = QPushButton("Add")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_btn.setEnabled(not page._calc_locked)
    add_btn.clicked.connect(lambda _=False: _calc_add_row(ctx, page))
    del_btn = QPushButton("Delete")
    del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    del_btn.setEnabled(not page._calc_locked)
    del_btn.clicked.connect(lambda _=False: _calc_delete_row(ctx, page))
    buttons.addWidget(add_btn)
    buttons.addWidget(del_btn)
    buttons.addStretch()
    lay.addLayout(buttons)
    page._calc_add_btn = add_btn
    page._calc_delete_btn = del_btn

    error = QLabel("")
    error.setWordWrap(True)
    error.setStyleSheet("color: #c0392b;")
    lay.addWidget(error)
    page._calc_error_label = error
    return panel


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def _calc_rebuild_rows(ctx, page, select_id=None) -> None:
    row_list = getattr(page, "_calc_row_list", None)
    if row_list is None:
        return
    page._calc_rebuilding = True
    scroll = row_list.verticalScrollBar().value()
    row_list.clear()
    page._calc_row_widgets = {}
    for index, row in enumerate(page._calc_rows):
        widget = _calc_build_row(ctx, page, row, index)
        item = QListWidgetItem()
        item.setSizeHint(QSize(10, max(48, widget.sizeHint().height())))
        row_list.addItem(item)
        row_list.setItemWidget(item, widget)
        page._calc_row_widgets[str(row.get("id"))] = widget
    row_list.verticalScrollBar().setValue(scroll)
    if select_id:
        index = _calc_row_index(page, select_id)
        if index >= 0:
            row_list.setCurrentRow(index)
    page._calc_rebuilding = False
    _calc_update_errors(ctx, page)
    _calc_update_row_selection(ctx, page)


def _calc_update_row_sizes(ctx, page) -> None:
    row_list = getattr(page, "_calc_row_list", None)
    if row_list is None:
        return
    for index in range(row_list.count()):
        item = row_list.item(index)
        widget = row_list.itemWidget(item)
        if widget is not None:
            item.setSizeHint(QSize(10, max(48, widget.sizeHint().height())))


def _calc_update_row_selection(ctx, page) -> None:
    for row_id, widget in (page._calc_row_widgets or {}).items():
        widget.set_selected(row_id == page._calc_selected_id)


def _calc_build_row(ctx, page, row, index) -> QWidget:
    frame = _CalcRowFrame(
        str(row.get("id")), lambda rid: _calc_select_row(ctx, page, rid)
    )
    h = QHBoxLayout(frame)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(6)

    handle = _CalcDragHandle(str(row.get("id")))
    handle.setEnabled(not page._calc_locked)
    h.addWidget(handle)

    type_combo = _NoWheelComboBox()
    for label, value in _TYPE_LABELS:
        type_combo.addItem(label, value)
    idx = type_combo.findData(row.get("type", "clip"))
    type_combo.setCurrentIndex(max(0, idx))
    type_combo.setFixedWidth(_COL_TYPE)
    type_combo.setEnabled(not page._calc_locked)
    type_combo.currentIndexChanged.connect(
        lambda _=0, r=row, c=type_combo:
            _calc_type_changed(ctx, page, r, c)
    )
    h.addWidget(type_combo)

    files_btn = QPushButton("")
    files_btn.setFixedWidth(_COL_FILES)
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setToolTip("Choose which selected files this row applies to")
    files_btn.setEnabled(not page._calc_locked)
    files_btn.clicked.connect(
        lambda _=False, r=row: _calc_choose_row_files(ctx, page, r)
    )
    h.addWidget(files_btn)

    params_host = QWidget()
    params_lay = QVBoxLayout(params_host)
    params_lay.setContentsMargins(0, 0, 0, 0)
    params_lay.setSpacing(2)
    editor = _calc_build_editor(ctx, page, row, index)
    params_lay.addWidget(editor)
    error_label = QLabel("")
    error_label.setWordWrap(True)
    error_label.setStyleSheet("color: #c0392b;")
    error_label.hide()
    params_lay.addWidget(error_label)
    h.addWidget(params_host, 1)

    name_edit = QLineEdit(str(row.get("output") or ""))
    name_edit.setFixedWidth(_COL_NAME)
    name_edit.setPlaceholderText("new channel")
    name_edit.setEnabled(not page._calc_locked)
    name_edit.setToolTip("Name of the channel this row creates")
    name_edit.textChanged.connect(
        lambda text, r=row, w=frame: _calc_name_changed(ctx, page, r, text, w)
    )
    name_edit.editingFinished.connect(
        lambda: _calc_name_finished(ctx, page)
    )
    h.addWidget(name_edit)

    visible = QCheckBox()
    visible.setFixedWidth(_COL_VISIBLE)
    visible.setChecked(bool(row.get("visible", True)))
    visible.setToolTip("Show this channel to downstream modules")
    visible.setEnabled(not page._calc_locked)
    visible.toggled.connect(
        lambda checked, r=row: _calc_visible_changed(ctx, page, r, checked)
    )
    h.addWidget(visible)

    frame._calc_type_combo = type_combo
    frame._calc_files_btn = files_btn
    frame._calc_params_host = params_host
    frame._calc_editor = editor
    frame._calc_error_label = error_label
    frame._calc_name_edit = name_edit
    frame._calc_visible = visible
    _calc_refresh_files_btn(ctx, page, row, files_btn)
    return frame


def _calc_refresh_files_btn(ctx, page, row, button) -> None:
    files = _calc_row_files(page, row)
    total = len(page._calc_files)
    if total and len(files) == total:
        text = f"All {total} files"
    else:
        text = f"{len(files)} / {total} files"
    button.setText(text)
    names = [
        _calc_file_name(ctx, path, page._calc_task_id) for path in files
    ]
    button.setToolTip(
        "Applies to:\n" + "\n".join(names) if names
        else "No files selected for this row"
    )


def _calc_build_editor(ctx, page, row, index) -> QWidget:
    kind = row.get("type", "clip")
    if kind == "scale":
        return _calc_scale_editor(ctx, page, row, index)
    if kind == "add":
        return _calc_add_editor(ctx, page, row, index)
    if kind == "contrast":
        return _calc_contrast_editor(ctx, page, row, index)
    return _calc_clip_editor(ctx, page, row, index)


def _calc_clip_editor(ctx, page, row, index) -> QWidget:
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    h.addWidget(QLabel("Channel"))
    combo = _calc_source_combo(
        ctx, page, index, row.get("source"), not page._calc_locked
    )
    combo.currentIndexChanged.connect(
        lambda _=0: _calc_source_changed(ctx, page, row, combo, index)
    )
    h.addWidget(combo, 1)
    combo.setMinimumWidth(130)
    h.addWidget(QLabel("Min"))
    low = _calc_spin(row.get("min"), 0.0)
    low.setFixedWidth(96)
    low.setToolTip(
        "Filled from the channel's data range; edit to fix the threshold"
    )
    low.setEnabled(not page._calc_locked)
    low.valueChanged.connect(
        lambda value: _calc_clip_threshold(ctx, page, row, "min", value)
    )
    h.addWidget(low)
    h.addWidget(QLabel("Max"))
    high = _calc_spin(row.get("max"), 1.0)
    high.setFixedWidth(96)
    high.setEnabled(not page._calc_locked)
    high.valueChanged.connect(
        lambda value: _calc_clip_threshold(ctx, page, row, "max", value)
    )
    h.addWidget(high)
    host._calc_source = combo
    host._calc_min = low
    host._calc_max = high
    return host


def _calc_scale_editor(ctx, page, row, index) -> QWidget:
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    h.addWidget(QLabel("Channel"))
    combo = _calc_source_combo(
        ctx, page, index, row.get("source"), not page._calc_locked
    )
    combo.currentIndexChanged.connect(
        lambda _=0: _calc_source_changed(ctx, page, row, combo, index)
    )
    combo.setMinimumWidth(130)
    h.addWidget(combo, 1)
    h.addWidget(QLabel("Method"))
    method = _NoWheelComboBox()
    method.addItem("Log", "log10")
    method.addItem("Asinh", "arcsinh")
    idx = method.findData(row.get("method", "log10"))
    method.setCurrentIndex(max(0, idx))
    method.setFixedWidth(86)
    method.setEnabled(not page._calc_locked)
    h.addWidget(method)
    h.addWidget(QLabel("Cofactor"))
    cofactor = _NoWheelDoubleSpinBox()
    cofactor.setDecimals(3)
    cofactor.setRange(0.001, 1_000_000.0)
    try:
        cofactor.setValue(float(row.get("cofactor") or 5.0))
    except (TypeError, ValueError):
        cofactor.setValue(5.0)
    cofactor.setFixedWidth(100)
    cofactor.setEnabled(
        not page._calc_locked and row.get("method") == "arcsinh"
    )
    cofactor.valueChanged.connect(
        lambda value: _calc_scale_cofactor(ctx, page, row, value)
    )
    h.addWidget(cofactor)

    def on_method(_index=0):
        row["method"] = method.currentData() or "log10"
        cofactor.setEnabled(
            not page._calc_locked and row["method"] == "arcsinh"
        )
        _calc_changed(ctx, page, row)

    method.currentIndexChanged.connect(on_method)
    host._calc_source = combo
    host._calc_method = method
    host._calc_cofactor = cofactor
    return host


def _calc_add_editor(ctx, page, row, index) -> QWidget:
    host = QWidget()
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    terms = row.setdefault("terms", [])
    if not terms:
        terms.append({"weight": 1.0, "source": None})
    for term_index, term in enumerate(terms):
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(QLabel("Weight"))
        weight = _NoWheelDoubleSpinBox()
        weight.setDecimals(3)
        weight.setRange(-1e12, 1e12)
        try:
            weight.setValue(float(term.get("weight", 1.0)))
        except (TypeError, ValueError):
            weight.setValue(1.0)
        weight.setFixedWidth(82)
        weight.setEnabled(not page._calc_locked)
        weight.valueChanged.connect(
            lambda value, t=term: _calc_term_weight(ctx, page, row, t, value)
        )
        h.addWidget(weight)
        h.addWidget(QLabel("×"))
        combo = _calc_source_combo(
            ctx, page, index, term.get("source"), not page._calc_locked
        )
        combo.setMinimumWidth(130)
        combo.currentIndexChanged.connect(
            lambda _=0, t=term, c=combo:
                _calc_term_source(ctx, page, row, t, c)
        )
        h.addWidget(combo, 1)
        delete = QPushButton("Delete")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.setFixedWidth(62)
        delete.setEnabled(
            not page._calc_locked and len(terms) > 1
        )
        delete.clicked.connect(
            lambda _=False, t=term:
                _calc_remove_term(ctx, page, row, t)
        )
        h.addWidget(delete)
        v.addWidget(line)
    add = QPushButton("Add channel")
    add.setCursor(Qt.CursorShape.PointingHandCursor)
    add.setEnabled(not page._calc_locked)
    add.clicked.connect(lambda _=False: _calc_add_term(ctx, page, row))
    v.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
    return host


def _calc_contrast_editor(ctx, page, row, index) -> QWidget:
    host = QWidget()
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    top = QHBoxLayout()
    top.setSpacing(6)
    top.addWidget(QLabel("Channel"))
    combo = _calc_source_combo(
        ctx, page, index, row.get("source"), not page._calc_locked
    )
    combo.setMinimumWidth(130)
    combo.currentIndexChanged.connect(
        lambda _=0: _calc_source_changed(ctx, page, row, combo, index)
    )
    top.addWidget(combo, 1)

    row.setdefault("interpolation", "cubic")
    domain = row.setdefault(
        "domain", {"auto": True, "min": None, "max": None}
    )
    auto = QCheckBox("Auto")
    auto.setChecked(bool(domain.get("auto", True)))
    auto.setEnabled(not page._calc_locked)
    top.addWidget(auto)
    recompute = QPushButton("Recompute")
    recompute.setCursor(Qt.CursorShape.PointingHandCursor)
    recompute.setEnabled(not page._calc_locked)
    top.addWidget(recompute)
    v.addLayout(top)

    domain_row = QHBoxLayout()
    domain_row.setSpacing(6)
    domain_row.addWidget(QLabel("Input min"))
    low = _calc_spin(domain.get("min"), 0.0)
    low.setFixedWidth(96)
    low.setEnabled(not page._calc_locked)
    low.valueChanged.connect(
        lambda value: _calc_domain_changed(ctx, page, row, "min", value)
    )
    domain_row.addWidget(low)
    domain_row.addWidget(QLabel("max"))
    high = _calc_spin(domain.get("max"), 1.0)
    high.setFixedWidth(96)
    high.setEnabled(not page._calc_locked)
    high.valueChanged.connect(
        lambda value: _calc_domain_changed(ctx, page, row, "max", value)
    )
    domain_row.addWidget(high)
    domain_row.addStretch()
    v.addLayout(domain_row)

    curve = _widgets_curve_editor._CurveEditor(row.get("anchors"))
    curve.setEnabled(not page._calc_locked)
    curve.setMinimumHeight(120)
    curve.changed.connect(
        lambda: _calc_curve_changed(ctx, page, row, curve)
    )
    v.addWidget(curve)
    reset = QPushButton("Reset to identity")
    reset.setCursor(Qt.CursorShape.PointingHandCursor)
    reset.setEnabled(not page._calc_locked)
    reset.clicked.connect(curve.reset_identity)
    v.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)

    status = QLabel("")
    status.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    status.hide()
    v.addWidget(status)

    def on_auto(checked):
        domain["auto"] = bool(checked)
        if checked:
            _calc_schedule_range(ctx, page, row)
        _calc_changed(ctx, page, row)

    auto.toggled.connect(on_auto)
    recompute.clicked.connect(
        lambda _=False: _calc_recompute_domain(ctx, page, row, auto)
    )
    host._calc_source = combo
    host._calc_domain_min = low
    host._calc_domain_max = high
    host._calc_domain_auto = auto
    host._calc_curve = curve
    host._calc_status = status
    return host


# ---------------------------------------------------------------------------
# Row edit handlers
# ---------------------------------------------------------------------------

def _calc_changed(ctx, page, row, rebuild: bool = False) -> None:
    _calc_mark_dirty(ctx, page)
    if rebuild:
        _calc_rebuild_rows(ctx, page, select_id=str(row.get("id")))
    else:
        _calc_update_errors(ctx, page)
    _calc_schedule_preview(ctx, page)


def _calc_type_changed(ctx, page, row, combo) -> None:
    kind = combo.currentData() or "clip"
    if kind == row.get("type"):
        return
    row["type"] = kind
    row.setdefault("source", None)
    if kind == "add":
        row.setdefault("terms", [{"weight": 1.0, "source": None}])
    elif kind == "scale":
        row.setdefault("method", "log10")
        row.setdefault("cofactor", 5.0)
    elif kind == "contrast":
        # The previous domain belonged to whatever source the row had
        # before; recompute it for the current source.
        row["domain"] = {"auto": True, "min": None, "max": None}
        row.setdefault("anchors", [[0.0, 0.0], [1.0, 1.0]])
        row.setdefault("interpolation", "cubic")
    elif kind == "clip":
        row.setdefault("auto_range", True)
        if row.get("auto_range", True):
            row["min"] = None
            row["max"] = None
    _calc_rebuild_rows(ctx, page, select_id=str(row.get("id")))
    if kind in ("clip", "contrast"):
        _calc_schedule_range(ctx, page, row)
    _calc_changed(ctx, page, row)


def _calc_source_changed(ctx, page, row, combo, index) -> None:
    source = _calc_source_dict(combo.currentData())
    row["source"] = source
    kind = row.get("type")
    if kind == "clip":
        row["auto_range"] = True
        row["min"] = None
        row["max"] = None
    elif kind == "contrast":
        domain = row.setdefault("domain", {})
        domain["auto"] = True
        domain["min"] = None
        domain["max"] = None
    _calc_rebuild_rows(ctx, page, select_id=str(row.get("id")))
    _calc_schedule_range(ctx, page, row)
    _calc_changed(ctx, page, row)


def _calc_clip_threshold(ctx, page, row, key, value) -> None:
    if page._calc_updating:
        return
    row[key] = float(value)
    row["auto_range"] = False
    _calc_changed(ctx, page, row)


def _calc_scale_cofactor(ctx, page, row, value) -> None:
    if page._calc_updating:
        return
    row["cofactor"] = float(value)
    _calc_changed(ctx, page, row)


def _calc_term_weight(ctx, page, row, term, value) -> None:
    if page._calc_updating:
        return
    term["weight"] = float(value)
    _calc_changed(ctx, page, row)


def _calc_term_source(ctx, page, row, term, combo) -> None:
    term["source"] = _calc_source_dict(combo.currentData())
    _calc_changed(ctx, page, row)


def _calc_add_term(ctx, page, row) -> None:
    row.setdefault("terms", []).append({"weight": 1.0, "source": None})
    _calc_rebuild_rows(ctx, page, select_id=str(row.get("id")))
    _calc_changed(ctx, page, row)


def _calc_remove_term(ctx, page, row, term) -> None:
    terms = row.get("terms") or []
    if len(terms) <= 1:
        return
    try:
        terms.remove(term)
    except ValueError:
        return
    _calc_rebuild_rows(ctx, page, select_id=str(row.get("id")))
    _calc_changed(ctx, page, row)


def _calc_domain_changed(ctx, page, row, key, value) -> None:
    if page._calc_updating:
        return
    domain = row.setdefault("domain", {})
    domain[key] = float(value)
    domain["auto"] = False
    widget = page._calc_row_widgets.get(str(row.get("id")))
    if widget is not None:
        auto = getattr(widget._calc_editor, "_calc_domain_auto", None)
        if auto is not None and auto.isChecked():
            auto.blockSignals(True)
            auto.setChecked(False)
            auto.blockSignals(False)
    _calc_changed(ctx, page, row)


def _calc_curve_changed(ctx, page, row, editor) -> None:
    row["anchors"] = editor.anchors()
    # Anchor moves cannot invalidate a row; skip the full validation pass
    # while the user drags (it runs on the next real edit or save).
    _calc_mark_dirty(ctx, page)
    _calc_schedule_preview(ctx, page)


def _calc_name_changed(ctx, page, row, text, frame) -> None:
    row["output"] = text.strip()
    _calc_mark_dirty(ctx, page)
    _calc_update_errors(ctx, page)


def _calc_name_finished(ctx, page) -> None:
    # Downstream rows' source lists show this row's display name.
    _calc_rebuild_rows(ctx, page, select_id=page._calc_selected_id)
    _calc_update_errors(ctx, page)
    _calc_select_row(ctx, page, page._calc_selected_id, force=True)


def _calc_visible_changed(ctx, page, row, checked) -> None:
    row["visible"] = bool(checked)
    _calc_mark_dirty(ctx, page)
    _calc_update_errors(ctx, page)


def _calc_choose_row_files(ctx, page, row) -> None:
    names = [
        _calc_file_name(ctx, path, page._calc_task_id)
        for path in page._calc_files
    ]
    by_name = {
        _calc_file_name(ctx, path, page._calc_task_id): path
        for path in page._calc_files
    }
    current = [
        _calc_file_name(ctx, path, page._calc_task_id)
        for path in _calc_row_files(page, row)
    ]
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Files for this calculation", names, current, "Filter files…",
    )
    if chosen is None:
        return
    row["files"] = [
        by_name[name] for name in chosen if by_name.get(name)
    ]
    if row.get("type") == "clip":
        row["auto_range"] = True
        row["min"] = None
        row["max"] = None
    elif row.get("type") == "contrast":
        domain = row.setdefault("domain", {})
        domain["auto"] = True
        domain["min"] = None
        domain["max"] = None
    _calc_rebuild_rows(ctx, page, select_id=str(row.get("id")))
    _calc_schedule_range(ctx, page, row)
    _calc_changed(ctx, page, row)


# ---------------------------------------------------------------------------
# Add / delete / reorder
# ---------------------------------------------------------------------------

def _calc_add_row(ctx, page) -> None:
    if not page._calc_files:
        QMessageBox.information(
            ctx, "Calculation", "Select at least one data file first."
        )
        return
    columns = _calc_common_columns(ctx, page, page._calc_files)
    source = {"column": columns[0]} if columns else None
    row = {
        "id": uuid4().hex,
        "type": "clip",
        "files": list(page._calc_files),
        "output": _calc_next_name(ctx, page),
        "visible": True,
        "source": source,
        "min": None,
        "max": None,
        "auto_range": True,
    }
    page._calc_rows.append(row)
    page._calc_task_def.setdefault("params", {})["rows"] = page._calc_rows
    page._calc_selected_id = row["id"]
    _calc_rebuild_rows(ctx, page, select_id=row["id"])
    _calc_mark_dirty(ctx, page)
    _calc_schedule_range(ctx, page, row)
    _calc_select_row(ctx, page, row["id"], force=True)


def _calc_delete_row(ctx, page) -> None:
    row = _calc_row_by_id(page, page._calc_selected_id)
    if row is None:
        return
    row_id = str(row.get("id"))
    used_by = []
    for other in page._calc_rows:
        if other is row:
            continue
        sources = []
        if other.get("type") == "add":
            sources = [
                term.get("source") for term in other.get("terms") or []
            ]
        else:
            sources = [other.get("source")]
        for source in sources:
            if isinstance(source, dict) and source.get("row") == row_id:
                used_by.append(str(other.get("output") or "row"))
    if used_by:
        QMessageBox.warning(
            ctx, "Calculation",
            "This row cannot be deleted because these rows use its "
            "channel:\n" + "\n".join(used_by),
        )
        return
    index = _calc_row_index(page, row_id)
    page._calc_rows.pop(index)
    page._calc_task_def.setdefault("params", {})["rows"] = page._calc_rows
    if page._calc_rows:
        neighbour = page._calc_rows[min(index, len(page._calc_rows) - 1)]
        page._calc_selected_id = neighbour.get("id")
    else:
        page._calc_selected_id = None
    _calc_rebuild_rows(ctx, page, select_id=page._calc_selected_id)
    _calc_mark_dirty(ctx, page)
    _calc_rebuild_settings(ctx, page)
    _calc_select_row(ctx, page, page._calc_selected_id, force=True)


def _calc_reorder(ctx, page, row_id: str, target: int) -> None:
    if page._calc_locked:
        return
    old_index = _calc_row_index(page, row_id)
    if old_index < 0:
        return
    rows = page._calc_rows
    row = rows.pop(old_index)
    if target > old_index:
        target -= 1
    target = max(0, min(target, len(rows)))
    rows.insert(target, row)
    order_errors = _calc_order_errors(page)
    if order_errors:
        rows.pop(target)
        rows.insert(old_index, row)
        QMessageBox.warning(
            ctx, "Calculation",
            "The row cannot move there because it would put a calculation "
            "below a row that uses its channel:\n"
            + "\n".join(order_errors[:6]),
        )
        _calc_rebuild_rows(ctx, page, select_id=page._calc_selected_id)
        return
    _calc_rebuild_rows(ctx, page, select_id=row_id)
    _calc_mark_dirty(ctx, page)
    _calc_select_row(ctx, page, row_id, force=True)


def _calc_order_errors(page) -> list:
    errors = []
    positions = {
        str(row.get("id")): index
        for index, row in enumerate(page._calc_rows)
    }
    for index, row in enumerate(page._calc_rows):
        sources = []
        if row.get("type") == "add":
            sources = [
                term.get("source") for term in row.get("terms") or []
            ]
        else:
            sources = [row.get("source")]
        for source in sources:
            if not isinstance(source, dict) or not source.get("row"):
                continue
            other = positions.get(str(source["row"]))
            if other is None:
                continue
            if other >= index:
                errors.append(
                    f"'{row.get('output', '')}' uses a channel from "
                    f"'{_calc_name_at(page, other)}', which would be below it."
                )
    return errors


def _calc_name_at(page, index) -> str:
    if 0 <= index < len(page._calc_rows):
        return str(page._calc_rows[index].get("output") or "")
    return ""


# ---------------------------------------------------------------------------
# Selection / errors
# ---------------------------------------------------------------------------

def _calc_on_current_row(ctx, page, index: int) -> None:
    if page._calc_rebuilding:
        return
    if 0 <= index < len(page._calc_rows):
        _calc_select_row(
            ctx, page, str(page._calc_rows[index].get("id"))
        )


def _calc_select_row(ctx, page, row_id, force: bool = False) -> None:
    if not row_id:
        page._calc_selected_id = None
        _calc_update_row_selection(ctx, page)
        _calc_schedule_preview(ctx, page)
        return
    index = _calc_row_index(page, row_id)
    if index < 0:
        return
    if not force and page._calc_selected_id == row_id:
        _calc_update_row_selection(ctx, page)
        return
    page._calc_selected_id = row_id
    _calc_update_row_selection(ctx, page)
    row_list = getattr(page, "_calc_row_list", None)
    if row_list is not None and row_list.currentRow() != index:
        page._calc_rebuilding = True
        row_list.setCurrentRow(index)
        page._calc_rebuilding = False
    row = page._calc_rows[index]
    preview = page._calc_preview
    files = _calc_row_files(page, row)
    if files and preview.get("file") not in files:
        preview["file"] = files[0]
        preview["file_name"] = _calc_file_name(
            ctx, files[0], page._calc_task_id
        )
    output = str(row.get("output") or "")
    columns = _calc_preview_columns(ctx, page, preview.get("file"))
    if output and output in columns:
        if preview.get("type") == "image":
            preview["image_channel"] = output
        else:
            preview["x"] = output
            if preview.get("type") == "scatter":
                y = preview.get("y")
                if not y or y not in columns or y == output:
                    preview["y"] = _calc_other_column(columns, output)
    if preview.get("file"):
        _calc_rebuild_settings(ctx, page)
        _calc_render_preview(ctx, page)
    _calc_schedule_range(ctx, page, row)


def _calc_other_column(columns, exclude) -> str | None:
    for name in columns or []:
        if name != exclude and name not in ("x", "y"):
            return name
    return columns[0] if columns else None


def _calc_preview_columns(ctx, page, path) -> list:
    if not path:
        return []
    columns = list(
        _data_columns._columns_for(ctx, path, page._calc_task_id)
    )
    for row in page._calc_rows:
        if path in (row.get("files") or []):
            name = str(row.get("output") or "")
            if name and name not in columns:
                columns.append(name)
    return columns


def _calc_update_errors(ctx, page) -> None:
    errors = _calc_row_errors(ctx, page)
    for row_id, widget in (page._calc_row_widgets or {}).items():
        row_errors = errors.get(row_id) or []
        label = getattr(widget, "_calc_error_label", None)
        if label is not None:
            if row_errors:
                label.setText("; ".join(row_errors))
                label.show()
            else:
                label.clear()
                label.hide()
        name_edit = getattr(widget, "_calc_name_edit", None)
        if name_edit is not None:
            conflict = any(
                "already used" in message or "more than one row" in message
                for message in row_errors
            )
            name_edit.setStyleSheet(
                "QLineEdit { border: 1px solid #e53935; "
                "background: #fff5f5; }" if conflict else ""
            )
            name_edit.setToolTip(
                "New channel name is not valid: it is already used by a "
                "column or another row." if conflict
                else "Name of the channel this row creates"
            )
    if not page._calc_files:
        page._calc_error_label.setText(
            "Select at least one data file for this module."
        )
    elif errors:
        first = next(iter(errors.values()))
        page._calc_error_label.setText(
            f"{len(errors)} row(s) need attention: {first[0]}"
        )
    else:
        page._calc_error_label.setText("")
    _calc_update_row_sizes(ctx, page)


# ---------------------------------------------------------------------------
# Range computation
# ---------------------------------------------------------------------------

def _calc_schedule_ranges(ctx, page) -> None:
    for row in page._calc_rows:
        if row.get("type") not in ("clip", "contrast"):
            continue
        domain = row.get("domain") or {}
        needs = (
            row.get("min") is None or row.get("max") is None
            if row.get("type") == "clip"
            else (
                domain.get("auto", True)
                and (domain.get("min") is None or domain.get("max") is None)
            )
        )
        if needs:
            _calc_schedule_range(ctx, page, row)


def _calc_schedule_initial_ranges(ctx, page) -> None:
    _calc_schedule_ranges(ctx, page)


def _calc_schedule_range(ctx, page, row) -> None:
    if page._calc_locked or not page._calc_rows:
        return
    if row.get("type") not in ("clip", "contrast"):
        return
    source = row.get("source") or {}
    files = _calc_row_files(page, row)
    if not files:
        return
    index = _calc_row_index(page, str(row.get("id")))
    if index < 0:
        return
    if source.get("column"):
        source_name = str(source["column"])
        extra = None
    elif source.get("row"):
        prev = _calc_row_by_id(page, str(source["row"]))
        if prev is None or not prev.get("output"):
            return
        source_name = str(prev["output"])
        prior = _calculation_stage({"rows": page._calc_rows[:index]})
        extra = [("calculation", prior)] if prior else None
    else:
        return

    page._calc_range_token += 1
    token = page._calc_range_token
    page._calc_range_pending[str(row.get("id"))] = token
    widget = page._calc_row_widgets.get(str(row.get("id")))
    status = None
    if widget is not None:
        status = getattr(widget._calc_editor, "_calc_status", None)
    if status is not None:
        status.setText("Computing data range…")
        status.show()
    page._calc_range_worker.request(
        token, ctx, page._calc_task_id, files, source_name, extra
    )
    if not page._calc_range_timer.isActive():
        page._calc_range_timer.start()


def _calc_poll_ranges(ctx, page) -> None:
    results = page._calc_range_worker.poll()
    if not results:
        if not page._calc_range_pending:
            page._calc_range_timer.stop()
        return
    changed = False
    for token, low, high, errors in results:
        row_id = next(
            (
                rid for rid, value in page._calc_range_pending.items()
                if value == token
            ),
            None,
        )
        if row_id is None:
            continue
        page._calc_range_pending.pop(row_id, None)
        row = _calc_row_by_id(page, row_id)
        if row is None:
            continue
        widget = page._calc_row_widgets.get(row_id)
        status = None
        if widget is not None:
            status = getattr(widget._calc_editor, "_calc_status", None)
        if low is None or high is None:
            if status is not None:
                status.setText("Could not compute the data range.")
                status.show()
            continue
        if row.get("type") == "clip" and not row.get("auto_range", True):
            if status is not None:
                status.hide()
            continue
        if row.get("type") == "contrast" and not (
            row.get("domain") or {}
        ).get("auto", True):
            if status is not None:
                status.hide()
            continue
        if high <= low:
            high = low + 1.0
        page._calc_updating = True
        try:
            if row.get("type") == "clip":
                row["min"] = float(low)
                row["max"] = float(high)
                row["auto_range"] = True
            else:
                domain = row.setdefault("domain", {})
                domain["min"] = float(low)
                domain["max"] = float(high)
                domain["auto"] = True
            if widget is not None:
                editor = getattr(widget, "_calc_editor", None)
                if editor is not None:
                    spin = getattr(editor, "_calc_min", None) or getattr(
                        editor, "_calc_domain_min", None
                    )
                    if spin is not None:
                        spin.blockSignals(True)
                        spin.setValue(float(low))
                        spin.blockSignals(False)
                    spin = getattr(editor, "_calc_max", None) or getattr(
                        editor, "_calc_domain_max", None
                    )
                    if spin is not None:
                        spin.blockSignals(True)
                        spin.setValue(float(high))
                        spin.blockSignals(False)
                    if status is not None:
                        status.hide()
        finally:
            page._calc_updating = False
        changed = True
    if changed:
        _calc_mark_dirty(ctx, page)
        _calc_update_errors(ctx, page)
        _calc_schedule_preview(ctx, page)
    if not page._calc_range_pending:
        page._calc_range_timer.stop()


def _calc_recompute_domain(ctx, page, row, auto_check) -> None:
    domain = row.setdefault("domain", {})
    domain["auto"] = True
    auto_check.blockSignals(True)
    auto_check.setChecked(True)
    auto_check.blockSignals(False)
    _calc_schedule_range(ctx, page, row)
    _calc_changed(ctx, page, row)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _calc_preview_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Preview</b>"))

    plot_widget = _widgets_plot_renderer._figure_make_plot_widget(
        ctx, page._calc_preview
    )
    plot_widget.setMinimumSize(120, 120)
    plot_widget.setSizePolicy(
        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
    )
    plot_widget.getPlotItem().getViewBox().setMouseEnabled(True, True)

    host = QWidget()
    grid = QVBoxLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.addWidget(plot_widget)
    overlay = QLabel("")
    overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    overlay.setWordWrap(True)
    overlay.setStyleSheet(
        "background-color: rgba(200, 200, 200, 210); color: #444; "
        "border: 1px solid #999; border-radius: 6px; padding: 12px 16px;"
    )
    overlay.hide()
    grid.addWidget(overlay)
    lay.addWidget(host, 3)

    settings_scroll = QScrollArea()
    settings_scroll.setWidgetResizable(True)
    settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
    settings_scroll.setStyleSheet("background: transparent;")
    settings_host = QWidget()
    settings_host.setStyleSheet("background: transparent;")
    settings_layout = QVBoxLayout(settings_host)
    settings_layout.setContentsMargins(0, 0, 0, 0)
    settings_layout.setSpacing(6)
    settings_scroll.setWidget(settings_host)
    lay.addWidget(settings_scroll, 2)

    page._calc_plot_widget = plot_widget
    page._calc_overlay = overlay
    page._calc_settings_layout = settings_layout
    return panel


def _calc_rebuild_settings(ctx, page) -> None:
    layout = getattr(page, "_calc_settings_layout", None)
    if layout is None:
        return
    _widgets_panels._clear_settings_layout(ctx, layout)
    preview = page._calc_preview
    enabled = not page._calc_locked
    records = [
        {"parquet": path, "name": _calc_file_name(ctx, path, page._calc_task_id)}
        for path in page._calc_files
    ]
    columns = _calc_preview_columns(ctx, page, preview.get("file"))
    plot_widget = page._calc_plot_widget

    layout.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    for rec in records:
        file_combo.addItem(rec["name"], rec["parquet"])
    if preview.get("file") is not None:
        idx = file_combo.findData(preview["file"])
        if idx >= 0:
            file_combo.setCurrentIndex(idx)
    file_combo.setEnabled(enabled)
    layout.addWidget(file_combo)

    layout.addWidget(QLabel("Plot type"))
    type_combo = _NoWheelComboBox()
    type_combo.addItem("Scatter", "scatter")
    type_combo.addItem("Histogram", "histogram")
    type_combo.addItem("Image", "image")
    idx = type_combo.findData(preview.get("type", "scatter"))
    type_combo.setCurrentIndex(max(0, idx))
    type_combo.setEnabled(enabled)
    layout.addWidget(type_combo)

    plot_type = preview.get("type", "scatter")
    x_combo = y_combo = channel_combo = None
    if plot_type == "image":
        layout.addWidget(QLabel("Color channel"))
        channel_combo = _calc_param_combo(
            columns, preview.get("image_channel"), enabled
        )
        layout.addWidget(channel_combo)
        rec = _widgets_plot_renderer._figure_record(ctx, preview.get("file"))
        draft = _calculation_stage({"rows": page._calc_rows})
        _widgets_image_level._image_level_controls(
            ctx, layout, preview.get("image_channel"),
            preview.get("image_min"), preview.get("image_max"), enabled,
            on_minmax=lambda lo, hi: _calc_set_image_levels(
                ctx, page, lo, hi
            ),
            task_id=page._calc_task_id,
            parquet_path=rec.get("parquet") if rec else None,
            extra_stages=[("calculation", draft)] if draft else None,
            configs=_widgets_plot_renderer._figure_axis_scale_configs(
                ctx, page._calc_task_id,
                rec.get("parquet") if rec else None,
                preview.get("image_channel"),
            ),
        )
    else:
        layout.addWidget(QLabel("X parameter"))
        x_combo = _calc_param_combo(columns, preview.get("x"), enabled)
        layout.addWidget(x_combo)
        if plot_type == "scatter":
            layout.addWidget(QLabel("Y parameter"))
            y_combo = _calc_param_combo(columns, preview.get("y"), enabled)
            layout.addWidget(y_combo)

    if plot_type == "scatter":
        _widgets_plot_renderer._figure_scatter_color_settings(
            ctx, layout, preview, plot_widget, columns, enabled,
            on_change=lambda: _calc_schedule_preview(ctx, page),
            on_rebuild=lambda: (
                _calc_rebuild_settings(ctx, page),
                _calc_render_preview(ctx, page),
            ),
            task_id=page._calc_task_id,
        )

    if plot_type != "image":
        layout.addWidget(QLabel("X range"))
        layout.addWidget(_widgets_plot_renderer._figure_range_row(
            ctx, preview, "x", plot_widget, enabled,
            configs=_widgets_plot_renderer._figure_axis_scale_configs(
                ctx, page._calc_task_id, preview.get("file"),
                preview.get("x"),
            ),
        ))
    if plot_type == "scatter":
        layout.addWidget(QLabel("Y range"))
        layout.addWidget(_widgets_plot_renderer._figure_range_row(
            ctx, preview, "y", plot_widget, enabled,
            configs=_widgets_plot_renderer._figure_axis_scale_configs(
                ctx, page._calc_task_id, preview.get("file"),
                preview.get("y"),
            ),
        ))

    _widgets_plot_renderer._figure_display_toggles(
        ctx, layout, preview, enabled,
        on_change=lambda: _widgets_plot_renderer._figure_apply_display_options(
            ctx, preview, plot_widget
        ),
    )
    layout.addStretch()

    def on_type():
        previous = preview.get("type")
        preview["type"] = type_combo.currentData()
        row = _calc_row_by_id(page, page._calc_selected_id)
        output = str((row or {}).get("output") or "")
        if preview["type"] == "image":
            # Image axes are the pixel coordinates, never a channel.
            preview["last_y"] = preview.get("y")
            preview["x"] = "x"
            preview["y"] = "y"
            if output:
                preview["image_channel"] = output
        elif output:
            preview["x"] = output
            columns = _calc_preview_columns(
                ctx, page, preview.get("file")
            )
            last_y = preview.get("last_y")
            if last_y in columns and last_y not in ("x", "y"):
                preview["y"] = last_y
            elif previous == "image" or preview.get("y") in ("x", "y"):
                preview["y"] = _calc_other_column(columns, output)
        _calc_rebuild_settings(ctx, page)
        _calc_render_preview(ctx, page)

    def on_file():
        path = file_combo.currentData()
        preview["file"] = path
        preview["file_name"] = _calc_file_name(
            ctx, path, page._calc_task_id
        )
        available = _calc_preview_columns(ctx, page, path)
        for key in ("x", "y", "image_channel"):
            if preview.get(key) not in available:
                preview[key] = None
        _calc_rebuild_settings(ctx, page)
        _calc_render_preview(ctx, page)

    def on_param(key, combo):
        preview[key] = combo.currentText() or None
        _calc_schedule_preview(ctx, page)

    type_combo.currentIndexChanged.connect(lambda _=0: on_type())
    file_combo.currentIndexChanged.connect(lambda _=0: on_file())
    if x_combo is not None:
        x_combo.currentIndexChanged.connect(lambda _=0: on_param("x", x_combo))
    if y_combo is not None:
        y_combo.currentIndexChanged.connect(lambda _=0: on_param("y", y_combo))
    if channel_combo is not None:
        channel_combo.currentIndexChanged.connect(
            lambda _=0: on_param("image_channel", channel_combo)
        )
    page._calc_file_combo = file_combo


def _calc_set_image_levels(ctx, page, low, high) -> None:
    page._calc_preview["image_min"] = low
    page._calc_preview["image_max"] = high
    _calc_schedule_preview(ctx, page)


def _calc_schedule_preview(ctx, page) -> None:
    timer = getattr(page, "_calc_preview_timer", None)
    if timer is not None:
        timer.start()


def _calc_render_preview(ctx, page) -> None:
    timer = getattr(page, "_calc_preview_timer", None)
    if timer is not None:
        timer.stop()
    preview = getattr(page, "_calc_preview", None)
    plot_widget = getattr(page, "_calc_plot_widget", None)
    if preview is None or plot_widget is None:
        return
    overlay = getattr(page, "_calc_overlay", None)
    row = _calc_row_by_id(page, page._calc_selected_id)
    path = preview.get("file")
    if not path:
        plot_widget.clear()
        plot_widget.setTitle(
            "Select a data file to preview", color="#6b7785", size="10pt"
        )
        if overlay is not None:
            overlay.hide()
        return
    if row is None:
        plot_widget.clear()
        plot_widget.setTitle(
            "Add a calculation row to see a preview",
            color="#6b7785", size="10pt",
        )
        if overlay is not None:
            overlay.hide()
        return
    if path not in (row.get("files") or []):
        plot_widget.clear()
        plot_widget.setTitle("")
        if overlay is not None:
            overlay.setText(
                "This row does not apply to the preview file.\n"
                "Choose another file or add it to the row's Files."
            )
            overlay.show()
        return
    if overlay is not None:
        overlay.hide()

    columns = _calc_preview_columns(ctx, page, path)
    plot_type = preview.get("type", "scatter")
    output = str(row.get("output") or "")
    stage = _calculation_stage({"rows": page._calc_rows})
    produced = {
        str(item.get("output"))
        for item in (stage or {}).get("rows") or []
    }
    if output and output not in produced:
        # The row is incomplete (missing source, thresholds or range) and
        # was skipped while compiling the stage.  Showing the previous
        # render here would be misleading.
        plot_widget.clear()
        plot_widget.setTitle("")
        if overlay is not None:
            overlay.setText(
                "This calculation row is incomplete.\n"
                "Fix the parameters shown in red above."
            )
            overlay.show()
        return
    missing = []
    if plot_type == "image":
        # Keep the axes fixed to the image's pixel coordinates even when a
        # saved preview still carries the scatter channel names.
        preview["x"] = "x"
        preview["y"] = "y"
        channel = preview.get("image_channel") or output
        if channel not in columns:
            missing.append(str(channel))
        else:
            preview["image_channel"] = channel
            preview["layers"] = [{
                "parameter": channel,
                "color": (255, 255, 255),
                "name": str(channel),
                "min": preview.get("image_min"),
                "max": preview.get("image_max"),
            }]
    else:
        x_name = preview.get("x") or output
        if x_name not in columns:
            missing.append(str(x_name))
        else:
            preview["x"] = x_name
        if plot_type == "scatter":
            y_name = preview.get("y")
            if not y_name or y_name not in columns:
                y_name = _calc_other_column(columns, x_name)
                preview["y"] = y_name
            if y_name not in columns:
                missing.append(str(y_name))
    if missing:
        if overlay is not None:
            overlay.setText(
                "Parameter not in this file:\n" + ", ".join(missing)
            )
            overlay.show()
        plot_widget.clear()
        return

    extra = [("calculation", stage)] if stage is not None else None
    # Extra stages are appended to the upstream map pipeline by the renderer.
    _widgets_plot_renderer._figure_render(
        ctx, preview, plot_widget,
        task_id=page._calc_task_id, extra_stages=extra,
    )


# ---------------------------------------------------------------------------
# Save / close / refresh
# ---------------------------------------------------------------------------

def _calc_save_now(ctx, page) -> None:
    errors = _calc_row_errors(ctx, page)
    if not page._calc_files:
        QMessageBox.warning(
            ctx, "Calculation", "Select at least one data file first."
        )
        return
    if errors:
        messages = []
        for row in page._calc_rows:
            for message in errors.get(str(row.get("id")), [])[:2]:
                messages.append(f"{row.get('output') or 'row'}: {message}")
            if len(messages) >= 6:
                break
        QMessageBox.warning(
            ctx, "Calculation",
            "Fix these problems before saving:\n" + "\n".join(messages[:6]),
        )
        return
    params = page._calc_task_def.setdefault("params", {})
    params["files"] = list(page._calc_files)
    params["rows"] = page._calc_rows
    if ctx.project is not None:
        ctx.project.save()
    page._calc_snapshot = copy.deepcopy(params)
    page._calc_dirty = False
    _calc_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Calculation saved")
    ctx._module_saved(page)


def _calc_close(ctx, page) -> None:
    if page._calc_dirty:
        reply = QMessageBox.question(
            ctx, "Close Calculation",
            "There are unsaved changes.\nClose without saving? Unsaved "
            "changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._calc_task_def["params"] = page._calc_snapshot
    timer = getattr(page, "_calc_range_timer", None)
    if timer is not None:
        timer.stop()
    ctx._close_module_page(page)


def _calc_cleanup(ctx, page) -> None:
    """Stop the page's timers and background range worker."""
    for name in ("_calc_preview_timer", "_calc_range_timer"):
        timer = getattr(page, name, None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
    worker = getattr(page, "_calc_range_worker", None)
    if worker is not None:
        worker._stop.set()


def _calc_refresh(ctx, page) -> None:
    """Refresh available files/columns after an upstream module changed."""
    available = {
        rec.get("parquet") for rec in _calc_available_files(ctx, page._calc_task_id)
    }
    page._calc_files = [
        path for path in page._calc_files if path in available
    ]
    for row in page._calc_rows:
        row["files"] = [
            path for path in row.get("files") or [] if path in page._calc_files
        ]
    preview = getattr(page, "_calc_preview", None)
    if preview is not None and preview.get("file") not in page._calc_files:
        preview["file"] = page._calc_files[0] if page._calc_files else None
        preview["file_name"] = (
            _calc_file_name(ctx, preview["file"], page._calc_task_id)
            if preview.get("file") else None
        )
    if hasattr(page, "_calc_files_list"):
        _calc_rebuild_files_list(ctx, page)
    if hasattr(page, "_calc_row_list"):
        _calc_rebuild_rows(ctx, page, select_id=page._calc_selected_id)
    if hasattr(page, "_calc_settings_layout"):
        _calc_rebuild_settings(ctx, page)
        _calc_render_preview(ctx, page)
