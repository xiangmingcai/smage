"""Read dimension/metadata info from data files (Data Import → Peek)."""
from __future__ import annotations

import re
import warnings
from pathlib import Path


def parameter_names(dimensions: dict) -> list:
    """Return the extractable parameter names for a dimensions dict."""
    kind = dimensions.get("kind")
    if kind == "image":
        names = dimensions.get("channel_names") or []
        if not names:
            names = [f"Channel {i + 1}" for i in range(dimensions.get("channels", 0))]
        return list(names)
    return list(dimensions.get("columns", []))


def read_dimensions(path: str | Path) -> dict:
    """Return dimension info for a data file, based on its extension."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".fcs":
        return _read_fcs(path)
    if ext in (".tif", ".tiff"):
        return _read_image(path)
    if ext == ".csv":
        return _read_csv(path)
    if ext == ".parquet":
        return _read_parquet(path)
    raise ValueError(f"Unsupported file type: {ext}")


def fcs_column_names(data) -> list:
    """Return combined long_short column names for a flowio FlowData object."""
    short = list(data.pnn_labels or [])
    long_names = list(data.pns_labels or [])
    columns = []
    for i in range(data.channel_count):
        s = (short[i] if i < len(short) else "").strip()
        l = (long_names[i] if i < len(long_names) else "").strip()
        if l and l != s:
            name = f"{l}_{s}" if s else l
        else:
            name = s or l or f"Channel {i + 1}"
        columns.append(name)
    return columns


def _read_fcs(path: Path) -> dict:
    import flowio

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data = flowio.FlowData(str(path), ignore_offset_error=True)
    return {"kind": "fcs", "rows": int(data.event_count), "columns": fcs_column_names(data)}


def _read_image(path: Path) -> dict:
    import tifffile

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with tifffile.TiffFile(str(path)) as tf:
            series = tf.series[0]
            shape = list(series.shape)
            axes = series.axes or ""
            xml = tf.ome_metadata or ""
    channel_names = re.findall(r'<Channel\b[^>]*?Name="([^"]+)"', xml) if xml else []
    channels = shape[0] if len(shape) >= 3 else 1
    return {
        "kind": "image",
        "shape": shape,
        "axes": axes,
        "channels": channels,
        "channel_names": channel_names,
        # Pixel dimensions, so the import page can offer a resize without
        # decoding the image again.
        "height": int(shape[-2]) if len(shape) >= 2 else 0,
        "width": int(shape[-1]) if len(shape) >= 2 else 0,
    }


def _read_csv(path: Path) -> dict:
    import pandas as pd

    columns = list(pd.read_csv(str(path), nrows=0).columns)
    rows = 0
    for chunk in pd.read_csv(str(path), chunksize=200000):
        rows += len(chunk)
    return {"kind": "table", "rows": rows, "columns": columns}


def _read_parquet(path: Path) -> dict:
    import pyarrow.parquet as pq

    metadata = pq.read_metadata(str(path))
    return {
        "kind": "table",
        "rows": metadata.num_rows,
        "columns": metadata.schema.names,
    }
