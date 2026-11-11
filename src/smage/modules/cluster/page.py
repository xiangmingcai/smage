"""Cluster module: FlowSOM / PhenoGraph / Leiden / PARC."""
from __future__ import annotations

from PySide6.QtCore import (
    QTimer,
    Qt,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from pathlib import Path
from smage.core.constants import (
    MODULE_STATE_MODIFIED,
    _FEATURE_LOG_LIMIT,
)
from smage.data.image_index import _build_image_index_files
from smage.widgets.controls import (
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from smage.widgets.resources import _lock_pixmap
import copy
import hashlib
import json
import queue
import threading
from functools import partial
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import image_index as _data_image_index
from smage.data import naming as _data_naming
from smage.data import sampling as _data_sampling
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


_CLUSTER_SEED = 42

_CLUSTER_METHOD_DESC = {
    "kmeans": "Centroid-based: each point joins the nearest of k centroids. Fast.",
    "flowsom": "Self-organizing map + consensus hierarchical clustering; the cytometry standard.",
    "phenograph": "k-nearest-neighbor graph + Louvain; finds irregular and rare populations.",
    "leiden": "Louvain's successor on a kNN graph; the single-cell default.",
    "dbscan": "Density-based; finds arbitrary shapes and marks outliers as 0.",
    "hierarchical": "Bottom-up agglomerative clustering; builds a cluster tree.",
    "parc": "Graph-based (HDBSCAN + community detection); fast on large data.",
}

def _cluster_signature(ctx, params, parquet_path, task_id=None) -> str:
    """Change signature for one file's clustering output."""
    try:
        st = Path(parquet_path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "method": params.get("method"),
        "normalize": params.get("normalize"),
        "max_events": params.get("max_events"),
        "columns": params.get("columns") or [],
        "name": params.get("name"),
        "kmeans": params.get("kmeans") or {},
        "flowsom": params.get("flowsom") or {},
        "phenograph": params.get("phenograph") or {},
        "leiden": params.get("leiden") or {},
        "dbscan": params.get("dbscan") or {},
        "hierarchical": params.get("hierarchical") or {},
        "parc": params.get("parc") or {},
        "source": str(parquet_path),
        "stat": stat,
    }
    # FlowSOM and PARC run in the user's extension environment; a new
    # version can change the clusters, so old results become stale.
    method = params.get("method")
    if method in ("flowsom", "parc"):
        payload["extension_version"] = (
            ctx._ext_entry(method).get("version") or ""
        )
    sample = _data_sampling._subsample_sig(ctx, task_id, parquet_path)
    if sample:
        payload["sample"] = sample
    payload = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _cluster_params_for(ctx, task_def) -> dict:
    """Saved params of a Cluster task (its live snapshot while open)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "cluster":
        snap = getattr(reg["page"], "_cluster_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _build_cluster_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Cluster page: method/files | variables | params | log."""
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
    close_btn.clicked.connect(lambda _=False: _cluster_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("method", "kmeans")
    params.setdefault("files", [])
    params.setdefault("columns", [])
    params.setdefault("normalize", True)
    params.setdefault("max_events", 5000)
    params.setdefault("name", "cluster")
    params.setdefault("kmeans", {"k": 20})
    params.setdefault("flowsom", {"nodes": 100, "k": 20})
    params.setdefault("phenograph", {"k": 30})
    params.setdefault("leiden", {"k": 30, "resolution": 1.0})
    params.setdefault("dbscan", {"eps": 0.5, "min_samples": 10})
    params.setdefault("hierarchical", {"k": 20})
    params.setdefault("parc", {"knn": 30, "resolution": 1.0})
    params.setdefault("outputs", {})
    params.setdefault("log", [])
    page._cluster_task_def = task_def
    page._cluster_task_id = task_def.get("id")
    page._cluster_params = params
    page._cluster_locked = locked
    page._cluster_save_btn = save_btn
    page._cluster_dirty = False
    page._cluster_snapshot = copy.deepcopy(params)
    page._cluster_log_lines = list(params.get("log") or [])
    page._cluster_running = False
    page._cluster_cancel = False
    page._cluster_updating = False
    save_btn.clicked.connect(lambda _=False: _cluster_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_cluster_left_panel(ctx, page))
    splitter.addWidget(_cluster_center_panel(ctx, page))
    splitter.addWidget(_cluster_right_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 1)
    splitter.setSizes([320, 320, 420])
    row.addWidget(splitter, 1)

    row.addWidget(_cluster_log_panel(ctx, page))
    layout.addWidget(content, 1)

    _cluster_refresh(ctx, page)
    return page


def _cluster_log_panel(ctx, page) -> QWidget:
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
    log.setFixedHeight(130)
    for line in getattr(page, "_cluster_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._cluster_log = log
    return panel


def _cluster_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_cluster_log_lines", None)
    if lines is None:
        lines = []
        page._cluster_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    if getattr(page, "_cluster_params", None) is not None:
        page._cluster_params["log"] = list(lines)
    box = getattr(page, "_cluster_log", None)
    if box is not None:
        for line in str(text).splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")


def _cluster_left_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Algorithm</b>"))
    method = _NoWheelComboBox()
    method.addItem("K-means", "kmeans")
    method.addItem("FlowSOM", "flowsom")
    method.addItem("PhenoGraph", "phenograph")
    method.addItem("Leiden", "leiden")
    method.addItem("DBSCAN", "dbscan")
    method.addItem("Hierarchical", "hierarchical")
    method.addItem("PARC", "parc")
    method.setEnabled(not page._cluster_locked)
    lay.addWidget(method)
    page._cluster_method = method

    method_desc = QLabel("")
    method_desc.setWordWrap(True)
    method_desc.setStyleSheet("color: #6b7785;")
    lay.addWidget(method_desc)
    page._cluster_method_desc = method_desc

    lay.addWidget(QLabel("<b>Data files</b>"))
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(not page._cluster_locked)
    lay.addWidget(files_btn)
    files_box = QListWidget()
    files_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(files_box, 1)
    page._cluster_files_box = files_box
    files_count = QLabel("")
    files_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(files_count)
    page._cluster_files_count = files_count

    method.currentIndexChanged.connect(lambda _=0: _cluster_on_method(ctx, page))
    files_btn.clicked.connect(lambda _=False: _cluster_select_files(ctx, page))
    return panel


def _cluster_center_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Channels</b>"))
    hint = QLabel(
        "Channels shared by every selected file. All files are clustered "
        "together, so only common channels can be used."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    vars_btn = QPushButton("Select channels…")
    vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    vars_btn.setEnabled(not page._cluster_locked)
    lay.addWidget(vars_btn)
    vars_box = QListWidget()
    vars_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(vars_box, 1)
    page._cluster_vars_box = vars_box
    vars_count = QLabel("")
    vars_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(vars_count)
    page._cluster_vars_count = vars_count

    vars_btn.clicked.connect(lambda _=False: _cluster_select_vars(ctx, page))
    return panel


def _cluster_right_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Algorithm parameters</b>"))

    lay.addWidget(QLabel("Max events per file"))
    max_events = _NoWheelSpinBox()
    max_events.setRange(0, 2_000_000_000)
    max_events.setValue(5000)
    max_events.setEnabled(not page._cluster_locked)
    lay.addWidget(max_events)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Randomly cap the points each table (flow) file contributes "
        "(0 = use everything). Image files are always clustered in full — "
        "use an upstream Subsample module to thin a huge image. Fixed seed."
    ))
    page._cluster_max_events = max_events

    def _param_host(rows):
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        row = 0
        for label, widget, hint in rows:
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(widget, row, 1)
            row += 1
            grid.addWidget(_widgets_panels._param_hint(ctx, hint), row, 0, 1, 2)
            row += 1
        return host

    kmeans_k = _NoWheelSpinBox()
    kmeans_k.setRange(2, 1000)
    kmeans_k.setValue(20)
    kmeans_host = _param_host([
        ("k (clusters)", kmeans_k,
         "Number of clusters to split the data into."),
    ])
    page._cluster_kmeans_host = kmeans_host
    page._cluster_kmeans_k = kmeans_k

    flowsom_nodes = _NoWheelSpinBox()
    flowsom_nodes.setRange(4, 10000)
    flowsom_nodes.setValue(100)
    flowsom_k = _NoWheelSpinBox()
    flowsom_k.setRange(2, 1000)
    flowsom_k.setValue(20)
    flowsom_host = _param_host([
        ("SOM nodes", flowsom_nodes,
         "Size of the ctx-organising map; more nodes = finer overclustering."),
        ("k (clusters)", flowsom_k,
         "Clusters the SOM nodes are merged into."),
    ])
    page._cluster_flowsom_host = flowsom_host
    page._cluster_flowsom_nodes = flowsom_nodes
    page._cluster_flowsom_k = flowsom_k

    # PhenoGraph: k (neighbors); cluster count auto-determined.
    phenograph_k = _NoWheelSpinBox()
    phenograph_k.setRange(2, 1000)
    phenograph_k.setValue(30)
    phenograph_host = _param_host([
        ("k (neighbors)", phenograph_k,
         "Neighbours per point in the graph; higher = fewer, larger clusters."),
    ])
    page._cluster_phenograph_host = phenograph_host
    page._cluster_phenograph_k = phenograph_k

    # Leiden: k (neighbors) + resolution.
    leiden_k = _NoWheelSpinBox()
    leiden_k.setRange(2, 1000)
    leiden_k.setValue(30)
    leiden_res = _NoWheelDoubleSpinBox()
    leiden_res.setRange(0.05, 10.0)
    leiden_res.setDecimals(2)
    leiden_res.setSingleStep(0.1)
    leiden_res.setValue(1.0)
    leiden_host = _param_host([
        ("k (neighbors)", leiden_k,
         "Neighbours per point in the graph."),
        ("resolution", leiden_res,
         "Higher = more, smaller clusters."),
    ])
    page._cluster_leiden_host = leiden_host
    page._cluster_leiden_k = leiden_k
    page._cluster_leiden_res = leiden_res

    # DBSCAN: eps + min_samples.
    dbscan_eps = _NoWheelDoubleSpinBox()
    dbscan_eps.setRange(0.01, 100.0)
    dbscan_eps.setDecimals(3)
    dbscan_eps.setSingleStep(0.05)
    dbscan_eps.setValue(0.5)
    dbscan_ms = _NoWheelSpinBox()
    dbscan_ms.setRange(2, 1000)
    dbscan_ms.setValue(10)
    dbscan_host = _param_host([
        ("eps", dbscan_eps,
         "Neighbourhood radius. Larger = fewer, bigger clusters."),
        ("min samples", dbscan_ms,
         "Points needed in a neighbourhood to count as dense."),
    ])
    page._cluster_dbscan_host = dbscan_host
    page._cluster_dbscan_eps = dbscan_eps
    page._cluster_dbscan_ms = dbscan_ms

    # Hierarchical (agglomerative): k.
    hier_k = _NoWheelSpinBox()
    hier_k.setRange(2, 1000)
    hier_k.setValue(20)
    hier_host = _param_host([
        ("k (clusters)", hier_k,
         "Where to cut the cluster tree."),
    ])
    page._cluster_hier_host = hier_host
    page._cluster_hier_k = hier_k

    # PARC: knn + resolution.
    parc_knn = _NoWheelSpinBox()
    parc_knn.setRange(2, 1000)
    parc_knn.setValue(30)
    parc_res = _NoWheelDoubleSpinBox()
    parc_res.setRange(0.05, 10.0)
    parc_res.setDecimals(2)
    parc_res.setSingleStep(0.1)
    parc_res.setValue(1.0)
    parc_host = _param_host([
        ("knn", parc_knn,
         "Neighbours per point in the graph."),
        ("resolution", parc_res,
         "Higher = more, smaller clusters."),
    ])
    page._cluster_parc_host = parc_host
    page._cluster_parc_knn = parc_knn
    page._cluster_parc_res = parc_res

    lay.addWidget(kmeans_host)
    lay.addWidget(flowsom_host)
    lay.addWidget(phenograph_host)
    lay.addWidget(leiden_host)
    lay.addWidget(dbscan_host)
    lay.addWidget(hier_host)
    lay.addWidget(parc_host)

    normalize = QCheckBox("Normalize (z-score)")
    normalize.setChecked(True)
    normalize.setEnabled(not page._cluster_locked)
    lay.addWidget(normalize)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Z-score the channels first, so no channel dominates the distance."
    ))
    page._cluster_normalize = normalize

    lay.addWidget(QLabel("<b>New channel name</b>"))
    name_row = QWidget()
    nh = QHBoxLayout(name_row)
    nh.setContentsMargins(0, 0, 0, 0)
    nh.setSpacing(4)
    name_edit = QLineEdit()
    nh.addWidget(name_edit, 1)
    warn = QLabel("⚠")
    warn.setToolTip("Duplicate channel name")
    warn.setStyleSheet("color: #b58900; font-weight: bold;")
    nh.addWidget(warn)
    lay.addWidget(name_row)
    page._cluster_name = name_edit
    page._cluster_warn = warn

    # Shown when the selected method needs an external extension that is
    # not configured (FlowSOM or PARC); Calculate is disabled meanwhile.
    ext_hint = QLabel("")
    ext_hint.setWordWrap(True)
    ext_hint.setStyleSheet(
        "color: #b58900; font-weight: bold; font-size: 8pt;"
    )
    ext_hint.setVisible(False)
    lay.addWidget(ext_hint)
    page._cluster_ext_hint = ext_hint

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(False)
    lay.addWidget(calc_btn)
    page._cluster_calc_btn = calc_btn
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._cluster_status = status
    lay.addStretch()

    for w in (kmeans_k, flowsom_nodes, flowsom_k, phenograph_k, leiden_k,
              leiden_res, dbscan_eps, dbscan_ms, hier_k, parc_knn, parc_res):
        w.setEnabled(not page._cluster_locked)
    name_edit.setEnabled(not page._cluster_locked)

    max_events.valueChanged.connect(lambda _=0: _cluster_on_max_events(ctx, page))
    kmeans_k.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "kmeans", "k", kmeans_k.value()))
    flowsom_nodes.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "flowsom", "nodes", flowsom_nodes.value()))
    flowsom_k.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "flowsom", "k", flowsom_k.value()))
    phenograph_k.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "phenograph", "k", phenograph_k.value()))
    leiden_k.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "leiden", "k", leiden_k.value()))
    leiden_res.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "leiden", "resolution", leiden_res.value()))
    dbscan_eps.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "dbscan", "eps", dbscan_eps.value()))
    dbscan_ms.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "dbscan", "min_samples", dbscan_ms.value()))
    hier_k.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "hierarchical", "k", hier_k.value()))
    parc_knn.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "parc", "knn", parc_knn.value()))
    parc_res.valueChanged.connect(lambda _=0: _cluster_on_param(ctx, page, "parc", "resolution", parc_res.value()))
    normalize.toggled.connect(lambda _=0: _cluster_on_normalize(ctx, page))
    name_edit.textEdited.connect(lambda _=0: _cluster_on_name(ctx, page))
    calc_btn.clicked.connect(lambda _=False: _cluster_calculate(ctx, page))
    return panel


