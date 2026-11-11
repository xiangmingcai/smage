"""Feature Down module: aggregate per-class features and ignore background values."""
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
from smage.core.feature import (
    _feature_aggregate_frame,
    _feature_count_name,
    _feature_safe_stem,
)
from smage.core.formatting import _json_stable
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelSpinBox,
)
from smage.widgets.resources import _lock_pixmap
import copy
import hashlib
import json
import math
import queue
import threading
from functools import partial
from smage.data import columns as _data_columns
from smage.data import feature_outputs as _data_feature_outputs
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.data import outputs as _data_outputs
from smage.data import sampling as _data_sampling
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


_FEAT_IGNORE_CACHE_NAME = "feature_values.json"

_FEAT_IGNORE_CACHE_LIMIT = 256

_FEAT_IGNORE_LEADERS = 64

def _build_feature_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Feature page: files (1) | aggregation rules (3) | log."""
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
    close_btn.clicked.connect(lambda _=False: _feat_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("feature", None)
    params.setdefault("files", [])
    params.setdefault("rules", [])
    params.setdefault("outputs", {})
    params.setdefault("log", [])
    # Feature values left out before aggregation (None = not chosen yet;
    # the most frequent value is suggested once the values are known).
    params.setdefault("ignore_values", None)
    # The first rule is always Count; it cannot be deleted.
    rules = params.get("rules") or []
    if not rules or (rules[0].get("method") != "count"):
        rules = [{"source": None, "method": "count", "lo": 0, "hi": 100,
                  "perc": None, "name": "count"}] + [
            r for r in rules if r.get("method") != "count"
        ]
        params["rules"] = rules
    page._feat_task_def = task_def
    page._feat_task_id = task_def.get("id")
    page._feat_params = params
    # The feature column is an output column with its own (deletable) row.
    _feat_sync_feature_rule(ctx, page)
    page._feat_locked = locked
    page._feat_save_btn = save_btn
    page._feat_dirty = False
    page._feat_snapshot = copy.deepcopy(params)
    page._feat_log_lines = list(params.get("log") or [])
    page._feat_running = False
    page._feat_cancel = False
    save_btn.clicked.connect(lambda _=False: _feat_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_feat_files_panel(ctx, page))
    splitter.addWidget(_feat_rules_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 3)
    splitter.setSizes([320, 880])
    row.addWidget(splitter, 1)

    row.addWidget(_feat_log_panel(ctx, page))
    layout.addWidget(content, 1)

    _feat_refresh_files(ctx, page)
    _feat_update_save_btn(ctx, page)
    return page


def _feat_log_panel(ctx, page) -> QWidget:
    """Timestamped log area shared by the whole module."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Log</b>"))
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Calculation output appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    log.setFixedHeight(120)
    for line in getattr(page, "_feat_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._feat_log = log
    return panel


def _feat_log(ctx, page, text) -> None:
    """Append a timestamped line to the Feature log (persisted with the module)."""
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_feat_log_lines", None)
    if lines is None:
        lines = []
        page._feat_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    # Keep the log bounded, then store it in the module's params so it is
    # saved with the project and shown again when the module is reopened.
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    if getattr(page, "_feat_params", None) is not None:
        page._feat_params["log"] = list(lines)
    box = getattr(page, "_feat_log", None)
    if box is not None:
        for line in str(text).splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")


def _feat_all_columns(ctx, page) -> list:
    """Every column visible at this module (union across available files)."""
    columns = []
    for rec in _data_files._available_files(ctx, page._feat_task_id):
        for name in _data_columns._columns_for(ctx, rec.get("parquet"), page._feat_task_id):
            if name not in columns:
                columns.append(name)
    return columns


def _feat_file_columns(ctx, page, parquet_path) -> list:
    return _data_columns._columns_for(ctx, parquet_path, page._feat_task_id)


def _feat_common_columns(ctx, page) -> list:
    """Columns shared by every selected file (rule sources must exist in all)."""
    files = list(page._feat_params.get("files") or [])
    if not files:
        return []
    common = set(_feat_file_columns(ctx, page, files[0]))
    for path in files[1:]:
        common &= set(_feat_file_columns(ctx, page, path))
    return sorted(common)


def _feat_files_panel(ctx, page) -> QWidget:
    """Left panel: feature variable, file picker and per-file status."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Feature channel</b>"))

    combo = _NoWheelComboBox()
    combo.addItem("", None)
    for name in _feat_all_columns(ctx, page):
        combo.addItem(name, name)
    idx = combo.findData(page._feat_params.get("feature"))
    if idx >= 0:
        combo.setCurrentIndex(idx)
    combo.setEnabled(not page._feat_locked)
    lay.addWidget(combo)
    page._feat_column_combo = combo

    page._feat_ignore_table = None
    page._feat_ignore_status = None
    page._feat_ignore_counts = None
    page._feat_ignore_default = None
    page._feat_ignore_token = 0
    page._feat_ignore_syncing = False
    page._feat_ignore_pending = set()
    page._feat_ignore_batch_total = 0
    page._feat_ignore_active = None
    page._feat_ignore_requests = None
    page._feat_ignore_results = None
    page._feat_ignore_thread = None
    page._feat_ignore_timer = None
    page._feat_ignore_cache = _feat_ignore_cache_load(ctx, page)
    lay.addWidget(_feat_ignore_section(ctx, page))

    lay.addWidget(QLabel("Data files"))
    select_btn = QPushButton("Select files…")
    select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    select_btn.setEnabled(not page._feat_locked)
    lay.addWidget(select_btn)

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
    page._feat_files_table = table

    def on_column_changed(_=0):
        page._feat_params["feature"] = combo.currentData()
        # The default group-key row follows the chosen variable.
        _feat_sync_feature_rule(ctx, page)
        # Keep only the files that contain the chosen feature variable.
        feature = page._feat_params["feature"]
        if feature:
            files = [
                p for p in page._feat_params.get("files") or []
                if feature in _feat_file_columns(ctx, page, p)
            ]
            page._feat_params["files"] = files
        _feat_refresh_files(ctx, page)
        _feat_mark_dirty(ctx, page)

    combo.currentIndexChanged.connect(on_column_changed)
    select_btn.clicked.connect(lambda _=False: _feat_select_files(ctx, page))
    return panel


def _feat_ignore_section(ctx, page) -> QWidget:
    """Background ignore: the most frequent feature values, checkable."""
    section = QWidget()
    lay = QVBoxLayout(section)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)

    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(6)
    header.addWidget(QLabel("<b>Background ignore</b>"))
    header.addStretch()
    status = QLabel("Select a feature channel and files")
    status.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    header.addWidget(status)
    lay.addLayout(header)

    hint = QLabel(
        "Checked values are left out before aggregation, so background "
        "(for example a mask label 0) is not treated as a cell."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(hint)

    table = QTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["Value", "Count", "Ignore"])
    head = table.horizontalHeader()
    head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(24)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    # Header plus the five most frequent values without scrolling.
    table.setFixedHeight(30 + 5 * 24)
    table.setEnabled(not page._feat_locked)
    table.itemChanged.connect(
        lambda item: _feat_ignore_toggled(ctx, page, item)
    )
    lay.addWidget(table)

    note = QLabel(
        "Ignored values will be missing from the matching data in "
        "Feature Up. That is expected, not an error."
    )
    note.setWordWrap(True)
    note.setStyleSheet("color: #8a94a6; font-size: 8pt;")
    lay.addWidget(note)

    page._feat_ignore_table = table
    page._feat_ignore_status = status
    return section


def _feat_value_text(ctx, value) -> str:
    """Compact display of a feature value (mask labels stay integral)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isfinite(number) and number == int(number):
        return str(int(number))
    return f"{number:.6g}"


def _feat_value_json(ctx, value):
    """JSON-safe form of a feature value (ints stay ints in the project)."""
    number = float(value)
    if math.isfinite(number) and number == int(number):
        return int(number)
    return number


def _feat_ignore_cache_path(ctx, page):
    if ctx.project is None or not page._feat_task_id:
        return None
    folder = ctx.project.path / "Module" / str(page._feat_task_id)
    return folder / _FEAT_IGNORE_CACHE_NAME


def _feat_ignore_cache_load(ctx, page) -> dict:
    """Per-file value counts saved with the module (key -> [[v, n]])."""
    path = _feat_ignore_cache_path(ctx, page)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    cache = {}
    for key, entry in entries.items():
        counts = entry.get("counts") if isinstance(entry, dict) else None
        if not isinstance(counts, list):
            continue
        try:
            cache[str(key)] = [
                [float(value), int(count)] for value, count in counts
            ]
        except (TypeError, ValueError):
            continue
    return cache


def _feat_ignore_cache_save(ctx, page) -> None:
    path = _feat_ignore_cache_path(ctx, page)
    if path is None:
        return
    cache = page._feat_ignore_cache
    while len(cache) > _FEAT_IGNORE_CACHE_LIMIT:
        cache.pop(next(iter(cache)), None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "version": 1,
                "entries": {
                    key: {"counts": counts}
                    for key, counts in cache.items()
                },
            }),
            encoding="utf-8",
        )
    except (OSError, TypeError):
        pass


