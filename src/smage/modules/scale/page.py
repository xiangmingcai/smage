"""Scale module: per-column linear/log10/arcsinh transforms."""
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from smage.core.constants import MODULE_STATE_MODIFIED
from smage.core.formatting import _format_number
from smage.core.scale_math import _forward_scale_value
from smage.core.settings import _default_palette
from smage.widgets.controls import _NoWheelComboBox
from smage.widgets.resources import _lock_pixmap
import copy
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import naming as _data_naming
from smage.widgets.gate_editor import editor as _widgets_gate_editor_editor
from smage.widgets import image_level as _widgets_image_level
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker
from smage.widgets.plot import renderer as _widgets_plot_renderer


def _scaling_column_set(ctx, task_id=None) -> list:
    """Return the sorted, de-duplicated set of input column names."""
    cols = set()
    for rec in _data_files._available_files(ctx, task_id):
        for c in _data_columns._columns_for(ctx, rec.get("parquet"), task_id):
            cols.add(c)
    return sorted(cols)


def _scaling_load_config(ctx, task_def: dict) -> list:
    """Return the per-column scaling config, merged with the current columns."""
    cols = _scaling_column_set(ctx, task_def.get("id"))
    params = task_def.setdefault("params", {})
    existing = params.get("columns") or []
    by_name = {c.get("name"): c for c in existing}
    merged = []
    for name in cols:
        c = by_name.get(name)
        if c is None:
            merged.append({"name": name, "method": "linear",
                           "param": None, "min": None, "max": None})
        else:
            merged.append({
                "name": name,
                "method": c.get("method", "linear"),
                "param": c.get("param"),
                "min": c.get("min"),
                "max": c.get("max"),
            })
    params["columns"] = merged
    return merged


def _scaling_load_files(ctx, task_def: dict) -> list:
    """Selected data files of a Scale module (defaults to every file)."""
    params = task_def.setdefault("params", {})
    files = params.get("files")
    if files is None:
        # Nothing is scaled until the user picks the files explicitly.
        files = []
        params["files"] = []
    return [str(p) for p in files]


def _scaling_union_columns(ctx, page) -> list:
    """Columns of the selected files (union), sorted."""
    columns = set()
    for path in page._scaling_files or []:
        columns |= set(_data_columns._columns_for(ctx, path, page._scaling_task_id))
    return sorted(columns)


def _scaling_visible_configs(ctx, page) -> list:
    """Configs whose column exists in at least one selected file."""
    visible = set(_scaling_union_columns(ctx, page))
    return [c for c in page._scaling_columns if c.get("name") in visible]


def _scaling_refresh_files_box(ctx, page) -> None:
    box = getattr(page, "_scaling_files_box", None)
    if box is None:
        return
    box.blockSignals(True)
    box.clear()
    for path in page._scaling_files or []:
        box.addItem(_data_naming._comp_file_name(ctx, path, page._scaling_task_id))
    box.blockSignals(False)
    count = getattr(page, "_scaling_files_count", None)
    if count is not None:
        count.setText(f"{len(page._scaling_files or [])} file(s) selected")


def _scaling_files_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Data files</b>"))
    hint = QLabel(
        "Only variables from these files are scaled by this module."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)

    button = QPushButton("Select files…")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setEnabled(not page._scaling_locked)
    button.clicked.connect(lambda _=False: _scaling_select_files(ctx, page))
    lay.addWidget(button)
    page._scaling_files_btn = button

    box = QListWidget()
    box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    box.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    lay.addWidget(box, 1)
    page._scaling_files_box = box

    count = QLabel("")
    count.setStyleSheet("color: #6b7785;")
    lay.addWidget(count)
    page._scaling_files_count = count
    _scaling_refresh_files_box(ctx, page)
    return panel


def _scaling_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._scaling_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, path, page._scaling_task_id)
        for path in (page._scaling_files or [])
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._scaling_files = [
        str(by_name[name]) for name in chosen if name in by_name
    ]
    _scaling_refresh_files_box(ctx, page)
    _scaling_rebuild_visible(ctx, page)
    _scaling_mark_dirty(ctx, page)