def _cluster_on_method(ctx, page) -> None:
    if getattr(page, "_cluster_updating", False):
        return
    page._cluster_params["method"] = page._cluster_method.currentData() or "kmeans"
    _cluster_refresh(ctx, page)
    _cluster_mark_dirty(ctx, page)


def _cluster_on_param(ctx, page, group, key, value) -> None:
    if getattr(page, "_cluster_updating", False):
        return
    page._cluster_params.setdefault(group, {})[key] = value
    _cluster_mark_dirty(ctx, page)


def _cluster_update_ext_state(ctx, page) -> None:
    """Warn when the selected method's external extension is missing.

    FlowSOM and PARC run in the user's Python environment; there is no
    setup shortcut — Calculate stays disabled until the extension is
    installed from Extension → Manage Extensions.
    """
    method = page._cluster_params.get("method") or ""
    missing = None
    if method == "flowsom" and not ctx._ext_ready("flowsom"):
        missing = "FlowSOM"
    elif method == "parc" and not ctx._ext_ready("parc"):
        missing = "PARC"
    hint = getattr(page, "_cluster_ext_hint", None)
    if hint is not None:
        if missing:
            hint.setText(
                f"{missing} extension is not configured. Install it in "
                "Extension → Manage Extensions before calculating."
            )
        hint.setVisible(bool(missing))
    _cluster_update_calc_btn(ctx, page)


