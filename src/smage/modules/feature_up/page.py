"""Feature Up module: map aggregated per-class features onto cells."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
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
from smage.core.settings import _debug_log
from smage.data.image_index import _build_image_index_files
from smage.widgets.controls import _NoWheelComboBox
from smage.widgets.resources import _lock_pixmap
import copy
from smage.data import columns as _data_columns
from smage.data import feature_outputs as _data_feature_outputs
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import sampling as _data_sampling
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


def _fup_matched_from(ctx, params, to_path):
    """The From file currently matched to a To file, or None."""
    matches = params.get("matches") or {}
    for from_path, to in matches.items():
        if to == to_path:
            return from_path
    return None


def _build_feature_up_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Feature Up page: files/keys (1) | match (2) | features (2) | log."""
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
    close_btn.clicked.connect(lambda _=False: _fup_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("from_files", [])
    params.setdefault("to_files", [])
    params.setdefault("key_from", None)
    params.setdefault("key_to", None)
    params.setdefault("matches", {})
    params.setdefault("features", [])
    params.setdefault("outputs", {})
    params.setdefault("log", [])
    page._fup_task_def = task_def
    page._fup_task_id = task_def.get("id")
    page._fup_params = params
    page._fup_locked = locked
    page._fup_save_btn = save_btn
    page._fup_dirty = False
    page._fup_snapshot = copy.deepcopy(params)
    page._fup_log_lines = list(params.get("log") or [])
    page._fup_running = False
    page._fup_cancel = False
    save_btn.clicked.connect(lambda _=False: ctx._fup_save_now(page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_fup_files_panel(ctx, page))
    splitter.addWidget(_fup_match_panel(ctx, page))
    splitter.addWidget(_fup_features_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 2)
    splitter.setStretchFactor(2, 2)
    splitter.setSizes([240, 480, 480])
    row.addWidget(splitter, 1)

    row.addWidget(_fup_log_panel(ctx, page))
    layout.addWidget(content, 1)

    _fup_refresh_files(ctx, page)
    _fup_update_save_btn(ctx, page)
    return page


def _fup_log_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Log</b>"))
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setPlaceholderText("Mapping output appears here…")
    log.setStyleSheet(
        "background: #1e1e1e; color: #d4d4d4; "
        "font-family: Consolas, monospace; font-size: 9pt;"
    )
    log.setFixedHeight(120)
    for line in getattr(page, "_fup_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._fup_log = log
    return panel


def _fup_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_fup_log_lines", None)
    if lines is None:
        lines = []
        page._fup_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    if getattr(page, "_fup_params", None) is not None:
        page._fup_params["log"] = list(lines)
    box = getattr(page, "_fup_log", None)
    if box is not None:
        for line in str(text).splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")


def _fup_files_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>From Files</b>"))
    hint_from = QLabel("The new (aggregated) files whose channels are mapped across.")
    hint_from.setWordWrap(True)
    hint_from.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint_from)
    from_btn = QPushButton("Select From files…")
    from_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    from_btn.setEnabled(not page._fup_locked)
    lay.addWidget(from_btn)
    from_box = QListWidget()
    from_box.setFixedHeight(80)
    from_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(from_box)
    page._fup_from_box = from_box

    lay.addWidget(QLabel("<b>To Files</b>"))
    hint_to = QLabel("The old (target) files that will receive the new channels.")
    hint_to.setWordWrap(True)
    hint_to.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint_to)
    to_btn = QPushButton("Select To files…")
    to_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    to_btn.setEnabled(not page._fup_locked)
    lay.addWidget(to_btn)
    to_box = QListWidget()
    to_box.setFixedHeight(80)
    to_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(to_box)
    page._fup_to_box = to_box

    lay.addWidget(QLabel("<b>Key in From Files</b>"))
    key_from = _NoWheelComboBox()
    key_from.setEnabled(not page._fup_locked)
    lay.addWidget(key_from)
    page._fup_key_from = key_from

    lay.addWidget(QLabel("<b>Key in To Files</b>"))
    key_to = _NoWheelComboBox()
    key_to.setEnabled(not page._fup_locked)
    lay.addWidget(key_to)
    page._fup_key_to = key_to

    hint_key = QLabel(
        "The two key columns identify matching rows (usually the same name)."
    )
    hint_key.setWordWrap(True)
    hint_key.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint_key)

    match_btn = QPushButton("Match")
    match_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    match_btn.setEnabled(not page._fup_locked)
    lay.addWidget(match_btn)
    page._fup_match_btn = match_btn

    lay.addStretch()

    from_btn.clicked.connect(lambda _=False: _fup_select_files(ctx, page, "from"))
    to_btn.clicked.connect(lambda _=False: _fup_select_files(ctx, page, "to"))
    match_btn.clicked.connect(lambda _=False: _fup_match(ctx, page))
    key_from.currentIndexChanged.connect(lambda _=0: _fup_on_key(ctx, page, "from"))
    key_to.currentIndexChanged.connect(lambda _=0: _fup_on_key(ctx, page, "to"))
    return panel


