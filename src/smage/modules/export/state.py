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

_EXPORT_DTYPE_MODES = [
    ("Highest dtype of each file", "per_file"),
    ("Highest dtype of all files", "all_files"),
]

_EXPORT_DTYPE_DEFAULT = "per_file"

_EXPORT_DTYPE_HELP_HTML = """\
<h2>Data types</h2>
<p>Integer types are exact and cannot store NaN or infinity. Float types
cover a much wider range, lose exactness as values grow and can store NaN
and infinity.</p>
<table border="1" cellspacing="0" cellpadding="4"
       style="border-collapse:collapse;">
<tr><th>Type</th><th>Bytes</th><th>Range</th>
    <th>Exact integers to</th></tr>
<tr><td>uint8</td><td>1</td><td>0 .. 255</td><td>all</td></tr>
<tr><td>int8</td><td>1</td><td>-128 .. 127</td><td>all</td></tr>
<tr><td>uint16</td><td>2</td><td>0 .. 65,535</td><td>all</td></tr>
<tr><td>int16</td><td>2</td><td>-32,768 .. 32,767</td><td>all</td></tr>
<tr><td>uint32</td><td>4</td><td>0 .. 4,294,967,295</td><td>all</td></tr>
<tr><td>int32</td><td>4</td><td>-2,147,483,648 .. 2,147,483,647</td>
    <td>all</td></tr>
<tr><td>uint64</td><td>8</td><td>0 .. 18,446,744,073,709,551,615</td>
    <td>all</td></tr>
<tr><td>int64</td><td>8</td>
    <td>-9,223,372,036,854,775,808 .. 9,223,372,036,854,775,807</td>
    <td>all</td></tr>
<tr><td>float16</td><td>2</td><td>-65,504 .. 65,504</td><td>2,048</td></tr>
<tr><td>float32</td><td>4</td><td>&plusmn;3.4028235&times;10<sup>38</sup></td>
    <td>16,777,216</td></tr>
<tr><td>float64</td><td>8</td><td>&plusmn;1.7976931&times;10<sup>308</sup></td>
    <td>9.0&times;10<sup>15</sup></td></tr>
</table>
<p>float16 keeps about 3 significant digits, float32 about 7 and float64
about 15&ndash;16.</p>

<h2>Software support</h2>
<table border="1" cellspacing="0" cellpadding="4"
       style="border-collapse:collapse;">
<tr><th>Type</th><th>tifffile *</th><th>Pillow</th>
    <th>ImageJ / Fiji</th></tr>
<tr><td>uint8</td><td>yes</td><td>yes</td><td>yes</td></tr>
<tr><td>uint16</td><td>yes</td><td>yes</td><td>yes</td></tr>
<tr><td>uint32</td><td>yes</td><td>type changes</td><td>partial</td></tr>
<tr><td>uint64</td><td>yes</td><td>cannot open</td><td>cannot open</td></tr>
<tr><td>int8</td><td>yes</td><td>type changes</td><td>partial</td></tr>
<tr><td>int16</td><td>yes</td><td>yes</td><td>yes</td></tr>
<tr><td>int32</td><td>yes</td><td>yes</td><td>partial</td></tr>
<tr><td>int64</td><td>yes</td><td>cannot open</td><td>cannot open</td></tr>
<tr><td>float16</td><td>yes</td><td>cannot open</td><td>cannot open</td></tr>
<tr><td>float32</td><td>yes</td><td>yes</td><td>yes</td></tr>
<tr><td>float64</td><td>yes</td><td>cannot open</td><td>Bio-Formats only</td></tr>
</table>
<p>* also scikit-image, imageio and napari. Pillow opens uint32 as int32 and
int8 as uint8, so large or negative values change silently. uint64, int64,
float16 and float64 are TIFF extensions, not part of the baseline TIFF 6.0
specification. If your software cannot open one of them, convert the file
with Python (for example tifffile and numpy) to a type it supports before
importing it.</p>

<h2>Export modes</h2>
<table border="1" cellspacing="0" cellpadding="4"
       style="border-collapse:collapse;">
<tr><th>Mode</th><th>Export dtype</th><th>Values</th></tr>
<tr><td>Highest dtype of each file</td>
    <td>the highest dtype among that file's selected channels</td>
    <td>kept as they are</td></tr>
<tr><td>Highest dtype of all files</td>
    <td>the highest dtype across every selected file</td>
    <td>kept as they are; files with a smaller type are promoted</td></tr>
</table>
<p>Both modes only cast values; nothing is rescaled or clipped. uint8,
uint16 and float32 are the safest choice when the file has to be opened by
other software.</p>
"""


class _ExportCancelled(Exception):
    """Raised inside a running export job when the user stops it."""


class _ExportPane:
    """Widgets and background-job state for one Export sub-page."""

    def __init__(self, kind: str) -> None:
        self.kind = kind              # "table" | "image"
        self.files_box = None
        self.files_table = None
        self.files_summary = None
        self.files_help_btn = None
        self.files_count = None
        self.files_btn = None
        self.cols_box = None
        self.cols_count = None
        self.cols_btn = None
        self.dir_edit = None
        self.browse_btn = None
        self.format_combo = None
        self.dtype_combo = None
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