def _cluster_on_normalize(ctx, page) -> None:
    if getattr(page, "_cluster_updating", False):
        return
    page._cluster_params["normalize"] = page._cluster_normalize.isChecked()
    _cluster_mark_dirty(ctx, page)


def _cluster_on_max_events(ctx, page) -> None:
    if getattr(page, "_cluster_updating", False):
        return
    page._cluster_params["max_events"] = int(page._cluster_max_events.value())
    _cluster_mark_dirty(ctx, page)


def _cluster_on_name(ctx, page) -> None:
    if getattr(page, "_cluster_updating", False):
        return
    page._cluster_params["name"] = page._cluster_name.text().strip()
    _cluster_mark_dirty(ctx, page)
    _cluster_update_name(ctx, page)


def _cluster_common_columns(ctx, page) -> list:
    files = page._cluster_params.get("files") or []
    if not files:
        return []
    common = set(_data_columns._columns_for(ctx, files[0], page._cluster_task_id))
    for p in files[1:]:
        common &= set(_data_columns._columns_for(ctx, p, page._cluster_task_id))
    return sorted(common)


def _cluster_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._cluster_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, p, page._cluster_task_id)
        for p in page._cluster_params.get("files") or []
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._cluster_params["files"] = [by_name[n] for n in chosen if n in by_name]
    common = set(_cluster_common_columns(ctx, page))
    page._cluster_params["columns"] = [
        c for c in page._cluster_params.get("columns") or [] if c in common
    ]
    _cluster_refresh(ctx, page)
    _cluster_mark_dirty(ctx, page)


