"""Export page: layout, parameter selection and background export jobs."""
from __future__ import annotations

import copy
import queue
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from smage.core.constants import _FEATURE_LOG_LIMIT
from smage.core.formatting import _fcs_safe_channel_names
from smage.core.pipeline import (
    _evaluate_stages, _pipeline_dependencies, _rename_applies,
)
from smage.data.image_index import _image_geometry
from smage.modules.export.state import (
    _EXPORT_EXT, _EXPORT_FORMATS, _ExportCancelled, _ExportPane,
)
from smage.runlog import runlog
from smage.widgets.controls import _NoWheelComboBox
from functools import partial
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


def _export_params_for(ctx, page, kind: str) -> dict:
    section = page._export_params.setdefault(kind, {})
    section.setdefault("files", [])
    section.setdefault("columns" if kind == "table" else "channels", [])
    section.setdefault("dir", "")
    section.setdefault("format", _EXPORT_FORMATS[kind][0][1])
    section.setdefault("log", [])
    return section


def _export_pane(ctx, page, kind: str) -> _ExportPane:
    return page._export_panes[kind]


def _export_default_columns(ctx, page, kind: str) -> list:
    return _export_params_for(ctx, page, kind)[
        "columns" if kind == "table" else "channels"
    ]


def _build_export_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
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
        draft = QLabel("Draft (locked)")
        draft.setStyleSheet("color: #b58900; font-weight: bold;")
        tl.addWidget(draft)
    tl.addStretch()
    save_btn = QPushButton("Save")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setEnabled(not locked)
    close_btn = QPushButton("Close")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(lambda _=False: _export_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("tab", "table")
    page._export_task_def = task_def
    page._export_task_id = task_def.get("id")
    page._export_params = params
    page._export_locked = locked
    page._export_save_btn = save_btn
    page._export_dirty = False
    page._export_snapshot = copy.deepcopy(params)
    page._export_panes = {
        "table": _ExportPane("table"),
        "image": _ExportPane("image"),
    }
    save_btn.clicked.connect(lambda _=False: _export_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QVBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(8)

    tabs = QTabWidget()
    tabs.addTab(_export_subpage(ctx, page, "table"), "Table Export")
    tabs.addTab(_export_subpage(ctx, page, "image"), "Image Export")
    tabs.setCurrentIndex(0 if params.get("tab") != "image" else 1)
    tabs.currentChanged.connect(
        lambda index: _export_on_tab(ctx, page, index)
    )
    page._export_tabs = tabs
    cl.addWidget(tabs, 1)
    layout.addWidget(content, 1)

    _export_refresh(ctx, page)
    _export_update_save_btn(ctx, page)
    return page


def _export_subpage(ctx, page, kind: str) -> QWidget:
    pane = _export_pane(ctx, page, kind)
    pane.log_box = None

    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.addWidget(_export_files_panel(ctx, page, kind))
    split.addWidget(_export_columns_panel(ctx, page, kind))
    split.addWidget(_export_settings_panel(ctx, page, kind))
    # Files | Columns | Settings share the width 1:1:1.
    for index in range(3):
        split.setStretchFactor(index, 1)
    split.setSizes([300, 300, 300])
    lay.addWidget(split, 1)

    def apply_ratio():
        total = split.width()
        if total <= 0:
            QTimer.singleShot(30, apply_ratio)
            return
        # The splitter's two handles take width too; leave them out so the
        # three panels themselves are equally wide.
        usable = max(3, total - 2 * split.handleWidth())
        third = usable // 3
        split.setSizes([third, third, usable - 2 * third])

    pane.apply_ratio = apply_ratio
    pane.splitter = split
    QTimer.singleShot(0, apply_ratio)
    return host


def _export_files_panel(ctx, page, kind: str) -> QWidget:
    pane = _export_pane(ctx, page, kind)
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    title = "Data files" if kind == "table" else "Image files"
    lay.addWidget(QLabel(f"<b>{title}</b>"))
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    btn = QPushButton("Select files…")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setEnabled(not page._export_locked)
    btn.clicked.connect(lambda _=False: _export_select_files(ctx, page, kind))
    lay.addWidget(btn)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(box, 1)
    hint = QLabel(
        "Pick the files to export. Image files can also be exported as "
        "tables."
        if kind == "table" else
        "Only image files are listed here."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)

    pane.files_btn = btn
    pane.files_box = box
    pane.files_count = count
    return panel


def _export_columns_panel(ctx, page, kind: str) -> QWidget:
    pane = _export_pane(ctx, page, kind)
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    title = "Columns" if kind == "table" else "Channels"
    lay.addWidget(QLabel(f"<b>{title}</b>"))
    if kind == "image":
        fixed = QLabel(
            "Fixed columns: x, y  (written first, not reorderable)"
        )
        fixed.setWordWrap(True)
        fixed.setStyleSheet("color: #6b7785;")
        lay.addWidget(fixed)
    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    btn = QPushButton(
        "Select columns…" if kind == "table" else "Select channels…"
    )
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setEnabled(not page._export_locked)
    btn.clicked.connect(lambda _=False: _export_select_columns(ctx, page, kind))
    lay.addWidget(btn)
    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    box.setDefaultDropAction(Qt.DropAction.MoveAction)
    box.setDragEnabled(not page._export_locked)
    box.setToolTip("Drag rows to change their order in the exported file")
    box.model().rowsMoved.connect(
        lambda *a: _export_on_order(ctx, page, kind)
    )
    lay.addWidget(box, 1)
    hint = QLabel(
        "Only columns shared by every selected file are offered. Drag to "
        "reorder — this is the column order in the exported file."
        if kind == "table" else
        "Only channels shared by every selected image file are offered. "
        "Drag to reorder — this is the channel order in the TIFF."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)

    pane.cols_btn = btn
    pane.cols_box = box
    pane.cols_count = count
    return panel


def _export_settings_panel(ctx, page, kind: str) -> QWidget:
    pane = _export_pane(ctx, page, kind)
    section = _export_params_for(ctx, page, kind)
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Export settings</b>"))
    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(6)
    grid.addWidget(QLabel("Output folder"), 0, 0, 1, 2)
    dir_edit = QLineEdit(str(section.get("dir") or ""))
    dir_edit.setReadOnly(True)
    dir_edit.setPlaceholderText("Choose a folder…")
    dir_edit.setEnabled(not page._export_locked)
    grid.addWidget(dir_edit, 1, 0)
    browse_btn = QPushButton("Browse…")
    browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    browse_btn.setEnabled(not page._export_locked)
    browse_btn.clicked.connect(lambda _=False: _export_browse(ctx, page, kind))
    grid.addWidget(browse_btn, 1, 1)
    grid.addWidget(QLabel("Format"), 2, 0, 1, 2)
    fmt_combo = _NoWheelComboBox()
    for label, value in _EXPORT_FORMATS[kind]:
        fmt_combo.addItem(label, value)
    index = fmt_combo.findData(section.get("format"))
    fmt_combo.setCurrentIndex(max(0, index))
    fmt_combo.setEnabled(
        not page._export_locked and len(_EXPORT_FORMATS[kind]) > 1
    )
    fmt_combo.currentIndexChanged.connect(
        lambda _i=0: _export_on_format(ctx, page, kind)
    )
    grid.addWidget(fmt_combo, 3, 0, 1, 2)
    lay.addLayout(grid)

    row = QHBoxLayout()
    export_btn = QPushButton("Export")
    export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    export_btn.clicked.connect(lambda _=False: _export_start(ctx, page, kind))
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel_btn.setVisible(False)
    cancel_btn.clicked.connect(lambda _=False: _export_cancel(ctx, page, kind))
    row.addWidget(export_btn, 1)
    row.addWidget(cancel_btn)
    lay.addLayout(row)

    progress = QProgressBar()
    progress.setTextVisible(False)
    progress.setFixedHeight(8)
    progress.setVisible(False)
    lay.addWidget(progress)
    status = QLabel("")
    status.setWordWrap(True)
    status.setStyleSheet("color: #6b7785;")
    lay.addWidget(status)

    lay.addWidget(QLabel("<b>Log</b>"))
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Export progress appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    for line in section.get("log") or []:
        log.appendPlainText(str(line))
    lay.addWidget(log, 1)

    pane.dir_edit = dir_edit
    pane.browse_btn = browse_btn
    pane.format_combo = fmt_combo
    pane.export_btn = export_btn
    pane.cancel_btn = cancel_btn
    pane.progress = progress
    pane.status = status
    pane.log_box = log
    return panel


def _export_records(ctx, page, kind: str) -> list:
    records = _data_files._available_files(ctx, page._export_task_id)
    if kind == "image":
        records = [
            rec for rec in records if _data_outputs._seg_is_image_file(ctx, rec)
        ]
    return records


def _export_schema_types(ctx, path) -> tuple:
    """(column names, numeric column names) of a parquet file."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        schema = pq.ParquetFile(path).schema_arrow
    except Exception:  # noqa: BLE001
        return set(), set()
    names = set(schema.names)
    numeric = set()
    for field in schema:
        t = field.type
        if (
            pa.types.is_integer(t) or pa.types.is_floating(t)
            or pa.types.is_boolean(t) or pa.types.is_decimal(t)
        ):
            numeric.add(field.name)
    return names, numeric


def _export_source_column(ctx, task_id, path, name: str) -> str:
    """Map a display column name back through upstream Rename modules."""
    for stage in reversed(_data_columns._column_rename_stages(ctx, task_id)):
        if not _rename_applies(stage, path):
            continue
        reverse = {v: k for k, v in stage["map"].items()}
        name = reverse.get(name, name)
    return name


def _export_common_columns(ctx, page, kind: str) -> list:
    files = list(_export_params_for(ctx, page, kind).get("files") or [])
    if not files:
        return []
    lists = [
        _data_columns._columns_for(ctx, path, page._export_task_id) for path in files
    ]
    if not lists:
        return []
    common = set(lists[0])
    for columns in lists[1:]:
        common &= set(columns)
    columns = [name for name in lists[0] if name in common]
    if kind == "image":
        columns = [c for c in columns if c not in ("x", "y")]
        # A channel must be numeric in every selected file.
        numeric_ok = None
        for path in files:
            schema_names, numeric = _export_schema_types(ctx, path)
            ok = set()
            for name in columns:
                source = _export_source_column(ctx,
                    page._export_task_id, path, name
                )
                # Columns generated by modules (masks, labels…) are numeric.
                if source not in schema_names or source in numeric:
                    ok.add(name)
            numeric_ok = ok if numeric_ok is None else (numeric_ok & ok)
        columns = [c for c in columns if c in (numeric_ok or set())]
    return columns


def _export_select_files(ctx, page, kind: str) -> None:
    records = _export_records(ctx, page, kind)
    labels = []
    by_label = {}
    seen = Counter()
    for rec in records:
        path = rec.get("parquet")
        if not path:
            continue
        label = _data_naming._comp_file_name(ctx, path, page._export_task_id)
        seen[label] += 1
        if seen[label] > 1:
            label = f"{label} [{_data_files._rel_data_key(ctx, path)}]"
        labels.append(label)
        by_label[label] = path
    section = _export_params_for(ctx, page, kind)
    current = [
        _data_naming._comp_file_name(ctx, path, page._export_task_id)
        for path in section.get("files") or []
        if path
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files" if kind == "table" else "Select image files",
        labels, [c for c in current if c in by_label],
        "Filter files...", reorder=True,
    )
    if chosen is None:
        return
    section["files"] = [
        by_label[label] for label in chosen if label in by_label
    ]
    if kind == "image" and not section.get("channels"):
        # Convenience: start with every available channel selected.
        section["channels"] = _export_common_columns(ctx, page, kind)
    _export_mark_dirty(ctx, page)
    _export_refresh(ctx, page)


def _export_select_columns(ctx, page, kind: str) -> None:
    section = _export_params_for(ctx, page, kind)
    key = "columns" if kind == "table" else "channels"
    options = _export_common_columns(ctx, page, kind)
    if not options:
        QMessageBox.information(
            ctx.host, "Select " + ("columns" if kind == "table" else "channels"),
            "Select data files with common columns first.",
        )
        return
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select columns" if kind == "table" else "Select channels",
        options, section.get(key) or [],
        "Filter columns...", reorder=True,
    )
    if chosen is None:
        return
    section[key] = list(chosen)
    _export_mark_dirty(ctx, page)
    _export_refresh(ctx, page)


def _export_on_order(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    if pane.syncing:
        return
    values = [
        pane.cols_box.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(pane.cols_box.count())
    ]
    key = "columns" if kind == "table" else "channels"
    _export_params_for(ctx, page, kind)[key] = [
        value for value in values if value
    ]
    _export_mark_dirty(ctx, page)


def _export_browse(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    section = _export_params_for(ctx, page, kind)
    start = str(section.get("dir") or "")
    folder = QFileDialog.getExistingDirectory(
        ctx.host, "Select export folder", start
    )
    if not folder:
        return
    section["dir"] = folder
    pane.dir_edit.setText(folder)
    _export_mark_dirty(ctx, page)
    _export_update_buttons(ctx, page, kind)


def _export_on_format(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    value = pane.format_combo.currentData()
    if value and value != _export_params_for(ctx, page, kind).get("format"):
        _export_params_for(ctx, page, kind)["format"] = value
        _export_mark_dirty(ctx, page)
        _export_update_buttons(ctx, page, kind)


def _export_on_tab(ctx, page, index: int) -> None:
    kind = "image" if index == 1 else "table"
    page._export_params["tab"] = kind
    # A hidden sub-page kept the splitter sizes it had before it was shown;
    # re-apply the 1:1:1 layout once the newly visible page has a size.
    pane = _export_pane(ctx, page, kind)
    if pane.apply_ratio is not None:
        QTimer.singleShot(0, pane.apply_ratio)


def _export_refresh(ctx, page) -> None:
    for kind in ("table", "image"):
        _export_refresh_pane(ctx, page, kind)


def _export_refresh_pane(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    section = _export_params_for(ctx, page, kind)
    records = _export_records(ctx, page, kind)
    by_path = {
        rec.get("parquet"): rec for rec in records if rec.get("parquet")
    }
    section["files"] = [
        path for path in section.get("files") or [] if path in by_path
    ]
    key = "columns" if kind == "table" else "channels"
    common = _export_common_columns(ctx, page, kind)
    section[key] = [c for c in section.get(key) or [] if c in common]

    pane.files_box.blockSignals(True)
    pane.files_box.clear()
    for path in section["files"]:
        item = QListWidgetItem(
            _data_naming._comp_file_name(ctx, path, page._export_task_id)
        )
        item.setToolTip(str(path))
        pane.files_box.addItem(item)
    pane.files_box.blockSignals(False)

    pane.syncing = True
    pane.cols_box.blockSignals(True)
    pane.cols_box.clear()
    for name in section[key]:
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setFlags(
            item.flags() | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        pane.cols_box.addItem(item)
    pane.cols_box.blockSignals(False)
    pane.syncing = False

    files = len(section["files"])
    pane.files_count.setText(
        f"{files} file{'s' if files != 1 else ''} selected"
    )
    count = len(section[key])
    if kind == "table":
        pane.cols_count.setText(
            f"{count} column{'s' if count != 1 else ''} selected"
        )
    else:
        pane.cols_count.setText(
            f"{count} channel{'s' if count != 1 else ''} selected"
        )
    _export_update_buttons(ctx, page, kind)


def _export_update_buttons(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    section = _export_params_for(ctx, page, kind)
    key = "columns" if kind == "table" else "channels"
    ready = bool(
        section.get("files") and section.get(key)
        and str(section.get("dir") or "").strip()
    )
    pane.export_btn.setEnabled(ready and not pane.running)
    pane.cancel_btn.setVisible(pane.running)
    pane.cancel_btn.setEnabled(pane.running)
    editable = not page._export_locked and not pane.running
    pane.files_btn.setEnabled(editable)
    pane.cols_btn.setEnabled(editable)
    pane.browse_btn.setEnabled(editable)
    pane.dir_edit.setEnabled(editable)
    pane.format_combo.setEnabled(
        editable and len(_EXPORT_FORMATS[kind]) > 1
    )


def _export_mark_dirty(ctx, page) -> None:
    page._export_dirty = True
    _export_update_save_btn(ctx, page)


def _export_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_export_save_btn", None)
    if btn is not None:
        btn.setEnabled(bool(page._export_dirty) and not page._export_locked)


def _export_log(ctx, page, kind: str, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    section = _export_params_for(ctx, page, kind)
    lines = section.setdefault("log", [])
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    pane = _export_pane(ctx, page, kind)
    if pane.log_box is not None:
        for line in str(text).splitlines():
            if line.strip():
                pane.log_box.appendPlainText(f"[{ts}] {line}")


def _export_file_name(ctx, label: str, ext: str) -> str:
    text = str(label or "")
    stem = Path(text).stem if Path(text).suffix else text
    stem = stem.strip()
    for ch in '<>:"/\\|?*':
        stem = stem.replace(ch, "_")
    stem = stem.strip().rstrip(". ").strip()
    if not stem:
        stem = "export"
    return stem + ext


def _export_targets(ctx, page, files, ext: str) -> list:
    items = []
    for path in files:
        label = _data_naming._comp_file_name(ctx, path, page._export_task_id)
        items.append((path, label, _export_file_name(ctx, label, ext)))
    return items


def _export_conflicts(ctx, out_dir: Path, items) -> list:
    problems = []
    seen = {}
    for _path, label, name in items:
        key = name.casefold()
        if key in seen:
            problems.append(
                f"{name} — the same output name for '{seen[key]}' "
                f"and '{label}'"
            )
        else:
            seen[key] = label
        target = out_dir / name
        if target.exists():
            problems.append(f"{name} — already exists in {out_dir}")
    return problems


def _export_start(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    if pane.running:
        return
    section = _export_params_for(ctx, page, kind)
    key = "columns" if kind == "table" else "channels"
    files = list(section.get("files") or [])
    selected = list(section.get(key) or [])
    folder = str(section.get("dir") or "").strip()
    if not files:
        QMessageBox.warning(ctx.host, "Export", "Select data files first.")
        return
    if not selected:
        QMessageBox.warning(
            ctx.host, "Export",
            "Select " + ("columns" if kind == "table" else "channels")
            + " first.",
        )
        return
    if not folder:
        QMessageBox.warning(
            ctx.host, "Export", "Choose an export folder first."
        )
        return
    if not _export_confirm(ctx, page, kind, files, selected, folder):
        return

    pane.running = True
    pane.cancel = False
    pane.progress.setRange(0, max(1, len(files)))
    pane.progress.setValue(0)
    pane.progress.setVisible(True)
    pane.status.setText("Exporting…")
    _export_update_buttons(ctx, page, kind)

    runlog.action(
        "export.start", module=str(page._export_task_id), tab=kind,
        files=len(files), columns=len(selected),
        format=str(section.get("format")), dir=folder,
    )
    q = queue.Queue()
    pane.queue = q
    pane.thread = threading.Thread(
        target=partial(_export_job, ctx),
        args=(page, kind, q, copy.deepcopy(section)),
        daemon=True,
    )
    pane.thread.start()
    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _export_poll(ctx, page, kind))
    timer.start()
    pane.timer = timer


def _export_confirm(ctx, page, kind: str, files, selected, folder: str) -> bool:
    section = _export_params_for(ctx, page, kind)
    fmt = str(section.get("format") or "")
    preview = ", ".join(selected[:8])
    if len(selected) > 8:
        preview += " …"
    label = "Columns" if kind == "table" else "Channels"
    count = len(files)
    lines = [
        f"Export {count} file{'s' if count != 1 else ''} "
        f"as {fmt.upper()}?",
        "",
        f"Destination: {folder}",
        f"{label} ({len(selected)}): {preview}",
    ]
    if kind == "image":
        lines.append("x and y are written as fixed columns.")
    reply = QMessageBox.question(
        ctx.host, "Export", "\n".join(lines),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def _export_cancel(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    if not pane.running:
        return
    pane.cancel = True
    pane.status.setText("Cancelling…")


def _export_cancelled(ctx, page, kind: str) -> bool:
    return bool(_export_pane(ctx, page, kind).cancel)


def _export_job(ctx, page, kind: str, q, params) -> None:
    try:
        if kind == "table":
            _export_job_table(ctx, page, q, params)
        else:
            _export_job_image(ctx, page, q, params)
    except _ExportCancelled:
        q.put(("cancelled", ""))
    except Exception as exc:  # noqa: BLE001
        runlog.error(exc)
        q.put(("error", f"{type(exc).__name__}: {exc}"))


def _export_job_table(ctx, page, q, params) -> None:
    files = list(params.get("files") or [])
    columns = list(params.get("columns") or [])
    fmt = str(params.get("format") or "csv")
    ext = _EXPORT_EXT.get(fmt, ".csv")
    out_dir = Path(str(params.get("dir") or "")).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    q.put(("log", f"Table export ({fmt.upper()}) — {len(files)} file(s), "
                  f"{len(columns)} column(s)"))
    items = _export_targets(ctx, page, files, ext)
    conflicts = _export_conflicts(ctx, out_dir, items)
    if conflicts:
        q.put(("conflict", conflicts))
        return
    total_rows = 0
    for index, (path, label, name) in enumerate(items):
        if _export_cancelled(ctx, page, "table"):
            raise _ExportCancelled
        q.put(("log", f"[{index + 1}/{len(items)}] {label} → {name}"))
        frame = _export_frame(ctx, page, path, columns)
        target = out_dir / name
        try:
            if fmt == "csv":
                frame.to_csv(target, index=False, encoding="utf-8-sig")
            elif fmt == "parquet":
                frame.to_parquet(target, index=False)
            else:
                for note in _export_write_fcs(ctx, target, frame):
                    q.put(("log", "  " + note))
        except Exception:
            # Never leave a half-written file behind: a retry would treat
            # it as a name conflict.
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        total_rows += len(frame)
        q.put(("log", f"  wrote {len(frame):,} rows × "
                      f"{len(frame.columns)} columns"))
        q.put(("progress", index + 1, len(items)))
    q.put(("done", {
        "files": len(items), "rows": total_rows, "dir": str(out_dir),
    }))


def _export_frame(ctx, page, path, columns):
    """Read the exported table exactly as the module sees the data."""
    import numpy as np
    import pandas as pd

    task_id = page._export_task_id
    schema_names, numeric = _export_schema_types(ctx, path)
    num_cols = []
    txt_cols = []
    for name in columns:
        source = _export_source_column(ctx, task_id, path, name)
        if source in schema_names and source not in numeric:
            txt_cols.append(name)
        else:
            num_cols.append(name)
    values = {}
    if num_cols:
        values.update(_data_columns._column_data(ctx, task_id, path, num_cols))
    if txt_cols:
        values.update(
            _data_columns._column_data(ctx, task_id, path, txt_cols, numeric=False)
        )
    length = len(next(iter(values.values()))) if values else 0
    data = {}
    for name in columns:
        array = values.get(name)
        data[name] = (
            array if array is not None else np.full(length, np.nan)
        )
    return pd.DataFrame(data, columns=list(columns))


def _export_write_fcs(ctx, target: Path, frame) -> list:
    """Write an FCS 3.1 file (float32 events); return log notes."""
    import numpy as np
    import pandas as pd
    from array import array as _array

    import flowio

    notes = []
    columns = list(frame.columns)
    matrix = np.empty((len(frame), len(columns)), dtype="float32")
    for j, name in enumerate(columns):
        source = frame[name]
        converted = pd.to_numeric(source, errors="coerce")
        lost = int(converted.isna().sum()) - int(source.isna().sum())
        if lost > 0:
            notes.append(
                f"'{name}': {lost:,} non-numeric value(s) written as NaN"
            )
        matrix[:, j] = converted.to_numpy(dtype="float64")
    channel_names, name_notes = _fcs_safe_channel_names(columns)
    notes.extend(name_notes)
    data = _array("f")
    data.frombytes(
        np.ascontiguousarray(matrix.reshape(-1), dtype="<f4").tobytes()
    )
    with open(target, "wb") as fh:
        flowio.create_fcs(
            fh, data, channel_names, opt_channel_names=channel_names
        )
    notes.append("FCS stores 32-bit floats")
    return notes


def _export_image_geometry(ctx, path):
    """(xmin, ymin, width, height) of an image parquet, or None."""
    manifest = _data_image_index._image_load_manifest(ctx, path)
    if manifest is not None and _data_image_index._image_index_valid(ctx, path, manifest):
        xmin = float(manifest["xmin"])
        xmax = float(manifest["xmax"])
        ymin = float(manifest["ymin"])
        ymax = float(manifest["ymax"])
    else:
        try:
            xmin, xmax, ymin, ymax, _total, _names = _image_geometry(path)
        except Exception:  # noqa: BLE001
            return None
    width = int(round(xmax - xmin)) + 1
    height = int(round(ymax - ymin)) + 1
    if width <= 0 or height <= 0:
        return None
    return xmin, ymin, width, height


def _export_job_image(ctx, page, q, params) -> None:
    import tifffile

    files = list(params.get("files") or [])
    channels = list(params.get("channels") or [])
    out_dir = Path(str(params.get("dir") or "")).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    q.put(("log", f"Image export (TIFF) — {len(files)} file(s), "
                  f"{len(channels)} channel(s)"))
    items = _export_targets(ctx, page, files, ".tif")
    conflicts = _export_conflicts(ctx, out_dir, items)
    if conflicts:
        q.put(("conflict", conflicts))
        return
    for index, (path, label, name) in enumerate(items):
        if _export_cancelled(ctx, page, "image"):
            raise _ExportCancelled
        geometry = _export_image_geometry(ctx, path)
        if geometry is None:
            q.put(("log", f"[{index + 1}/{len(items)}] {label}: "
                          "skipped — no image pixels found"))
            q.put(("progress", index + 1, len(items)))
            continue
        xmin, ymin, width, height = geometry
        q.put(("log", f"[{index + 1}/{len(items)}] {label} → {name} — "
                      f"{len(channels)} channel(s), {width}×{height}"))
        result = _export_image_data(ctx,
            page, path, channels, geometry, q,
        )
        array, missing, duplicates, outside, missing_channels = result
        if missing_channels:
            q.put(("log", "  not numeric / not found (written as NaN): "
                          + ", ".join(missing_channels)))
        target = out_dir / name
        try:
            if array.shape[0] == 1:
                tifffile.imwrite(
                    target, array[0], photometric="minisblack"
                )
            else:
                tifffile.imwrite(
                    target, array, photometric="minisblack",
                    metadata={"axes": "CYX"},
                )
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        q.put(("log", f"  wrote {array.shape[0]}×{array.shape[1]}×"
                      f"{array.shape[2]} (C×H×W), float32"))
        if missing:
            q.put(("log", f"  {missing:,} pixel(s) not covered by any row "
                          "— written as 0"))
        if duplicates:
            q.put(("log", f"  {duplicates:,} duplicate pixel(s) — the last "
                          "value was kept"))
        if outside:
            q.put(("log", f"  {outside:,} row(s) outside the image bounds "
                          "— ignored"))
        q.put(("progress", index + 1, len(items)))
    q.put(("done", {"files": len(items), "dir": str(out_dir)}))


def _export_image_data(ctx, page, path, channels, geometry, q):
    """Read one image into a (C, H, W) float32 array, in row batches."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq

    xmin, ymin, width, height = geometry
    task_id = page._export_task_id
    stages = _data_columns._map_stages(ctx, task_id)
    parquet = pq.ParquetFile(path)
    schema_names = set(parquet.schema_arrow.names)
    if "x" not in schema_names or "y" not in schema_names:
        raise ValueError("image file has no x/y columns")

    needed = _pipeline_dependencies(stages, channels, path)
    base_cols = [
        name for name in needed
        if name in schema_names and name not in ("x", "y")
    ]
    virtual = [
        name for name in channels
        if _export_source_column(ctx, task_id, path, name)
        not in schema_names
    ]
    virtual_values = {}
    if virtual:
        virtual_values = _data_columns._column_data(ctx,
            task_id, path, virtual, row_filter=False
        )

    array = np.zeros((len(channels), height, width), dtype="float32")
    filled = np.zeros(height * width, dtype=np.uint8)
    duplicates = 0
    outside = 0
    missing_channels = []
    offset = 0
    read_columns = ["x", "y"] + base_cols
    for batch in parquet.iter_batches(
        batch_size=1_000_000, columns=read_columns
    ):
        if _export_cancelled(ctx, page, "image"):
            raise _ExportCancelled
        frame = batch.to_pandas()
        rows = len(frame)
        x = pd.to_numeric(frame["x"], errors="coerce").to_numpy(
            dtype="float64"
        )
        y = pd.to_numeric(frame["y"], errors="coerce").to_numpy(
            dtype="float64"
        )
        values = {}
        for name in base_cols:
            values[name] = pd.to_numeric(
                frame[name], errors="coerce"
            ).to_numpy(dtype="float64")
        if stages:
            channel_values = _evaluate_stages(
                stages, values, channels, parquet_path=path,
                cache=None, origin=(xmin, ymin),
            )
        else:
            channel_values = {
                name: values[name] for name in channels if name in values
            }
        for name in virtual:
            source = virtual_values.get(name)
            if source is not None and len(source) >= offset + rows:
                channel_values[name] = np.asarray(
                    source[offset:offset + rows], dtype="float64"
                )

        finite = np.isfinite(x) & np.isfinite(y)
        xi = np.where(finite, np.rint(x - xmin), -1).astype(np.int64)
        yi = np.where(finite, np.rint(y - ymin), -1).astype(np.int64)
        valid = (
            finite & (xi >= 0) & (xi < width)
            & (yi >= 0) & (yi < height)
        )
        outside += int(rows - int(valid.sum()))
        if valid.any():
            linear = yi[valid] * width + xi[valid]
            # Rows that rewrite a pixel already covered (in this batch or
            # an earlier one) are duplicates; the last value wins.
            unique = np.unique(linear)
            fresh = unique[filled[unique] == 0]
            duplicates += int(len(linear) - len(fresh))
            filled[unique] = 1
            for j, name in enumerate(channels):
                values_j = channel_values.get(name)
                if values_j is None or len(values_j) != rows:
                    if name not in missing_channels:
                        missing_channels.append(name)
                    continue
                array[j].reshape(-1)[linear] = np.asarray(
                    values_j[valid], dtype="float32"
                )
        offset += rows

    missing = int(height * width - int(filled.sum()))
    return array, missing, duplicates, outside, missing_channels


def _export_poll(ctx, page, kind: str) -> None:
    pane = _export_pane(ctx, page, kind)
    if pane.queue is None:
        return
    finished = None
    while True:
        try:
            message = pane.queue.get_nowait()
        except queue.Empty:
            break
        tag = message[0]
        if tag == "log":
            _export_log(ctx, page, kind, message[1])
        elif tag == "progress":
            _tag, done, total = message
            pane.progress.setRange(0, max(1, int(total)))
            pane.progress.setValue(int(done))
            pane.status.setText(
                f"Exporting… {done}/{total} file(s)"
            )
        elif tag == "conflict":
            for line in message[1]:
                _export_log(ctx, page, kind, "CONFLICT: " + line)
            _export_log(ctx,
                page, kind, "Export stopped — no files were written."
            )
            finished = ("conflict", list(message[1]))
        elif tag == "error":
            _export_log(ctx, page, kind, "ERROR: " + message[1])
            finished = ("error", message[1])
        elif tag == "cancelled":
            _export_log(ctx, page, kind, "Export cancelled.")
            finished = ("cancelled", "")
        elif tag == "done":
            info = message[1]
            if kind == "table":
                text = (
                    f"Done — {info['files']} file(s), "
                    f"{info['rows']:,} rows → {info['dir']}"
                )
            else:
                text = f"Done — {info['files']} file(s) → {info['dir']}"
            _export_log(ctx, page, kind, text)
            finished = ("done", info)
    if finished is not None:
        _export_finish(ctx, page, kind, finished)


def _export_finish(ctx, page, kind: str, finished) -> None:
    pane = _export_pane(ctx, page, kind)
    state, payload = finished
    pane.running = False
    pane.cancel = False
    if pane.timer is not None:
        pane.timer.stop()
        pane.timer = None
    pane.queue = None
    pane.progress.setVisible(False)
    _export_update_buttons(ctx, page, kind)
    if state == "done":
        pane.status.setText("Export finished")
        runlog.info(
            "export.done", module=str(page._export_task_id), tab=kind,
        )
    elif state == "cancelled":
        pane.status.setText("Export cancelled")
        runlog.info(
            "export.cancelled", module=str(page._export_task_id),
            tab=kind,
        )
    elif state == "conflict":
        pane.status.setText("Export stopped — output names conflict")
        preview = "\n".join(f"· {line}" for line in payload[:12])
        if len(payload) > 12:
            preview += "\n· …"
        QMessageBox.warning(
            ctx.host, "Export stopped",
            "These output names would overwrite existing files or "
            "collide:\n\n" + preview
            + "\n\nRename or remove them, then export again. "
              "No files were written.",
        )
    else:
        pane.status.setText("Export failed")
        QMessageBox.warning(ctx.host, "Export failed", str(payload))


def _export_save_now(ctx, page) -> None:
    if ctx.project is not None:
        ctx.project.save()
    page._export_snapshot = copy.deepcopy(page._export_params)
    page._export_dirty = False
    _export_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Export settings saved")
    ctx._module_saved(page)


def _export_close(ctx, page) -> None:
    if page._export_dirty:
        reply = QMessageBox.question(
            ctx.host, "Close Export",
            "There are unsaved changes.\n"
            "Close without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._export_task_def["params"] = copy.deepcopy(
            page._export_snapshot
        )
    _export_cleanup(ctx, page)
    ctx._close_module_page(page)


def _export_cleanup(ctx, page) -> None:
    for pane in page._export_panes.values():
        pane.cancel = True
        if pane.timer is not None:
            pane.timer.stop()
            pane.timer = None
        pane.queue = None
        pane.running = False