def _scaling_rebuild_visible(ctx, page) -> None:
    """Re-filter the table and the batch list to the selected files."""
    page._scaling_rows = _scaling_visible_configs(ctx, page)
    _scaling_populate_table(ctx, page)
    _scaling_rebuild_apply_list(ctx, page)
    _scaling_render_preview(ctx, page)
    _scaling_rebuild_settings(ctx, page)


def _scaling_apply_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Apply to columns</b>"))
    hint = QLabel(
        "Choose variables, fill in any of the fields, then Apply. "
        "Empty fields keep their current value."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)

    lay.addWidget(QLabel("Variables"))
    select_btn = QPushButton("Select variables…")
    select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    select_btn.setEnabled(not page._scaling_locked)
    select_btn.clicked.connect(
        lambda _=False: _scaling_select_apply_vars(ctx, page)
    )
    lay.addWidget(select_btn)
    page._scaling_apply_select = select_btn

    listing = QListWidget()
    listing.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    listing.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    listing.setEnabled(not page._scaling_locked)
    lay.addWidget(listing, 1)
    page._scaling_apply_list = listing
    page._scaling_apply_vars = []

    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(6)
    grid.addWidget(QLabel("Method"), 0, 0)
    method = _NoWheelComboBox()
    method.addItem("—", None)
    for name in SCALING_METHODS:
        method.addItem(name, name)
    method.setEnabled(not page._scaling_locked)
    grid.addWidget(method, 0, 1)
    grid.addWidget(QLabel("Param"), 0, 2)
    param = QLineEdit()
    param.setPlaceholderText("unchanged")
    param.setEnabled(False)
    param.setStyleSheet(
        "QLineEdit:disabled { background: #e0e0e0; color: #9ca3af; }"
    )
    grid.addWidget(param, 0, 3)
    grid.addWidget(QLabel("Min"), 1, 0)
    min_edit = QLineEdit()
    min_edit.setPlaceholderText("unchanged")
    min_edit.setEnabled(not page._scaling_locked)
    grid.addWidget(min_edit, 1, 1)
    grid.addWidget(QLabel("Max"), 1, 2)
    max_edit = QLineEdit()
    max_edit.setPlaceholderText("unchanged")
    max_edit.setEnabled(not page._scaling_locked)
    grid.addWidget(max_edit, 1, 3)
    lay.addLayout(grid)
    page._scaling_apply_method = method
    page._scaling_apply_param = param
    page._scaling_apply_min = min_edit
    page._scaling_apply_max = max_edit

    def on_method():
        is_arcsinh = method.currentData() == "arcsinh"
        param.setEnabled((not page._scaling_locked) and is_arcsinh)

    method.currentIndexChanged.connect(lambda _=0: on_method())

    apply_btn = QPushButton("Apply")
    apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    apply_btn.setEnabled(not page._scaling_locked)
    apply_btn.clicked.connect(lambda _=False: _scaling_apply_batch(ctx, page))
    lay.addWidget(apply_btn)
    page._scaling_apply_btn = apply_btn
    return panel


def _scaling_select_apply_vars(ctx, page) -> None:
    options = _scaling_union_columns(ctx, page)
    if not options:
        QMessageBox.information(
            ctx.host, "Apply", "Select data files first."
        )
        return
    current = [
        name for name in (page._scaling_apply_vars or [])
        if name in options
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select variables", options, current, "Filter variables..."
    )
    if chosen is None:
        return
    page._scaling_apply_vars = list(chosen)
    _scaling_rebuild_apply_list(ctx, page)


def _scaling_rebuild_apply_list(ctx, page) -> None:
    listing = getattr(page, "_scaling_apply_list", None)
    if listing is None:
        return
    union = set(_scaling_union_columns(ctx, page))
    page._scaling_apply_vars = [
        name for name in (getattr(page, "_scaling_apply_vars", []) or [])
        if name in union
    ]
    listing.blockSignals(True)
    listing.clear()
    for name in page._scaling_apply_vars:
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, name)
        listing.addItem(item)
    listing.blockSignals(False)


