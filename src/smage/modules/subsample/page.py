"""Subsample page: allocation, per-file overrides and coverage masks."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.image_ops import _pool_mask
from smage.core.sampling import _subsample_allocate
from smage.widgets.controls import (
    _NoWheelComboBox, _NoWheelDoubleSpinBox,
)
from smage.widgets.resources import _lock_pixmap
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import sampling as _data_sampling
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.runlog import runlog


_SUBS_DEFAULT_COUNT = 10000
_SUBS_DEFAULT_PERCENT = 10.0


def _subsample_saved_mask(ctx, task, parquet_path, manifest):
    """Load a module's saved coverage mask, or None when absent/stale."""
    import numpy as np

    npy, meta_path = _data_sampling._subsample_coverage_paths(ctx, task, parquet_path)
    if npy is None or not npy.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta != _data_sampling._subsample_mask_meta(ctx, task, parquet_path, manifest):
        return None
    try:
        mask = np.load(npy)
    except (OSError, ValueError):
        return None
    return mask if mask.ndim == 2 else None


def _subsample_write_masks(ctx, page) -> int:
    """Persist this module's image coverage masks into its own folder.

    Called on Save, so the masks live with the module (and are removed
    with it). Returns the number of masks written.
    """
    import numpy as np

    task = page._subs_task_def
    folder = _data_sampling._subsample_coverage_dir(ctx, task.get("id"))
    if folder is None:
        return 0
    wanted = set()
    written = 0
    for path in page._subs_params.get("files") or []:
        ent = _data_image_index._ensure_image_index(ctx, path)
        if ent is None or ent.get("manifest") is None:
            continue  # table data needs no raster coverage mask
        manifest = ent["manifest"]
        mask = _data_sampling._subsample_own_mask(ctx, task, path, manifest)
        npy, meta_path = _data_sampling._subsample_coverage_paths(ctx, task, path)
        if npy is None:
            continue
        wanted.add(npy.name)
        wanted.add(meta_path.name)
        if mask is None:
            # Nothing hidden by this module: drop any stale mask.
            for p in (npy, meta_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            continue
        try:
            folder.mkdir(parents=True, exist_ok=True)
            np.save(npy, mask)
            meta_path.write_text(
                json.dumps(_data_sampling._subsample_mask_meta(ctx, task, path, manifest)),
                encoding="utf-8",
            )
            written += 1
        except OSError:
            continue
    # Remove masks for files that are no longer selected.
    try:
        for f in folder.iterdir():
            if f.is_file() and f.name not in wanted:
                f.unlink(missing_ok=True)
    except OSError:
        pass
    ctx._subsample_mask_cache = {}
    return written


def _subsample_module_mask(ctx, task, parquet_path, manifest):
    """This module's coverage mask: the saved one, else computed now."""
    cache = getattr(ctx, "_subsample_mask_cache", None)
    if cache is None:
        cache = {}
        ctx._subsample_mask_cache = cache
    key = (task.get("id"), str(parquet_path),
           _data_sampling._subsample_sig(ctx, task.get("id"), parquet_path))
    if key in cache:
        return cache[key]
    mask = _subsample_saved_mask(ctx, task, parquet_path, manifest)
    if mask is None:
        mask = _data_sampling._subsample_own_mask(ctx, task, parquet_path, manifest)
    if len(cache) > 8:
        cache.clear()
    cache[key] = mask
    return mask


def _subsample_pixel_mask(ctx, task_id, parquet_path, manifest):
    """Dense (H, W) mask of the pixels every upstream Subsample keeps.

    Each module stores its own mask in its folder; the visible set is the
    intersection of them (a row kept by every module on the way down).
    """
    import numpy as np

    masks = []
    for task in _data_sampling._subsample_tasks(ctx, task_id):
        mask = _subsample_module_mask(ctx, task, parquet_path, manifest)
        if mask is not None:
            masks.append(mask)
    if not masks:
        return None
    out = masks[0]
    for mask in masks[1:]:
        if mask.shape != out.shape:
            continue
        out = np.logical_and(out, mask).astype(np.uint8)
    return out


def _subsample_cover_ent(ctx, task_id, parquet_path, manifest):
    """Block-level "fraction of sampled pixels" entry for the image renderer.

    Same shape as the gate-filter mask entry, so the image renderer can
    crop it with `_image_mask_crop`; the renderer keeps a block opaque
    only when that fraction is above one half.
    """
    mask = _subsample_pixel_mask(ctx, task_id, parquet_path, manifest)
    if mask is None:
        return None
    blocks = [info["block"] for info in manifest.get("levels", [])]
    levels = _pool_mask(mask, blocks)
    if not levels:
        return None
    # `mask_2d` serves the deep-zoom path, where the renderer reads raw
    # pixels instead of a pyramid level.
    return {"levels": levels, "mask_2d": mask}


def _build_subsample_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Subsample page: settings (1) | per-file table (1)."""
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
    close_btn.clicked.connect(lambda _=False: _subs_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("files", [])
    params.setdefault("basis", "")
    params.setdefault("unit", "points")
    params.setdefault("count", None)
    params.setdefault("percent", None)
    params.setdefault("allocation", "uniform")
    params.setdefault("overrides", {})
    params.setdefault("gate_filters", [])
    page._subs_task_def = task_def
    page._subs_task_id = task_def.get("id")
    page._subs_params = params
    page._subs_locked = locked
    page._subs_save_btn = save_btn
    page._subs_dirty = False
    page._subs_snapshot = copy.deepcopy(params)
    page._subs_updating = False
    # Gate counts are computed one file per timer tick so a large selection
    # never blocks the UI; the cache holds (signature, count) per file.
    page._subs_original_sizes = {}
    page._subs_count_cache = {}
    page._subs_count_pending = []
    page._subs_gate_ready = True
    page._subs_gate_total = 0
    page._subs_gate_done_n = 0
    page._subs_count_generation = 0
    page._subs_options_cache = None
    gate_timer = QTimer(page)
    gate_timer.setInterval(10)
    gate_timer.timeout.connect(lambda: _subs_counts_tick(ctx, page))
    page._subs_counts_timer = gate_timer
    save_btn.clicked.connect(lambda _=False: _subs_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    settings_scroll = QScrollArea()
    settings_scroll.setWidgetResizable(True)
    settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
    # Scoped selector: a bare transparent rule also reaches QToolTip and
    # turns its palette black.
    settings_scroll.setObjectName("subsSettingsScroll")
    settings_scroll.setStyleSheet(
        "QScrollArea#subsSettingsScroll { background: transparent; }"
    )
    settings_scroll.setWidget(_subs_settings_panel(ctx, page))
    splitter.addWidget(settings_scroll)
    splitter.addWidget(_subs_table_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([400, 520])
    row.addWidget(splitter, 1)
    layout.addWidget(content, 1)

    _subs_refresh(ctx, page)
    return page


def _subs_settings_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)

    lay.addWidget(QLabel("<b>Data files</b>"))
    hint = QLabel(
        "Selected files are sampled; the others pass through untouched. "
        "Sampling only changes what downstream modules see — no new data "
        "is written."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(not page._subs_locked)
    lay.addWidget(files_btn)
    files_box = QListWidget()
    files_box.setFixedHeight(110)
    files_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(files_box)
    page._subs_files_box = files_box

    lay.addSpacing(8)
    lay.addWidget(QLabel("<b>Filter</b>"))
    filter_hint = QLabel(
        "Sample only inside the selected gates. Multiple gates are combined "
        "with OR; only gates available for every selected file are listed."
    )
    filter_hint.setWordWrap(True)
    filter_hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(filter_hint)
    filter_row = QHBoxLayout()
    filter_row.setSpacing(4)
    gates_btn = QPushButton("Select gates…")
    gates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    gates_btn.setEnabled(False)
    clear_gates_btn = QPushButton("Clear")
    clear_gates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    clear_gates_btn.setEnabled(False)
    filter_row.addWidget(gates_btn)
    filter_row.addWidget(clear_gates_btn)
    filter_row.addStretch()
    lay.addLayout(filter_row)
    gates_box = QListWidget()
    gates_box.setFixedHeight(90)
    gates_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(gates_box)
    filter_warning = QLabel("")
    filter_warning.setWordWrap(True)
    filter_warning.setStyleSheet("color: #b58900;")
    filter_warning.setVisible(False)
    lay.addWidget(filter_warning)
    gate_progress = QLabel("")
    gate_progress.setWordWrap(True)
    gate_progress.setStyleSheet("color: #6b7785;")
    gate_progress.setVisible(False)
    lay.addWidget(gate_progress)
    page._subs_gates_btn = gates_btn
    page._subs_clear_gates_btn = clear_gates_btn
    page._subs_gates_box = gates_box
    page._subs_filter_warning = filter_warning
    page._subs_gate_progress = gate_progress

    lay.addSpacing(8)
    lay.addWidget(QLabel("<b>Sampling method</b>"))
    basis = _NoWheelComboBox()
    basis.addItem("", None)
    basis.addItem("Per file", "per_file")
    basis.addItem("Total", "total")
    basis.setEnabled(not page._subs_locked)
    lay.addWidget(basis)
    page._subs_basis = basis

    unit_label = QLabel("Count by")
    lay.addWidget(unit_label)
    page._subs_unit_label = unit_label
    unit = _NoWheelComboBox()
    unit.addItem("Points per file", "points")
    unit.addItem("Percentage per file", "percent")
    unit.setEnabled(False)
    lay.addWidget(unit)
    page._subs_unit = unit

    count_label = QLabel("Points per file")
    lay.addWidget(count_label)
    page._subs_count_label = count_label
    count_box = _NoWheelDoubleSpinBox()
    count_box.setDecimals(0)
    count_box.setRange(1, 2_000_000_000)
    count_box.setValue(_SUBS_DEFAULT_COUNT)
    count_box.setEnabled(False)
    lay.addWidget(count_box)
    page._subs_count = count_box

    alloc_label = QLabel("Allocation")
    lay.addWidget(alloc_label)
    page._subs_alloc_label = alloc_label
    alloc = _NoWheelComboBox()
    alloc.addItem("Equal (same count per file)", "uniform")
    alloc.addItem("Weighted by file size", "weighted")
    alloc.setEnabled(False)
    lay.addWidget(alloc)
    page._subs_alloc = alloc

    lay.addStretch()

    files_btn.clicked.connect(lambda _=False: _subs_select_files(ctx, page))
    gates_btn.clicked.connect(lambda _=False: _subs_select_gates(ctx, page))
    clear_gates_btn.clicked.connect(lambda _=False: _subs_clear_gates(ctx, page))
    basis.currentIndexChanged.connect(lambda _=0: _subs_on_basis(ctx, page))
    unit.currentIndexChanged.connect(lambda _=0: _subs_on_unit(ctx, page))
    count_box.valueChanged.connect(lambda _=0: _subs_on_value(ctx, page))
    alloc.currentIndexChanged.connect(lambda _=0: _subs_on_alloc(ctx, page))
    return panel


def _subs_table_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Sampling</b>"))
    hint = QLabel(
        "The sampled count is editable — type a value to override the "
        "computed one for that file. In gates is the number of points "
        "inside the selected gates."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    table = QTableWidget()
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(
        ["File", "Original points", "In gates", "Sampled points"]
    )
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setStyleSheet("QTableWidget { background: #ffffff; border: none; }")
    lay.addWidget(table, 1)
    page._subs_table = table
    override_hint = QLabel("* Sampled counts were adjusted manually")
    override_hint.setStyleSheet("color: #b58900;")
    override_hint.setVisible(False)
    lay.addWidget(override_hint)
    page._subs_override_hint = override_hint
    table.itemChanged.connect(lambda item: _subs_on_cell(ctx, page, item))
    return panel


def _subs_visible_sizes(ctx, page) -> dict:
    return {
        path: _data_sampling._subsample_visible_count(ctx, page._subs_task_id, path)
        for path in (page._subs_params.get("files") or [])
    }


def _subs_gate_filters(page) -> list:
    return _data_sampling._subsample_gate_filters(page._subs_params)


def _subs_filter_options(ctx, page) -> list:
    """Common gates for the selected files (cached until the chain changes)."""
    files = tuple(str(p) for p in page._subs_params.get("files") or [])
    chain = tuple(
        task.get("id") for task in ctx._task_chain(page._subs_task_id)
        if task.get("type") == "gating"
    )
    key = (files, chain, getattr(ctx, "_stages_revision", 0))
    cached = getattr(page, "_subs_options_cache", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    options = _data_sampling._subsample_filter_options(
        ctx, page._subs_task_id, files
    )
    page._subs_options_cache = (key, options)
    return options


def _subs_validate_filters(ctx, page) -> None:
    """Drop gates that are no longer available for every selected file."""
    filters = _subs_gate_filters(page)
    warning = ""
    if filters:
        valid = {
            (entry.get("task_id"), entry.get("gate_id"))
            for _label, entry in _subs_filter_options(ctx, page)
        }
        kept = []
        seen = set()
        for entry in filters:
            key = (entry.get("task_id"), entry.get("gate_id"))
            if key in valid and key not in seen:
                seen.add(key)
                kept.append(entry)
        removed = len(filters) - len(kept)
        if removed:
            if page._subs_locked:
                warning = (
                    f"{removed} selected gate(s) are no longer available for "
                    "all selected files. Unlock the module to fix the "
                    "selection."
                )
            else:
                page._subs_params["gate_filters"] = kept
                warning = (
                    f"Removed {removed} gate(s) that are no longer available "
                    "for all selected files."
                )
                _subs_mark_dirty(ctx, page)
    box = getattr(page, "_subs_filter_warning", None)
    if box is not None:
        box.setText(warning)
        box.setVisible(bool(warning))


def _subs_prepare_counts(ctx, page) -> None:
    """Queue the files whose in-gate count must be (re)computed."""
    params = page._subs_params
    files = [str(p) for p in params.get("files") or []]
    filters = _subs_gate_filters(page)
    page._subs_count_generation = getattr(
        page, "_subs_count_generation", 0
    ) + 1
    timer = getattr(page, "_subs_counts_timer", None)
    if not filters:
        page._subs_count_cache = {}
        page._subs_count_pending = []
        page._subs_gate_ready = True
        page._subs_gate_total = 0
        page._subs_gate_done_n = 0
        if timer is not None:
            timer.stop()
        return
    cached = getattr(page, "_subs_count_cache", {}) or {}
    saved = _data_sampling._subsample_load_counts(
        ctx, page._subs_task_id
    )
    fresh = {}
    pending = []
    for path in files:
        sig = _data_sampling._subsample_count_sig(
            ctx, page._subs_task_def, path, params=params
        )
        entry = cached.get(path)
        if entry is None or entry[0] != sig:
            stored = saved.get(path)
            if stored is not None and stored.get("sig") == sig:
                try:
                    entry = (sig, int(stored.get("count")))
                except (TypeError, ValueError):
                    entry = None
        if entry is not None and entry[0] == sig:
            fresh[path] = entry
        else:
            pending.append(path)
    page._subs_count_cache = fresh
    page._subs_count_pending = pending
    page._subs_gate_total = len(files)
    page._subs_gate_done_n = len(files) - len(pending)
    page._subs_gate_ready = not pending
    if timer is not None:
        if pending:
            timer.start()
        else:
            timer.stop()


def _subs_counts_tick(ctx, page) -> None:
    """Compute one file's gate count per timer tick (keeps the UI alive)."""
    pending = list(getattr(page, "_subs_count_pending", []) or [])
    timer = getattr(page, "_subs_counts_timer", None)
    if not pending:
        if timer is not None:
            timer.stop()
        return
    generation = page._subs_count_generation
    params = page._subs_params
    path = pending.pop(0)
    page._subs_count_pending = pending
    sig = _data_sampling._subsample_count_sig(
        ctx, page._subs_task_def, path, params=params
    )
    try:
        count = _data_sampling._subsample_eligible_count(
            ctx, page._subs_task_def, path, params=params
        )
    except Exception as error:  # noqa: BLE001 - keep the page usable
        runlog.error("subsample.gate_count", exc=error)
        count = _data_sampling._subsample_visible_count(
            ctx, page._subs_task_id, path
        )
    if generation != page._subs_count_generation:
        return
    page._subs_count_cache[path] = (sig, int(count))
    page._subs_gate_done_n = getattr(page, "_subs_gate_done_n", 0) + 1
    if not pending:
        page._subs_gate_ready = True
        if timer is not None:
            timer.stop()
    _subs_refresh(ctx, page, restart_counts=False)


def _subs_effective_sizes(ctx, page) -> dict:
    """Rows available for sampling per file: the gate union when filtered."""
    files = [str(p) for p in page._subs_params.get("files") or []]
    original = getattr(page, "_subs_original_sizes", {}) or {}
    if not _subs_gate_filters(page):
        return {path: int(original.get(path, 0)) for path in files}
    if not getattr(page, "_subs_gate_ready", True):
        return {}
    cache = getattr(page, "_subs_count_cache", {}) or {}
    out = {}
    for path in files:
        entry = cache.get(path)
        if entry is None:
            return {}
        out[path] = int(entry[1])
    return out


def _subs_computed(ctx, page, sizes) -> dict:
    """Computed counts, ignoring manual overrides."""
    if not sizes:
        return {}
    params = dict(page._subs_params)
    params["overrides"] = {}
    return _subsample_allocate(params, sizes)


def _subs_refresh_filter_list(ctx, page) -> None:
    box = getattr(page, "_subs_gates_box", None)
    if box is None:
        return
    filters = _subs_gate_filters(page)
    labels = {}
    if filters:
        labels = {
            (entry.get("task_id"), entry.get("gate_id")): label
            for label, entry in _subs_filter_options(ctx, page)
        }
    wanted = []
    for entry in filters:
        key = (entry.get("task_id"), entry.get("gate_id"))
        label = labels.get(key)
        if label is None:
            task = ctx._find_task_by_id(entry.get("task_id"))
            module = (task or {}).get("name") or "?"
            label = f"{module} → {entry.get('gate_id')} (not available)"
        wanted.append(label)
    if box.count() == len(wanted) and all(
        box.item(i).text() == wanted[i] for i in range(len(wanted))
    ):
        return
    box.clear()
    for label in wanted:
        box.addItem(label)


def _subs_select_gates(ctx, page) -> None:
    files = list(page._subs_params.get("files") or [])
    if not files:
        QMessageBox.information(
            ctx.host, "Subsample", "Select data files first."
        )
        return
    options = _subs_filter_options(ctx, page)
    if not options:
        QMessageBox.information(
            ctx.host, "Subsample",
            "No gates are available for all selected files.",
        )
        return
    labels = [label for label, _entry in options]
    entry_by_label = dict(options)
    current = []
    labels_by_key = {
        (entry.get("task_id"), entry.get("gate_id")): label
        for label, entry in options
    }
    for entry in _subs_gate_filters(page):
        label = labels_by_key.get(
            (entry.get("task_id"), entry.get("gate_id"))
        )
        if label is not None:
            current.append(label)
    chosen = _widgets_picker._pick_items_dialog(
        ctx, "Select gates", labels, current, "Filter gates..."
    )
    if chosen is None:
        return
    selected = [
        entry_by_label[label] for label in chosen if label in entry_by_label
    ]
    if selected == _subs_gate_filters(page):
        return
    page._subs_params["gate_filters"] = selected
    _subs_param_changed(ctx, page)


def _subs_clear_gates(ctx, page) -> None:
    if not _subs_gate_filters(page):
        return
    page._subs_params["gate_filters"] = []
    _subs_param_changed(ctx, page)


def _subs_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._subs_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [_data_naming._comp_file_name(ctx, p, page._subs_task_id) for p in page._subs_params.get("files") or []]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._subs_params["files"] = [by_name[n] for n in chosen if n in by_name]
    files = set(page._subs_params["files"])
    # Drop overrides for files that are no longer selected.
    page._subs_params["overrides"] = {
        p: k for p, k in (page._subs_params.get("overrides") or {}).items()
        if p in files
    }
    if not files:
        # Gate filters only make sense with files to evaluate them on.
        page._subs_params["gate_filters"] = []
    _subs_refresh(ctx, page)
    _subs_mark_dirty(ctx, page)


def _subs_param_changed(ctx, page) -> None:
    """A sampling setting changed: confirm before discarding manual counts."""
    overrides = page._subs_params.get("overrides") or {}
    if overrides:
        reply = QMessageBox.question(
            ctx.host, "Subsample",
            "You adjusted some sampled counts manually.\n"
            "Recalculating with the new settings will overwrite them.\n\n"
            "Recalculate and discard the manual values?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            page._subs_params["overrides"] = {}
    _subs_refresh(ctx, page)
    _subs_mark_dirty(ctx, page)


def _subs_percent_mode(ctx, params) -> bool:
    """True when sampling is "a percentage of each file"."""
    return (
        (params.get("basis") or "") == "per_file"
        and (params.get("unit") or "points") == "percent"
    )


def _subs_on_basis(ctx, page) -> None:
    if page._subs_updating:
        return
    value = page._subs_basis.currentData()
    params = page._subs_params
    changed = value != (params.get("basis") or None)
    params["basis"] = value or ""
    if value:
        # First basis choice: seed the value the right field needs.
        if _subs_percent_mode(ctx, params) and params.get("percent") is None:
            params["percent"] = _SUBS_DEFAULT_PERCENT
        elif params.get("count") is None:
            params["count"] = int(page._subs_count.value()) or _SUBS_DEFAULT_COUNT
    if changed:
        _subs_param_changed(ctx, page)
    else:
        _subs_refresh(ctx, page)


def _subs_on_unit(ctx, page) -> None:
    if page._subs_updating:
        return
    params = page._subs_params
    value = page._subs_unit.currentData() or "points"
    if value == (params.get("unit") or "points"):
        return
    params["unit"] = value
    if _subs_percent_mode(ctx, params) and params.get("percent") is None:
        params["percent"] = _SUBS_DEFAULT_PERCENT
    elif params.get("count") is None:
        params["count"] = _SUBS_DEFAULT_COUNT
    _subs_param_changed(ctx, page)


def _subs_on_value(ctx, page) -> None:
    if page._subs_updating:
        return
    params = page._subs_params
    value = float(page._subs_count.value())
    if _subs_percent_mode(ctx, params):
        params["percent"] = value
    else:
        params["count"] = int(round(value))
    _subs_param_changed(ctx, page)


def _subs_on_alloc(ctx, page) -> None:
    if page._subs_updating:
        return
    value = page._subs_alloc.currentData() or "uniform"
    if value == (page._subs_params.get("allocation") or "uniform"):
        return
    page._subs_params["allocation"] = value
    _subs_param_changed(ctx, page)


def _subs_on_cell(ctx, page, item) -> None:
    if page._subs_updating or page._subs_locked:
        return
    if item.column() != 3:
        return
    path = item.data(Qt.ItemDataRole.UserRole)
    if not path:
        return
    sizes = _subs_effective_sizes(ctx, page)
    if path not in sizes:
        return
    text = (item.text() or "").strip()
    if not text:
        return
    try:
        value = int(float(text))
    except ValueError:
        _subs_refresh(ctx, page)
        return
    # A manual value never exceeds the file's own point count.
    value = max(0, min(value, sizes[path]))
    computed = _subs_computed(ctx, page, sizes).get(path)
    if computed is not None and value == computed:
        page._subs_params.get("overrides", {}).pop(path, None)
    else:
        page._subs_params.setdefault("overrides", {})[path] = value
    _subs_refresh(ctx, page)
    _subs_mark_dirty(ctx, page)


def _subs_refresh(ctx, page, restart_counts=True) -> None:
    params = page._subs_params
    files = list(params.get("files") or [])
    page._subs_original_sizes = _subs_visible_sizes(ctx, page)
    if restart_counts:
        _subs_validate_filters(ctx, page)
        _subs_prepare_counts(ctx, page)
    filters = _subs_gate_filters(page)
    sizes = _subs_effective_sizes(ctx, page)
    counts = _subsample_allocate(params, sizes) if sizes else {}

    page._subs_updating = True
    try:
        page._subs_basis.blockSignals(True)
        idx = page._subs_basis.findData(params.get("basis") or None)
        page._subs_basis.setCurrentIndex(max(0, idx))
        page._subs_basis.blockSignals(False)
        has_basis = bool(params.get("basis"))
        total = params.get("basis") == "total"
        per_file = params.get("basis") == "per_file"
        percent_mode = _subs_percent_mode(ctx, params)

        page._subs_unit.blockSignals(True)
        page._subs_unit.setCurrentIndex(
            max(0, page._subs_unit.findData(params.get("unit") or "points"))
        )
        page._subs_unit.setEnabled(per_file and not page._subs_locked)
        page._subs_unit.blockSignals(False)
        page._subs_unit_label.setVisible(per_file)
        page._subs_unit.setVisible(per_file)

        box = page._subs_count
        box.blockSignals(True)
        if percent_mode:
            box.setDecimals(1)
            box.setRange(0.1, 100.0)
            box.setSuffix(" %")
            if params.get("percent"):
                box.setValue(float(params["percent"]))
        else:
            box.setDecimals(0)
            box.setRange(1, 2_000_000_000)
            box.setSuffix("")
            if params.get("count"):
                box.setValue(int(params["count"]))
        box.setEnabled(has_basis and not page._subs_locked)
        box.blockSignals(False)
        page._subs_count_label.setText(
            "Percentage per file" if percent_mode
            else ("Total points" if total else "Points per file")
        )
        page._subs_alloc.blockSignals(True)
        page._subs_alloc.setCurrentIndex(
            max(0, page._subs_alloc.findData(params.get("allocation") or "uniform"))
        )
        page._subs_alloc.setEnabled(total and not page._subs_locked)
        page._subs_alloc.blockSignals(False)
        page._subs_alloc_label.setVisible(total)

        page._subs_files_box.clear()
        for path in files:
            page._subs_files_box.addItem(_data_naming._comp_file_name(ctx, path, page._subs_task_id))

        page._subs_gates_btn.setEnabled(
            bool(files) and not page._subs_locked
        )
        page._subs_clear_gates_btn.setEnabled(
            bool(filters) and not page._subs_locked
        )
        _subs_refresh_filter_list(ctx, page)
        if filters and not page._subs_gate_ready:
            page._subs_gate_progress.setText(
                "Calculating gate counts… "
                f"{page._subs_gate_done_n}/{page._subs_gate_total}"
            )
            page._subs_gate_progress.setVisible(True)
        else:
            page._subs_gate_progress.setVisible(False)

        table = page._subs_table
        table.blockSignals(True)
        table.setRowCount(len(files))
        for i, path in enumerate(files):
            name_item = QTableWidgetItem(_data_naming._comp_file_name(ctx, path, page._subs_task_id))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(i, 0, name_item)
            orig_item = QTableWidgetItem(str(page._subs_original_sizes.get(path, 0)))
            orig_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            orig_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(i, 1, orig_item)
            if not filters:
                in_gates_text = "—"
            else:
                entry = page._subs_count_cache.get(path)
                in_gates_text = "…" if entry is None else str(entry[1])
            in_gates_item = QTableWidgetItem(in_gates_text)
            in_gates_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            in_gates_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.setItem(i, 2, in_gates_item)
            k = counts.get(path)
            sampled = QTableWidgetItem("" if k is None else str(k))
            sampled.setData(Qt.ItemDataRole.UserRole, path)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not page._subs_locked and sizes:
                flags |= Qt.ItemFlag.ItemIsEditable
            sampled.setFlags(flags)
            sampled.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(i, 3, sampled)
        table.blockSignals(False)
    finally:
        page._subs_updating = False
    page._subs_override_hint.setVisible(
        bool(page._subs_params.get("overrides"))
    )
    _subs_update_save_btn(ctx, page)


def _subs_mark_dirty(ctx, page) -> None:
    page._subs_dirty = True
    _subs_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _subs_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_subs_save_btn", None)
    if btn is not None:
        btn.setEnabled(
            page._subs_dirty
            and not page._subs_locked
            and getattr(page, "_subs_gate_ready", True)
        )


def _subs_save_now(ctx, page) -> None:
    timer = getattr(page, "_subs_counts_timer", None)
    if timer is not None:
        timer.stop()
    params = page._subs_params
    page._subs_task_def["params"] = params
    # Commit the settings first: the mask builders read the saved snapshot.
    page._subs_snapshot = copy.deepcopy(params)
    page._subs_dirty = False
    if ctx.project is not None:
        ctx.project.save()
    # Image files get a coverage mask saved next to the module, so the
    # figure can mask unsampled pixels without recomputing every render.
    count = 0
    try:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        # Persist the in-gate counts first, so building the image coverage
        # masks (and later Total allocations downstream) can reuse them.
        files = [str(p) for p in params.get("files") or []]
        counts = {}
        for path in files:
            entry = page._subs_count_cache.get(path)
            sig = _data_sampling._subsample_count_sig(
                ctx, page._subs_task_def, path
            )
            if entry is not None and entry[0] == sig:
                counts[path] = int(entry[1])
            else:
                counts[path] = _data_sampling._subsample_eligible_count(
                    ctx, page._subs_task_def, path
                )
        if _subs_gate_filters(page):
            _data_sampling._subsample_write_counts(
                ctx, page._subs_task_def, counts
            )
        else:
            _data_sampling._subsample_clear_counts(ctx, page._subs_task_id)
        count = _subsample_write_masks(ctx, page)
    finally:
        QApplication.restoreOverrideCursor()
    ctx._subsample_cache = {}
    ctx._subsample_mask_cache = {}
    _subs_update_save_btn(ctx, page)
    message = "Subsample settings saved"
    if count:
        message += f" ({count} image mask(s))"
    ctx.statusBar().showMessage(message)
    ctx._module_saved(page)


def _subs_close(ctx, page) -> None:
    if getattr(page, "_subs_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Subsample",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._subs_task_def["params"] = page._subs_snapshot
    timer = getattr(page, "_subs_counts_timer", None)
    if timer is not None:
        timer.stop()
    ctx._close_module_page(page)