def _feat_file_stat(ctx, path) -> str:
    """size + nanosecond mtime of a file (relative paths use the project)."""
    if not path:
        return ""
    candidate = Path(str(path))
    if not candidate.is_absolute() and ctx.project is not None:
        candidate = ctx.project.path / candidate
    try:
        st = candidate.stat()
    except OSError:
        return ""
    return f"{int(st.st_size)}-{int(st.st_mtime_ns)}"


def _feat_feature_source_sig(ctx, task_id, path, feature) -> str:
    """Change signature of columns the feature value may come from."""
    parts = []
    try:
        mask_path = _data_outputs._seg_applied_columns(ctx, task_id, path).get(feature)
    except Exception:  # noqa: BLE001
        mask_path = None
    if mask_path:
        parts.append(["seg", _feat_file_stat(ctx, mask_path)])
    for kind, getter in (
        ("gate2col", partial(_data_feature_outputs._gate2col_applied_columns, ctx)),
        ("feature_up", partial(_data_feature_outputs._feature_up_applied_columns, ctx)),
        ("dimred", partial(_data_outputs._dimred_applied_columns, ctx)),
        ("cluster", partial(_data_outputs._cluster_applied_columns, ctx)),
        ("dc", partial(_data_outputs._dc_applied_columns, ctx)),
    ):
        try:
            rec = getter(task_id, path).get(feature)
        except Exception:  # noqa: BLE001
            rec = None
        if not rec:
            continue
        rec_path = rec.get("parquet") or rec.get("path") or ""
        parts.append([
            kind, str(rec_path), _feat_file_stat(ctx, rec_path),
            str(rec.get("signature") or ""),
        ])
    return hashlib.sha1(
        _json_stable(parts).encode("utf-8")
    ).hexdigest()


def _feat_ignore_cache_key(ctx, page, feature, path) -> str:
    """Identifies one file's worth of counts (invalidated when inputs change)."""
    try:
        stages = _json_stable(_data_columns._map_stages(ctx, page._feat_task_id))
    except Exception:  # noqa: BLE001
        stages = ""
    payload = [
        str(path), _feat_file_stat(ctx, path), str(feature),
        hashlib.sha1(stages.encode("utf-8")).hexdigest(),
        _data_sampling._subsample_sig(ctx, page._feat_task_id, path) or "",
        _feat_feature_source_sig(ctx,
            page._feat_task_id, path, feature
        ),
    ]
    return hashlib.sha1(
        json.dumps(payload).encode("utf-8")
    ).hexdigest()


