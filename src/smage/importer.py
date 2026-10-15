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


def import_file(path, params, sampling, sampling_percent, out_path) -> dict:
    """Import one file and write a Parquet; return {"rows", "columns"}."""
    if not params:
        raise ValueError("File has not been peeked yet (no parameters).")

    ext = Path(path).suffix.lower()
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
        df = _load_image(path, params)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    if sampling == "partial":
        fraction = int(sampling_percent) / 100.0
        df = df.sample(frac=fraction, random_state=IMPORT_SEED)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path)
    return {"rows": int(len(df)), "columns": list(df.columns)}


def _load_fcs(path: Path) -> pd.DataFrame:
    import flowio
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data = flowio.FlowData(str(path), ignore_offset_error=True)
    events = np.asarray(data.events, dtype=np.float32)
    events = np.reshape(events, (-1, data.channel_count))
    return pd.DataFrame(events, columns=fcs_column_names(data))


def _load_image(path: Path, params: list) -> pd.DataFrame:
    import tifffile
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with tifffile.TiffFile(str(path)) as tf:
            image = tf.series[0].asarray()  # (C, Y, X)

    channels, height, width = image.shape
    yy, xx = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    data = {"x": xx.ravel(), "y": yy.ravel()}
    for c in range(channels):
        param = params[c] if c < len(params) else None
        if param is None or not param.get("extract", True):
            continue
        name = param.get("rename", param.get("name", f"Channel {c + 1}"))
        data[name] = image[c].ravel()
    return pd.DataFrame(data)


def _select_and_rename(df: pd.DataFrame, params: list) -> pd.DataFrame:
    keep = {
        p["name"]: p.get("rename", p["name"])
        for p in params
        if p.get("extract", True)
    }
    columns = [name for name in keep if name in df.columns]
    return df[columns].rename(columns=keep)
