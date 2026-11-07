"""Constants and per-sub-page state for the Export module."""
from __future__ import annotations


_EXPORT_FORMATS = {
    "table": [
        ("CSV (comma separated)", "csv"),
        ("Parquet", "parquet"),
        ("FCS", "fcs"),
    ],
    "image": [("TIFF", "tiff")],
}

_EXPORT_EXT = {
    "csv": ".csv", "parquet": ".parquet", "fcs": ".fcs", "tiff": ".tif",
}

class _ExportCancelled(Exception):
    """Raised inside a running export job when the user stops it."""


class _ExportPane:
    """Widgets and background-job state for one Export sub-page."""

    def __init__(self, kind: str) -> None:
        self.kind = kind              # "table" | "image"
        self.files_box = None
        self.files_count = None
        self.files_btn = None
        self.cols_box = None
        self.cols_count = None
        self.cols_btn = None
        self.dir_edit = None
        self.browse_btn = None
        self.format_combo = None
        self.export_btn = None
        self.cancel_btn = None
        self.progress = None
        self.status = None
        self.log_box = None
        self.splitter = None
        self.apply_ratio = None
        self.running = False
        self.cancel = False
        self.queue = None
        self.timer = None
        self.thread = None
        self.syncing = False

    @property
    def list_key(self) -> str:
        return "columns" if self.kind == "table" else "channels"
