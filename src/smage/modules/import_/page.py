"""Data Import module: read source files into the project."""
from __future__ import annotations


from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QScrollArea,
)


from PySide6.QtCore import (
    QTimer,
    Qt,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from collections import Counter
from pathlib import Path
from smage.data.image_index import _build_image_index_files
from smage.dimensions import (
    parameter_names,
    read_dimensions,
)
from smage.importer import import_file
from smage.runlog import runlog
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from smage.widgets.resources import _dot_pixmap
from uuid import uuid4
import queue
import threading
from functools import partial
from smage.data import files as _data_files
from smage.widgets import panels as _widgets_panels


IMAGE_RESIZE_MIN = 8
IMAGE_RESIZE_MAX = 32768
# Guard against an accidental upscale that would exhaust memory (pixels).
IMAGE_RESIZE_PIXEL_LIMIT = 64_000_000


def _import_locked(ctx) -> bool:
    """True if Data Import is currently locked (session unlocks apply)."""
    task = _data_files._import_task(ctx)
    return task is not None and ctx._task_locked(task)


def _load_import_files(ctx) -> None:
    """Load the Data Import file list and processed results from the project."""
    task = _data_files._import_task(ctx)
    if task is None:
        ctx._import_files = []
        ctx._processed_files = []
        return
    params = task.setdefault("params", {})
    files = params.get("files")
    if files is None:
        files = []
        params["files"] = files
    ctx._import_files = files
    for entry in ctx._import_files:
        entry.setdefault("id", uuid4().hex)
    processed = params.get("processed")
    if processed is None:
        processed = []
        params["processed"] = processed
    ctx._processed_files = processed


def _persist_import_files(ctx) -> None:
    """Save the project (the import file list lives in the import task's params)."""
    if ctx.project is not None:
        ctx.project.save()


def _build_data_import_page(ctx) -> QWidget:
    """Build the Data Import page: Input | Parameter | Processed."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    locked = _import_locked(ctx)
    page._import_locked_state = locked
    layout.addWidget(ctx._build_module_toolbar(
        "Data Import", page, include_save=False, locked=locked,
        include_settings=False, lock_text="Locked",
    ))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)
    middle_panel = _widgets_panels._build_middle_panel(ctx)
    input_panel = _build_input_panel(ctx)
    processed_panel = _build_processed_panel(ctx)
    cl.addWidget(input_panel, 1)
    cl.addWidget(middle_panel, 1)
    cl.addWidget(processed_panel, 1)
    layout.addWidget(content, 1)
    if locked:
        _set_import_controls_enabled(ctx, False)
    return page


def _build_sample_panel(ctx) -> QFrame:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Sample</b>"))

    desc = QLabel("Sampling settings for the selected file.")
    desc.setWordWrap(True)
    desc.setStyleSheet("color: #5a6673;")
    lay.addWidget(desc)
    ctx.sample_desc = desc

    row = QHBoxLayout()
    row.setSpacing(6)
    ctx.sample_combo = _NoWheelComboBox()
    ctx.sample_combo.addItem("Full sampling", "full")
    ctx.sample_combo.addItem("Partial random sampling", "partial")
    row.addWidget(ctx.sample_combo)

    ctx.sample_percent_spin = _NoWheelSpinBox()
    ctx.sample_percent_spin.setRange(1, 100)
    ctx.sample_percent_spin.setValue(10)
    ctx.sample_percent_spin.setSuffix(" %")
    ctx.sample_percent_spin.setVisible(False)
    ctx.sample_percent_spin.setMaximumWidth(80)
    row.addWidget(ctx.sample_percent_spin)

    row.addStretch()

    ctx.sample_apply_btn = QPushButton("Apply to")
    ctx.sample_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ctx.sample_apply_btn.clicked.connect(partial(_apply_sampling_to_files, ctx))
    row.addWidget(ctx.sample_apply_btn)
    lay.addLayout(row)

    lay.addStretch()

    ctx.sample_combo.currentIndexChanged.connect(partial(_on_sampling_changed, ctx))
    ctx.sample_percent_spin.valueChanged.connect(partial(_on_sampling_changed, ctx))
    return panel


def _build_processed_panel(ctx) -> QFrame:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Processed</b>"))
    ctx.processed_list = QListWidget()
    ctx.processed_list.setFrameShape(QFrame.Shape.NoFrame)
    lay.addWidget(ctx.processed_list)
    _refresh_processed_panel(ctx)
    return panel


def _refresh_processed_panel(ctx) -> None:
    ctx.processed_list.clear()
    for record in ctx._processed_files:
        name = record.get("name", "")
        rows = record.get("rows", "?")
        original = record.get("original_size")
        imported = record.get("imported_size")
        resized = bool(
            original and imported and list(original) != list(imported)
        )
        suffix = f"  ·  {imported[0]}×{imported[1]}" if imported else ""
        item = QListWidgetItem(f"{name}  —  {rows} rows{suffix}")
        tooltip = record.get("parquet", "")
        if resized:
            tooltip += (
                f"\nResized {original[0]}×{original[1]} → "
                f"{imported[0]}×{imported[1]}"
            )
        item.setToolTip(tooltip)
        ctx.processed_list.addItem(item)


def _set_import_controls_enabled(ctx, enabled: bool) -> None:
    """Enable/disable the Data Import controls (used when the task is locked)."""
    widgets = [
        ctx.select_btn, ctx.select_all_box, ctx.peek_btn, ctx.import_btn,
        ctx.delete_btn, ctx.copy_params_btn, ctx.paste_params_btn,
        ctx.apply_params_btn, ctx.sample_combo, ctx.sample_percent_spin,
        ctx.sample_apply_btn,
    ]
    widgets.extend(getattr(ctx, "_resize_controls", []) or [])
    for widget in widgets:
        if widget is not None:
            widget.setEnabled(enabled)


def _build_input_panel(ctx) -> QFrame:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)

    header = QHBoxLayout()
    header.addWidget(QLabel("<b>Input</b>"))
    header.addStretch()
    ctx.select_btn = QPushButton("Select Files…")
    ctx.select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ctx.select_btn.clicked.connect(partial(_select_import_files, ctx))
    header.addWidget(ctx.select_btn)
    lay.addLayout(header)

    ctx.select_all_box = QCheckBox("Select all")
    ctx.select_all_box.setTristate(True)
    lay.addWidget(ctx.select_all_box)

    ctx.import_list = QListWidget()
    ctx.import_list.setFrameShape(QFrame.Shape.NoFrame)
    lay.addWidget(ctx.import_list)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(6)
    ctx.peek_btn = QPushButton("Peek")
    ctx.peek_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ctx.peek_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    ctx.peek_btn.clicked.connect(partial(_peek_files, ctx))
    btn_row.addWidget(ctx.peek_btn, 1)
    ctx.import_btn = QPushButton("Import")
    ctx.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ctx.import_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    ctx.import_btn.clicked.connect(partial(_run_import, ctx))
    btn_row.addWidget(ctx.import_btn, 1)
    ctx.delete_btn = QPushButton("Delete")
    ctx.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ctx.delete_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    ctx.delete_btn.clicked.connect(partial(_delete_selected_files, ctx))
    btn_row.addWidget(ctx.delete_btn, 1)
    lay.addLayout(btn_row)

    # Reset checkboxes to unchecked each time the module opens, so an
    # accidental Peek doesn't overwrite carefully edited parameter names.
    for entry in ctx._import_files:
        entry["checked"] = False

    _refresh_import_list(ctx)

    ctx.select_all_box.stateChanged.connect(partial(_on_select_all_state_changed, ctx))
    ctx.import_list.itemChanged.connect(partial(_on_item_check_changed, ctx))
    ctx.import_list.itemSelectionChanged.connect(partial(_on_import_selection_changed, ctx))
    return panel


def _select_import_files(ctx) -> None:
    files, _ = QFileDialog.getOpenFileNames(
        ctx.host,
        "Select data files",
        "",
        "Data files (*.fcs *.csv *.tif *.tiff *.parquet);;All files (*)",
    )
    if not files:
        return
    for path in files:
        ctx._import_files.append(
            {
                "id": uuid4().hex,
                "path": path,
                "status": "pending",
                "checked": True,
                "dimensions": None,
                "sampling": "full",
                "sampling_percent": 10,
            }
        )
    _persist_import_files(ctx)
    _refresh_import_list(ctx)


def _refresh_import_list(ctx) -> None:
    ctx._updating_checks = True
    ctx.import_list.clear()
    name_counts = Counter(Path(e["path"]).name for e in ctx._import_files)
    for entry in ctx._import_files:
        path = entry["path"]
        status = entry.get("status", "pending")
        base = Path(path).name
        if name_counts[base] > 1:
            text = f"{base}  —  {Path(path).parent}"
        else:
            text = base

        item = QListWidgetItem(text)
        item.setToolTip(_status_tooltip(ctx, entry))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked
            if entry.get("checked", True)
            else Qt.CheckState.Unchecked
        )
        color = IMPORT_STATUS_COLORS.get(status, IMPORT_STATUS_COLORS["pending"])
        item.setIcon(QIcon(_dot_pixmap(color)))
        ctx.import_list.addItem(item)
    ctx._updating_checks = False
    _update_select_all_state(ctx)


def _peek_files(ctx) -> None:
    """Read dimension info for each checked file, with a progress bar."""
    checked = [
        i for i in range(ctx.import_list.count())
        if ctx.import_list.item(i).checkState() == Qt.CheckState.Checked
    ]
    if not checked:
        return

    reply = QMessageBox.question(
        ctx.host,
        "Peek",
        f"Peek will overwrite the parameter name settings of "
        f"{len(checked)} selected file(s). Continue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    progress = QProgressDialog(
        "Peeking files...", "Cancel", 0, len(checked), ctx.host
    )
    progress.setWindowTitle("Peek")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setValue(0)

    for step, i in enumerate(checked):
        if progress.wasCanceled():
            break
        entry = ctx._import_files[i]
        try:
            dims = read_dimensions(entry["path"])
            entry["dimensions"] = dims
            entry["parameters"] = [
                {"name": n, "extract": True, "rename": n}
                for n in parameter_names(dims)
            ]
            if dims.get("kind") == "image":
                # Sampling would punch holes in an image; resize replaces it.
                entry["sampling"] = "full"
                if not isinstance(entry.get("image_resize"), dict):
                    entry["image_resize"] = _image_resize_defaults(dims)
            entry["status"] = "peeked"
            entry["error"] = None
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
        progress.setValue(step + 1)
        QApplication.processEvents()

    _persist_import_files(ctx)
    _refresh_import_list(ctx)
    progress.close()


def _run_import(ctx) -> None:
    """Import each checked file in a background thread (the UI stays live)."""
    if ctx.project is None or getattr(ctx, "_import_running", False):
        return
    checked = [
        i for i in range(ctx.import_list.count())
        if ctx.import_list.item(i).checkState() == Qt.CheckState.Checked
    ]
    if not checked:
        return

    reply = QMessageBox.question(
        ctx.host,
        "Import",
        "Importing will overwrite already-imported files. "
        "Are you sure you want to import?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    reply = QMessageBox.question(
        ctx.host,
        "Import",
        f"You have checked {len(checked)} file(s). Are these the correct files?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    not_peeked = [
        Path(ctx._import_files[i]["path"]).name
        for i in checked
        if not ctx._import_files[i].get("parameters")
    ]
    if not_peeked:
        QMessageBox.warning(
            ctx.host,
            "Import",
            "The following files have not been peeked yet and need to be "
            "peeked first:\n" + "\n".join(not_peeked),
        )
        return

    for i in checked:
        entry = ctx._import_files[i]
        resize = _ensure_image_resize(entry)
        if resize is None or not resize.get("enabled"):
            continue
        pixels = int(resize["width"]) * int(resize["height"])
        if pixels > IMAGE_RESIZE_PIXEL_LIMIT:
            QMessageBox.warning(
                ctx.host,
                "Import",
                f"{Path(entry['path']).name}: the resize target has "
                f"{pixels / 1_000_000:.1f} megapixels, which is too large "
                "to import. Choose a smaller size.",
            )
            return

    # Snapshot everything the worker needs so the user editing the Input
    # list mid-import cannot change data underneath the thread.
    jobs = []
    for i in checked:
        entry = ctx._import_files[i]
        dimensions = entry.get("dimensions") or {}
        is_image = dimensions.get("kind") == "image"
        resize = _ensure_image_resize(entry) if is_image else None
        jobs.append({
            "index": i,
            "path": entry["path"],
            "id": entry.get("id"),
            "name": Path(entry["path"]).name,
            "parameters": list(entry.get("parameters") or []),
            # Images are never randomly sampled; they are resized instead.
            "sampling": "full" if is_image else entry.get("sampling", "full"),
            "sampling_percent": entry.get("sampling_percent", 10),
            "image_resize": dict(resize) if resize else None,
        })

    ctx._import_running = True
    ctx._import_cancel = False
    ctx._import_jobs = jobs
    import time as _time
    ctx._import_started = _time.perf_counter()
    runlog.action(
        "import.start", files=len(jobs),
        names=[job["name"] for job in jobs],
    )
    ctx.peek_btn.setEnabled(False)
    ctx.import_btn.setEnabled(False)

    progress = QProgressDialog(
        "Importing files…", "Cancel", 0, len(jobs), ctx.host
    )
    progress.setWindowTitle("Import")
    progress.setWindowModality(Qt.WindowModality.NonModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setValue(0)
    progress.canceled.connect(partial(_cancel_import, ctx))
    ctx._import_progress = progress

    q = queue.Queue()
    ctx._import_queue = q
    ctx._import_thread = threading.Thread(
        target=partial(_import_worker, ctx), args=(jobs, q), daemon=True
    )
    ctx._import_thread.start()

    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(partial(_import_poll, ctx))
    timer.start()
    ctx._import_timer = timer


def _cancel_import(ctx) -> None:
    """Cancel the running import (checked by the worker between files)."""
    ctx._import_cancel = True


def _import_worker(ctx, jobs, q) -> None:
    """Import the queued files one by one; results are posted to the queue."""
    total = len(jobs)
    for step, job in enumerate(jobs):
        if ctx._import_cancel:
            q.put(("cancelled", step, total))
            return
        name = job["name"]
        q.put(("status", step, name))
        try:
            out_path = ctx.project.data_dir / f"{job['id']}.parquet"
            result = import_file(
                job["path"],
                job["parameters"],
                job["sampling"],
                job["sampling_percent"],
                out_path,
                image_resize=job.get("image_resize"),
            )
            # Build the image LOD sample index now, so Figure views open
            # image files instantly (no first-open build).
            if Path(job["path"]).suffix.lower() in (".tif", ".tiff"):
                last = [-1]

                def _idx_progress(f, _last=last, _step=step, _name=name):
                    if ctx._import_cancel:
                        return
                    pct = int(round(f * 100))
                    if pct != _last[0]:
                        _last[0] = pct
                        q.put(("idx", _step, _name, f))

                try:
                    _build_image_index_files(str(out_path), _idx_progress)
                except Exception:
                    pass  # Figure will fall back to a lazy build
            q.put(("done", step, result, str(out_path)))
        except Exception as exc:
            q.put(("error", step, str(exc)))
    q.put(("finished",))


def _import_poll(ctx) -> None:
    """Apply worker results to the UI and the import state."""
    q = getattr(ctx, "_import_queue", None)
    progress = getattr(ctx, "_import_progress", None)
    if q is None or progress is None:
        return
    finished = False
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "status":
            _step, name = msg[1], msg[2]
            progress.setLabelText(f"Importing {name}…")
        elif kind == "idx":
            _step, name, frac = msg[1], msg[2], msg[3]
            progress.setLabelText(
                f"Importing {name}: building image index… {int(round(frac * 100))}%"
            )
        elif kind == "done":
            step, result, out_path = msg[1], msg[2], msg[3]
            entry = ctx._import_files[ctx._import_jobs[step]["index"]]
            entry["status"] = "processed"
            entry["error"] = None
            entry["imported"] = {"parquet": out_path, "rows": result["rows"]}
            record = {
                "name": Path(entry["path"]).name,
                "source": entry["path"],
                "parquet": out_path,
                "rows": result["rows"],
            }
            for key in ("original_size", "imported_size"):
                if result.get(key):
                    entry["imported"][key] = list(result[key])
                    record[key] = list(result[key])
            ctx._column_index[_data_files._rel_data_key(ctx, out_path)] = result["columns"]
            ctx.project.save_columns(ctx._column_index)
            ctx._processed_files[:] = [
                r for r in ctx._processed_files if r.get("source") != entry["path"]
            ]
            ctx._processed_files.append(record)
            progress.setValue(step + 1)
        elif kind == "error":
            step, err = msg[1], msg[2]
            entry = ctx._import_files[ctx._import_jobs[step]["index"]]
            entry["status"] = "error"
            entry["error"] = err
            entry["imported"] = None
            progress.setValue(step + 1)
        elif kind == "cancelled":
            progress.setLabelText("Import cancelled.")
            finished = True
        elif kind == "finished":
            finished = True
    if not finished:
        return
    _import_finish(ctx)


def _import_finish(ctx) -> None:
    """Stop the import worker and refresh the Input / Processed panels."""
    timer = getattr(ctx, "_import_timer", None)
    if timer is not None:
        timer.stop()
        ctx._import_timer = None
    ctx._import_queue = None
    ctx._import_thread = None
    jobs = ctx._import_jobs or []
    ctx._import_jobs = None
    ctx._import_running = False
    if jobs:
        import time as _time
        failed = sum(
            1 for job in jobs
            if (ctx._import_files[job["index"]].get("status") == "error")
        )
        runlog.info(
            "import.finish", files=len(jobs), errors=failed,
            seconds=round(_time.perf_counter()
                          - getattr(ctx, "_import_started",
                                    _time.perf_counter()), 3),
        )
    progress = getattr(ctx, "_import_progress", None)
    if progress is not None:
        progress.close()
        ctx._import_progress = None
    ctx.peek_btn.setEnabled(True)
    ctx.import_btn.setEnabled(True)
    _persist_import_files(ctx)
    _refresh_import_list(ctx)
    _refresh_processed_panel(ctx)


IMPORT_STATUS_COLORS = {
    "pending": "#9aa0a6",    # just selected (default)
    "peeked": "#4285f4",     # dimensions read (Peek)
    "processed": "#34a853",  # imported / converted
    "error": "#e53935",      # Peek or import failed
}


def _build_parameter_panel(ctx) -> QFrame:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Parameter</b>"))

    ctx.parameter_summary = QLabel("Select a file to see its parameters.")
    ctx.parameter_summary.setWordWrap(True)
    ctx.parameter_summary.setStyleSheet("color: #5a6673;")
    lay.addWidget(ctx.parameter_summary)

    button_row = QHBoxLayout()
    button_row.setSpacing(6)
    ctx.copy_params_btn = QPushButton("Copy")
    ctx.paste_params_btn = QPushButton("Paste")
    ctx.apply_params_btn = QPushButton("Apply to")
    for b in (ctx.copy_params_btn, ctx.paste_params_btn, ctx.apply_params_btn):
        b.setCursor(Qt.CursorShape.PointingHandCursor)
    ctx.copy_params_btn.clicked.connect(partial(_copy_parameters, ctx))
    ctx.paste_params_btn.clicked.connect(partial(_paste_parameters, ctx))
    ctx.apply_params_btn.clicked.connect(partial(_apply_to_files, ctx))
    button_row.addWidget(ctx.copy_params_btn)
    button_row.addWidget(ctx.paste_params_btn)
    button_row.addWidget(ctx.apply_params_btn)
    button_row.addStretch()
    lay.addLayout(button_row)

    ctx.parameter_scroll = QScrollArea()
    ctx.parameter_scroll.setWidgetResizable(True)
    ctx.parameter_scroll.setFrameShape(QFrame.Shape.NoFrame)
    ctx.parameter_scroll.setStyleSheet("background: transparent;")

    ctx.parameter_rows = QWidget()
    ctx.parameter_rows.setStyleSheet("background: transparent;")
    ctx.parameter_rows_layout = QVBoxLayout(ctx.parameter_rows)
    ctx.parameter_rows_layout.setContentsMargins(0, 0, 0, 0)
    ctx.parameter_rows_layout.setSpacing(2)
    ctx.parameter_scroll.setWidget(ctx.parameter_rows)
    lay.addWidget(ctx.parameter_scroll, 1)
    return panel


def _delete_selected_files(ctx) -> None:
    """Remove the checked files from Input and their imported files from Processed."""
    checked = [
        i for i in range(ctx.import_list.count())
        if ctx.import_list.item(i).checkState() == Qt.CheckState.Checked
    ]
    if not checked:
        return

    reply = QMessageBox.question(
        ctx.host,
        "Delete",
        f"Delete {len(checked)} file(s)?\n"
        "This will also delete their corresponding imported files (in Processed).",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    for i in sorted(checked, reverse=True):
        source = ctx._import_files[i]["path"]
        for record in list(ctx._processed_files):
            if record.get("source") == source:
                parquet = record.get("parquet")
                if parquet:
                    try:
                        Path(parquet).unlink(missing_ok=True)
                    except OSError:
                        pass
                    ctx._column_index.pop(_data_files._rel_data_key(ctx, parquet), None)
        ctx._processed_files[:] = [
            r for r in ctx._processed_files if r.get("source") != source
        ]
        del ctx._import_files[i]

    ctx.project.save_columns(ctx._column_index)
    _persist_import_files(ctx)
    _refresh_import_list(ctx)
    _refresh_processed_panel(ctx)
    ctx.statusBar().showMessage(f"Deleted {len(checked)} file(s)")


def _status_tooltip(ctx, entry) -> str:
    """Tooltip describing the status dot (and the error, if any)."""
    status = entry.get("status", "pending")
    tip = STATUS_DESCRIPTIONS.get(status, status)
    if status == "error":
        error = entry.get("error", "")
        if error:
            tip += f"\n{error}"
    return tip


def _on_select_all_state_changed(ctx, state: int) -> None:
    if ctx._updating_checks:
        return
    if state == Qt.CheckState.PartiallyChecked.value:
        return
    check_state = (
        Qt.CheckState.Checked
        if state == Qt.CheckState.Checked.value
        else Qt.CheckState.Unchecked
    )
    ctx._updating_checks = True
    for i in range(ctx.import_list.count()):
        ctx.import_list.item(i).setCheckState(check_state)
    ctx._updating_checks = False


def _on_item_check_changed(ctx, item) -> None:
    if ctx._updating_checks:
        return
    index = ctx.import_list.row(item)
    if 0 <= index < len(ctx._import_files):
        checked = item.checkState() == Qt.CheckState.Checked
        if ctx._import_files[index].get("checked", True) != checked:
            ctx._import_files[index]["checked"] = checked
            _persist_import_files(ctx)
    _update_select_all_state(ctx)


def _update_select_all_state(ctx) -> None:
    total = ctx.import_list.count()
    checked = sum(
        1
        for i in range(total)
        if ctx.import_list.item(i).checkState() == Qt.CheckState.Checked
    )
    if total == 0 or checked == 0:
        state = Qt.CheckState.Unchecked
    elif checked == total:
        state = Qt.CheckState.Checked
    else:
        state = Qt.CheckState.PartiallyChecked
    ctx._updating_checks = True
    ctx.select_all_box.setCheckState(state)
    ctx._updating_checks = False


def _on_import_selection_changed(ctx) -> None:
    item = ctx.import_list.currentItem()
    if item is None:
        _show_file_parameters(ctx, None)
        return
    index = ctx.import_list.row(item)
    if 0 <= index < len(ctx._import_files):
        _show_file_parameters(ctx, ctx._import_files[index])
    else:
        _show_file_parameters(ctx, None)


def _show_file_parameters(ctx, entry) -> None:
    ctx._current_entry = entry
    _show_file_sampling(ctx, entry)
    _clear_parameter_rows(ctx)
    if entry is None:
        ctx.parameter_summary.setText("Select a file to see its parameters.")
        ctx.parameter_rows_layout.addStretch()
        return

    dims = entry.get("dimensions")
    base = Path(entry["path"]).name
    if dims is None:
        if entry.get("status") == "error":
            ctx.parameter_summary.setText(f"{base}\nStatus: error (Peek failed)")
        else:
            ctx.parameter_summary.setText(
                f"{base}\nStatus: not peeked yet — click Peek first"
            )
        ctx.parameter_rows_layout.addStretch()
        return

    kind = dims.get("kind")
    if kind == "fcs":
        info = (
            f"FCS  ·  {dims.get('rows')} events  ·  "
            f"{len(dims.get('columns', []))} channels"
        )
    elif kind == "image":
        width, height = _image_dimensions(dims)
        info = (
            f"Image  ·  {width} × {height} px  ·  "
            f"{dims.get('channels')} channels"
        )
    else:
        info = (
            f"Table  ·  {dims.get('rows')} rows  ·  "
            f"{len(dims.get('columns', []))} columns"
        )
    ctx.parameter_summary.setText(f"{base}\n{info}")
    locked = _import_locked(ctx)

    if kind == "image":
        _build_image_resize_section(ctx, entry, dims, locked)

    params = entry.get("parameters")
    if params is None:
        params = [
            {"name": n, "extract": True, "rename": n}
            for n in parameter_names(dims)
        ]
        entry["parameters"] = params
        _persist_import_files(ctx)

    # Column headers
    header = QWidget()
    hh = QHBoxLayout(header)
    hh.setContentsMargins(0, 2, 0, 4)
    hh.setSpacing(6)
    header_style = "font-weight: bold; color: #5a6673;"
    h_orig = QLabel("Original name")
    h_orig.setMinimumWidth(PARAM_ORIG_WIDTH)
    h_orig.setStyleSheet(header_style)
    hh.addWidget(h_orig)
    h_inc = QLabel("Extract")
    h_inc.setStyleSheet(header_style)
    hh.addWidget(h_inc)
    h_new = QLabel("Imported name")
    h_new.setStyleSheet(header_style)
    hh.addWidget(h_new, 1)
    ctx.parameter_rows_layout.addWidget(header)

    for param in params:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(6)

        orig_label = QLabel(param.get("name", ""))
        orig_label.setMinimumWidth(PARAM_ORIG_WIDTH)
        orig_label.setStyleSheet("color: #2c3a48;")
        h.addWidget(orig_label)

        check = QCheckBox()
        check.setChecked(param.get("extract", True))
        check.setToolTip("Extract this parameter")
        check.setEnabled(not locked)
        h.addWidget(check)

        line_edit = QLineEdit(param.get("rename", param.get("name", "")))
        line_edit.setPlaceholderText("parameter name")
        line_edit.setEnabled((not locked) and param.get("extract", True))
        line_edit.setToolTip("Rename this parameter (used after import)")
        h.addWidget(line_edit, 1)

        check.toggled.connect(
            lambda checked, pp=param, ll=line_edit:
                _on_param_extract_toggled(ctx, pp, ll, checked)
        )
        line_edit.editingFinished.connect(
            lambda pp=param, ll=line_edit: _on_param_renamed(ctx, pp, ll)
        )

        ctx.parameter_rows_layout.addWidget(row)

    ctx.parameter_rows_layout.addStretch()


def _image_dimensions(dims) -> tuple[int, int]:
    """Pixel (width, height) of an image dimensions dict."""
    dims = dims or {}
    shape = list(dims.get("shape") or [])
    height = int(
        dims.get("height") or (shape[-2] if len(shape) >= 2 else 0)
    )
    width = int(dims.get("width") or (shape[-1] if shape else 0))
    return width, height


def _image_resize_defaults(dims) -> dict:
    width, height = _image_dimensions(dims)
    return {
        "enabled": False,
        "width": width or IMAGE_RESIZE_MIN,
        "height": height or IMAGE_RESIZE_MIN,
        "keep_aspect": True,
        "method": "smooth",
    }


def _clamp_resize_size(value, original) -> int:
    upper = max(IMAGE_RESIZE_MAX, int(original or 0))
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = int(original or IMAGE_RESIZE_MIN)
    return max(IMAGE_RESIZE_MIN, min(value, upper))


def _ensure_image_resize(entry):
    """Return the mutable resize settings for an image entry, or None."""
    dims = entry.get("dimensions") or {}
    if dims.get("kind") != "image":
        return None
    defaults = _image_resize_defaults(dims)
    resize = entry.get("image_resize")
    if not isinstance(resize, dict):
        resize = defaults
        entry["image_resize"] = resize
    else:
        for key, value in defaults.items():
            resize.setdefault(key, value)
    resize["width"] = _clamp_resize_size(resize.get("width"), defaults["width"])
    resize["height"] = _clamp_resize_size(
        resize.get("height"), defaults["height"]
    )
    resize["enabled"] = bool(resize.get("enabled"))
    resize["keep_aspect"] = bool(resize.get("keep_aspect", True))
    if resize.get("method") not in ("smooth", "nearest"):
        resize["method"] = "smooth"
    return resize


def _build_image_resize_section(ctx, entry, dims, locked: bool) -> None:
    """Resize controls shown above the channel table for image files."""
    resize = _ensure_image_resize(entry)
    if resize is None:
        return
    original_w, original_h = _image_dimensions(dims)
    max_w = max(IMAGE_RESIZE_MAX, original_w)
    max_h = max(IMAGE_RESIZE_MAX, original_h)
    state = {"updating": True}

    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 10, 0, 4)
    lay.setSpacing(4)
    lay.addWidget(QLabel("<b>Image resize</b>"))

    enabled = QCheckBox("Resize on import")
    enabled.setToolTip(
        "Resize the image while importing. The original file is not changed."
    )
    enabled.setChecked(bool(resize.get("enabled")))
    enabled.setEnabled(not locked)
    lay.addWidget(enabled)

    size_grid = QGridLayout()
    size_grid.setHorizontalSpacing(6)
    size_grid.setVerticalSpacing(4)
    pixels_header = QLabel("Pixels")
    pixels_header.setStyleSheet("color: #6b7785;")
    ratio_header = QLabel("Ratio")
    ratio_header.setStyleSheet("color: #6b7785;")
    pixels_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
    ratio_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
    size_grid.addWidget(pixels_header, 0, 1)
    size_grid.addWidget(ratio_header, 0, 2)

    size_grid.addWidget(QLabel("Width"), 1, 0)
    width_spin = _NoWheelSpinBox()
    width_spin.setRange(IMAGE_RESIZE_MIN, max_w)
    width_spin.setSuffix(" px")
    width_spin.setMaximumWidth(90)
    size_grid.addWidget(width_spin, 1, 1)
    width_ratio = _NoWheelDoubleSpinBox()
    width_ratio.setDecimals(2)
    width_ratio.setRange(0.01, 10.0)
    width_ratio.setSingleStep(0.01)
    # Commit on Enter/focus-out: normalising the linked pixel mid-keystroke
    # would overwrite what the user is typing.
    width_ratio.setKeyboardTracking(False)
    width_ratio.setSuffix("×")
    width_ratio.setMaximumWidth(100)
    width_ratio.setToolTip(
        "Resize ratio for width: target pixels ÷ original pixels, rounded "
        "to two decimals. The pixel value is what gets imported."
    )
    size_grid.addWidget(width_ratio, 1, 2)

    size_grid.addWidget(QLabel("Height"), 2, 0)
    height_spin = _NoWheelSpinBox()
    height_spin.setRange(IMAGE_RESIZE_MIN, max_h)
    height_spin.setSuffix(" px")
    height_spin.setMaximumWidth(90)
    size_grid.addWidget(height_spin, 2, 1)
    height_ratio = _NoWheelDoubleSpinBox()
    height_ratio.setDecimals(2)
    height_ratio.setRange(0.01, 10.0)
    height_ratio.setSingleStep(0.01)
    height_ratio.setKeyboardTracking(False)
    height_ratio.setSuffix("×")
    height_ratio.setMaximumWidth(100)
    height_ratio.setToolTip(
        "Resize ratio for height: target pixels ÷ original pixels, rounded "
        "to two decimals. The pixel value is what gets imported."
    )
    size_grid.addWidget(height_ratio, 2, 2)
    size_grid.setColumnStretch(3, 1)
    lay.addLayout(size_grid)

    keep = QCheckBox("Keep aspect ratio (Y follows X)")
    keep.setToolTip(
        "When checked, Height is computed from Width so the original "
        "proportions stay the same."
    )
    keep.setChecked(bool(resize.get("keep_aspect", True)))
    lay.addWidget(keep)

    method_row = QHBoxLayout()
    method_row.setSpacing(6)
    method_row.addWidget(QLabel("Method"))
    method = _NoWheelComboBox()
    method.addItem("Smooth (anti-aliased)", "smooth")
    method.addItem("Nearest neighbor", "nearest")
    method.setCurrentIndex(
        max(0, method.findData(resize.get("method", "smooth")))
    )
    method_row.addWidget(method, 1)
    lay.addLayout(method_row)

    info = QLabel()
    info.setStyleSheet("color: #5a6673;")
    info.setWordWrap(True)
    lay.addWidget(info)

    def height_for_width() -> int:
        if original_w > 0:
            value = int(
                round(width_spin.value() * original_h / float(original_w))
            )
        else:
            value = height_spin.value()
        return max(IMAGE_RESIZE_MIN, min(max_h, value))

    def clamp_width(value) -> int:
        return max(IMAGE_RESIZE_MIN, min(max_w, int(round(value))))

    def clamp_height(value) -> int:
        return max(IMAGE_RESIZE_MIN, min(max_h, int(round(value))))

    def ratio_for(value, original) -> float:
        """Target / original, rounded to the ratio field's two decimals."""
        if original <= 0:
            return 0.01
        return max(0.01, min(10.0, round(value / float(original), 2)))

    def pixel_for_width(ratio) -> int:
        if original_w <= 0:
            return width_spin.value()
        return clamp_width(float(ratio) * original_w)

    def pixel_for_height(ratio) -> int:
        if original_h <= 0:
            return height_spin.value()
        return clamp_height(float(ratio) * original_h)

    def set_ratio(spin, value) -> None:
        spin.blockSignals(True)
        spin.setValue(float(value))
        spin.blockSignals(False)

    width_spin.setValue(
        int(resize.get("width") or original_w or IMAGE_RESIZE_MIN)
    )
    height_spin.setValue(
        int(resize.get("height") or original_h or IMAGE_RESIZE_MIN)
    )
    state["updating"] = False
    # Keep the stored height in step with the aspect lock after an edit or
    # after the source image changed size.
    if keep.isChecked():
        computed = height_for_width()
        if computed != height_spin.value():
            state["updating"] = True
            height_spin.setValue(computed)
            state["updating"] = False
            resize["height"] = int(computed)
            _persist_import_files(ctx)
    set_ratio(width_ratio, ratio_for(width_spin.value(), original_w))
    set_ratio(height_ratio, ratio_for(height_spin.value(), original_h))

    def update_enabled() -> None:
        on = enabled.isChecked() and not locked
        width_spin.setEnabled(on)
        width_ratio.setEnabled(on and original_w > 0)
        keep.setEnabled(on)
        method.setEnabled(on)
        height_on = on and not keep.isChecked()
        height_spin.setEnabled(height_on)
        height_ratio.setEnabled(height_on and original_h > 0)

    def update_info() -> None:
        if enabled.isChecked():
            info.setText(
                f"Original {original_w} × {original_h}   →   "
                f"Imported {width_spin.value()} × {height_spin.value()}"
            )
        else:
            info.setText(
                f"Original {original_w} × {original_h}   ·   no resize"
            )

    def store() -> None:
        if state["updating"]:
            return
        resize["enabled"] = bool(enabled.isChecked())
        resize["width"] = int(width_spin.value())
        resize["height"] = int(height_spin.value())
        resize["keep_aspect"] = bool(keep.isChecked())
        resize["method"] = method.currentData() or "smooth"
        _persist_import_files(ctx)

    def on_width_changed(*_args, ratio=None) -> None:
        if state["updating"]:
            return
        state["updating"] = True
        try:
            if ratio is not None:
                target = pixel_for_width(ratio)
                if width_spin.value() != target:
                    width_spin.setValue(target)
            set_ratio(width_ratio, ratio_for(width_spin.value(), original_w))
            if keep.isChecked():
                target_h = height_for_width()
                if height_spin.value() != target_h:
                    height_spin.setValue(target_h)
            set_ratio(
                height_ratio, ratio_for(height_spin.value(), original_h)
            )
        finally:
            state["updating"] = False
        update_info()
        store()

    def on_height_changed(*_args, ratio=None) -> None:
        if state["updating"] or keep.isChecked():
            return
        state["updating"] = True
        try:
            if ratio is not None:
                target = pixel_for_height(ratio)
                if height_spin.value() != target:
                    height_spin.setValue(target)
            set_ratio(
                height_ratio, ratio_for(height_spin.value(), original_h)
            )
        finally:
            state["updating"] = False
        update_info()
        store()

    def on_keep_toggled(*_args) -> None:
        update_enabled()
        on_width_changed()

    def on_toggle(*_args) -> None:
        update_enabled()
        update_info()
        store()

    enabled.toggled.connect(on_toggle)
    keep.toggled.connect(on_keep_toggled)
    width_spin.valueChanged.connect(lambda _=0: on_width_changed())
    width_ratio.valueChanged.connect(
        lambda value=0.0: on_width_changed(ratio=float(value))
    )
    height_spin.valueChanged.connect(lambda _=0: on_height_changed())
    height_ratio.valueChanged.connect(
        lambda value=0.0: on_height_changed(ratio=float(value))
    )
    method.currentIndexChanged.connect(lambda _=0: store())

    update_enabled()
    update_info()

    ctx._resize_enabled = enabled
    ctx._resize_width = width_spin
    ctx._resize_height = height_spin
    ctx._resize_width_ratio = width_ratio
    ctx._resize_height_ratio = height_ratio
    ctx._resize_keep = keep
    ctx._resize_method = method
    ctx._resize_controls = [
        enabled, width_spin, height_spin, width_ratio, height_ratio,
        keep, method,
    ]
    ctx.parameter_rows_layout.addWidget(box)


def _clear_parameter_rows(ctx) -> None:
    ctx._resize_controls = []
    for name in (
        "_resize_enabled", "_resize_width", "_resize_height",
        "_resize_width_ratio", "_resize_height_ratio",
        "_resize_keep", "_resize_method",
    ):
        setattr(ctx, name, None)
    while ctx.parameter_rows_layout.count():
        item = ctx.parameter_rows_layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()  # avoid a visible top-level window while detaching
            widget.setParent(None)
            widget.deleteLater()


def _on_param_extract_toggled(ctx, param, line_edit, checked) -> None:
    param["extract"] = checked
    line_edit.setEnabled(checked)
    _persist_import_files(ctx)


def _on_param_renamed(ctx, param, line_edit) -> None:
    param["rename"] = line_edit.text().strip() or param.get("name", "")
    _persist_import_files(ctx)


def _copy_parameters(ctx) -> None:
    _flash_button(ctx, ctx.copy_params_btn)
    entry = ctx._current_entry
    if entry is None or not entry.get("parameters"):
        QMessageBox.information(ctx, "Copy", "No parameters to copy. Peek a file first.")
        return
    lines = ["Original name\tExtract\tImported name"]
    for p in entry["parameters"]:
        extract = "True" if p.get("extract", True) else "False"
        lines.append(f"{p.get('name', '')}\t{extract}\t{p.get('rename', '')}")
    QApplication.clipboard().setText("\n".join(lines))
    ctx.statusBar().showMessage("Parameters copied to clipboard")


def _paste_parameters(ctx) -> None:
    _flash_button(ctx, ctx.paste_params_btn)
    entry = ctx._current_entry
    if entry is None or not entry.get("parameters"):
        QMessageBox.information(ctx, "Paste", "No parameters to paste into. Peek a file first.")
        return

    text = QApplication.clipboard().text().strip()
    if not text:
        QMessageBox.warning(ctx, "Paste", "Clipboard is empty.")
        return

    rows = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            QMessageBox.warning(
                ctx.host,
                "Paste",
                f"Format error on line {line_number}: expected 3 tab-separated "
                f"columns (Original name, Extract, Imported name), got {len(fields)}.",
            )
            return
        rows.append(fields)

    # Skip the header row if present (e.g. from a previous Copy).
    if (
        rows
        and rows[0][0].strip().lower() == "original name"
        and rows[0][1].strip().lower() in ("extract", "include")
    ):
        rows = rows[1:]

    params = entry["parameters"]
    if len(rows) != len(params):
        QMessageBox.warning(
            ctx.host,
            "Paste",
            f"Count mismatch: this file has {len(params)} parameters, "
            f"but you pasted {len(rows)} rows.",
        )
        return

    by_name = {r[0].strip(): r for r in rows}
    if len(by_name) != len(rows):
        QMessageBox.warning(ctx, "Paste", "Duplicate original names in pasted data.")
        return

    pasted_names = set(by_name)
    param_names = {p.get("name", "") for p in params}
    if pasted_names != param_names:
        missing = sorted(param_names - pasted_names)
        extra = sorted(pasted_names - param_names)
        message = "Original names do not match."
        if missing:
            message += f"\nMissing from paste: {', '.join(missing)}"
        if extra:
            message += f"\nUnexpected in paste: {', '.join(extra)}"
        QMessageBox.warning(ctx, "Paste", message)
        return

    for p in params:
        name = p.get("name", "")
        extract_text = by_name[name][1].strip()
        rename = by_name[name][2].strip()
        p["extract"] = extract_text.lower() in ("true", "1", "yes", "y", "t")
        p["rename"] = rename or name

    _persist_import_files(ctx)
    _show_file_parameters(ctx, entry)
    ctx.statusBar().showMessage("Parameters pasted")


def _flash_button(ctx, button) -> None:
    """Briefly highlight a button so the user sees the click registered."""
    if button is None:
        return
    button.setStyleSheet(
        "QPushButton { background: #dbeafa; border: 1px solid #5aa0e0; color: #1d4f7a; }"
    )
    QTimer.singleShot(250, lambda: button.setStyleSheet(""))


def _apply_to_files(ctx) -> None:
    _flash_button(ctx, ctx.apply_params_btn)
    entry = ctx._current_entry
    if entry is None or not entry.get("parameters"):
        QMessageBox.information(ctx, "Apply to", "No parameters to apply. Peek a file first.")
        return

    candidates = _find_same_parameter_files(ctx, entry)
    if not candidates:
        QMessageBox.information(
            ctx.host, "Apply to", "No other files have the same parameter names."
        )
        return

    selected = _show_apply_dialog(ctx, candidates)
    if not selected:
        return

    _apply_parameter_settings(ctx, entry, selected)
    _persist_import_files(ctx)
    ctx.statusBar().showMessage(f"Applied to {len(selected)} file(s)")


def _find_same_parameter_files(ctx, entry):
    """Return indices of files whose parameter names match the given entry's."""
    current_names = {p["name"] for p in entry["parameters"]}
    candidates = []
    for i, other in enumerate(ctx._import_files):
        if other is entry:
            continue
        params = other.get("parameters")
        if params and {p["name"] for p in params} == current_names:
            candidates.append(i)
    return candidates


def _show_apply_dialog(ctx, candidates):
    """Show a dialog to choose which files to apply the settings to."""
    dialog = QDialog(ctx.host)
    dialog.setWindowTitle("Apply to")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("Apply current parameter settings to:"))

    file_list = QListWidget()
    for idx in candidates:
        name = Path(ctx._import_files[idx]["path"]).name
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, idx)
        file_list.addItem(item)
    layout.addWidget(file_list)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    confirm_btn = QPushButton("Confirm")
    cancel_btn = QPushButton("Cancel")
    confirm_btn.clicked.connect(dialog.accept)
    cancel_btn.clicked.connect(dialog.reject)
    btn_row.addWidget(confirm_btn)
    btn_row.addWidget(cancel_btn)
    layout.addLayout(btn_row)

    dialog.exec()
    if dialog.result() != QDialog.DialogCode.Accepted.value:
        return None

    selected = []
    for i in range(file_list.count()):
        item = file_list.item(i)
        if item.checkState() == Qt.CheckState.Checked:
            selected.append(item.data(Qt.ItemDataRole.UserRole))
    return selected


