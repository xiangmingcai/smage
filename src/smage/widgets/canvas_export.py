"""Save a QPainter-drawn canvas as PNG/JPEG or a fully vector PDF.

The Figure modules draw into pyqtgraph scenes, so their export helper
rasterises the plot area and keeps only the axes as text.  The Heatmap
canvas paints everything itself with QPainter, so it can be rendered
straight into ``QPdfWriter``: every cell, grid line, dendrogram branch and
label stays a vector shape or editable text.  PNG and JPEG are rendered at
three times the screen size, matching the Figure export density.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF, QPoint, QSizeF, QStandardPaths, Qt
from PySide6.QtGui import (
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
)

# QPdfWriter replays the screen's 96 dpi coordinates; the vector output is
# resolution-independent anyway.
_PDF_DPI = 96
# PDF pages cannot exceed 200 inches (14400 pt).  Larger canvases are
# scaled down to fit instead of failing to save.
_PDF_MAX_POINTS = 14000.0
# Device pixels per screen pixel for PNG/JPEG.
_RASTER_SCALE = 3.0

_FILTERS = (
    ("PDF - editable text and vector shapes (*.pdf)", ".pdf"),
    ("PNG image (*.png)", ".png"),
    ("JPEG image (*.jpg *.jpeg)", ".jpg"),
)

_LAST_DIR = ""

_VECTOR_TEXT_FLAG = "_export_vector_text"


def _canvas_export_render(canvas, painter) -> None:
    """Render the canvas, switching rotated labels to vector text.

    Canvases that cache rotated labels as pixmaps (for sharp on-screen
    drawing on scaled displays) check this flag and paint the text with a
    rotated painter instead, so the PDF keeps it editable.
    """
    previous = getattr(canvas, _VECTOR_TEXT_FLAG, False)
    setattr(canvas, _VECTOR_TEXT_FLAG, True)
    try:
        canvas.render(painter, QPoint(0, 0))
    finally:
        setattr(canvas, _VECTOR_TEXT_FLAG, previous)


def _canvas_export_image(canvas, path, fmt: str = "png",
                         scale: float = _RASTER_SCALE) -> bool:
    """Write the canvas as a PNG or JPEG at `scale` device pixels per pixel."""
    scale = max(0.1, float(scale))
    width = max(1, int(round(canvas.width() * scale)))
    height = max(1, int(round(canvas.height() * scale)))
    image = QImage(
        width, height, QImage.Format.Format_ARGB32_Premultiplied
    )
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        painter.scale(scale, scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        _canvas_export_render(canvas, painter)
    finally:
        painter.end()
    if fmt == "jpg":
        return bool(image.save(str(path), "JPG", 95))
    return bool(image.save(str(path), "PNG"))


def _canvas_export_pdf(canvas, path) -> bool:
    """Write the canvas as a PDF with editable text and vector shapes."""
    width_pt = max(1.0, canvas.width() * 72.0 / _PDF_DPI)
    height_pt = max(1.0, canvas.height() * 72.0 / _PDF_DPI)
    factor = min(1.0, _PDF_MAX_POINTS / max(width_pt, height_pt))
    writer = QPdfWriter(str(path))
    writer.setResolution(_PDF_DPI)
    writer.setCreator("Smage")
    writer.setPageSize(
        QPageSize(
            QSizeF(width_pt * factor, height_pt * factor),
            QPageSize.Unit.Point,
        )
    )
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    painter = QPainter(writer)
    try:
        if not painter.isActive():
            return False
        painter.scale(factor, factor)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        _canvas_export_render(canvas, painter)
    finally:
        painter.end()
    return True


def _painter_export_image(paint, width: float, height: float, path,
                          fmt: str = "png",
                          scale: float = _RASTER_SCALE) -> bool:
    """Write a QPainter callback at `scale` device pixels per logical unit."""
    scale = max(0.1, float(scale))
    image = QImage(
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        painter.scale(scale, scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        paint(painter)
    finally:
        painter.end()
    if fmt == "jpg":
        return bool(image.save(str(path), "JPG", 95))
    return bool(image.save(str(path), "PNG"))


def _painter_export_pdf(paint, width: float, height: float, path) -> bool:
    """Write a QPainter callback as a PDF with editable text and vectors."""
    width_pt = max(1.0, float(width) * 72.0 / _PDF_DPI)
    height_pt = max(1.0, float(height) * 72.0 / _PDF_DPI)
    factor = min(1.0, _PDF_MAX_POINTS / max(width_pt, height_pt))
    writer = QPdfWriter(str(path))
    writer.setResolution(_PDF_DPI)
    writer.setCreator("Smage")
    writer.setPageSize(
        QPageSize(
            QSizeF(width_pt * factor, height_pt * factor),
            QPageSize.Unit.Point,
        )
    )
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    painter = QPainter(writer)
    try:
        if not painter.isActive():
            return False
        painter.scale(factor, factor)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        paint(painter)
    finally:
        painter.end()
    return True


def _canvas_export_safe_name(name: str) -> str:
    """A filename Windows accepts, without an extension."""
    text = " ".join(str(name or "").split())
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text.strip(" .") or "canvas"


def _export_choose_path(parent, suggested_name: str,
                        dialog_title: str) -> tuple[str, str] | None:
    """Ask for an output file; return ``(path, suffix)`` or None."""
    from PySide6.QtWidgets import QFileDialog

    global _LAST_DIR
    start_dir = _LAST_DIR
    if not start_dir:
        start_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        )
    if not start_dir:
        start_dir = str(Path.home())
    base = _canvas_export_safe_name(suggested_name)
    initial = str(Path(start_dir) / (base + ".pdf"))
    filters = ";;".join(item[0] for item in _FILTERS)
    path, selected = QFileDialog.getSaveFileName(
        parent, dialog_title, initial, filters
    )
    if not path:
        return None
    suffix = Path(path).suffix.lower()
    if suffix not in (".pdf", ".png", ".jpg", ".jpeg"):
        extension = next(
            (ext for label, ext in _FILTERS if label == selected), ".pdf"
        )
        path += extension
        suffix = extension
    _LAST_DIR = str(Path(path).parent)
    return path, suffix


def _canvas_export_save(canvas, parent=None, suggested_name: str = "canvas",
                        dialog_title: str = "Save As") -> str | None:
    """Ask for a file and save the canvas; return the written path or None.

    The selected filter picks the format when the typed name has no
    extension; an explicit .pdf/.png/.jpg/.jpeg always wins.
    """
    chosen = _export_choose_path(parent, suggested_name, dialog_title)
    if chosen is None:
        return None
    path, suffix = chosen
    if suffix == ".pdf":
        ok = _canvas_export_pdf(canvas, path)
    else:
        ok = _canvas_export_image(
            canvas, path, "jpg" if suffix in (".jpg", ".jpeg") else "png"
        )
    if not ok:
        raise RuntimeError(f"Could not write {path}")
    return path


def _painter_export_save(paint, width: float, height: float, parent=None,
                         suggested_name: str = "plot",
                         dialog_title: str = "Save As") -> str | None:
    """Ask for a file and write a QPainter callback; return the path or None."""
    chosen = _export_choose_path(parent, suggested_name, dialog_title)
    if chosen is None:
        return None
    path, suffix = chosen
    if suffix == ".pdf":
        ok = _painter_export_pdf(paint, width, height, path)
    else:
        ok = _painter_export_image(
            paint, width, height, path,
            "jpg" if suffix in (".jpg", ".jpeg") else "png",
        )
    if not ok:
        raise RuntimeError(f"Could not write {path}")
    return path