def _cluster_select_vars(ctx, page) -> None:
    options = _cluster_common_columns(ctx, page)
    if not options:
        QMessageBox.information(
            ctx.host, "Select channels", "Select data files first."
        )
        return
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select channels",
        options,
        page._cluster_params.get("columns") or [],
        "Filter channels...",
    )
    if chosen is None:
        return
    page._cluster_params["columns"] = list(chosen)
    _cluster_refresh(ctx, page)
    _cluster_mark_dirty(ctx, page)


def _cluster_existing_columns(ctx, page) -> set:
    cols = set()
    for path in page._cluster_params.get("files") or []:
        cols |= set(_data_columns._columns_for(ctx, path, page._cluster_task_id))
    return cols


def _cluster_name_conflict(ctx, page) -> bool:
    name = (page._cluster_params.get("name") or "").strip()
    if not name:
        return False
    return name in _cluster_existing_columns(ctx, page)


def _cluster_update_name(ctx, page) -> None:
    page._cluster_warn.setVisible(_cluster_name_conflict(ctx, page))
    _cluster_update_calc_btn(ctx, page)


def _cluster_update_calc_btn(ctx, page) -> None:
    params = page._cluster_params
    name = (params.get("name") or "").strip()
    ok = bool(
        params.get("files")
        and params.get("columns")
        and name
        and not _cluster_name_conflict(ctx, page)
    )
    method = str(params.get("method") or "")
    missing = None
    if method == "flowsom" and not ctx._ext_ready("flowsom"):
        missing = "FlowSOM"
    elif method == "parc" and not ctx._ext_ready("parc"):
        missing = "PARC"
    page._cluster_calc_btn.setEnabled(
        ok and not page._cluster_locked and missing is None
    )
    if missing:
        page._cluster_calc_btn.setToolTip(
            f"Install the {missing} extension first "
            "(Extension → Manage Extensions)."
        )
    else:
        page._cluster_calc_btn.setToolTip("")