def _feat_ignore_file_counts(ctx, page, feature, path) -> list:
    """[[value, count]] of the feature variable in one file."""
    import numpy as np

    values = _data_columns._column_data(ctx, page._feat_task_id, path, [feature])
    array = values.get(feature)
    if array is None:
        return []
    array = np.asarray(array, dtype="float64")
    array = array[np.isfinite(array)]
    if array.size == 0:
        return []
    unique, counts = np.unique(array, return_counts=True)
    # A value that leads the global ranking leads its own file too, so
    # keeping each file's leaders is enough for the global top five.
    if unique.size > _FEAT_IGNORE_LEADERS:
        pick = np.argpartition(
            -counts, _FEAT_IGNORE_LEADERS - 1
        )[:_FEAT_IGNORE_LEADERS]
        unique, counts = unique[pick], counts[pick]
    return [[float(value), int(count)] for value, count in zip(unique, counts)]


def _feat_ignore_combine(ctx, page, only_cached: bool=False):
    """Aggregate the selected files' cached counts (None when incomplete)."""
    params = page._feat_params
    feature = params.get("feature")
    files = list(params.get("files") or [])
    if not feature or not files:
        return None
    totals = {}
    found = False
    for path in files:
        key = _feat_ignore_cache_key(ctx, page, feature, path)
        counts = page._feat_ignore_cache.get(key)
        if counts is None:
            if only_cached:
                continue
            return None
        found = True
        for value, count in counts:
            value = float(value)
            totals[value] = totals.get(value, 0) + int(count)
    if not found:
        return None
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))


def _feat_ignore_apply(ctx, page, counts) -> None:
    page._feat_ignore_counts = counts
    # Default: the most frequent value (background in a mask image). It
    # stays implicit until the user toggles a box or calculates, so simply
    # opening and saving the module does not invalidate saved outputs.
    page._feat_ignore_default = float(counts[0][0]) if counts else None
    _feat_ignore_fill(ctx, page, counts)


def _feat_ignore_start_worker(ctx, page) -> None:
    if getattr(page, "_feat_ignore_requests", None) is not None:
        return
    page._feat_ignore_requests = queue.Queue()
    page._feat_ignore_results = queue.Queue()
    page._feat_ignore_thread = threading.Thread(
        target=partial(_feat_ignore_worker, ctx), args=(page,), daemon=True
    )
    page._feat_ignore_thread.start()


def _feat_ignore_start_timer(ctx, page) -> None:
    if getattr(page, "_feat_ignore_timer", None) is not None:
        return
    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _feat_ignore_poll(ctx, page))
    timer.start()
    page._feat_ignore_timer = timer


def _feat_ignore_worker(ctx, page) -> None:
    """Count one file at a time; superseded jobs stop at a file boundary."""
    while True:
        requests = getattr(page, "_feat_ignore_requests", None)
        if requests is None:
            return
        job = requests.get()
        if job is None:
            return
        token, feature, items = job
        for path, key in items:
            if getattr(page, "_feat_ignore_requests", None) is not requests:
                return
            if int(getattr(page, "_feat_ignore_token", 0)) != token:
                break
            try:
                counts = _feat_ignore_file_counts(ctx, page, feature, path)
            except Exception:  # noqa: BLE001
                counts = []
            results = getattr(page, "_feat_ignore_results", None)
            if results is None:
                return
            results.put(("file", token, feature, key, path, counts))
        results = getattr(page, "_feat_ignore_results", None)
        if results is not None:
            results.put(("done", token, feature))


def _feat_ignore_poll(ctx, page) -> None:
    results = getattr(page, "_feat_ignore_results", None)
    if results is None:
        return
    cache_changed = False
    while True:
        try:
            message = results.get_nowait()
        except queue.Empty:
            break
        if message[0] == "file":
            _tag, token, feature, key, _path, counts = message
            if counts:
                page._feat_ignore_cache[key] = counts
                cache_changed = True
            if token != page._feat_ignore_token:
                continue
            pending = page._feat_ignore_pending
            pending.discard(key)
            status = getattr(page, "_feat_ignore_status", None)
            total = page._feat_ignore_batch_total
            if status is not None and total:
                status.setText(
                    f"Counting values… {total - len(pending)}/{total}"
                )
        elif message[0] == "done":
            _tag, token, _feature = message
            if token != page._feat_ignore_token:
                continue
            page._feat_ignore_pending = set()
            page._feat_ignore_active = None
            combined = _feat_ignore_combine(ctx, page)
            if combined is not None:
                _feat_ignore_apply(ctx, page, combined)
            timer = getattr(page, "_feat_ignore_timer", None)
            if timer is not None:
                timer.stop()
                page._feat_ignore_timer = None
    if cache_changed:
        _feat_ignore_cache_save(ctx, page)


def _feat_ignore_cleanup(ctx, page) -> None:
    page._feat_ignore_token = int(
        getattr(page, "_feat_ignore_token", 0)
    ) + 1
    page._feat_ignore_active = None
    page._feat_ignore_pending = set()
    timer = getattr(page, "_feat_ignore_timer", None)
    if timer is not None:
        timer.stop()
        page._feat_ignore_timer = None
    requests = getattr(page, "_feat_ignore_requests", None)
    page._feat_ignore_requests = None
    page._feat_ignore_results = None
    if requests is not None:
        try:
            requests.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass


def _feat_ignore_refresh(ctx, page) -> None:
    """Rebuild the background-ignore table for the current files."""
    table = getattr(page, "_feat_ignore_table", None)
    if table is None:
        return
    params = page._feat_params
    feature = params.get("feature")
    files = list(params.get("files") or [])
    status = getattr(page, "_feat_ignore_status", None)
    if not feature or not files:
        page._feat_ignore_token = int(
            getattr(page, "_feat_ignore_token", 0)
        ) + 1
        page._feat_ignore_active = None
        page._feat_ignore_pending = set()
        page._feat_ignore_batch_total = 0
        page._feat_ignore_counts = None
        page._feat_ignore_default = None
        _feat_ignore_fill(ctx, page, [])
        if status is not None:
            status.setText("Select a feature channel and files")
        return
    _feat_ignore_start_worker(ctx, page)
    pending = []
    for path in files:
        key = _feat_ignore_cache_key(ctx, page, feature, path)
        if key not in page._feat_ignore_cache:
            pending.append((path, key))
    if not pending:
        page._feat_ignore_token = int(
            getattr(page, "_feat_ignore_token", 0)
        ) + 1
        page._feat_ignore_active = None
        page._feat_ignore_pending = set()
        page._feat_ignore_batch_total = 0
        combined = _feat_ignore_combine(ctx, page)
        if combined is not None:
            _feat_ignore_apply(ctx, page, combined)
        if status is not None:
            status.setText("Most frequent values")
        return
    batch_key = (feature, tuple(key for _path, key in pending))
    if batch_key == getattr(page, "_feat_ignore_active", None):
        # These exact files are already being counted; keep that job.
        return
    page._feat_ignore_token = int(
        getattr(page, "_feat_ignore_token", 0)
    ) + 1
    page._feat_ignore_active = batch_key
    page._feat_ignore_pending = {key for _path, key in pending}
    page._feat_ignore_batch_total = len(pending)
    # Show what is already cached; the rest arrives from the worker.
    cached = _feat_ignore_combine(ctx, page, only_cached=True)
    _feat_ignore_fill(ctx, page, cached or [])
    if status is not None:
        status.setText(f"Counting values… 0/{len(pending)}")
    _feat_ignore_start_timer(ctx, page)
    page._feat_ignore_requests.put(
        (page._feat_ignore_token, feature, pending)
    )


def _feat_ignore_fill(ctx, page, counts) -> None:
    """Draw the top five values (plus any ignored value outside them)."""
    table = getattr(page, "_feat_ignore_table", None)
    if table is None:
        return
    params = page._feat_params
    explicit = params.get("ignore_values")
    if explicit is None:
        default = getattr(page, "_feat_ignore_default", None)
        ignored = [default] if default is not None else []
    else:
        ignored = [float(v) for v in explicit]
    rows = []
    seen = set()
    for value, count in counts[:5]:
        value = float(value)
        rows.append((value, int(count)))
        seen.add(value)
    count_map = {float(v): int(c) for v, c in counts}
    for value in ignored:
        if value not in seen:
            rows.append((value, count_map.get(value, 0)))
            seen.add(value)

    page._feat_ignore_syncing = True
    try:
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for row, (value, count) in enumerate(rows):
            vitem = QTableWidgetItem(_feat_value_text(ctx, value))
            vitem.setData(Qt.ItemDataRole.UserRole, float(value))
            vitem.setFlags(Qt.ItemFlag.ItemIsEnabled)
            vitem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.setItem(row, 0, vitem)
            citem = QTableWidgetItem(f"{count:,}")
            citem.setFlags(Qt.ItemFlag.ItemIsEnabled)
            citem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.setItem(row, 1, citem)
            iitem = QTableWidgetItem("")
            iitem.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            iitem.setCheckState(
                Qt.CheckState.Checked if value in ignored
                else Qt.CheckState.Unchecked
            )
            table.setItem(row, 2, iitem)
    finally:
        table.blockSignals(False)
        page._feat_ignore_syncing = False

    status = getattr(page, "_feat_ignore_status", None)
    if status is not None:
        status.setText(
            "Most frequent values" if rows else "No values to show"
        )


def _feat_ignore_toggled(ctx, page, item) -> None:
    if getattr(page, "_feat_ignore_syncing", False):
        return
    table = getattr(page, "_feat_ignore_table", None)
    if table is None or item is None or item.column() != 2:
        return
    values = []
    for row in range(table.rowCount()):
        cell = table.item(row, 2)
        name = table.item(row, 0)
        if cell is None or name is None:
            continue
        if cell.checkState() == Qt.CheckState.Checked:
            value = name.data(Qt.ItemDataRole.UserRole)
            if value is not None:
                values.append(_feat_value_json(ctx, value))
    values = sorted(set(values))
    params = page._feat_params
    if params.get("ignore_values") != values:
        params["ignore_values"] = values
        _feat_mark_dirty(ctx, page)


def _feat_ignore_prepare(ctx, page) -> None:
    """Make sure a default is chosen before a calculation starts."""
    params = page._feat_params
    if params.get("ignore_values") is not None:
        return
    feature = params.get("feature")
    files = list(params.get("files") or [])
    if not feature or not files:
        return
    changed = False
    for path in files:
        key = _feat_ignore_cache_key(ctx, page, feature, path)
        if key in page._feat_ignore_cache:
            continue
        try:
            counts = _feat_ignore_file_counts(ctx, page, feature, path)
        except Exception:  # noqa: BLE001
            counts = []
        page._feat_ignore_cache[key] = counts
        changed = True
    if changed:
        _feat_ignore_cache_save(ctx, page)
    combined = _feat_ignore_combine(ctx, page)
    if combined:
        page._feat_ignore_default = float(combined[0][0])
        params["ignore_values"] = [
            _feat_value_json(ctx, combined[0][0])
        ]
        _feat_ignore_fill(ctx, page, combined)


def _feat_select_files(ctx, page) -> None:
    """Pick the data files this module aggregates."""
    feature = page._feat_params.get("feature")
    if not feature:
        QMessageBox.information(
            ctx.host, "Select files", "Choose the feature channel first."
        )
        return
    candidates = [
        rec for rec in _data_files._available_files(ctx, page._feat_task_id)
        if feature in _feat_file_columns(ctx, page, rec.get("parquet"))
    ]
    names = [rec.get("name", "") for rec in candidates]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in candidates}
    current = [_data_naming._comp_file_name(ctx, p, page._feat_task_id) for p in page._feat_params.get("files") or []]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    files = []
    for name in chosen:
        path = by_name.get(name)
        if path and path not in files:
            files.append(path)
    page._feat_params["files"] = files
    _feat_refresh_files(ctx, page)
    _feat_mark_dirty(ctx, page)