def _apply_parameter_settings(ctx, entry, indices) -> None:
    """Copy the entry's extract/rename settings to the given files by name."""
    settings = {
        p["name"]: (p.get("extract", True), p.get("rename", p.get("name", "")))
        for p in entry["parameters"]
    }
    for idx in indices:
        other = ctx._import_files[idx]
        for p in other["parameters"]:
            name = p["name"]
            if name in settings:
                extract, rename = settings[name]
                p["extract"] = extract
                p["rename"] = rename


def _show_file_sampling(ctx, entry) -> None:
    ctx._updating_sample = True
    dimensions = (entry or {}).get("dimensions") or {}
    is_image = dimensions.get("kind") == "image"
    if entry is None:
        sampling = "full"
        ctx.sample_combo.setCurrentIndex(0)
        ctx.sample_percent_spin.setVisible(False)
    else:
        # Images are never randomly sampled: random pixel rows break the
        # image. They are reduced with the resize controls instead.
        sampling = "full" if is_image else entry.get("sampling", "full")
        index = ctx.sample_combo.findData(sampling)
        if index < 0:
            index = 0
        ctx.sample_combo.setCurrentIndex(index)
        ctx.sample_percent_spin.setValue(int(entry.get("sampling_percent", 10)))
        ctx.sample_percent_spin.setVisible(sampling == "partial")
    ctx._updating_sample = False
    if is_image:
        ctx.sample_desc.setText(
            "Images are reduced with Resize, not by sampling."
        )
    else:
        ctx.sample_desc.setText("Sampling settings for the selected file.")
    enabled = (not _import_locked(ctx)) and not is_image
    ctx.sample_combo.setEnabled(enabled)
    ctx.sample_percent_spin.setEnabled(enabled)
    ctx.sample_apply_btn.setEnabled(enabled)


