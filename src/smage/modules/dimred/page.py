"""Dimension Reduction module: PCA / UMAP / t-SNE."""
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
from smage.data import project_paths as _project_paths
from smage.data import sampling as _data_sampling
from smage.widgets import image_raster as _widgets_image_raster
from smage.widgets import panels as _widgets_panels
from smage.widgets import picker as _widgets_picker


_DIMRED_SEED = 42

_DIMRED_PCA_DIMS = 50

def _dimred_signature(ctx, params, parquet_path, task_id=None) -> str:
    """Change signature for one file's dimensionality-reduction output."""
    try:
        st = Path(parquet_path).stat()
        stat = f"{int(st.st_size)}-{int(st.st_mtime_ns)}"
    except OSError:
        stat = ""
    payload = {
        "method": params.get("method"),
        "normalize": params.get("normalize"),
        "pca": params.get("pca"),
        "parallel": params.get("parallel"),
        "tsne_backend": params.get("tsne_backend"),
        "max_events": params.get("max_events"),
        "columns": params.get("columns") or [],
        "names": params.get("names") or [],
        "umap": params.get("umap") or {},
        "tsne": params.get("tsne") or {},
        "source": _project_paths._stable_path_key(ctx, parquet_path),
        "stat": stat,
    }
    sample = _data_sampling._subsample_sig(ctx, task_id, parquet_path)
    if sample:
        payload["sample"] = sample
    payload = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _dimred_params_for(ctx, task_def) -> dict:
    """Saved params of a Dimension Reduction task (its live snapshot)."""
    task_id = task_def.get("id")
    reg = ctx._module_registry.get(task_id)
    if reg is not None and reg.get("task_type") == "dimred":
        snap = getattr(reg["page"], "_dimred_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return task_def.get("params") or {}


def _dimred_column_values(ctx, parquet_path, rec, name):
    """Row-aligned float64 values of one Dimension Reduction column."""
    import numpy as np
    import pandas as pd

    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None
    dparquet = rec["parquet"]
    if rec.get("kind") == "image":
        man = _data_image_index._image_load_manifest(ctx, dparquet)
        if man is None:
            return None
        try:
            src = pd.read_parquet(parquet_path, columns=["x", "y"])
            ddf = pd.read_parquet(dparquet, columns=["x", "y", name])
        except Exception:  # noqa: BLE001
            return None
        xmin = float(man["xmin"])
        ymin = float(man["ymin"])
        width = int(round(float(man["xmax"]) - xmin)) + 1
        height = int(round(float(man["ymax"]) - ymin)) + 1
        dxi = np.rint(ddf["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        dyi = np.rint(ddf["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        grid = np.full((height, width), np.nan, dtype="float64")
        dok = (dxi >= 0) & (dxi < width) & (dyi >= 0) & (dyi < height)
        grid[dyi[dok], dxi[dok]] = ddf[name].to_numpy(dtype="float64")[dok]
        sxi = np.rint(src["x"].to_numpy(dtype="float64") - xmin).astype(np.int64)
        syi = np.rint(src["y"].to_numpy(dtype="float64") - ymin).astype(np.int64)
        sok = (sxi >= 0) & (sxi < width) & (syi >= 0) & (syi < height)
        out = np.full(len(src), np.nan, dtype="float64")
        out[sok] = grid[syi[sok], sxi[sok]]
        return out
    try:
        df = pd.read_parquet(dparquet, columns=[name])
    except Exception:  # noqa: BLE001
        return None
    return df[name].to_numpy(dtype="float64")


def _dimred_raster(ctx, rec, bbox, spacing):
    """Return (scalar_2d, data_rect) for a Dimension Reduction channel."""
    if not isinstance(rec, dict) or not rec.get("parquet"):
        return None, None
    ent = _data_image_index._ensure_image_index(ctx, rec["parquet"])
    if ent is None or ent.get("manifest") is None:
        return None, None
    crop, rect, _frac = _widgets_image_raster._image_raster(ctx,
        rec["parquet"], ent["manifest"], bbox, spacing, rec.get("channel"), None, None
    )
    return crop, rect


def _build_dimred_page(ctx, name: str, task_def: dict, locked: bool) -> QWidget:
    """Build the Dimension Reduction page: method/files | variables | params | log."""
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
    close_btn.clicked.connect(lambda _=False: _dimred_close(ctx, page))
    tl.addWidget(save_btn)
    tl.addWidget(close_btn)
    layout.addWidget(bar)

    params = task_def.setdefault("params", {})
    params.setdefault("method", "umap")
    params.setdefault("files", [])
    params.setdefault("columns", [])
    params.setdefault("normalize", True)
    params.setdefault("pca", True)
    params.setdefault("parallel", True)
    params.setdefault("tsne_backend", "sklearn")
    params.setdefault("max_events", 5000)
    params.setdefault("umap", {"n_neighbors": 15, "min_dist": 0.1})
    params.setdefault("tsne", {"perplexity": 30, "n_iter": 1000})
    params.setdefault("names", ["umap_1", "umap_2"])
    params.setdefault("outputs", {})
    params.setdefault("log", [])
    page._dimred_task_def = task_def
    page._dimred_task_id = task_def.get("id")
    page._dimred_params = params
    page._dimred_locked = locked
    page._dimred_save_btn = save_btn
    page._dimred_dirty = False
    page._dimred_snapshot = copy.deepcopy(params)
    page._dimred_log_lines = list(params.get("log") or [])
    page._dimred_running = False
    page._dimred_cancel = False
    page._dimred_updating = False
    save_btn.clicked.connect(lambda _=False: _dimred_save_now(ctx, page))

    content = QWidget()
    content.setStyleSheet("background: #f5f8fb;")
    row = QVBoxLayout(content)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(10)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(_dimred_left_panel(ctx, page))
    splitter.addWidget(_dimred_center_panel(ctx, page))
    splitter.addWidget(_dimred_right_panel(ctx, page))
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 1)
    splitter.setSizes([320, 320, 420])
    row.addWidget(splitter, 1)

    row.addWidget(_dimred_log_panel(ctx, page))
    layout.addWidget(content, 1)

    _dimred_refresh(ctx, page)
    _dimred_warm_umap(ctx)
    return page


def _dimred_warm_umap(ctx) -> None:
    """Pre-compile UMAP's numba kernels in the background.

    UMAP's first fit_transform in a process spends ~30 s on numba JIT
    compilation (the "cold" run). Warming it up while the user configures
    the module hides that one-time cost from their first Calculate.
    """
    if getattr(ctx, "_umap_warmed", False):
        return
    ctx._umap_warmed = True

    def _warm():
        import numpy as np
        import umap

        try:
            umap.UMAP(
                n_neighbors=15, min_dist=0.1, n_components=2, random_state=42
            ).fit_transform(
                np.random.default_rng(0).normal(0, 1, (300, 4))
            )
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_warm, daemon=True).start()


def _dimred_warm_opentsne(ctx) -> None:
    """Pre-compile openTSNE's numba kernels the first time it is chosen."""
    if getattr(ctx, "_opentsne_warmed", False):
        return
    ctx._opentsne_warmed = True

    def _warm():
        import numpy as np

        try:
            from openTSNE import TSNE

            TSNE(
                n_components=2, perplexity=30, n_iter=50, random_state=42
            ).fit(np.random.default_rng(0).normal(0, 1, (300, 4)))
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_warm, daemon=True).start()


