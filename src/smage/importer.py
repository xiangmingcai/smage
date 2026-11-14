"""Import data files into the unified tabular (Parquet) format.

The import step reads a data file, keeps only the parameters marked "extract",
renames them to their "rename" values, applies the sampling strategy, and
writes a Parquet file into the project's data store.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from smage.dimensions import fcs_column_names

# Fixed seed for random sampling, so imports are reproducible.
IMPORT_SEED = 42


def import_file(path, params, sampling, sampling_percent, out_path,
                image_resize=None) -> dict:
    """Import one file and write a Parquet; return {"rows", "columns"}.

    Image files accept ``image_resize`` with ``enabled``, ``width``,
    ``height`` and ``method`` (``smooth`` or ``nearest``).  The image is
    resized before it is flattened into pixel rows.
    """
    if not params:
        raise ValueError("File has not been peeked yet (no parameters).")

    ext = Path(path).suffix.lower()
    image_meta = None
    if ext == ".fcs":
        df = _load_fcs(path)
        df = _select_and_rename(df, params)
    elif ext == ".csv":
        df = pd.read_csv(path)
        df = _select_and_rename(df, params)
    elif ext == ".parquet":
        df = pd.read_parquet(path)
        df = _select_and_rename(df, params)
    elif ext in (".tif", ".tiff"):
        df, image_meta = _load_image(path, params, image_resize)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    # Random pixel sampling would punch holes in an image; images are reduced
    # with the resize settings instead.
    if sampling == "partial" and ext not in (".tif", ".tiff"):
        fraction = int(sampling_percent) / 100.0
        df = df.sample(frac=fraction, random_state=IMPORT_SEED)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path)
    result = {"rows": int(len(df)), "columns": list(df.columns)}
    if image_meta:
        result.update(image_meta)
    return result


def _load_fcs(path: Path) -> pd.DataFrame:
    import flowio
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data = flowio.FlowData(str(path), ignore_offset_error=True)
    events = np.asarray(data.events, dtype=np.float32)
    events = np.reshape(events, (-1, data.channel_count))
    return pd.DataFrame(events, columns=fcs_column_names(data))


def _image_resize_target(resize, width: int, height: int):
    """Return (target width, target height, method) for an image import."""
    if not isinstance(resize, dict) or not resize.get("enabled"):
        return int(width), int(height), "smooth"
    try:
        target_w = int(resize.get("width") or width)
        target_h = int(resize.get("height") or height)
    except (TypeError, ValueError):
        return int(width), int(height), "smooth"
    method = str(resize.get("method") or "smooth").lower()
    if method not in ("smooth", "nearest"):
        method = "smooth"
    return max(1, target_w), max(1, target_h), method


def _load_image(path: Path, params: list, resize=None):
    """Decode an image and flatten it to pixel rows.

    Returns ``(dataframe, {"original_size", "imported_size"})``.  Only the
    channels marked "extract" are resized and kept.
    """
    import tifffile
    import warnings

    from smage.core.image_ops import _resize_plane

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with tifffile.TiffFile(str(path)) as tf:
            image = tf.series[0].asarray()

    if image.ndim == 2:
        image = image[np.newaxis, ...]  # (Y, X) -> (1, Y, X)
    if image.ndim != 3:
        raise ValueError(
            f"Unsupported image shape {tuple(image.shape)}; expected (C, Y, X)"
        )
    channels, height, width = image.shape
    target_w, target_h, method = _image_resize_target(resize, width, height)
    resizes = (target_w, target_h) != (width, height)

    yy, xx = np.meshgrid(
        np.arange(target_h), np.arange(target_w), indexing="ij"
    )
    data = {"x": xx.ravel(), "y": yy.ravel()}
    for c in range(channels):
        param = params[c] if c < len(params) else None
        if param is None or not param.get("extract", True):
            continue
        name = param.get("rename", param.get("name", f"Channel {c + 1}"))
        plane = image[c]
        if resizes:
            plane = _resize_plane(plane, target_w, target_h, method)
        data[name] = plane.ravel()
    meta = {
        "original_size": [int(width), int(height)],
        "imported_size": [int(target_w), int(target_h)],
    }
    return pd.DataFrame(data), meta


def _select_and_rename(df: pd.DataFrame, params: list) -> pd.DataFrame:
    keep = {
        p["name"]: p.get("rename", p["name"])
        for p in params
        if p.get("extract", True)
    }
    columns = [name for name in keep if name in df.columns]
    return df[columns].rename(columns=keep)
