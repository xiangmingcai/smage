"""Icons, pixmaps and process/storage size helpers."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


_MODULE_ICON_CACHE: dict = {}
_MODULE_ICON_DIR = None
_MODULE_ICON_DIR_CHECKED = False

def _dot_pixmap(color: str) -> QPixmap:
    """Build a small colored-dot pixmap (placeholder for an icon)."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 12, 12)
    painter.end()
    return pixmap


def _module_icon_dir():
    """Folder with the per-module SVG icons, or None when they are missing."""
    global _MODULE_ICON_DIR, _MODULE_ICON_DIR_CHECKED
    if _MODULE_ICON_DIR_CHECKED:
        return _MODULE_ICON_DIR
    from pathlib import Path as _Path

    package = _Path(__file__).resolve().parent.parent
    candidates = [
        package / "icons",                        # packaged next to the package
        package.parents[1] / "data" / "icon",     # source checkout
        _Path.cwd() / "data" / "icon",
    ]
    for folder in candidates:
        if (folder / "import.svg").is_file():
            _MODULE_ICON_DIR = folder
            break
    _MODULE_ICON_DIR_CHECKED = True
    return _MODULE_ICON_DIR


def _module_icon_pixmap(task_type: str, size: int):
    """Render one module icon at `size` px, or None when there is no SVG."""
    if not task_type:
        return None
    key = (task_type, size)
    cached = _MODULE_ICON_CACHE.get(key)
    if cached is not None:
        return cached or None
    folder = _module_icon_dir()
    if folder is None:
        _MODULE_ICON_CACHE[key] = False
        return None
    path = folder / f"{task_type}.svg"
    if not path.is_file():
        _MODULE_ICON_CACHE[key] = False
        return None
    try:
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(path))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
    except Exception:  # noqa: BLE001 - fall back to the coloured dot
        _MODULE_ICON_CACHE[key] = False
        return None
    _MODULE_ICON_CACHE[key] = pixmap
    return pixmap


def _module_icon(task_type: str, fallback_color: str = "#9aa0a6") -> QIcon:
    """QIcon for a module; several sizes so hi-DPI screens stay sharp."""
    icon = QIcon()
    rendered = False
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = _module_icon_pixmap(task_type, size)
        if pixmap is not None:
            icon.addPixmap(pixmap)
            rendered = True
    if rendered:
        return icon
    return QIcon(_dot_pixmap(fallback_color))


def _drag_handle_pixmap(color: str = "#8a94a6", size: int = 14) -> QPixmap:
    """Build the grip icon shown before a draggable list item."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    pen = QPen(QColor(color))
    pen.setWidth(1)
    painter.setPen(pen)
    for y in (4, 7, 10):
        painter.drawLine(3, y, size - 4, y)
    painter.end()
    return pixmap


def _lock_pixmap(color: str = "#b58900", size: int = 14) -> QPixmap:
    """Build a small padlock pixmap for locked modules."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Shackle (top half arc).
    painter.drawArc(QRectF(3.5, 1.5, size - 7.0, size - 7.0), 0, 180 * 16)
    # Body.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(2.0, 5.0, size - 4.0, size - 5.0), 1.6, 1.6)
    painter.end()
    return pixmap


def _folder_size(path: Path):
    """(bytes, file count) of a folder, walking it iteratively."""
    import os

    total = 0
    count = 0
    stack = [Path(path)]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def _process_memory_bytes():
    """Working set of this process (Windows), or None when unavailable."""
    try:
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        # K32GetProcessMemoryInfo is the Windows 7+ entry point; psapi.dll is
        # the fallback for older systems.
        for library in (kernel32, ctypes.windll.psapi):
            function = getattr(library, "K32GetProcessMemoryInfo", None) or getattr(
                library, "GetProcessMemoryInfo", None
            )
            if function is None:
                continue
            function.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            function.restype = wintypes.BOOL
            if function(
                kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            ):
                return int(counters.WorkingSetSize)
        return None
    except Exception:  # noqa: BLE001
        return None


def _system_memory_bytes():
    """(total, available) physical memory in bytes, or None."""
    try:
        import ctypes
        from ctypes import wintypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except Exception:  # noqa: BLE001
        return None