def _dimred_log_panel(ctx, page) -> QWidget:
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
    for line in getattr(page, "_dimred_log_lines", []):
        log.appendPlainText(line)
    lay.addWidget(log)
    page._dimred_log = log
    return panel


def _dimred_log(ctx, page, text) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    lines = getattr(page, "_dimred_log_lines", None)
    if lines is None:
        lines = []
        page._dimred_log_lines = lines
    for line in str(text).splitlines():
        if line.strip():
            lines.append(f"[{ts}] {line}")
    if len(lines) > _FEATURE_LOG_LIMIT:
        del lines[:-_FEATURE_LOG_LIMIT]
    if getattr(page, "_dimred_params", None) is not None:
        page._dimred_params["log"] = list(lines)
    box = getattr(page, "_dimred_log", None)
    if box is not None:
        for line in str(text).splitlines():
            if line.strip():
                box.appendPlainText(f"[{ts}] {line}")


def _dimred_left_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Algorithm</b>"))
    method = _NoWheelComboBox()
    method.addItem("UMAP", "umap")
    method.addItem("t-SNE", "tsne")
    method.setEnabled(not page._dimred_locked)
    lay.addWidget(method)
    page._dimred_method = method

    lay.addWidget(QLabel("<b>Data files</b>"))
    files_btn = QPushButton("Select files…")
    files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    files_btn.setEnabled(not page._dimred_locked)
    lay.addWidget(files_btn)
    files_box = QListWidget()
    files_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(files_box, 1)
    page._dimred_files_box = files_box
    files_count = QLabel("")
    files_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(files_count)
    page._dimred_files_count = files_count

    method.currentIndexChanged.connect(lambda _=0: _dimred_on_method(ctx, page))
    files_btn.clicked.connect(lambda _=False: _dimred_select_files(ctx, page))
    return panel