def _fup_match_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Match</b>"))
    table = QTableWidget()
    table.setColumnCount(6)
    table.setHorizontalHeaderLabels([
        "From File", "To File", "From keys", "To keys", "From only", "To only",
    ])
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    for c in (2, 3, 4, 5):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    page._fup_match_table = table
    hint = QLabel(
        "From/To keys = distinct key values in each file. "
        "From only / To only = values present in one file but missing in the "
        "other (ideally both 0)."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    return panel


def _fup_features_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Channels to map</b>"))
    select_btn = QPushButton("Select channels…")
    select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    select_btn.setEnabled(not page._fup_locked)
    lay.addWidget(select_btn)

    table = QTableWidget()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(
        ["Channel in From File", "New name in To Files"]
    )
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    page._fup_features_table = table

    btns = QHBoxLayout()
    del_btn = QPushButton("Delete")
    del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    del_btn.setEnabled(not page._fup_locked)
    btns.addWidget(del_btn)
    btns.addStretch()
    lay.addLayout(btns)

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(not page._fup_locked)
    lay.addWidget(calc_btn)
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._fup_calc_btn = calc_btn
    page._fup_calc_status = status

    select_btn.clicked.connect(lambda _=False: _fup_select_features(ctx, page))
    del_btn.clicked.connect(lambda _=False: _fup_delete_feature(ctx, page))
    calc_btn.clicked.connect(lambda _=False: _fup_calculate(ctx, page))
    return panel


def _fup_refresh_files(ctx, page) -> None:
    from_box = getattr(page, "_fup_from_box", None)
    to_box = getattr(page, "_fup_to_box", None)
    params = page._fup_params
    if from_box is not None:
        from_box.blockSignals(True)
        from_box.clear()
        for p in params.get("from_files") or []:
            from_box.addItem(_data_naming._comp_file_name(ctx, p, page._fup_task_id))
        from_box.blockSignals(False)
    if to_box is not None:
        to_box.blockSignals(True)
        to_box.clear()
        for p in params.get("to_files") or []:
            to_box.addItem(_data_naming._comp_file_name(ctx, p, page._fup_task_id))
        to_box.blockSignals(False)
    _fup_refresh_key_combos(ctx, page)
    _fup_rebuild_matches(ctx, page)
    _fup_rebuild_features(ctx, page)


def _fup_common_columns(ctx, page, files_key) -> list:
    """Columns shared by every file selected under `files_key`."""
    paths = list(page._fup_params.get(files_key) or [])
    if not paths:
        return []
    common = set(_data_columns._columns_for(ctx, paths[0], page._fup_task_id))
    for p in paths[1:]:
        common &= set(_data_columns._columns_for(ctx, p, page._fup_task_id))
    return sorted(common)


def _fup_refresh_key_combos(ctx, page) -> None:
    params = page._fup_params
    for which, combo_attr, key_attr, files_key in (
        ("from", "_fup_key_from", "key_from", "from_files"),
        ("to", "_fup_key_to", "key_to", "to_files"),
    ):
        combo = getattr(page, combo_attr, None)
        if combo is None:
            continue
        cols = _fup_common_columns(ctx, page, files_key)
        cur = params.get(key_attr)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("", None)
        for c in cols:
            combo.addItem(c, c)
        idx = combo.findData(cur)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)


def _fup_on_key(ctx, page, which) -> None:
    params = page._fup_params
    combo = getattr(page, f"_fup_key_{which}", None)
    if combo is None:
        return
    params[f"key_{which}"] = combo.currentData()
    _fup_mark_dirty(ctx, page)
    _fup_rebuild_matches(ctx, page)