def _scaling_apply_batch(ctx, page) -> None:
    """Copy the filled-in fields onto every ticked variable."""
    names = set(getattr(page, "_scaling_apply_vars", []) or [])
    if not names:
        QMessageBox.information(
            ctx.host, "Apply", "Select at least one variable first."
        )
        return
    method = page._scaling_apply_method.currentData()
    param_text = page._scaling_apply_param.text().strip()
    min_text = page._scaling_apply_min.text().strip()
    max_text = page._scaling_apply_max.text().strip()
    if not any((method, param_text, min_text, max_text)):
        QMessageBox.information(
            ctx.host, "Apply", "Set at least one of Method / Param / Min / Max."
        )
        return
    try:
        param_value = float(param_text) if param_text else None
    except ValueError:
        QMessageBox.warning(ctx.host, "Apply", "Param must be a number.")
        return
    try:
        min_value = int(float(min_text)) if min_text else None
        max_value = int(float(max_text)) if max_text else None
    except ValueError:
        QMessageBox.warning(ctx.host, "Apply", "Min/Max must be numbers.")
        return

    for config in page._scaling_columns:
        if config.get("name") not in names:
            continue
        if method:
            config["method"] = method
            if method != "arcsinh" and param_value is None:
                config["param"] = None
        if param_value is not None:
            config["param"] = param_value
        if min_value is not None:
            config["min"] = min_value
        if max_value is not None:
            config["max"] = max_value

    _scaling_populate_table(ctx, page)
    _scaling_mark_dirty(ctx, page)
    _scaling_render_preview(ctx, page)
    _scaling_rebuild_settings(ctx, page)
    ctx.statusBar().showMessage(
        f"Applied settings to {len(names)} column(s)", 3000
    )