def _dimred_center_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Channels</b>"))
    hint = QLabel(
        "Channels shared by every selected file. All files are reduced "
        "together in one embedding, so only common channels can be used."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #6b7785;")
    lay.addWidget(hint)
    vars_btn = QPushButton("Select channels…")
    vars_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    vars_btn.setEnabled(not page._dimred_locked)
    lay.addWidget(vars_btn)
    vars_box = QListWidget()
    vars_box.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    lay.addWidget(vars_box, 1)
    page._dimred_vars_box = vars_box
    vars_count = QLabel("")
    vars_count.setStyleSheet("color: #6b7785;")
    lay.addWidget(vars_count)
    page._dimred_vars_count = vars_count

    vars_btn.clicked.connect(lambda _=False: _dimred_select_vars(ctx, page))
    return panel


def _dimred_right_panel(ctx, page) -> QWidget:
    panel = _widgets_panels._build_panel_frame(ctx)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(6)
    lay.addWidget(QLabel("<b>Algorithm parameters</b>"))

    lay.addWidget(QLabel("Max events per file"))
    max_events = _NoWheelSpinBox()
    max_events.setRange(0, 2_000_000_000)
    max_events.setValue(5000)
    max_events.setEnabled(not page._dimred_locked)
    lay.addWidget(max_events)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Randomly cap the points each table (flow) file contributes "
        "(0 = use everything). 5,000–10,000 per file is a good default for "
        "UMAP, fewer for t-SNE. Image files always use every pixel. Fixed "
        "seed, so the sample is reproducible."
    ))
    page._dimred_max_events = max_events
    max_events.valueChanged.connect(lambda _=0: _dimred_on_max_events(ctx, page))

    # UMAP parameters
    umap_host = QWidget()
    umap_l = QGridLayout(umap_host)
    umap_l.setContentsMargins(0, 0, 0, 0)
    umap_l.setVerticalSpacing(2)
    umap_l.setColumnStretch(1, 1)
    row = 0
    umap_l.addWidget(QLabel("n_neighbors"), row, 0)
    nn = _NoWheelSpinBox()
    nn.setRange(2, 500)
    nn.setValue(15)
    umap_l.addWidget(nn, row, 1)
    row += 1
    umap_l.addWidget(_widgets_panels._param_hint(ctx,
        "Neighbours each point balances. Higher = more global structure."
    ), row, 0, 1, 2)
    row += 1
    umap_l.addWidget(QLabel("min_dist"), row, 0)
    md = _NoWheelDoubleSpinBox()
    md.setRange(0.0, 0.99)
    md.setDecimals(3)
    md.setSingleStep(0.01)
    md.setValue(0.1)
    umap_l.addWidget(md, row, 1)
    row += 1
    umap_l.addWidget(_widgets_panels._param_hint(ctx,
        "How tightly points may pack. Lower = denser, more separated clumps."
    ), row, 0, 1, 2)
    page._dimred_umap_host = umap_host
    page._dimred_nn = nn
    page._dimred_md = md

    # t-SNE parameters
    tsne_host = QWidget()
    tsne_l = QGridLayout(tsne_host)
    tsne_l.setContentsMargins(0, 0, 0, 0)
    tsne_l.setVerticalSpacing(2)
    tsne_l.setColumnStretch(1, 1)
    row = 0
    tsne_l.addWidget(QLabel("perplexity"), row, 0)
    perp = _NoWheelDoubleSpinBox()
    perp.setRange(2.0, 500.0)
    perp.setDecimals(1)
    perp.setValue(30.0)
    tsne_l.addWidget(perp, row, 1)
    row += 1
    tsne_l.addWidget(_widgets_panels._param_hint(ctx,
        "Roughly the number of neighbours per point; 5–50 is typical."
    ), row, 0, 1, 2)
    row += 1
    tsne_l.addWidget(QLabel("n_iter"), row, 0)
    nit = _NoWheelSpinBox()
    nit.setRange(250, 10000)
    nit.setValue(1000)
    tsne_l.addWidget(nit, row, 1)
    row += 1
    tsne_l.addWidget(_widgets_panels._param_hint(ctx,
        "Optimisation steps. More = slower but more settled."
    ), row, 0, 1, 2)
    row += 1
    backend_label = QLabel("implementation")
    tsne_l.addWidget(backend_label, row, 0)
    backend = _NoWheelComboBox()
    backend.addItem("sklearn (default)", "sklearn")
    backend.addItem("openTSNE (experimental)", "opentsne")
    tsne_l.addWidget(backend, row, 1)
    row += 1
    tsne_l.addWidget(_widgets_panels._param_hint(ctx,
        "sklearn is the default; openTSNE is experimental and was slower "
        "on this machine."
    ), row, 0, 1, 2)
    page._dimred_tsne_host = tsne_host
    page._dimred_perp = perp
    page._dimred_nit = nit
    page._dimred_backend = backend
    page._dimred_backend_label = backend_label

    lay.addWidget(umap_host)
    lay.addWidget(tsne_host)

    normalize = QCheckBox("Normalize (z-score)")
    normalize.setChecked(True)
    normalize.setEnabled(not page._dimred_locked)
    lay.addWidget(normalize)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Z-score every channel first, so no channel dominates the distance."
    ))
    page._dimred_normalize = normalize

    pca = QCheckBox("PCA pre-reduction (faster)")
    pca.setChecked(True)
    pca.setEnabled(not page._dimred_locked)
    lay.addWidget(pca)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Reduce to 50 dimensions with PCA before fitting, when more "
        "channels are selected. Standard practice, usually faster."
    ))
    page._dimred_pca = pca

    parallel = QCheckBox("Allow parallel (non-reproducible)")
    parallel.setChecked(True)
    parallel.setEnabled(not page._dimred_locked)
    lay.addWidget(parallel)
    lay.addWidget(_widgets_panels._param_hint(ctx,
        "Use every CPU core. Much faster, but the result changes on every "
        "run (the fixed seed is dropped)."
    ))
    page._dimred_parallel = parallel

    lay.addWidget(QLabel("<b>New channel names</b>"))
    name_box = QWidget()
    nh = QGridLayout(name_box)
    nh.setContentsMargins(0, 0, 0, 0)
    nh.addWidget(QLabel("Dim 1"), 0, 0)
    name1 = QLineEdit()
    nh.addWidget(name1, 0, 1)
    warn1 = QLabel("⚠")
    warn1.setToolTip("Duplicate channel name")
    warn1.setStyleSheet("color: #b58900; font-weight: bold;")
    nh.addWidget(warn1, 0, 2)
    nh.addWidget(QLabel("Dim 2"), 1, 0)
    name2 = QLineEdit()
    nh.addWidget(name2, 1, 1)
    warn2 = QLabel("⚠")
    warn2.setToolTip("Duplicate channel name")
    warn2.setStyleSheet("color: #b58900; font-weight: bold;")
    nh.addWidget(warn2, 1, 2)
    lay.addWidget(name_box)
    page._dimred_name1 = name1
    page._dimred_name2 = name2
    page._dimred_warn1 = warn1
    page._dimred_warn2 = warn2

    estimate = QLabel("")
    estimate.setStyleSheet("color: #6b7785;")
    estimate.setWordWrap(True)
    lay.addWidget(estimate)
    page._dimred_estimate = estimate

    calc_btn = QPushButton("Calculate")
    calc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    calc_btn.setEnabled(False)
    lay.addWidget(calc_btn)
    page._dimred_calc_btn = calc_btn
    status = QLabel("")
    status.setStyleSheet("color: #6b7785;")
    status.setWordWrap(True)
    lay.addWidget(status)
    page._dimred_status = status
    lay.addStretch()

    for w in (nn, md, perp, nit):
        w.setEnabled(not page._dimred_locked)
    name1.setEnabled(not page._dimred_locked)
    name2.setEnabled(not page._dimred_locked)

    nn.valueChanged.connect(lambda _=0: _dimred_on_param(ctx, page, "umap", "n_neighbors", nn.value()))
    md.valueChanged.connect(lambda _=0: _dimred_on_param(ctx, page, "umap", "min_dist", md.value()))
    perp.valueChanged.connect(lambda _=0: _dimred_on_param(ctx, page, "tsne", "perplexity", perp.value()))
    nit.valueChanged.connect(lambda _=0: _dimred_on_param(ctx, page, "tsne", "n_iter", nit.value()))
    backend.currentIndexChanged.connect(lambda _=0: _dimred_on_backend(ctx, page))
    normalize.toggled.connect(lambda _=0: _dimred_on_normalize(ctx, page))
    pca.toggled.connect(lambda _=0: _dimred_on_pca(ctx, page))
    parallel.toggled.connect(lambda _=0: _dimred_on_parallel(ctx, page))
    name1.textEdited.connect(lambda _=0: _dimred_on_names(ctx, page))
    name2.textEdited.connect(lambda _=0: _dimred_on_names(ctx, page))
    calc_btn.clicked.connect(lambda _=False: _dimred_calculate(ctx, page))
    return panel