def _cluster_refresh(ctx, page) -> None:
    params = page._cluster_params
    page._cluster_updating = True
    try:
        page._cluster_method.blockSignals(True)
        page._cluster_method.setCurrentIndex(
            max(0, page._cluster_method.findData(params.get("method", "kmeans")))
        )
        page._cluster_method.blockSignals(False)

        kmeans = params.get("kmeans") or {}
        flowsom = params.get("flowsom") or {}
        phenograph = params.get("phenograph") or {}
        leiden = params.get("leiden") or {}
        dbscan = params.get("dbscan") or {}
        hier = params.get("hierarchical") or {}
        parc = params.get("parc") or {}
        page._cluster_kmeans_k.blockSignals(True)
        page._cluster_kmeans_k.setValue(int(kmeans.get("k", 20)))
        page._cluster_kmeans_k.blockSignals(False)
        page._cluster_flowsom_nodes.blockSignals(True)
        page._cluster_flowsom_nodes.setValue(int(flowsom.get("nodes", 100)))
        page._cluster_flowsom_nodes.blockSignals(False)
        page._cluster_flowsom_k.blockSignals(True)
        page._cluster_flowsom_k.setValue(int(flowsom.get("k", 20)))
        page._cluster_flowsom_k.blockSignals(False)
        page._cluster_phenograph_k.blockSignals(True)
        page._cluster_phenograph_k.setValue(int(phenograph.get("k", 30)))
        page._cluster_phenograph_k.blockSignals(False)
        page._cluster_leiden_k.blockSignals(True)
        page._cluster_leiden_k.setValue(int(leiden.get("k", 30)))
        page._cluster_leiden_k.blockSignals(False)
        page._cluster_leiden_res.blockSignals(True)
        page._cluster_leiden_res.setValue(float(leiden.get("resolution", 1.0)))
        page._cluster_leiden_res.blockSignals(False)
        page._cluster_dbscan_eps.blockSignals(True)
        page._cluster_dbscan_eps.setValue(float(dbscan.get("eps", 0.5)))
        page._cluster_dbscan_eps.blockSignals(False)
        page._cluster_dbscan_ms.blockSignals(True)
        page._cluster_dbscan_ms.setValue(int(dbscan.get("min_samples", 10)))
        page._cluster_dbscan_ms.blockSignals(False)
        page._cluster_hier_k.blockSignals(True)
        page._cluster_hier_k.setValue(int(hier.get("k", 20)))
        page._cluster_hier_k.blockSignals(False)
        page._cluster_parc_knn.blockSignals(True)
        page._cluster_parc_knn.setValue(int(parc.get("knn", 30)))
        page._cluster_parc_knn.blockSignals(False)
        page._cluster_parc_res.blockSignals(True)
        page._cluster_parc_res.setValue(float(parc.get("resolution", 1.0)))
        page._cluster_parc_res.blockSignals(False)

        method = params.get("method")
        page._cluster_kmeans_host.setVisible(method == "kmeans")
        page._cluster_flowsom_host.setVisible(method == "flowsom")
        _cluster_update_ext_state(ctx, page)
        page._cluster_phenograph_host.setVisible(method == "phenograph")
        page._cluster_leiden_host.setVisible(method == "leiden")
        page._cluster_dbscan_host.setVisible(method == "dbscan")
        page._cluster_hier_host.setVisible(method == "hierarchical")
        page._cluster_parc_host.setVisible(method == "parc")
        page._cluster_method_desc.setText(_CLUSTER_METHOD_DESC.get(method, ""))

        page._cluster_normalize.blockSignals(True)
        page._cluster_normalize.setChecked(bool(params.get("normalize", True)))
        page._cluster_normalize.blockSignals(False)

        page._cluster_max_events.blockSignals(True)
        page._cluster_max_events.setValue(int(params.get("max_events", 5000)))
        page._cluster_max_events.blockSignals(False)

        page._cluster_name.blockSignals(True)
        page._cluster_name.setText(params.get("name", "cluster"))
        page._cluster_name.blockSignals(False)

        files = params.get("files") or []
        page._cluster_files_box.clear()
        for path in files:
            page._cluster_files_box.addItem(
                _data_naming._comp_file_name(ctx, path, page._cluster_task_id)
            )
        page._cluster_files_count.setText(f"{len(files)} file(s) selected")

        columns = params.get("columns") or []
        page._cluster_vars_box.clear()
        for c in columns:
            page._cluster_vars_box.addItem(str(c))
        page._cluster_vars_count.setText(f"{len(columns)} channel(s) selected")
    finally:
        page._cluster_updating = False
    _cluster_update_name(ctx, page)