def _build_scaling_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Scale page: files+apply (1) | columns (2) | preview (1)."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    # Toolbar with a Save button (scale changes are saved only on click).
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
    close_btn.clicked.connect(lambda _=False: _scaling_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    # Display-only notice, top-left under the toolbar.
    notice = QLabel(
        "Scale is visualisation only: it changes how values are plotted and "
        "where gates are drawn, never the stored data."
    )
    notice.setWordWrap(True)
    notice.setStyleSheet(
        "color: #6b7785; padding: 6px 12px 0 12px; font-size: 8.5pt;"
    )
    layout.addWidget(notice)

    page._scaling_task_def = task_def
    page._scaling_task_id = task_def.get("id")
    page._scaling_locked = locked
    page._scaling_columns = _scaling_load_config(ctx, task_def)
    page._scaling_files = _scaling_load_files(ctx, task_def)
    page._scaling_rows = []
    page._scaling_dirty = False
    page._scaling_minmax_filled = set()
    page._scaling_snapshot = copy.deepcopy(task_def.get("params", {}))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    cl = QHBoxLayout(content)
    cl.setContentsMargins(12, 12, 12, 12)
    cl.setSpacing(12)

    # Left: data files (top) + batch apply (bottom), equal heights.
    left_split = QSplitter(Qt.Orientation.Vertical)
    left_split.setChildrenCollapsible(False)
    left_split.addWidget(_scaling_files_panel(ctx, page))
    left_split.addWidget(_scaling_apply_panel(ctx, page))
    left_split.setStretchFactor(0, 1)
    left_split.setStretchFactor(1, 1)
    left_split.setSizes([320, 320])

    # Middle: columns table.
    colnames_panel = _widgets_panels._build_panel_frame(ctx)
    cll = QVBoxLayout(colnames_panel)
    cll.setContentsMargins(10, 10, 10, 10)
    cll.setSpacing(6)
    cll.addWidget(QLabel("<b>Columns</b>"))
    table = QTableWidget()
    table.setColumnCount(5)
    table.setHorizontalHeaderLabels(["Name", "Method", "Param", "Min", "Max"])
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.setStyleSheet(
        "QTableWidget { background: #ffffff; border: none; }"
        "QTableWidget::item:selected { background: #dbeafa; }"
    )
    cll.addWidget(table, 1)

    # Build the preview config first so the plot widget's A/F buttons can
    # bind to it (same dict object is mutated in place later).
    records = _data_files._available_files(ctx, task_def.get("id"))
    rec = records[0] if records else None
    cols = _data_columns._columns_for(ctx,
        rec.get("parquet"), task_def.get("id")
    ) if rec else []
    page._scaling_preview = {
        "type": "scatter",
        "image_channel": None,
        "image_min": None,
        "image_max": None,
        "bins": 20,
        "hist_mode": "histogram",
        "hist_color": (0, 0, 200),
        "curve_color": (200, 40, 40),
        "file": rec.get("parquet") if rec else None,
        "file_name": rec.get("name") if rec else None,
        "x": cols[0] if cols else None,
        "y": cols[1] if len(cols) > 1 else (cols[0] if cols else None),
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
        "show_axes": True,
        "show_axis_names": True,
        "show_indices": True,
        "show_sample_name": True,
    }

    # Right: preview (scatter + settings).
    preview_panel = _widgets_panels._build_panel_frame(ctx)
    pl = QVBoxLayout(preview_panel)
    pl.setContentsMargins(10, 10, 10, 10)
    pl.setSpacing(6)
    pl.addWidget(QLabel("<b>Preview</b>"))
    plot_widget = _widgets_plot_renderer._figure_make_plot_widget(ctx, page._scaling_preview)
    # The preview column is narrow: never let the plot demand more than
    # the panel gives it (it used to be clipped on the right).
    plot_widget.setMinimumSize(120, 120)
    plot_widget.setSizePolicy(
        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
    )
    # The preview is a normal interactive plot (unlike a figure card).
    plot_widget.getPlotItem().getViewBox().setMouseEnabled(True, True)

    # Overlay shown when a selected column is missing from the current file.
    plot_host = QWidget()
    host_grid = QGridLayout(plot_host)
    host_grid.setContentsMargins(0, 0, 0, 0)
    host_grid.addWidget(plot_widget, 0, 0)
    overlay = QWidget()
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    ov = QVBoxLayout(overlay)
    ov.setContentsMargins(0, 0, 0, 0)
    ov.addStretch()
    ov_row = QHBoxLayout()
    ov_row.addStretch()
    overlay_box = QLabel("")
    overlay_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    overlay_box.setWordWrap(True)
    overlay_box.setStyleSheet(
        "background-color: rgba(200, 200, 200, 210); color: #444; "
        "border: 1px solid #999; border-radius: 6px; padding: 14px 18px;"
    )
    overlay_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
    ov_row.addWidget(overlay_box)
    ov_row.addStretch()
    ov.addLayout(ov_row)
    ov.addStretch()
    overlay.hide()
    host_grid.addWidget(overlay, 0, 0)
    pl.addWidget(plot_host, 3)

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
    pl.addWidget(settings_scroll, 2)

    # The three parts are drag-resizable (2 : 5 : 3 by default).
    content_split = QSplitter(Qt.Orientation.Horizontal)
    content_split.setChildrenCollapsible(False)
    content_split.addWidget(left_split)
    content_split.addWidget(colnames_panel)
    content_split.addWidget(preview_panel)
    content_split.setStretchFactor(0, 2)
    content_split.setStretchFactor(1, 5)
    content_split.setStretchFactor(2, 3)
    content_split.setSizes([300, 750, 450])
    cl.addWidget(content_split, 1)
    layout.addWidget(content, 1)

    page._scaling_table = table
    page._scaling_plot_widget = plot_widget
    page._scaling_settings_layout = settings_layout
    page._scaling_overlay = overlay
    page._scaling_overlay_box = overlay_box
    page._scaling_save_btn = save_btn
    save_btn.clicked.connect(lambda _=False: _scaling_save_now(ctx, page))
    _scaling_update_save_btn(ctx, page)

    page._scaling_rows = _scaling_visible_configs(ctx, page)
    _scaling_populate_table(ctx, page)
    _scaling_rebuild_apply_list(ctx, page)
    table.itemSelectionChanged.connect(lambda: _scaling_on_selection(ctx, page))
    _scaling_on_selection(ctx, page)
    return page


def _scaling_populate_table(ctx, page) -> None:
    table = page._scaling_table
    locked = page._scaling_locked
    table.setRowCount(0)
    page._scaling_param_edits = {}
    page._scaling_min_edits = {}
    page._scaling_max_edits = {}
    for row_idx, config in enumerate(page._scaling_rows):
        table.insertRow(row_idx)
        name_item = QTableWidgetItem(config["name"])
        name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row_idx, 0, name_item)

        method_combo = _NoWheelComboBox()
        method_combo.addItems(SCALING_METHODS)
        idx = method_combo.findText(config.get("method", "linear"))
        method_combo.setCurrentIndex(max(0, idx))
        method_combo.setEnabled(not locked)
        method_combo.currentIndexChanged.connect(
            lambda _=0, r=row_idx, c=config, m=method_combo:
                _scaling_method_changed(ctx, page, r, c, m)
        )
        table.setCellWidget(row_idx, 1, method_combo)

        # Param (arcsinh cofactor) is only editable for arcsinh.
        param_edit = QLineEdit()
        param_val = config.get("param")
        param_edit.setText("" if param_val is None else str(param_val))
        param_edit.editingFinished.connect(
            lambda e=param_edit, c=config: _scaling_field_edited(ctx, page, c, "param", e)
        )
        table.setCellWidget(row_idx, 2, param_edit)
        page._scaling_param_edits[row_idx] = param_edit
        _scaling_update_param_cell(ctx, page, config, param_edit)

        for col_idx, key in ((3, "min"), (4, "max")):
            edit = QLineEdit()
            val = config.get(key)
            edit.setText("" if val is None else str(val))
            edit.setEnabled(not locked)
            edit.editingFinished.connect(
                lambda e=edit, c=config, k=key: _scaling_field_edited(ctx, page, c, k, e)
            )
            table.setCellWidget(row_idx, col_idx, edit)
            if key == "min":
                page._scaling_min_edits[row_idx] = edit
            else:
                page._scaling_max_edits[row_idx] = edit

    if table.rowCount() > 0:
        table.selectRow(0)