def _feat_file_status(ctx, page, parquet_path) -> str:
    """'Calculated' only when a current, matching output exists for the file."""
    params = page._feat_params
    rec = (params.get("outputs") or {}).get(parquet_path)
    if not isinstance(rec, dict) or not rec.get("path"):
        return FEATURE_STATUS_PENDING
    if ctx.project is None:
        return FEATURE_STATUS_PENDING
    path = ctx.project.path / rec["path"]
    if not path.is_file():
        return FEATURE_STATUS_PENDING
    if rec.get("signature") != _data_feature_outputs._feature_signature(ctx, params, parquet_path):
        return FEATURE_STATUS_PENDING
    return FEATURE_STATUS_DONE


def _feat_refresh_files(ctx, page) -> None:
    """Rebuild the file status table and the rules table."""
    table = getattr(page, "_feat_files_table", None)
    if table is not None:
        files = list(page._feat_params.get("files") or [])
        table.blockSignals(True)
        table.setRowCount(len(files))
        for i, path in enumerate(files):
            name_item = QTableWidgetItem(_data_naming._comp_file_name(ctx, path, page._feat_task_id))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(i, 0, name_item)
            status = _feat_file_status(ctx, page, path)
            sitem = QTableWidgetItem(status)
            sitem.setFlags(Qt.ItemFlag.ItemIsEnabled)
            sitem.setForeground(
                QColor("#1a7f37") if status == FEATURE_STATUS_DONE
                else QColor("#9aa0a6")
            )
            table.setItem(i, 1, sitem)
        table.blockSignals(False)
    _feat_rebuild_rules(ctx, page)
    _feat_ignore_refresh(ctx, page)


def _feat_feature_rule(params):
    """The group-key row of the rules table, or None when it was deleted."""
    for rule in params.get("rules") or []:
        if rule.get("method") == "feature":
            return rule
    return None


def _feat_key_name(params):
    """Output name of the feature-variable column (None = leave it out)."""
    if params.get("feature_rule", True) is False:
        return None
    rule = _feat_feature_rule(params)
    return ((rule or {}).get("name") or "").strip() or params.get("feature")


def _feat_sync_feature_rule(ctx, page) -> None:
    """Keep the default group-key row in step with the feature variable.

    The feature column is an output column like the aggregated ones, so it
    gets its own row in the rules table (and can be deleted) instead of
    being added silently.
    """
    params = page._feat_params
    feature = params.get("feature")
    rules = params.setdefault("rules", [])
    rule = _feat_feature_rule(params)
    if params.get("feature_rule", True) is False:
        if rule is not None:
            rules.remove(rule)
        return
    if rule is None:
        if not feature:
            return
        rule = {
            "source": feature, "method": "feature", "lo": 0, "hi": 100,
            "perc": None, "name": feature,
        }
        index = 1 if rules and rules[0].get("method") == "count" else 0
        rules.insert(index, rule)
        return
    # Follow a changed feature variable, unless the user renamed the column.
    old_source = rule.get("source")
    name = (rule.get("name") or "").strip()
    rule["source"] = feature
    if not name or name == old_source or name == "feature":
        rule["name"] = feature or ""


def _feat_rules_panel(ctx, page) -> QWidget:
    """Right panel: the 4-column aggregation rules table."""
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Aggregation rules</b>"))

    table = QTableWidget()
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(
        ["Source channel", "Method", "Range (% per class)", "New column name"]
    )
    header = table.horizontalHeader()
    for column in range(4):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    page._feat_rules_table = table

    note = QLabel(
        "Range (% per class): for each class, use only the points between "
        "these two percentiles, sorted by the row's own source channel. "
        "0–100 means all points (e.g. 0–30 = dimmest 30%, 90–100 = "
        "brightest 10%). The Count row always uses all points. The Feature "
        "row keeps the feature channel as the identifier column; delete "
        "it to leave that column out."
    )
    note.setWordWrap(True)
    note.setStyleSheet("color: #6b7785;")
    lay.addWidget(note)

    btns = QHBoxLayout()
    add_btn = QPushButton("Add")
    add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_btn.setEnabled(not page._feat_locked)
    del_btn = QPushButton("Delete")
    del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    del_btn.setEnabled(not page._feat_locked)
    btns.addWidget(add_btn)
    btns.addWidget(del_btn)
    btns.addStretch()
    lay.addLayout(btns)

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(not page._feat_locked)
    lay.addWidget(calc_btn)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._feat_calc_btn = calc_btn
    page._feat_calc_status = status

    def on_add():
        page._feat_params["rules"].append(
            {"source": None, "method": "mean", "lo": 0, "hi": 100,
             "perc": None, "name": ""}
        )
        _feat_refresh_rules(ctx, page)
        _feat_mark_dirty(ctx, page)

    def on_delete():
        row = table.currentRow()
        rules = page._feat_params["rules"]
        if row <= 0 or row >= len(rules):
            if row == 0:
                QMessageBox.information(
                    ctx.host, "Delete", "The Count row is fixed and cannot be deleted."
                )
            return
        rule = rules[row]
        if rule.get("method") == "feature":
            # Remember the choice: the row is re-created with the module.
            page._feat_params["feature_rule"] = False
        del rules[row]
        _feat_refresh_rules(ctx, page)
        _feat_mark_dirty(ctx, page)

    add_btn.clicked.connect(lambda _=False: on_add())
    del_btn.clicked.connect(lambda _=False: on_delete())
    calc_btn.clicked.connect(lambda _=False: _feat_calculate(ctx, page))
    return panel