def _cluster_validate(ctx, page):
    params = page._cluster_params
    if not params.get("files"):
        return False, "Select at least one data file."
    if not params.get("columns"):
        return False, "Select at least one channel."
    name = (params.get("name") or "").strip()
    if not name:
        return False, "Set the new channel name."
    if _cluster_name_conflict(ctx, page):
        return False, "The new channel name collides with an existing column."
    return True, ""


def _cluster_sample_seed(ctx, page, path) -> int:
    payload = f"smage-cluster-sample-v1|{page._cluster_task_id}|{path}"
    return int(hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16], 16)


def _cluster_prepare(ctx, X, normalize):
    """Fill missing values with column means, optionally z-score."""
    import numpy as np

    col_mean = np.nanmean(X, axis=0)
    col_mean = np.where(np.isfinite(col_mean), col_mean, 0.0)
    nan = np.isnan(X)
    if nan.any():
        X = X.copy()
        X[nan] = np.take(col_mean, np.where(nan)[1])
    if normalize:
        std = np.std(X, axis=0)
        std = np.where(std > 0, std, 1.0)
        X = (X - col_mean) / std
    return X


def _cluster_fit(ctx, method, cfg, X, seed):
    """Fit the chosen clustering algorithm; return 1-based labels.

    FlowSOM is not handled here: it runs in the user's external Python
    environment (see ``_cluster_fit_flowsom``), so Smage never imports it.
    """
    import numpy as np

    if method == "kmeans":
        from sklearn.cluster import KMeans

        model = KMeans(
            n_clusters=int(cfg.get("k", 20)), n_init="auto", random_state=seed
        )
        return model.fit_predict(X).astype("int64") + 1
    if method == "dbscan":
        from sklearn.cluster import DBSCAN

        model = DBSCAN(
            eps=float(cfg.get("eps", 0.5)),
            min_samples=int(cfg.get("min_samples", 10)),
        )
        # -1 (noise) becomes 0; real clusters become 1..k.
        return model.fit_predict(X).astype("int64") + 1
    if method == "hierarchical":
        from sklearn.cluster import AgglomerativeClustering

        model = AgglomerativeClustering(n_clusters=int(cfg.get("k", 20)))
        return model.fit_predict(X).astype("int64") + 1
    # PhenoGraph and Leiden build a kNN graph first (scanpy).
    import scanpy as sc
    import anndata as ad

    adata = ad.AnnData(np.asarray(X, dtype="float64"))
    try:
        sc.pp.neighbors(adata, n_neighbors=int(cfg.get("k", 30)), random_state=seed)
    except TypeError:
        sc.pp.neighbors(adata, n_neighbors=int(cfg.get("k", 30)))
    if method == "leiden":
        sc.tl.leiden(
            adata, resolution=float(cfg.get("resolution", 1.0)), random_state=seed
        )
        return adata.obs["leiden"].to_numpy(dtype="int64") + 1
    # PhenoGraph ≈ kNN graph + Louvain community detection.
    sc.tl.louvain(adata, resolution=1.0, random_state=seed)
    return adata.obs["louvain"].to_numpy(dtype="int64") + 1


def _cluster_fit_parc(ctx, page, q, cfg, X, seed):
    return ctx._parc_external(
        page, q, X, int(cfg.get("knn", 30)),
        float(cfg.get("resolution", 1.0)), seed,
        cancel_flag="_cluster_cancel",
    )


def _cluster_fit_flowsom(ctx, page, q, cfg, X, seed):
    """Run FlowSOM in the extension's external Python and return labels."""
    import math

    import numpy as np

    nodes = int(cfg.get("nodes", 100))
    xdim = int(math.isqrt(nodes)) or 1
    ydim = max(1, math.ceil(nodes / xdim))
    result = ctx._flowsom_external(
        page, q, X, xdim, ydim, int(cfg.get("k", 20)), seed,
        cancel_flag="_cluster_cancel",
    )
    labels = result["cell_cluster"]
    if labels.size != X.shape[0]:
        raise ValueError(
            "FlowSOM returned an unexpected number of labels "
            f"({labels.size} for {X.shape[0]} rows)"
        )
    return labels + 1