def _scaling_update_param_cell(ctx, page, config, param_edit) -> None:
    """Enable the Param cell only for arcsinh; gray it out otherwise."""
    is_arcsinh = config.get("method") == "arcsinh"
    param_edit.setEnabled((not page._scaling_locked) and is_arcsinh)
    param_edit.setStyleSheet(
        "QLineEdit:disabled { background: #e0e0e0; color: #9ca3af; }"
    )
    if is_arcsinh and config.get("param") is None:
        # Show the default cofactor when the user hasn't set one yet.
        param_edit.setText("5")


def _scaling_method_changed(ctx, page, row_idx, config, combo) -> None:
    config["method"] = combo.currentText()
    param_edit = page._scaling_param_edits.get(row_idx)
    if param_edit is not None:
        _scaling_update_param_cell(ctx, page, config, param_edit)
    _scaling_mark_dirty(ctx, page)
    _scaling_render_preview(ctx, page)
    _scaling_rebuild_settings(ctx, page)


def _scaling_field_edited(ctx, page, config, key, edit) -> None:
    text = edit.text().strip()
    if text == "":
        config[key] = None
    else:
        try:
            if key in ("min", "max"):
                # Min/max are display ranges shown as integers only.
                config[key] = int(float(text))
                edit.setText(str(config[key]))
            else:
                config[key] = float(text)
        except ValueError:
            edit.setText("" if config.get(key) is None else str(config[key]))
            return
    _scaling_mark_dirty(ctx, page)
    _scaling_render_preview(ctx, page)
    _scaling_rebuild_settings(ctx, page)


def _scaling_mark_dirty(ctx, page) -> None:
    page._scaling_dirty = True
    _scaling_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _scaling_save_now(ctx, page) -> None:
    params = page._scaling_task_def.setdefault("params", {})
    params["columns"] = page._scaling_columns
    params["files"] = list(page._scaling_files or [])
    if ctx.project is not None:
        ctx.project.save()
    page._scaling_snapshot = copy.deepcopy(page._scaling_task_def.get("params", {}))
    page._scaling_dirty = False
    _scaling_update_save_btn(ctx, page)
    ctx.statusBar().showMessage("Scale saved")
    ctx._module_saved(page)


def _scaling_update_save_btn(ctx, page) -> None:
    page._scaling_save_btn.setEnabled(page._scaling_dirty and not page._scaling_locked)