def _feat_rebuild_rules(ctx, page) -> None:
    """Rebuild the rules table widgets from params (without losing edits)."""
    table = getattr(page, "_feat_rules_table", None)
    if table is None:
        return
    params = page._feat_params
    rules = params.get("rules") or []
    common = _feat_common_columns(ctx, page)
    enabled = not page._feat_locked

    table.blockSignals(True)
    # Clear via row count so old cell widgets are destroyed too (items are
    # cleared by clearContents, cell widgets are not).
    table.setRowCount(0)
    table.setRowCount(len(rules))

    def mark():
        _feat_mark_dirty(ctx, page)
        _feat_refresh_statuses(ctx, page)

    def rebuild_soon():
        # Rebuilding the table destroys the widget whose signal we are
        # handling, so defer the rebuild to the next event-loop turn.
        QTimer.singleShot(0, lambda: _feat_rebuild_rules(ctx, page))

    for i, rule in enumerate(rules):
        is_count = rule.get("method") == "count"
        is_feature = rule.get("method") == "feature"

        # Column 0: source variable (fixed label for Count / group key).
        if is_count:
            item = QTableWidgetItem("(all rows)")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(QColor("#6b7785"))
            table.setItem(i, 0, item)
        elif is_feature:
            item = QTableWidgetItem(str(rule.get("source") or "(feature channel)"))
            # Selectable so clicking it makes this the row the Delete button
            # acts on (unlike the fixed Count row above).
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            item.setForeground(QColor("#6b7785"))
            item.setToolTip(
                "The chosen Feature channel. This row keeps its value as an "
                "output column; delete it to leave the identifier out."
            )
            table.setItem(i, 0, item)
        else:
            combo = _NoWheelComboBox()
            combo.addItem("", None)
            names = list(common)
            if rule.get("source") and rule["source"] not in names:
                names.append(rule["source"])
            for name in names:
                combo.addItem(name, name)
            idx = combo.findData(rule.get("source"))
            combo.setCurrentIndex(max(0, idx))
            combo.setEnabled(enabled)

            def on_source(value, r=rule):
                old_auto = _feat_auto_name(ctx, r)
                r["source"] = value
                current = (r.get("name") or "").strip()
                if not current or current == old_auto:
                    r["name"] = _feat_auto_name(ctx, r)
                rebuild_soon()
                mark()

            combo.currentIndexChanged.connect(
                lambda _=0, c=combo, cb=on_source: cb(c.currentData())
            )
            table.setCellWidget(i, 0, combo)

        # Column 1: method (+ percentile value when relevant).
        method_box = QWidget()
        mh = QHBoxLayout(method_box)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.setSpacing(4)
        method_combo = _NoWheelComboBox()
        if is_feature:
            method_combo.addItem("Group key", "feature")
            method_combo.setEnabled(False)
        else:
            for label, key in FEATURE_METHODS:
                method_combo.addItem(label, key)
            midx = method_combo.findData(rule.get("method", "mean"))
            method_combo.setCurrentIndex(max(0, midx))
        percent_spin = None
        if is_count:
            method_combo.setEnabled(False)
        elif not is_feature:
            def on_method(value, r=rule):
                r["method"] = value
                rebuild_soon()
                mark()
            method_combo.currentIndexChanged.connect(
                lambda _=0, c=method_combo, cb=on_method: cb(c.currentData())
            )
        mh.addWidget(method_combo)
        if rule.get("method") == "percentile":
            percent_spin = _NoWheelSpinBox()
            percent_spin.setRange(0, 100)
            percent_spin.setSingleStep(1)
            percent_spin.setValue(int(rule.get("perc") or 50))
            percent_spin.setEnabled(enabled)
            percent_spin.setFixedWidth(76)
            percent_spin.setToolTip("Percentile to compute within each class")

            def on_perc(value, r=rule):
                r["perc"] = int(value)
                mark()

            percent_spin.valueChanged.connect(
                lambda v, cb=on_perc: cb(v)
            )
            mh.addWidget(percent_spin)
        table.setCellWidget(i, 1, method_box)

        # Column 2: in-class percentile range.
        range_box = QWidget()
        rh = QHBoxLayout(range_box)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(4)
        lo_spin = _NoWheelSpinBox()
        hi_spin = _NoWheelSpinBox()
        for spin, value in ((lo_spin, rule.get("lo", 0)),
                            (hi_spin, rule.get("hi", 100))):
            spin.setRange(0, 100)
            spin.setSingleStep(1)
            spin.setValue(int(value))
            spin.setFixedWidth(72)
            spin.setEnabled(enabled and not is_count and not is_feature)
        if is_count:
            tip = "Not used by the Count row (always counts all points)"
        elif is_feature:
            tip = "Not used by the group-key row (it keeps the feature value)"
        else:
            tip = ("Use only the points between these percentiles within "
                   "each class (sorted by this row's source channel)")
        lo_spin.setToolTip(tip)
        hi_spin.setToolTip(tip)

        def on_range(_=0, r=rule, lo=lo_spin, hi=hi_spin):
            if r.get("method") in ("count", "feature"):
                return
            r["lo"] = int(lo.value())
            r["hi"] = int(hi.value())
            mark()

        lo_spin.valueChanged.connect(lambda v, cb=on_range: cb())
        hi_spin.valueChanged.connect(lambda v, cb=on_range: cb())
        rh.addWidget(lo_spin)
        rh.addWidget(QLabel("–"))
        rh.addWidget(hi_spin)
        table.setCellWidget(i, 2, range_box)

        # Column 3: new column name.
        if is_count:
            # Default 'count'; resolved to count_1, count_2… on a clash.
            rule["name"] = _feat_count_name(ctx, params)
        name_edit = QLineEdit(str(rule.get("name") or _feat_auto_name(ctx, rule)))
        name_edit.setEnabled(enabled)

        def on_name(text, r=rule):
            r["name"] = text.strip()
            mark()

        name_edit.textEdited.connect(lambda t, cb=on_name: cb(t))
        table.setCellWidget(i, 3, name_edit)

    table.blockSignals(False)


def _feat_refresh_rules(ctx, page) -> None:
    """Rebuild the rules table after a structural change."""
    _feat_rebuild_rules(ctx, page)


def _feat_auto_name(ctx, rule) -> str:
    """Default new-column name: <source>_<method> (or 'count')."""
    if rule.get("method") == "count":
        return "count"
    if rule.get("method") == "feature":
        return rule.get("source") or "feature"
    source = rule.get("source") or "value"
    method = rule.get("method") or "mean"
    if method == "percentile":
        perc = rule.get("perc")
        suffix = f"p{int(perc)}" if perc else "p"
    else:
        suffix = method
    return f"{source}_{suffix}"