def _cluster_write_file(ctx, page, path, labels, row_indices=None):
    """Write one file's labels; return (rel, columns, kind)."""
    import numpy as np
    import pandas as pd

    params = page._cluster_params
    name = (params.get("name") or "cluster").strip()
    stem = Path(str(path)).stem
    rel = _data_files._module_rel(ctx, page._cluster_task_id, f"{stem}_cluster.parquet")
    out_path = ctx.project.path / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ent = _data_image_index._ensure_image_index(ctx, path)
    is_image = ent is not None and ent.get("manifest") is not None
    n_full = _data_columns._data_row_count(ctx, path)
    sel = np.asarray(row_indices) if row_indices is not None else np.arange(len(labels))
    sel = sel.astype(np.int64)

    if is_image:
        man = ent["manifest"]
        xmin = int(round(float(man["xmin"])))
        ymin = int(round(float(man["ymin"])))
        width = int(round(float(man["xmax"]) - float(man["xmin"]))) + 1
        height = int(round(float(man["ymax"]) - float(man["ymin"]))) + 1
        src = pd.read_parquet(path, columns=["x", "y"])
        xi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        yi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        ok = (sel >= 0) & (sel < len(xi))
        grid = np.full(height * width, np.nan, dtype="float64")
        s = sel[ok]
        sx, sy = xi[s], yi[s]
        good = (sx >= 0) & (sx < width) & (sy >= 0) & (sy < height)
        flat = sy[good] * width + sx[good]
        grid[flat] = labels[ok][good]
        xx = np.tile(np.arange(width, dtype=np.int32), height) + xmin
        yy = np.repeat(np.arange(height, dtype=np.int32), width) + ymin
        pd.DataFrame({"x": xx, "y": yy, name: grid}).to_parquet(
            out_path, index=False
        )
        _build_image_index_files(str(out_path))
        kind = "image"
    else:
        full = np.full(n_full, np.nan, dtype="float64")
        if len(labels) == len(sel):
            full[sel] = labels
        else:
            n = min(len(labels), n_full)
            full[:n] = labels[:n]
        pd.DataFrame({name: full}).to_parquet(out_path, index=False)
        kind = "table"
    return rel, [name], kind