def _dimred_is_tsne(ctx, method) -> bool:
    return method == "tsne"


def _dimred_sample_seed(ctx, page, path) -> int:
    """Stable per-(module, file) seed for the built-in per-file downsample."""
    payload = f"smage-dimred-sample-v1|{page._dimred_task_id}|{path}"
    return int(hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16], 16)


def _dimred_on_method(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    method = page._dimred_method.currentData() or "umap"
    params = page._dimred_params
    old = params.get("method", "umap")
    old_defaults = ["umap_1", "umap_2"] if not _dimred_is_tsne(ctx, old) else ["tsne_1", "tsne_2"]
    new_defaults = ["tsne_1", "tsne_2"] if _dimred_is_tsne(ctx, method) else ["umap_1", "umap_2"]
    if params.get("names") == old_defaults:
        params["names"] = list(new_defaults)
    params["method"] = method
    _dimred_refresh(ctx, page)
    _dimred_mark_dirty(ctx, page)


def _dimred_on_param(ctx, page, group, key, value) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    page._dimred_params.setdefault(group, {})[key] = value
    _dimred_mark_dirty(ctx, page)


def _dimred_on_normalize(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    page._dimred_params["normalize"] = page._dimred_normalize.isChecked()
    _dimred_mark_dirty(ctx, page)


def _dimred_on_pca(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    page._dimred_params["pca"] = page._dimred_pca.isChecked()
    _dimred_mark_dirty(ctx, page)


def _dimred_on_parallel(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    page._dimred_params["parallel"] = page._dimred_parallel.isChecked()
    _dimred_mark_dirty(ctx, page)
    _dimred_refresh(ctx, page)


def _dimred_on_max_events(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    page._dimred_params["max_events"] = int(page._dimred_max_events.value())
    _dimred_mark_dirty(ctx, page)
    _dimred_refresh(ctx, page)


def _dimred_on_backend(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    value = page._dimred_backend.currentData() or "sklearn"
    page._dimred_params["tsne_backend"] = value
    _dimred_mark_dirty(ctx, page)
    if value == "opentsne":
        _dimred_warm_opentsne(ctx)


def _dimred_on_names(ctx, page) -> None:
    if getattr(page, "_dimred_updating", False):
        return
    page._dimred_params["names"] = [
        page._dimred_name1.text().strip(),
        page._dimred_name2.text().strip(),
    ]
    _dimred_mark_dirty(ctx, page)
    _dimred_update_names(ctx, page)


def _dimred_common_columns(ctx, page) -> list:
    files = page._dimred_params.get("files") or []
    if not files:
        return []
    common = set(_data_columns._columns_for(ctx, files[0], page._dimred_task_id))
    for p in files[1:]:
        common &= set(_data_columns._columns_for(ctx, p, page._dimred_task_id))
    return sorted(common)


def _dimred_select_files(ctx, page) -> None:
    records = _data_files._available_files(ctx, page._dimred_task_id)
    names = [rec.get("name", "") for rec in records]
    by_name = {rec.get("name", ""): rec.get("parquet") for rec in records}
    current = [
        _data_naming._comp_file_name(ctx, p, page._dimred_task_id)
        for p in page._dimred_params.get("files") or []
    ]
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select data files", names, current, "Filter files..."
    )
    if chosen is None:
        return
    page._dimred_params["files"] = [by_name[n] for n in chosen if n in by_name]
    # Variables that are no longer common are dropped.
    common = set(_dimred_common_columns(ctx, page))
    page._dimred_params["columns"] = [
        c for c in page._dimred_params.get("columns") or [] if c in common
    ]
    _dimred_refresh(ctx, page)
    _dimred_mark_dirty(ctx, page)


def _dimred_select_vars(ctx, page) -> None:
    files = page._dimred_params.get("files") or []
    if not files:
        QMessageBox.information(
            ctx.host, "Select channels", "Select data files first."
        )
        return
    options = _dimred_common_columns(ctx, page)
    if not options:
        QMessageBox.information(
            ctx.host, "Select channels",
            "The selected files have no common channels.\n"
            "Dimension reduction needs channels shared by every file.",
        )
        return
    chosen = _widgets_picker._pick_items_dialog(ctx,
        "Select channels",
        options,
        page._dimred_params.get("columns") or [],
        "Filter channels...",
    )
    if chosen is None:
        return
    page._dimred_params["columns"] = list(chosen)
    _dimred_refresh(ctx, page)
    _dimred_mark_dirty(ctx, page)


def _dimred_existing_columns(ctx, page) -> set:
    cols = set()
    for path in page._dimred_params.get("files") or []:
        cols |= set(_data_columns._columns_for(ctx, path, page._dimred_task_id))
    return cols


def _dimred_name_conflicts(ctx, page) -> list:
    names = page._dimred_params.get("names") or ["", ""]
    existing = _dimred_existing_columns(ctx, page)
    conflicts = []
    for name in names:
        if name and name in existing:
            conflicts.append(name)
    if names[0] and names[1] and names[0] == names[1]:
        if names[0] not in conflicts:
            conflicts.append(names[0])
    return conflicts


def _dimred_update_names(ctx, page) -> None:
    conflicts = set(_dimred_name_conflicts(ctx, page))
    names = page._dimred_params.get("names") or ["", ""]
    page._dimred_warn1.setVisible(bool(names[0]) and names[0] in conflicts)
    page._dimred_warn2.setVisible(bool(names[1]) and names[1] in conflicts)
    _dimred_update_calc_btn(ctx, page)


def _dimred_update_calc_btn(ctx, page) -> None:
    params = page._dimred_params
    names = params.get("names") or ["", ""]
    ok = bool(
        params.get("files")
        and params.get("columns")
        and names[0] and names[1]
        and names[0] != names[1]
        and not _dimred_name_conflicts(ctx, page)
    )
    page._dimred_calc_btn.setEnabled(ok and not page._dimred_locked)


def _dimred_estimate(ctx, page):
    """Rough pre-run runtime estimate (order of magnitude), or None."""
    params = page._dimred_params
    files = params.get("files") or []
    columns = params.get("columns") or []
    if not files or not columns:
        return None
    try:
        max_events = int(params.get("max_events") or 0)
        n = 0
        for p in files:
            vis = _data_sampling._subsample_visible_count(ctx, page._dimred_task_id, p)
            n += min(vis, max_events) if (max_events > 0 and _data_image_index._ensure_image_index(ctx, p) is None) else vis
    except Exception:  # noqa: BLE001
        return None
    if n <= 0:
        return None
    d = len(columns)
    d_eff = min(d, _DIMRED_PCA_DIMS) if params.get("pca", True) else d
    method = params.get("method", "umap")
    parallel = bool(params.get("parallel", False))
    if method == "umap":
        nn = int((params.get("umap") or {}).get("n_neighbors", 15))
        # Measured on a reference laptop: ~50 s per 1e6 point·dims (serial,
        # because the fixed reproducibility seed disables parallelism).
        secs = n * d_eff / 1e6 * 50.0 * max(0.5, nn / 15.0)
        if parallel:
            secs /= 5.0  # parallel uses every core
    else:
        n_iter = int((params.get("tsne") or {}).get("n_iter", 1000))
        secs = n * d_eff / 1e6 * 280.0 * (n_iter / 1000.0)
        if parallel:
            secs /= 2.0
    return secs, n, d_eff


def _dimred_estimate_text(ctx, page) -> str:
    est = _dimred_estimate(ctx, page)
    if est is None:
        return ""
    secs, n, d_eff = est
    parts = [f"{n:,} points × {d_eff} dims"]
    if secs < 60:
        parts.append("≈ %ds" % max(1, int(round(secs))))
    elif secs < 3600:
        parts.append("≈ %.1f min" % (secs / 60.0))
    else:
        parts.append("≈ %.1f h" % (secs / 3600.0))
    last = page._dimred_params.get("last_fit_seconds")
    if last:
        parts.append("· last run %.0fs" % last)
    return " · ".join(parts)


def _dimred_refresh(ctx, page) -> None:
    params = page._dimred_params
    page._dimred_updating = True
    try:
        page._dimred_method.blockSignals(True)
        page._dimred_method.setCurrentIndex(
            max(0, page._dimred_method.findData(params.get("method", "umap")))
        )
        page._dimred_method.blockSignals(False)

        umap = params.get("umap") or {}
        tsne = params.get("tsne") or {}
        page._dimred_nn.blockSignals(True)
        page._dimred_nn.setValue(int(umap.get("n_neighbors", 15)))
        page._dimred_nn.blockSignals(False)
        page._dimred_md.blockSignals(True)
        page._dimred_md.setValue(float(umap.get("min_dist", 0.1)))
        page._dimred_md.blockSignals(False)
        page._dimred_perp.blockSignals(True)
        page._dimred_perp.setValue(float(tsne.get("perplexity", 30.0)))
        page._dimred_perp.blockSignals(False)
        page._dimred_nit.blockSignals(True)
        page._dimred_nit.setValue(int(tsne.get("n_iter", 1000)))
        page._dimred_nit.blockSignals(False)

        page._dimred_backend.blockSignals(True)
        page._dimred_backend.setCurrentIndex(
            max(0, page._dimred_backend.findData(
                params.get("tsne_backend", "sklearn")
            ))
        )
        page._dimred_backend.blockSignals(False)

        page._dimred_umap_host.setVisible(params.get("method") == "umap")
        page._dimred_tsne_host.setVisible(params.get("method") == "tsne")

        page._dimred_normalize.blockSignals(True)
        page._dimred_normalize.setChecked(bool(params.get("normalize", True)))
        page._dimred_normalize.blockSignals(False)

        page._dimred_pca.blockSignals(True)
        page._dimred_pca.setChecked(bool(params.get("pca", True)))
        page._dimred_pca.blockSignals(False)

        page._dimred_parallel.blockSignals(True)
        page._dimred_parallel.setChecked(bool(params.get("parallel", False)))
        page._dimred_parallel.blockSignals(False)

        page._dimred_max_events.blockSignals(True)
        page._dimred_max_events.setValue(int(params.get("max_events", 5000)))
        page._dimred_max_events.blockSignals(False)

        names = params.get("names") or ["umap_1", "umap_2"]
        page._dimred_name1.blockSignals(True)
        page._dimred_name1.setText(names[0])
        page._dimred_name1.blockSignals(False)
        page._dimred_name2.blockSignals(True)
        page._dimred_name2.setText(names[1])
        page._dimred_name2.blockSignals(False)

        files = params.get("files") or []
        page._dimred_files_box.clear()
        for path in files:
            page._dimred_files_box.addItem(
                _data_naming._comp_file_name(ctx, path, page._dimred_task_id)
            )
        page._dimred_files_count.setText(f"{len(files)} file(s) selected")

        columns = params.get("columns") or []
        page._dimred_vars_box.clear()
        for c in columns:
            page._dimred_vars_box.addItem(str(c))
        page._dimred_vars_count.setText(f"{len(columns)} channel(s) selected")
    finally:
        page._dimred_updating = False
    _dimred_update_names(ctx, page)
    page._dimred_estimate.setText(_dimred_estimate_text(ctx, page))


def _dimred_validate(ctx, page):
    params = page._dimred_params
    if not params.get("files"):
        return False, "Select at least one data file."
    if not params.get("columns"):
        return False, "Select at least one channel."
    names = params.get("names") or ["", ""]
    if not names[0] or not names[1]:
        return False, "Set both new channel names."
    if names[0] == names[1]:
        return False, "The two new channel names must differ."
    if _dimred_name_conflicts(ctx, page):
        return False, "A new channel name collides with an existing column."
    return True, ""


def _dimred_run(ctx, page, q):
    """Run the whole reduction in a background thread, posting progress."""
    import time

    import numpy as np

    params = page._dimred_params
    files = list(params.get("files") or [])
    columns = list(params.get("columns") or [])
    n_files = len(files)
    failed = 0

    class _FitWriter:
        """Captures the fit library's stdout and turns it into progress."""

        def __init__(ctx, q, method, total_iter):
            ctx.q = q
            ctx.method = method
            ctx.total_iter = total_iter
            ctx.start = time.time()
            ctx._buf = ""

        def write(ctx, text):
            ctx._buf += text.replace("\r", "\n")
            while "\n" in ctx._buf:
                line, ctx._buf = ctx._buf.split("\n", 1)
                ctx._parse(line.strip())

        def flush(ctx):
            if ctx._buf.strip():
                ctx._parse(ctx._buf.strip())
                ctx._buf = ""

        def _parse(ctx, line):
            import re

            if not line:
                return
            pct = None
            if ctx.method == "umap":
                # UMAP (tqdm) prints "… 123/500 …" or "completed 123 / 500".
                m = re.search(r"(\d+)\s*/\s*(\d+)", line)
                if m:
                    total = int(m.group(2))
                    pct = 100.0 * int(m.group(1)) / total if total else None
            else:
                m = re.search(r"Iteration\s+(\d+)", line)
                if m and ctx.total_iter:
                    pct = 100.0 * int(m.group(1)) / ctx.total_iter
            if pct is None:
                return
            elapsed = time.time() - ctx.start
            eta = None
            if pct > 0:
                total = elapsed * 100.0 / pct
                eta = max(0.0, total - elapsed)
            ctx.q.put(("fitprogress", min(pct, 100.0), eta))

    try:
        q.put(("log", "— Starting dimension reduction —"))
        parts = []
        counts = []
        row_indices = []
        max_events = int(params.get("max_events") or 0)
        total_before = 0
        total_after = 0
        for i, path in enumerate(files):
            name = _data_naming._comp_file_name(ctx, path, page._dimred_task_id)
            q.put(("log", f"Reading {i + 1}/{n_files}: {name}"))
            q.put(("status", f"Reading {i + 1}/{n_files} ({25 * (i + 1) // n_files}%)"))
            values = _data_columns._column_data(ctx, page._dimred_task_id, path, columns)
            if any(c not in values for c in columns):
                raise ValueError(f"{name} is missing a channel")
            X = np.column_stack([values[c] for c in columns]).astype("float64")
            n_vis = len(X)
            total_before += n_vis
            up_idx = _data_sampling._subsample_row_index(ctx, page._dimred_task_id, path)
            full_rows = (
                np.asarray(up_idx, dtype=np.int64)
                if up_idx is not None else np.arange(n_vis, dtype=np.int64)
            )
            # This module's own per-file downsample (like FlowJo). Pixel-level
            # (image) files are reduced in full: downsampling would leave
            # holes that render black.
            is_image = _data_image_index._ensure_image_index(ctx, path) is not None
            if max_events > 0 and not is_image and n_vis > max_events:
                seed = _dimred_sample_seed(ctx, page, path)
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
            raise ValueError("no data to reduce")
        X = np.vstack(parts)
        q.put(("log", f"Combined {X.shape[0]} row(s) × {X.shape[1]} channel(s)"))
        if max_events > 0 and total_after < total_before:
            q.put(("log", f"Downsampled {total_before:,} → {total_after:,} points "
                          f"(max {max_events:,} per file)"))

        pca_on = bool(params.get("pca", True)) and X.shape[1] > _DIMRED_PCA_DIMS
        q.put(("log", "Normalizing (z-score)…" if params.get("normalize")
               else "Normalization skipped"))
        X = _dimred_prepare(ctx, X, bool(params.get("normalize")), pca_on)
        if pca_on:
            q.put(("log", f"PCA: reduced to {X.shape[1]} dimensions (faster)"))

        method = params.get("method", "umap")
        base = "tsne" if _dimred_is_tsne(ctx, method) else "umap"
        cfg = params.get("umap") if base == "umap" else params.get("tsne")
        detail = ", ".join(f"{k}={v}" for k, v in (cfg or {}).items())
        q.put(("log", f"Fitting {method.upper()} ({detail})…"))
        q.put(("status", "Fitting… (25%)"))
        t0 = time.time()
        writer = _FitWriter(q, base, int((cfg or {}).get("n_iter", 1000)))
        import contextlib
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            embedding = _dimred_fit(ctx,
                base, cfg, X, _DIMRED_SEED,
                verbose=True, parallel=bool(params.get("parallel", False)),
                tsne_backend=params.get("tsne_backend", "sklearn"),
            )
        fit_seconds = time.time() - t0
        q.put(("fit_time", fit_seconds))
        q.put(("log", f"Fit finished in {fit_seconds:.1f}s"))

        outputs = {}
        offset = 0
        names = params.get("names") or ["dim_1", "dim_2"]
        for i, (path, n) in enumerate(zip(files, counts)):
            if getattr(page, "_dimred_cancel", False):
                q.put(("log", "Calculation cancelled."))
                break
            name = _data_naming._comp_file_name(ctx, path, page._dimred_task_id)
            coords = embedding[offset:offset + n]
            offset += n
            q.put(("status", f"Writing {i + 1}/{n_files} ({90 + 10 * (i + 1) // n_files}%)"))
            try:
                rel, cols, kind = _dimred_write_file(ctx,
                    page, path, coords, row_indices[i]
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                q.put(("log", f"✗ {name}: {exc}"))
                continue
            outputs[path] = {
                "path": rel,
                "columns": cols,
                "kind": kind,
                "signature": _dimred_signature(ctx,
                    params, path, page._dimred_task_id
                ),
            }
            q.put(("log", f"✓ {name} → {Path(rel).name} ({len(cols)} channel(s))"))
        q.put(("done", outputs, len(outputs), failed))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


def _dimred_fit(ctx, method, cfg, X, seed, verbose=False, parallel=False, tsne_backend='sklearn'):
    """Fit the chosen algorithm and return the (N, 2) embedding.

    `parallel` trades reproducibility for speed: the algorithms then use
    every CPU core (and drop the fixed seed).
    """
    if method == "umap":
        import umap

        reducer = umap.UMAP(
            n_neighbors=int(cfg.get("n_neighbors", 15)),
            min_dist=float(cfg.get("min_dist", 0.1)),
            n_components=2,
            random_state=None if parallel else seed,
            n_jobs=-1 if parallel else 1,
            verbose=verbose,
        )
        return reducer.fit_transform(X)
    if tsne_backend == "opentsne":
        from openTSNE import TSNE as OpenTSNE

        model = OpenTSNE(
            n_components=2,
            perplexity=float(cfg.get("perplexity", 30.0)),
            n_iter=int(cfg.get("n_iter", 1000)),
            random_state=None if parallel else seed,
            n_jobs=-1 if parallel else 1,
            verbose=False,
        )
        return model.fit(X)
    from sklearn.manifold import TSNE

    model = TSNE(
        n_components=2,
        perplexity=float(cfg.get("perplexity", 30.0)),
        max_iter=int(cfg.get("n_iter", 1000)),
        init="pca",
        random_state=None if parallel else seed,
        n_jobs=-1 if parallel else 1,
        verbose=1 if verbose else 0,
    )
    return model.fit_transform(X)


def _dimred_prepare(ctx, X, normalize, pca):
    """Fill missing values with column means, optionally z-score, then PCA."""
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
    if pca and X.shape[1] > _DIMRED_PCA_DIMS:
        from sklearn.decomposition import PCA

        X = PCA(
            n_components=_DIMRED_PCA_DIMS, random_state=_DIMRED_SEED
        ).fit_transform(X)
    return X


def _dimred_write_file(ctx, page, path, coords, row_indices=None):
    """Write one file's embedding; return (rel, columns, kind).

    `row_indices` are the full-file row numbers the coords correspond to
    (after upstream sampling and this module's own per-file downsample).
    """
    import numpy as np
    import pandas as pd

    params = page._dimred_params
    names = params.get("names") or ["dim_1", "dim_2"]
    stem = Path(str(path)).stem
    rel = _data_files._module_rel(ctx, page._dimred_task_id, f"{stem}_dimred.parquet")
    out_path = ctx.project.path / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ent = _data_image_index._ensure_image_index(ctx, path)
    is_image = ent is not None and ent.get("manifest") is not None
    n_full = _data_columns._data_row_count(ctx, path)
    sel = np.asarray(row_indices) if row_indices is not None else np.arange(len(coords))
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
        g1 = np.full(height * width, np.nan, dtype="float64")
        g2 = np.full(height * width, np.nan, dtype="float64")
        s = sel[ok]
        sx, sy = xi[s], yi[s]
        good = (sx >= 0) & (sx < width) & (sy >= 0) & (sy < height)
        flat = sy[good] * width + sx[good]
        g1[flat] = coords[ok][good][:, 0]
        g2[flat] = coords[ok][good][:, 1]
        xx = np.tile(np.arange(width, dtype=np.int32), height) + xmin
        yy = np.repeat(np.arange(height, dtype=np.int32), width) + ymin
        pd.DataFrame({"x": xx, "y": yy, names[0]: g1, names[1]: g2}).to_parquet(
            out_path, index=False
        )
        _build_image_index_files(str(out_path))
        kind = "image"
    else:
        full = np.full((n_full, 2), np.nan, dtype="float64")
        if len(coords) == len(sel):
            full[sel] = coords
        else:
            n = min(len(coords), n_full)
            full[:n] = coords[:n]
        pd.DataFrame({names[0]: full[:, 0], names[1]: full[:, 1]}).to_parquet(
            out_path, index=False
        )
        kind = "table"
    return rel, list(names), kind


def _dimred_calculate(ctx, page) -> None:
    ok, message = _dimred_validate(ctx, page)
    if not ok:
        QMessageBox.warning(ctx.host, "Calculate", message)
        return
    params = page._dimred_params
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

    page._dimred_running = True
    page._dimred_cancel = False
    page._dimred_status.setText("Calculating…")
    page._dimred_calc_btn.setEnabled(False)

    q = queue.Queue()
    page._dimred_queue = q
    page._dimred_thread = threading.Thread(
        target=partial(_dimred_run, ctx), args=(page, q), daemon=True
    )
    page._dimred_thread.start()

    timer = QTimer(ctx.host)
    timer.setInterval(100)
    timer.timeout.connect(lambda: _dimred_poll(ctx, page))
    timer.start()
    page._dimred_timer = timer


def _dimred_poll(ctx, page) -> None:
    q = getattr(page, "_dimred_queue", None)
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
            _dimred_log(ctx, page, msg[1])
        elif kind == "status":
            page._dimred_status.setText(msg[1])
        elif kind == "fit_time":
            params = page._dimred_params
            params["last_fit_seconds"] = float(msg[1])
            page._dimred_estimate.setText(_dimred_estimate_text(ctx, page))
        elif kind == "fitprogress":
            pct, eta = msg[1], msg[2]
            text = f"Fitting… {pct:.0f}%"
            if eta is not None:
                text += f" — ~{eta:.0f}s remaining"
            page._dimred_status.setText(text)
        elif kind == "done":
            outputs, done, failed = msg[1], msg[2], msg[3]
            params = page._dimred_params
            params["outputs"] = outputs
            page._dimred_task_def["output"] = {"saved": bool(done)}
            page._dimred_task_def["params"] = params
            if ctx.project is not None:
                ctx.project.save()
            page._dimred_snapshot = copy.deepcopy(params)
            page._dimred_dirty = False
            _dimred_update_save_btn(ctx, page)
            page._dimred_done = done
            page._dimred_failed = failed
            finished = True
        elif kind == "error":
            error = msg[1]
            finished = True
    if not finished:
        return
    page._dimred_running = False
    timer = getattr(page, "_dimred_timer", None)
    if timer is not None:
        timer.stop()
        page._dimred_timer = None
    _dimred_update_calc_btn(ctx, page)
    if error:
        _dimred_log(ctx, page, f"✗ {error}")
        QMessageBox.warning(ctx.host, "Calculate", error)
        page._dimred_status.setText("Failed")
    else:
        summary = f"Calculated {page._dimred_done} file(s)."
        if getattr(page, "_dimred_failed", 0):
            summary += f" {page._dimred_failed} failed — see the log."
        page._dimred_status.setText(summary)
        _dimred_log(ctx, page, summary)
        ctx.statusBar().showMessage(summary)
        ctx._module_saved(page)


def _dimred_mark_dirty(ctx, page) -> None:
    page._dimred_dirty = True
    _dimred_update_save_btn(ctx, page)
    ctx._module_set_state(page, MODULE_STATE_MODIFIED)


def _dimred_update_save_btn(ctx, page) -> None:
    btn = getattr(page, "_dimred_save_btn", None)
    if btn is not None:
        btn.setEnabled(page._dimred_dirty and not page._dimred_locked)


def _dimred_save_now(ctx, page) -> None:
    params = page._dimred_params
    page._dimred_task_def["params"] = params
    if ctx.project is not None:
        ctx.project.save()
    page._dimred_snapshot = copy.deepcopy(params)
    page._dimred_dirty = False
    _dimred_update_save_btn(ctx, page)
    _dimred_log(ctx, page, "Settings saved")
    ctx.statusBar().showMessage("Dimension Reduction settings saved")
    ctx._module_saved(page)


def _dimred_close(ctx, page) -> None:
    if getattr(page, "_dimred_running", False):
        reply = QMessageBox.question(
            ctx.host, "Close Dimension Reduction",
            "A calculation is still running.\nStop it and close? "
            "This run's results will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # The fit itself cannot be interrupted; signal it to stop at the
        # next file and stay open until the worker finishes.
        page._dimred_cancel = True
        _dimred_log(ctx, page, "Stopping after the current file…")
        return
    if getattr(page, "_dimred_dirty", False):
        reply = QMessageBox.question(
            ctx.host, "Close Dimension Reduction",
            "There are unsaved changes.\nClose without saving? "
            "Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log_lines = list(getattr(page, "_dimred_log_lines", []) or [])
        page._dimred_task_def["params"] = page._dimred_snapshot
        page._dimred_task_def["params"]["log"] = log_lines
    ctx._close_module_page(page)