def _feat_refresh_statuses(ctx, page) -> None:
    """Update only the status column of the file table."""
    table = getattr(page, "_feat_files_table", None)
    if table is None:
        return
    files = list(page._feat_params.get("files") or [])
    for i, path in enumerate(files):
        if i >= table.rowCount():
            break
        status = _feat_file_status(ctx, page, path)
        item = table.item(i, 1)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(i, 1, item)
        item.setText(status)
        item.setForeground(
            QColor("#1a7f37") if status == FEATURE_STATUS_DONE
            else QColor("#9aa0a6")
        )


def _feat_mark_dirty(ctx, page) -> None:
    page._feat_dirty = True
    _feat_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)
    if getattr(page, "_feat_calc_status", None) is not None:
        page._feat_calc_status.setText("")


def _feat_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_feat_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._feat_dirty and not page._feat_locked)


def _feat_save_now(ctx, page) -> None:
    """Persist the module's settings without recomputing."""
    params = page._feat_params
    page._feat_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._feat_snapshot = copy.deepcopy(params)
    page._feat_dirty = False
    _feat_update_save_btn(ctx, page)
    _feat_log(ctx, page, "Settings saved")
    ctx.statusBar().showMessage("Feature settings saved")
    ctx._module_saved(page)


def _feat_validate(ctx, page):
    """Return (ok, message) for the current settings."""
    params = page._feat_params
    if not params.get("feature"):
        return False, "Choose the feature channel first."
    if not params.get("files"):
        return False, "Select at least one data file."
    rules = params.get("rules") or []
    if not rules:
        return False, "Add at least one aggregation rule."
    common = set(_feat_common_columns(ctx, page))
    names = []
    for i, rule in enumerate(rules):
        method = rule.get("method")
        if method == "count":
            if i != 0:
                return False, "Count must be the first row."
            name = (rule.get("name") or "").strip()
            if not name:
                return False, "Give the Count row a column name."
            names.append(name)
            continue
        if method == "feature":
            source = rule.get("source")
            if not source or source != params.get("feature"):
                return False, (
                    f"Row {i + 1}: this row keeps the feature channel; "
                    "delete it or choose the matching channel."
                )
            if source not in common:
                return False, (
                    f"Row {i + 1}: '{source}' is not present in every selected file."
                )
            name = (rule.get("name") or "").strip()
            if not name:
                return False, f"Row {i + 1}: give the feature column a name."
            names.append(name)
            continue
        source = rule.get("source")
        if not source:
            return False, f"Row {i + 1}: choose a source channel."
        if source not in common:
            return False, (
                f"Row {i + 1}: '{source}' is not present in every selected file."
            )
        lo = float(rule.get("lo", 0.0))
        hi = float(rule.get("hi", 100.0))
        if not (0.0 <= lo < hi <= 100.0):
            return False, (
                f"Row {i + 1}: the range must satisfy 0 ≤ from < to ≤ 100."
            )
        if rule.get("method") == "percentile":
            perc = rule.get("perc")
            if perc is None or not (0.0 <= float(perc) <= 100.0):
                return False, f"Row {i + 1}: the percentile must be 0–100."
        name = (rule.get("name") or "").strip()
        if not name:
            return False, f"Row {i + 1}: give the new column a name."
        names.append(name)
    if len(names) != len(set(names)):
        return False, "New column names must be unique."
    return True, ""


def _feat_count_name(ctx, params) -> str:
    """The Count column name, resolved to be unique (see the module helper)."""
    return _feature_count_name(params)


def _feat_output_stem(ctx, page, parquet_path) -> str:
    """Output file stem: '<source file name>_<feature>', '_new' on a clash.

    The source file's display name (the name shown in the Workflow / file
    lists) is used, not the internal parquet stem. A previous output of
    this same file is overwritten in place when the name is unchanged.
    """
    params = page._feat_params
    feature = params.get("feature") or "feature"
    outputs = params.get("outputs") or {}
    existing = outputs.get(parquet_path) if isinstance(outputs, dict) else None
    old_stem = (
        Path(existing["path"]).stem
        if isinstance(existing, dict) and existing.get("path") else None
    )
    display = _data_naming._comp_file_name(ctx, parquet_path, page._feat_task_id) or Path(str(parquet_path)).name
    base = _feature_safe_stem(f"{Path(display).stem}_{feature}")
    if old_stem == base:
        return base
    used = {
        Path(rec["path"]).stem
        for source, rec in outputs.items()
        if source != parquet_path and isinstance(rec, dict) and rec.get("path")
    }
    if ctx.project is not None:
        folder = _data_files._module_dir(ctx, page._feat_task_id)
        if folder is not None and folder.is_dir():
            used |= {
                p.stem for p in folder.glob("*.parquet") if p.stem != old_stem
            }
    stem = base
    n = 0
    while stem in used:
        n += 1
        stem = f"{base}_new" if n == 1 else f"{base}_new{n}"
    if n:
        _feat_log(ctx,
            page,
            f"'{base}' already exists — writing as '{stem}' instead.",
        )
    return stem