def _fup_select_files(ctx, page, which) -> None:
    records = _data_files._available_files(ctx, page._fup_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    key = "from_files" if which == "from" else "to_files"
    current = [_data_naming._comp_file_name(ctx, p, page._fup_task_id) for p in page._fup_params.get(key) or []]
    title = "Select From files" if which == "from" else "Select To files"
    chosen = _widgets_picker._pick_items_dialog(ctx, title, names, current, "Filter files...")
    if chosen is None:
        return
    page._fup_params[key] = [by_name[n] for n in chosen if n in by_name]
    _fup_refresh_files(ctx, page)
    _fup_mark_dirty(ctx, page)


def _fup_select_features(ctx, page) -> None:
    from_cols = _fup_common_columns(ctx, page, "from_files")
    if not from_cols:
        QMessageBox.information(ctx.host, "Channels", "Select From files first.")
        return
    key_from = page._fup_params.get("key_from")
    options = [c for c in from_cols if c != key_from]
    existing = [f.get("source") for f in page._fup_params.get("features") or []]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select channels", options, existing, "Filter channels..."
    )
    if chosen is None:
        return
    kept = {
        f.get("source"): f for f in page._fup_params.get("features") or []
        if f.get("source") in chosen
    }
    features = []
    for source in chosen:
        if source in kept:
            features.append(kept[source])
        else:
            features.append({"source": source, "name": source})
    page._fup_params["features"] = features
    _fup_rebuild_features(ctx, page)
    _fup_mark_dirty(ctx, page)


def _fup_delete_feature(ctx, page) -> None:
    table = page._fup_features_table
    row = table.currentRow()
    features = page._fup_params.get("features") or []
    if 0 <= row < len(features):
        del features[row]
        _fup_rebuild_features(ctx, page)
        _fup_mark_dirty(ctx, page)


def _fup_rebuild_features(ctx, page) -> None:
    table = getattr(page, "_fup_features_table", None)
    if table is None:
        return
    features = page._fup_params.get("features") or []
    enabled = not page._fup_locked
    dup = _fup_duplicate_names(ctx, page)
    table.blockSignals(True)
    table.setRowCount(0)
    table.setRowCount(len(features))
    for i, feat in enumerate(features):
        src_item = QTableWidgetItem(feat.get("source") or "")
        src_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(i, 0, src_item)

        name_box = QWidget()
        nh = QHBoxLayout(name_box)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(4)
        name_edit = QLineEdit(str(feat.get("name") or ""))
        name_edit.setEnabled(enabled)
        warn = QLabel("⚠")
        warn.setToolTip("Duplicate channel name")
        warn.setStyleSheet("color: #b58900; font-weight: bold;")

        def on_name(text, f=feat, warn_label=warn):
            f["name"] = text.strip()
            warn_label.setVisible(
                (f.get("name") or "").strip() in _fup_duplicate_names(ctx, page)
            )
            _fup_mark_dirty(ctx, page)

        name_edit.textEdited.connect(lambda t, cb=on_name: cb(t))
        nh.addWidget(name_edit, 1)
        nh.addWidget(warn)
        table.setCellWidget(i, 1, name_box)
        warn.setVisible((feat.get("name") or "").strip() in dup)
    table.blockSignals(False)


def _fup_existing_columns(ctx, page) -> set:
    cols = set()
    for path in page._fup_params.get("to_files") or []:
        cols |= set(_data_columns._columns_for(ctx, path, page._fup_task_id))
    return cols


def _fup_duplicate_names(ctx, page) -> set:
    existing = _fup_existing_columns(ctx, page)
    dup = set()
    seen = set()
    for feat in page._fup_params.get("features") or []:
        name = (feat.get("name") or "").strip()
        if not name:
            continue
        if name in existing or name in seen:
            dup.add(name)
        seen.add(name)
    return dup


def _fup_display_core(ctx, path, task_id) -> str:
    """Display name without a real file extension (``.tif``, ``.fcs``).

    ``Path.stem`` is not usable here: ``raw.ome_Mask`` would lose
    ``.ome_Mask`` (it looks like a suffix but is part of the sample name).
    Only strip a short alphanumeric extension.
    """
    name = _data_naming._comp_file_name(ctx, path, task_id)
    suffix = Path(name).suffix
    if suffix and 1 < len(suffix) <= 6 and suffix[1:].isalnum():
        return name[: -len(suffix)]
    return name