def _scaling_close(ctx, page) -> None:
    """Close the page, prompting first if there are unsaved changes."""
    if page._scaling_dirty:
        reply = QMessageBox.question(
            ctx.host,
            "Close Scale",
            "There are unsaved changes.\nClose without saving? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._scaling_task_def["params"] = page._scaling_snapshot
    ctx._close_module_page(page)


def _scaling_on_selection(ctx, page) -> None:
    row = page._scaling_table.currentRow()
    rows = getattr(page, "_scaling_rows", [])
    if row < 0 or row >= len(rows):
        return
    name = rows[row]["name"]
    preview = page._scaling_preview
    # Clicking a variable drives the preview: x for scatter/histogram,
    # the image channel for image plots. The user can still override
    # either of them in the preview settings.
    if preview.get("type") == "image":
        preview["image_channel"] = name
        # Every click shows the channel's default (auto) levels.
        preview["image_min"] = None
        preview["image_max"] = None
    else:
        preview["x"] = name
    _scaling_autofill_minmax(ctx, page, row)
    _scaling_render_preview(ctx, page)
    _scaling_rebuild_settings(ctx, page)


def _scaling_config_for(ctx, page, column):
    """Return the config dict for `column`, or None."""
    for c in page._scaling_columns:
        if c.get("name") == column:
            return c
    return None


def _scaling_autofill_minmax(ctx, page, row_idx) -> None:
    """Fill a row's min/max from the preview sample's data (once)."""
    if row_idx in page._scaling_minmax_filled:
        return
    page._scaling_minmax_filled.add(row_idx)
    rows = getattr(page, "_scaling_rows", page._scaling_columns)
    if row_idx >= len(rows):
        return
    config = rows[row_idx]
    name = config.get("name")
    rec = _widgets_plot_renderer._figure_record(ctx, page._scaling_preview.get("file"))
    if not rec or not name:
        return
    import numpy as np

    # Real values (upstream data stages applied); the Scale transform itself
    # is display-only and does not change the stored Min/Max range.
    data = _data_columns._column_data(
        ctx, page._scaling_task_id, rec["parquet"], [name]
    ).get(name)
    if data is None:
        return  # column not in this file -> leave empty
    vals = np.asarray(data, dtype="float64")
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return
    config["min"] = int(vals.min())
    config["max"] = int(vals.max())
    min_edit = page._scaling_min_edits.get(row_idx)
    max_edit = page._scaling_max_edits.get(row_idx)
    if min_edit is not None:
        min_edit.setText(_format_number(config["min"]))
    if max_edit is not None:
        max_edit.setText(_format_number(config["max"]))


def _scaling_render_preview(ctx, page) -> None:
    preview = page._scaling_preview
    rec = _widgets_plot_renderer._figure_record(ctx, preview.get("file"))
    cols = _data_columns._columns_for(ctx,
        rec.get("parquet"), page._scaling_task_id
    ) if rec else []
    plot_type = preview.get("type", "scatter")
    if plot_type == "image":
        needed = [preview.get("image_channel")]
    elif plot_type == "histogram":
        needed = [preview.get("x")]
    else:
        needed = [preview.get("x"), preview.get("y")]
    missing = [str(n) for n in needed if n and n not in cols]
    if missing:
        page._scaling_overlay_box.setText(
            "Parameter not in this file:\n" + ", ".join(missing)
        )
        page._scaling_overlay.show()
    else:
        page._scaling_overlay.hide()
    # This module's current (possibly unsaved) scale configs, applied
    # only to the files picked on the left.
    selected = {str(p) for p in (page._scaling_files or [])}
    transforms = {}
    if str(preview.get("file")) in selected:
        for c in page._scaling_columns:
            if c.get("name"):
                transforms.setdefault(c["name"], []).append(c)

    # Drive the preview axes from each column's min/max. Those are stored
    # as ORIGINAL values, so they are mapped into plot units here.
    x_cfg = _scaling_config_for(ctx, page, preview.get("x"))
    y_cfg = _scaling_config_for(ctx, page, preview.get("y"))

    def plot_bound(config, key, column):
        if not config or plot_type == "image":
            return None
        value = config.get(key)
        if value is None:
            return None
        configs = _widgets_plot_renderer._figure_axis_scale_configs(ctx,
            page._scaling_task_id, preview.get("file"), column, transforms
        )
        return _forward_scale_value(value, configs)

    preview["xmin"] = plot_bound(x_cfg, "min", preview.get("x"))
    preview["xmax"] = plot_bound(x_cfg, "max", preview.get("x"))
    preview["ymin"] = (
        plot_bound(y_cfg, "min", preview.get("y"))
        if plot_type == "scatter" else None
    )
    preview["ymax"] = (
        plot_bound(y_cfg, "max", preview.get("y"))
        if plot_type == "scatter" else None
    )

    viewbox = page._scaling_plot_widget.getPlotItem().getViewBox()
    if plot_type != "image":
        viewbox.setAspectLocked(False)

    if plot_type == "image":
        plot_dict = dict(preview)
        channel = preview.get("image_channel")
        available = [c for c in cols if c not in ("x", "y")]
        if not channel or channel not in available:
            channel = available[0] if available else None
        if channel:
            preview["image_channel"] = channel
            plot_dict["image_channel"] = channel
            plot_dict["layers"] = [{
                "parameter": channel,
                "color": (255, 255, 255),
                "name": str(channel),
                "min": preview.get("image_min"),
                "max": preview.get("image_max"),
            }]
        _widgets_plot_renderer._figure_render_image(ctx,
            plot_dict, page._scaling_plot_widget,
            column_transforms=transforms, task_id=page._scaling_task_id,
        )
        return

    _widgets_plot_renderer._figure_render(ctx,
        preview, page._scaling_plot_widget,
        column_transforms=transforms, task_id=page._scaling_task_id,
    )


def _scaling_set_preview_file(ctx, page, file_path, file_name=None) -> None:
    """Point the preview at another file, keeping the axis/channel choices.

    Switching files used to reset X/Y and the image channel to the new
    file's first columns, which also discarded the per-column min/max the
    axis ranges are derived from.  Keep the user's selection; when a chosen
    parameter is absent from the new file the preview shows the
    "Parameter not in this file" overlay instead.
    """
    preview = page._scaling_preview
    preview["file"] = file_path
    preview["file_name"] = file_name or _data_naming._comp_file_name(
        ctx, file_path, page._scaling_task_id
    )
    rec = _widgets_plot_renderer._figure_record(ctx, file_path)
    columns = _data_columns._columns_for(
        ctx, rec.get("parquet"), page._scaling_task_id
    ) if rec else []

    if not preview.get("x"):
        preview["x"] = columns[0] if columns else None
    if not preview.get("y"):
        preview["y"] = (
            columns[1] if len(columns) > 1
            else (columns[0] if columns else None)
        )
    if not preview.get("color_param"):
        preview["color_param"] = preview.get("x")
    channels = [c for c in columns if c not in ("x", "y")]
    if not preview.get("image_channel"):
        preview["image_channel"] = channels[0] if channels else None

    # Image display levels belong to the new file; they are not axis
    # parameters.
    preview["image_min"] = None
    preview["image_max"] = None
    _scaling_render_preview(ctx, page)
    _scaling_rebuild_settings(ctx, page)


def _scaling_set_image_levels(ctx, page, lo, hi) -> None:
    page._scaling_preview["image_min"] = lo
    page._scaling_preview["image_max"] = hi
    _scaling_render_preview(ctx, page)


def _scaling_rebuild_settings(ctx, page) -> None:
    layout = page._scaling_settings_layout
    _widgets_panels._clear_settings_layout(ctx, layout)
    preview = page._scaling_preview
    enabled = not page._scaling_locked
    plot_widget = page._scaling_plot_widget

    records = _data_files._available_files(ctx, page._scaling_task_id)
    rec = _widgets_plot_renderer._figure_record(ctx, preview.get("file"))
    columns = _data_columns._columns_for(ctx,
        rec.get("parquet"), page._scaling_task_id
    ) if rec else []
    # Scale configs at this point: upstream scales plus this module's
    # current (possibly unsaved) settings.
    live_transforms = {}
    for c in page._scaling_columns:
        if c.get("name"):
            live_transforms.setdefault(c["name"], []).append(c)

    layout.addWidget(QLabel("File"))
    file_combo = _NoWheelComboBox()
    for r in records:
        file_combo.addItem(r.get("name", ""), r.get("parquet", ""))
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
        layout.addWidget(QLabel("Channel"))
        channels = [c for c in columns if c not in ("x", "y")]
        channel_combo = _widgets_gate_editor_editor._gating_param_combo(ctx,
            channels, preview.get("image_channel"), enabled
        )
        layout.addWidget(channel_combo)
        _widgets_image_level._image_level_controls(ctx,
            layout, preview.get("image_channel"),
            preview.get("image_min"), preview.get("image_max"),
            enabled,
            on_minmax=lambda lo, hi: _scaling_set_image_levels(ctx,
                page, lo, hi
            ),
            task_id=page._scaling_task_id,
            parquet_path=rec.get("parquet") if rec else None,
            configs=_widgets_plot_renderer._figure_axis_scale_configs(ctx,
                page._scaling_task_id,
                rec.get("parquet") if rec else None,
                preview.get("image_channel"),
                live_transforms,
            ),
        )
    else:
        layout.addWidget(QLabel("X parameter"))
        x_combo = _widgets_gate_editor_editor._gating_param_combo(ctx, columns, preview.get("x"), enabled)
        layout.addWidget(x_combo)
        if plot_type == "scatter":
            layout.addWidget(QLabel("Y parameter"))
            y_combo = _widgets_gate_editor_editor._gating_param_combo(ctx, columns, preview.get("y"), enabled)
            layout.addWidget(y_combo)

    if plot_type == "scatter":
        _widgets_plot_renderer._figure_scatter_color_settings(ctx,
            layout, preview, plot_widget, columns, enabled,
            on_change=lambda: _scaling_render_preview(ctx, page),
            on_rebuild=lambda: (
                _scaling_rebuild_settings(ctx, page),
                _scaling_render_preview(ctx, page),
            ),
            task_id=page._scaling_task_id,
        )

    if plot_type != "image":
        layout.addWidget(QLabel("X range"))
        layout.addWidget(_widgets_plot_renderer._figure_range_row(ctx,
            preview, "x", plot_widget, enabled,
            configs=_widgets_plot_renderer._figure_axis_scale_configs(ctx,
                page._scaling_task_id, preview.get("file"),
                preview.get("x"), live_transforms,
            ),
        ))
    if plot_type == "scatter":
        layout.addWidget(QLabel("Y range"))
        layout.addWidget(_widgets_plot_renderer._figure_range_row(ctx,
            preview, "y", plot_widget, enabled,
            configs=_widgets_plot_renderer._figure_axis_scale_configs(ctx,
                page._scaling_task_id, preview.get("file"),
                preview.get("y"), live_transforms,
            ),
        ))

    _widgets_plot_renderer._figure_display_toggles(ctx,
        layout, preview, enabled,
        on_change=lambda: _widgets_plot_renderer._figure_apply_display_options(ctx,
            preview, plot_widget
        ),
    )
    layout.addStretch()

    def on_type():
        preview["type"] = type_combo.currentData()
        if preview["type"] == "image" and not preview.get("image_channel"):
            row = page._scaling_table.currentRow()
            rows = getattr(page, "_scaling_rows", [])
            if 0 <= row < len(rows):
                preview["image_channel"] = rows[row]["name"]
        elif preview["type"] != "image" and not preview.get("x"):
            row = page._scaling_table.currentRow()
            rows = getattr(page, "_scaling_rows", [])
            if 0 <= row < len(rows):
                preview["x"] = rows[row]["name"]
        _scaling_rebuild_settings(ctx, page)
        _scaling_render_preview(ctx, page)

    page._scaling_file_combo = file_combo

    def on_file_changed():
        _scaling_set_preview_file(
            ctx, page, file_combo.currentData(), file_combo.currentText()
        )

    def on_param(attr):
        def handler(value):
            preview[attr] = value
            _scaling_render_preview(ctx, page)
        return handler

    file_combo.currentIndexChanged.connect(lambda _=0: on_file_changed())
    type_combo.currentIndexChanged.connect(lambda _=0: on_type())
    if x_combo is not None:
        x_combo.currentTextChanged.connect(on_param("x"))
    if y_combo is not None:
        y_combo.currentTextChanged.connect(on_param("y"))
    if channel_combo is not None:
        channel_combo.currentTextChanged.connect(on_param("image_channel"))


SCALING_METHODS = ["linear", "log10", "arcsinh"]