def _feat_calculate(ctx, page) -> None:
    """Aggregate every selected file and save the generated parquet files."""
    ok, message = _feat_validate(ctx, page)
    if not ok:
        QMessageBox.warning(ctx.host, "Calculate", message)
        return
    # A default ignore value is suggested by the Background ignore widget;
    # make sure it is set even if the user calculated immediately.
    _feat_ignore_prepare(ctx, page)
    params = page._feat_params
    outputs = params.get("outputs") or {}
    selected = params.get("files") or []
    existing = [p for p in selected if isinstance(outputs.get(p), dict)]
    if existing:
        reply = QMessageBox.question(
            ctx.host,
            "Calculate",
            f"{len(existing)} file(s) already have results.\n"
            "Recalculate and overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    status = getattr(page, "_feat_calc_status", None)
    if status is not None:
        status.setText("Calculating…")
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    done = 0
    failed = []
    page._feat_running = True
    page._feat_cancel = False
    try:
        for path in selected:
            if getattr(page, "_feat_cancel", False):
                _feat_log(ctx, page, "Calculation cancelled.")
                break
            name = _data_naming._comp_file_name(ctx, path, page._feat_task_id)
            if status is not None:
                status.setText(f"Calculating… {name}")
            QApplication.processEvents()
            previous = outputs.get(path)
            try:
                rel, columns, warnings = _feat_compute_file(ctx, page, path)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                _feat_log(ctx, page, f"✗ {name}: {exc}")
                continue
            # A renamed output (e.g. after this file's name scheme changed)
            # leaves its old parquet behind; remove it.
            if isinstance(previous, dict) and previous.get("path"):
                if previous["path"] != rel and ctx.project is not None:
                    try:
                        (ctx.project.path / previous["path"]).unlink(
                            missing_ok=True
                        )
                    except OSError:
                        pass
            for warning in warnings:
                _feat_log(ctx, page, warning)
            outputs[path] = {
                "path": rel,
                "name": Path(rel).stem,
                "columns": columns,
                "signature": _data_feature_outputs._feature_signature(ctx,
                    params, path, page._feat_task_id
                ),
            }
            done += 1
            _feat_log(ctx,
                page,
                f"✓ {name} → {Path(rel).name} "
                f"({len(columns)} column(s))",
            )
    finally:
        QApplication.restoreOverrideCursor()
        page._feat_running = False

    # Drop stale mappings for files that are no longer selected.
    for path in list(outputs.keys()):
        if path not in selected:
            outputs.pop(path, None)
    params["outputs"] = outputs
    page._feat_task_def["output"] = {"saved": bool(outputs)}
    page._feat_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._feat_snapshot = copy.deepcopy(params)
    page._feat_dirty = False
    _feat_update_save_btn(ctx, page)
    _feat_refresh_files(ctx, page)

    summary = f"Calculated {done} file(s)."
    if failed:
        summary += f" {len(failed)} failed — see the log."
    if status is not None:
        status.setText(summary)
    _feat_log(ctx, page, summary)
    ctx.statusBar().showMessage(summary)
    ctx._module_saved(page)


def _feat_compute_file(ctx, page, parquet_path):
    """Aggregate one source file; return (relative path, columns, warnings)."""
    import numpy as np
    import pandas as pd

    params = page._feat_params
    feature = params["feature"]
    rules = params.get("rules") or []
    count_name = _feat_count_name(ctx, params)
    # The group-key row carries the feature column, which is read separately.
    sources = []
    for rule in rules:
        source = rule.get("source")
        if not source or rule.get("method") == "feature":
            continue
        if source == feature or source in sources:
            continue
        sources.append(source)

    values = _data_columns._column_data(ctx,
        page._feat_task_id, parquet_path, [feature] + sources
    )
    if feature not in values:
        raise ValueError(f"'{feature}' is missing from this file")
    data = {feature: values[feature]}
    for source in sources:
        if source not in values:
            raise ValueError(f"'{source}' is missing from this file")
        data[source] = values[source]
    df = pd.DataFrame(data)
    before = len(df)
    df = df[np.isfinite(df[feature])]
    dropped = before - len(df)
    warnings = []
    if dropped:
        warnings.append(
            f"{_data_naming._comp_file_name(ctx, parquet_path, page._feat_task_id)}: skipped {dropped} row(s) "
            f"with an empty '{feature}' value"
        )
    # Values the user marked "ignore" in Background ignore are left out
    # before aggregation. By default that is the most frequent value of
    # the feature variable, which is the background of a segmentation
    # mask (label 0). This no longer depends on where the column came from.
    ignored = [float(v) for v in (params.get("ignore_values") or [])]
    if ignored:
        drop = df[feature].isin(ignored)
        skipped = int(drop.sum())
        if skipped:
            df = df[~drop]
            labels = ", ".join(
                _feat_value_text(ctx, v) for v in ignored[:6]
            )
            if len(ignored) > 6:
                labels += " …"
            warnings.append(
                f"{_data_naming._comp_file_name(ctx, parquet_path, page._feat_task_id)}: "
                f"skipped {skipped} row(s) with ignored value(s) "
                f"{labels}"
            )
    if df.empty:
        raise ValueError("no rows with a valid feature value")

    out, columns, agg_warnings = _feature_aggregate_frame(
        df, feature, rules, count_name, _feat_key_name(params)
    )
    warnings.extend(agg_warnings)

    stem = _feat_output_stem(ctx, page, parquet_path)
    rel = _data_files._module_rel(ctx, page._feat_task_id, f"{stem}.parquet")
    if ctx.project is None:
        raise RuntimeError("no project")
    path = ctx.project.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return rel, columns, warnings


def _feat_close(ctx, page) -> None:
    if getattr(page, "_feat_running", False):
        reply = QMessageBox.question(
            ctx.host,
            "Close Feature",
            "A calculation is still running.\n"
            "Stop it and close the module?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Stop at the next file; the page stays open until the loop ends so
        # its widgets are never destroyed mid-calculation.
        page._feat_cancel = True
        _feat_log(ctx, page, "Stopping after the current file…")
        return
    if getattr(page, "_feat_dirty", False):
        reply = QMessageBox.question(
            ctx.host,
            "Close Feature",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Keep the log: it is part of the module's record, not a setting.
        log_lines = list(getattr(page, "_feat_log_lines", []) or [])
        page._feat_task_def["params"] = page._feat_snapshot
        page._feat_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)


FEATURE_METHODS = [
    ("Count", "count"),
    ("Sum", "sum"),
    ("Max", "max"),
    ("Min", "min"),
    ("Mean", "mean"),
    ("Median", "median"),
    ("Percentile", "percentile"),
]

FEATURE_METHOD_LABELS = {key: label for label, key in FEATURE_METHODS}

FEATURE_STATUS_PENDING = "Not calculated"

FEATURE_STATUS_DONE = "Calculated"