def _on_sampling_changed(ctx) -> None:
    if ctx._updating_sample:
        return
    entry = ctx._current_entry
    if entry is None:
        return
    sampling = ctx.sample_combo.currentData()
    entry["sampling"] = sampling
    entry["sampling_percent"] = ctx.sample_percent_spin.value()
    ctx.sample_percent_spin.setVisible(sampling == "partial")
    _persist_import_files(ctx)


def _apply_sampling_to_files(ctx) -> None:
    _flash_button(ctx, ctx.sample_apply_btn)
    entry = ctx._current_entry
    if entry is None:
        QMessageBox.information(ctx, "Apply to", "No file selected.")
        return
    if (entry.get("dimensions") or {}).get("kind") == "image":
        QMessageBox.information(
            ctx, "Apply to",
            "Image files are reduced with Resize, not by sampling.",
        )
        return

    sampling = entry.get("sampling", "full")
    percent = int(entry.get("sampling_percent", 10))

    candidates = [
        i for i, other in enumerate(ctx._import_files)
        if other is not entry
        and (other.get("dimensions") or {}).get("kind") != "image"
    ]
    if not candidates:
        QMessageBox.information(ctx, "Apply to", "No other files to apply to.")
        return

    selected = _show_apply_dialog(ctx, candidates)
    if not selected:
        return

    for idx in selected:
        ctx._import_files[idx]["sampling"] = sampling
        ctx._import_files[idx]["sampling_percent"] = percent

    _persist_import_files(ctx)
    ctx.statusBar().showMessage(f"Applied sampling to {len(selected)} file(s)")


STATUS_DESCRIPTIONS = {
    "pending": "Not peeked yet",
    "peeked": "Peeked — dimensions read",
    "processed": "Imported successfully",
    "error": "Error",
}


PARAM_ORIG_WIDTH = 150