def _cluster_run(ctx, page, q):
    """Run clustering in a background thread, posting progress."""
    import time

    import numpy as np

    params = page._cluster_params
    files = list(params.get("files") or [])
    columns = list(params.get("columns") or [])
    n_files = len(files)
    failed = 0
    try:
        q.put(("log", "— Starting clustering —"))
        parts = []
        counts = []
        row_indices = []
        max_events = int(params.get("max_events") or 0)
        total_before = 0
        total_after = 0
        for i, path in enumerate(files):
            name = _data_naming._comp_file_name(ctx, path, page._cluster_task_id)
            q.put(("log", f"Reading {i + 1}/{n_files}: {name}"))
            q.put(("status", f"Reading {i + 1}/{n_files} ({25 * (i + 1) // n_files}%)"))
            values = _data_columns._column_data(ctx, page._cluster_task_id, path, columns)
            if any(c not in values for c in columns):
                raise ValueError(f"{name} is missing a channel")
            X = np.column_stack([values[c] for c in columns]).astype("float64")
            n_vis = len(X)
            total_before += n_vis
            up_idx = _data_sampling._subsample_row_index(ctx, page._cluster_task_id, path)
            full_rows = (
                np.asarray(up_idx, dtype=np.int64)
                if up_idx is not None else np.arange(n_vis, dtype=np.int64)
            )
            # Pixel-level (image) files are clustered in full: downsampling
            # would leave holes that render black. max_events only applies
            # to table (flow) data.
            is_image = _data_image_index._ensure_image_index(ctx, path) is not None
            if max_events > 0 and not is_image and n_vis > max_events:
                seed = _cluster_sample_seed(ctx, page, path)
                keep = np.sort(
                    np.random.default_rng(seed).permutation(n_vis)[:max_events]
                )
                X = X[keep]
                full_rows = full_rows[keep]
            total_after += len(X)
            parts.append(X)
            counts.append(len(X))
            row_indices.append(full_rows)
        if not parts:
            raise ValueError("no data to cluster")
        X = np.vstack(parts)
        q.put(("log", f"Combined {X.shape[0]} row(s) × {X.shape[1]} channel(s)"))
        if max_events > 0 and total_after < total_before:
            q.put(("log", f"Downsampled {total_before:,} → {total_after:,} points "
                          f"(max {max_events:,} per file)"))

        q.put(("log", "Normalizing (z-score)…" if params.get("normalize")
               else "Normalization skipped"))
        X = _cluster_prepare(ctx, X, bool(params.get("normalize")))

        method = params.get("method", "kmeans")
        # Each method keeps its own parameter group; FlowSOM runs in the
        # external extension environment instead of this process.
        cfg = params.get(method) or {}
        detail = ", ".join(f"{k}={v}" for k, v in cfg.items())
        q.put(("log", f"Clustering {method.upper()} ({detail})…"))
        q.put(("status", "Clustering… (25%)"))
        t0 = time.time()
        if method == "flowsom":
            labels = _cluster_fit_flowsom(ctx,
                page, q, cfg, X, _CLUSTER_SEED
            )
        elif method == "parc":
            labels = _cluster_fit_parc(ctx,
                page, q, cfg, X, _CLUSTER_SEED
            )
        else:
            labels = _cluster_fit(ctx, method, cfg, X, _CLUSTER_SEED)
        fit_seconds = time.time() - t0
        q.put(("log", f"Clustering finished in {fit_seconds:.1f}s"))

        outputs = {}
        offset = 0
        for i, (path, n) in enumerate(zip(files, counts)):
            if getattr(page, "_cluster_cancel", False):
                q.put(("log", "Calculation cancelled."))
                break
            name = _data_naming._comp_file_name(ctx, path, page._cluster_task_id)
            seg = labels[offset:offset + n]
            offset += n
            q.put(("status", f"Writing {i + 1}/{n_files} ({90 + 10 * (i + 1) // n_files}%)"))
            try:
                rel, cols, kind = _cluster_write_file(ctx,
                    page, path, seg, row_indices[i]
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                q.put(("log", f"✗ {name}: {exc}"))
                continue
            outputs[path] = {
                "path": rel,
                "columns": cols,
                "kind": kind,
                "signature": _cluster_signature(ctx,
                    params, path, page._cluster_task_id
                ),
            }
            q.put(("log", f"✓ {name} → {Path(rel).name} ({len(cols)} channel)"))
        q.put(("done", outputs, len(outputs), failed))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _cluster_calculate(ctx, page) -> None:
    ok, message = _cluster_validate(ctx, page)
    if not ok:
        QMessageBox.warning(ctx.host, "Calculate", message)
        return
    params = page._cluster_params
    method = params.get("method") or ""
    if method in ("flowsom", "parc") and not ctx._ext_ready(method):
        name = "FlowSOM" if method == "flowsom" else "PARC"
        QMessageBox.information(
            ctx.host, "Calculate",
            f"{name} extension is not configured.\n"
            f"Open Extension → Manage Extensions and install {name} "
            "in a separate Python environment first.",
        )
        return
    files = params.get("files") or []
    existing = [p for p in files if isinstance((params.get("outputs") or {}).get(p), dict)]
    if existing:
        reply = QMessageBox.question(
            ctx.host, "Calculate",
            f"{len(existing)} file(s) already have results.\n"
            "Recalculate and overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

    page._cluster_running = True
    page._cluster_cancel = False
    page._cluster_status.setText("Calculating…")
    page._cluster_calc_btn.setEnabled(False)

    q = queue.Queue()
    page._cluster_queue = q
    page._cluster_thread = threading.Thread(
        target=partial(_cluster_run, ctx), args=(page, q), daemon=True
    )
    page._cluster_thread.start()

    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _cluster_poll(ctx, page))
    timer.start()
    page._cluster_timer = timer


def _cluster_poll(ctx, page) -> None:
    q = getattr(page, "_cluster_queue", None)
    if q is None:
        return
    finished = False
    error = None
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            break
        kind = msg[0]
        if kind == "log":
            _cluster_log(ctx, page, msg[1])
        elif kind == "status":
            page._cluster_status.setText(msg[1])
        elif kind == "done":
            outputs, done, failed = msg[1], msg[2], msg[3]
            params = page._cluster_params
            params["outputs"] = outputs
            page._cluster_task_def["output"] = {"saved": bool(done)}
            page._cluster_task_def["params"] = params
            if ctx.project is not None:
                ctx.project.save()
            page._cluster_snapshot = copy.deepcopy(params)
            page._cluster_dirty = False
            _cluster_update_save_btn(ctx, page)
            page._cluster_done = done
            page._cluster_failed = failed
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    page._cluster_running = False
    timer = getattr(page, "_cluster_timer", None)
    if timer is not None:
        timer.stop()
        page._cluster_timer = None
    _cluster_update_calc_btn(ctx, page)
    if error:
        _cluster_log(ctx, page, f"✗ {error}")
        QMessageBox.warning(ctx.host, "Calculate", error)
        page._cluster_status.setText("Failed")
    else:
        summary = f"Calculated {page._cluster_done} file(s)."
        if getattr(page, "_cluster_failed", 0):
            summary += f" {page._cluster_failed} failed — see the log."
        page._cluster_status.setText(summary)
        _cluster_log(ctx, page, summary)
        ctx.statusBar().showMessage(summary)
        ctx._module_saved(page)


def _cluster_mark_dirty(ctx, page) -> None:
    page._cluster_dirty = True
    _cluster_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _cluster_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_cluster_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._cluster_dirty and not page._cluster_locked)


def _cluster_save_now(ctx, page) -> None:
    params = page._cluster_params
    page._cluster_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._cluster_snapshot = copy.deepcopy(params)
    page._cluster_dirty = False
    _cluster_update_save_btn(ctx, page)
    _cluster_log(ctx, page, "Settings saved")
    ctx.statusBar().showMessage("Cluster settings saved")
    ctx._module_saved(page)


def _cluster_close(ctx, page) -> None:
    if getattr(page, "_cluster_running", False):
        reply = QMessageBox.question(
            ctx.host, "Close Cluster",
            "A calculation is still running.\nStop it and close? "
            "This run's results will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        page._cluster_cancel = True
        _cluster_log(ctx, page, "Stopping after the current file…")
        return
    if getattr(page, "_cluster_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Cluster",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log_lines = list(getattr(page, "_cluster_log_lines", []) or [])
        page._cluster_task_def["params"] = page._cluster_snapshot
        page._cluster_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)