def _fup_default_to(ctx, page, from_path):
    """Default pairing: the To file whose display name is the longest prefix.

    Imported files are stored as ``data/<hash>.parquet``, so the stored file
    stem says nothing about the sample. Match on the user-visible names
    instead (``cells.tif`` for a To file, ``cells_Mask`` for a From file).
    """
    from_core = _fup_display_core(ctx, from_path, page._fup_task_id)
    best = None
    best_len = -1
    for to_path in page._fup_params.get("to_files") or []:
        if str(to_path) == str(from_path):
            continue
        to_core = _fup_display_core(ctx, to_path, page._fup_task_id)
        if (
            to_core
            and from_core.startswith(to_core)
            and len(to_core) > best_len
        ):
            best = to_path
            best_len = len(to_core)
    return best


def _fup_match(ctx, page) -> None:
    params = page._fup_params
    if not params.get("from_files"):
        QMessageBox.warning(ctx.host, "Match", "Select at least one From file.")
        return
    if not params.get("to_files"):
        QMessageBox.warning(ctx.host, "Match", "Select at least one To file.")
        return
    if not params.get("key_from") or not params.get("key_to"):
        QMessageBox.warning(
            ctx.host, "Match", "Choose a key column for both From and To files."
        )
        return
    if params.get("matches"):
        reply = QMessageBox.question(
            ctx.host, "Match",
            "Re-matching will overwrite the current file pairing.\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    from_files = params.get("from_files") or []
    matches = {}
    for from_path in from_files:
        to_path = _fup_default_to(ctx, page, from_path)
        if to_path is not None:
            matches[from_path] = to_path
    params["matches"] = matches
    _fup_rebuild_matches(ctx, page)
    _fup_mark_dirty(ctx, page)
    if matches:
        _fup_log(
            ctx, page,
            f"Matched {len(matches)} of {len(from_files)} From file(s).",
        )
    else:
        message = (
            "No From file name starts with a To file name.\n\n"
            "Choose the To file for each row in the Match table, or rename "
            "the files so the matching names share a prefix."
        )
        _fup_log(ctx, page, "No automatic match found.")
        QMessageBox.information(ctx.host, "Match", message)


def _fup_key_values(ctx, task_id, parquet_path, key):
    """Identifier column values for matching, in their stored dtype.

    Prefers the pipeline view (renames, virtual columns), then falls back
    to the stored parquet column when the pipeline cannot expose it.
    """
    if not parquet_path or not key:
        return None
    try:
        values = _data_columns._column_data(ctx, task_id, parquet_path, [key], numeric=False)
        if values.get(key) is not None:
            return values[key]
    except Exception as exc:  # noqa: BLE001
        _debug_log(
            f"feature up: column_data failed for {key!r} in {parquet_path}: {exc!r}"
        )
    try:
        import pandas as pd

        frame = pd.read_parquet(parquet_path, columns=[key])
        return frame[key].to_numpy()
    except Exception as exc:  # noqa: BLE001
        _debug_log(
            f"feature up: key {key!r} unreadable in {parquet_path}: {exc!r}"
        )
    return None


def _fup_fill_match_stats(ctx, page, row, from_path, to_path) -> None:
    import pandas as pd

    table = getattr(page, "_fup_match_table", None)
    if table is None:
        return
    for col in range(2, 6):
        table.setItem(row, col, QTableWidgetItem(""))
    params = page._fup_params
    key_from = params.get("key_from")
    key_to = params.get("key_to")
    if not from_path or not to_path or not key_from or not key_to:
        return

    def unavailable(missing, key, reason=None):
        for col in (2, 3):
            item = QTableWidgetItem("—")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(
                reason
                or f"Column '{key}' could not be read from the {missing} file"
            )
            table.setItem(row, col, item)

    for side, path in (("From", from_path), ("To", to_path)):
        if not Path(str(path)).is_file():
            unavailable(
                side, "",
                f"The {side} file is not on disk:\n{path}\n\n"
                "Re-run the module that produced it, or select another file.",
            )
            return

    try:
        fv = _fup_key_values(ctx, page._fup_task_id, from_path, key_from)
        if fv is None:
            unavailable("From", key_from)
            return
        tv = _fup_key_values(ctx, page._fup_task_id, to_path, key_to)
        if tv is None:
            unavailable("To", key_to)
            return
        fv = pd.Series(fv).dropna()
        tv = pd.Series(tv).dropna()
        fu = fv.nunique()
        tu = tv.nunique()
        fset = set(fv.unique().tolist())
        tset = set(tv.unique().tolist())
        fonly = len(fset - tset)
        tonly = len(tset - fset)
        items = {
            2: (str(fu), False),
            3: (str(tu), False),
            4: (str(fonly), fonly > 0),
            5: (str(tonly), tonly > 0),
        }
        for col, (text, bad) in items.items():
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if bad:
                item.setForeground(QColor("#c0392b"))
            table.setItem(row, col, item)
    except Exception as exc:  # noqa: BLE001
        _debug_log(f"feature up match stats failed: {exc!r}")
        unavailable("selected", key_from)


def _fup_rebuild_match_row(ctx, page, row) -> None:
    table = getattr(page, "_fup_match_table", None)
    if table is None:
        return
    from_files = page._fup_params.get("from_files") or []
    from_path = from_files[row] if row < len(from_files) else None
    combo = table.cellWidget(row, 1)
    to_path = combo.currentData() if combo is not None else None
    _fup_fill_match_stats(ctx, page, row, from_path, to_path)


def _fup_rebuild_matches(ctx, page) -> None:
    table = getattr(page, "_fup_match_table", None)
    if table is None:
        return
    params = page._fup_params
    from_files = params.get("from_files") or []
    to_files = params.get("to_files") or []
    enabled = not page._fup_locked
    table.blockSignals(True)
    table.setRowCount(0)
    table.setRowCount(len(from_files))
    for i, from_path in enumerate(from_files):
        from_missing = not Path(str(from_path)).is_file()
        text = _data_naming._comp_file_name(ctx, from_path, page._fup_task_id)
        if from_missing:
            text += "  (missing)"
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if from_missing:
            item.setForeground(QColor("#c0392b"))
            item.setToolTip(
                "This file is no longer on disk — re-run the module that "
                "created it, or pick another From file."
            )
        table.setItem(i, 0, item)

        combo = _NoWheelComboBox()
        combo.addItem("", None)
        for to_path in to_files:
            label = _data_naming._comp_file_name(ctx, to_path, page._fup_task_id)
            if not Path(str(to_path)).is_file():
                label += "  (missing)"
            combo.addItem(label, to_path)
        cur = (params.get("matches") or {}).get(from_path)
        idx = combo.findData(cur)
        combo.setCurrentIndex(max(0, idx))
        combo.setEnabled(enabled)

        def on_to(value, f=from_path, ri=i):
            matches = params.setdefault("matches", {})
            if value:
                matches[f] = value
            else:
                matches.pop(f, None)
            _fup_rebuild_match_row(ctx, page, ri)
            _fup_mark_dirty(ctx, page)

        combo.currentIndexChanged.connect(
            lambda _=0, c=combo, cb=on_to: cb(c.currentData())
        )
        table.setCellWidget(i, 1, combo)
        _fup_fill_match_stats(ctx, page, i, from_path, cur)
    table.blockSignals(False)


def _fup_validate(ctx, page):
    params = page._fup_params
    if not params.get("from_files"):
        return False, "Select at least one From file."
    if not params.get("to_files"):
        return False, "Select at least one To file."
    if not params.get("key_from") or not params.get("key_to"):
        return False, "Choose a key column for both From and To files."
    features = params.get("features") or []
    if not features:
        return False, "Select at least one channel to map."
    from_common = set(_fup_common_columns(ctx, page, "from_files"))
    to_common = set(_fup_common_columns(ctx, page, "to_files"))
    if params.get("key_from") not in from_common:
        return False, f"Key '{params['key_from']}' is not in every From file."
    if params.get("key_to") not in to_common:
        return False, f"Key '{params['key_to']}' is not in every To file."
    for feat in features:
        if feat.get("source") not in from_common:
            return False, f"Channel '{feat.get('source')}' is not in every From file."
    names = [(feat.get("name") or "").strip() for feat in features]
    if any(not n for n in names):
        return False, "Give every mapped channel a new column name."
    if len(names) != len(set(names)):
        return False, "New column names must be unique."
    existing = _fup_existing_columns(ctx, page)
    if any(n in existing for n in names):
        return False, "A new column name collides with an existing To-file column."
    return True, ""


def _fup_calculate(ctx, page) -> None:
    ok, message = _fup_validate(ctx, page)
    if not ok:
        QMessageBox.warning(ctx.host, "Calculate", message)
        return
    params = page._fup_params
    matches = params.get("matches") or {}
    from_files = params.get("from_files") or []
    unmatched = [f for f in from_files if not matches.get(f)]
    if unmatched:
        names = ", ".join(_data_naming._comp_file_name(ctx, f, page._fup_task_id) for f in unmatched)
        reply = QMessageBox.question(
            ctx.host, "Calculate",
            f"{len(unmatched)} From file(s) have no matched To file and "
            f"will not be processed:\n{names}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
    outputs = params.get("outputs") or {}
    to_targets = list(dict.fromkeys(matches.values()))
    existing = [t for t in to_targets if isinstance(outputs.get(t), dict)]
    if existing:
        reply = QMessageBox.question(
            ctx.host, "Calculate",
            f"{len(existing)} file(s) already have results.\n"
            "Recalculate and overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    status = getattr(page, "_fup_calc_status", None)
    if status is not None:
        status.setText("Calculating…")
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    done = 0
    failed = []
    skipped = len(unmatched)
    page._fup_running = True
    page._fup_cancel = False
    try:
        for to_path in to_targets:
            if getattr(page, "_fup_cancel", False):
                _fup_log(ctx, page, "Calculation cancelled.")
                break
            name = _data_naming._comp_file_name(ctx, to_path, page._fup_task_id)
            if status is not None:
                status.setText(f"Calculating… {name}")
            QApplication.processEvents()
            try:
                rel, columns, kind = _fup_compute_file(ctx, page, to_path)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}: {exc}")
                _fup_log(ctx, page, f"✗ {name}: {exc}")
                continue
            outputs[to_path] = {
                "path": rel,
                "columns": columns,
                "kind": kind,
                "signature": _data_feature_outputs._feature_up_signature(ctx,
                    params, to_path, page._fup_task_id
                ),
            }
            done += 1
            # The stored output name starts with the To file's hash; show the
            # name the user knows instead.
            out_name = f"{Path(name).stem}_mapped.parquet"
            _fup_log(ctx,
                page,
                f"✓ {name} → {out_name} ({len(columns)} channel(s))",
            )
    finally:
        QApplication.restoreOverrideCursor()
        page._fup_running = False

    for to_path in list(outputs.keys()):
        if to_path not in to_targets:
            outputs.pop(to_path, None)
    params["outputs"] = outputs
    page._fup_task_def["output"] = {"saved": bool(outputs)}
    page._fup_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._fup_snapshot = copy.deepcopy(params)
    page._fup_dirty = False
    _fup_update_save_btn(ctx, page)
    _fup_rebuild_matches(ctx, page)

    summary = f"Calculated {done} file(s)."
    if skipped:
        summary += f" {skipped} skipped (no match)."
    if failed:
        summary += f" {len(failed)} failed — see the log."
    if status is not None:
        status.setText(summary)
    _fup_log(ctx, page, summary)
    ctx.statusBar().showMessage(summary)
    ctx._module_saved(page)


def _fup_compute_file(ctx, page, to_path):
    """Map features from the matched From file onto `to_path`; return (rel, columns, kind)."""
    import numpy as np
    import pandas as pd

    params = page._fup_params
    from_path = _fup_matched_from(ctx, params, to_path)
    if not from_path:
        raise ValueError("no matched From file")
    key_from = params["key_from"]
    key_to = params["key_to"]
    features = params.get("features") or []
    sources = [f["source"] for f in features]
    names = [(f.get("name") or f["source"]).strip() for f in features]

    # Build the From lookup table (key -> feature values); the first row
    # wins when a key value repeats. Keys are read as stored (text keys
    # must not be coerced to NaN); feature values are numeric.
    from_keys = _fup_key_values(ctx, page._fup_task_id, from_path, key_from)
    if from_keys is None:
        raise ValueError(f"'{key_from}' is missing from the From file")
    fv = _data_columns._column_data(ctx, page._fup_task_id, from_path, sources)
    from_keys = pd.Series(from_keys).astype("object")
    tmp = [f"__fup_src_{j}" for j in range(len(sources))]
    lf = pd.DataFrame({
        "__fup_key": from_keys,
        **{tmp[j]: fv[s] for j, s in enumerate(sources)},
    })
    lf = lf[lf["__fup_key"].notna()]
    dup = int(len(lf) - lf.drop_duplicates(subset="__fup_key", keep="first").shape[0])
    if dup:
        _fup_log(ctx,
            page,
            f"{_data_naming._comp_file_name(ctx, from_path, page._fup_task_id)}: {dup} duplicate key value(s) "
            f"— kept the first row.",
        )
    lf = lf.drop_duplicates(subset="__fup_key", keep="first")

    tv = _fup_key_values(ctx, page._fup_task_id, to_path, key_to)
    if tv is None:
        raise ValueError(f"'{key_to}' is missing from the To file")
    merged = pd.DataFrame({"__fup_key": pd.Series(tv).astype("object")}).merge(
        lf, on="__fup_key", how="left"
    )
    row_vals = {}
    for j, name in enumerate(names):
        row_vals[name] = pd.to_numeric(
            merged[tmp[j]], errors="coerce"
        ).to_numpy(dtype="float64")

    # The To file may be sampled upstream. The mapped file stays row-aligned
    # to the whole file (downstream readers expect that), so the sampled
    # rows are scattered back and the rest stays empty — those rows are
    # never read below the Subsample module.
    n_full = _data_columns._data_row_count(ctx, to_path)
    index = _data_sampling._subsample_row_index(ctx, page._fup_task_id, to_path)
    if index is not None and row_vals:
        for name in list(row_vals):
            values = row_vals[name]
            if len(values) == len(index):
                full = np.full(n_full, np.nan, dtype="float64")
                full[index] = values[: len(index)]
                row_vals[name] = full

    ent = _data_image_index._ensure_image_index(ctx, to_path)
    is_image = ent is not None and ent.get("manifest") is not None
    stem = Path(str(to_path)).stem
    rel = _data_files._module_rel(ctx, page._fup_task_id, f"{stem}_mapped.parquet")
    path = ctx.project.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)

    if is_image:
        man = ent["manifest"]
        xmin = int(round(float(man["xmin"])))
        ymin = int(round(float(man["ymin"])))
        width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
        height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
        src = pd.read_parquet(to_path, columns=["x", "y"])
        xi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        yi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        ok = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        flat = yi[ok] * width + xi[ok]
        xx = np.tile(np.arange(width, dtype=np.int32), height) + xmin
        yy = np.repeat(np.arange(height, dtype=np.int32), width) + ymin
        data = {"x": xx, "y": yy}
        for name in names:
            grid = np.full(height * width, np.nan, dtype="float64")
            grid[flat] = row_vals[name][ok]
            data[name] = grid
        pd.DataFrame(data).to_parquet(path, index=False)
        _build_image_index_files(str(path))
        kind = "image"
    else:
        pd.DataFrame(row_vals).to_parquet(path, index=False)
        kind = "table"
    return rel, names, kind


def _fup_mark_dirty(ctx, page) -> None:
    page._fup_dirty = True
    _fup_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _fup_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_fup_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._fup_dirty and not page._fup_locked)


def _fup_save_now(ctx, page) -> None:
    params = page._fup_params
    page._fup_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._fup_snapshot = copy.deepcopy(params)
    page._fup_dirty = False
    _fup_update_save_btn(ctx, page)
    _fup_log(ctx, page, "Settings saved")
    ctx.statusBar().showMessage("Feature Up settings saved")
    ctx._module_saved(page)


def _fup_close(ctx, page) -> None:
    if getattr(page, "_fup_running", False):
        reply = QMessageBox.question(
            ctx.host, "Close Feature Up",
            "A calculation is still running.\nStop it and close the module?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._fup_cancel = True
        _fup_log(ctx, page, "Stopping after the current file…")
        return
    if getattr(page, "_fup_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Feature Up",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log_lines = list(getattr(page, "_fup_log_lines", []) or [])
        page._fup_task_def["params"] = page._fup_snapshot
        page._fup_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)
