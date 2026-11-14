"""Save a plot widget as an image or a hybrid, editable PDF.

PNG and JPEG export the whole plot at a higher pixel density than the
screen. PDF keeps the axes, tick labels, titles and the side legend as
editable text and vector lines, while the plot area itself (points, image
raster, grid) is embedded as one bitmap: millions of scatter points and
image pixels do not vectorise well, and a bitmap keeps both the file size
and the appearance faithful to what the screen shows.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QMarginsF,
    QPoint,
    QRectF,
    QSizeF,
    QStandardPaths,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
)

# QPdfWriter replays the cached axis pictures (QPicture) using the writer's
# resolution; matching the screen's 96 dpi keeps the recorded font sizes and
# text positions intact. Vector output stays resolution-independent.
_PDF_DPI = 96
# Device pixels per screen pixel for the exported plot.
_RASTER_SCALE = 3.0
# Minimum scale of the plot-area bitmap inside the PDF (~288 dpi on paper).
_PDF_RASTER_SCALE = 3.0

_FILTERS = (
    ("PDF - editable text, plot as image (*.pdf)", ".pdf"),
    ("PNG image (*.png)", ".png"),
    ("JPEG image (*.jpg *.jpeg)", ".jpg"),
)

_LAST_DIR = ""


def _plot_export_legend(plot_widget):
    """The visible colour legend shown beside an image plot, if any."""
    legend = getattr(plot_widget, "_legend_widget", None)
    if legend is None or not legend.isVisible():
        return None
    if legend.width() <= 0 or legend.height() <= 0:
        return None
    return legend


def _plot_export_layout(plot_widget):
    """Return (scene rect, legend, total width, total height, legend gap)."""
    scene_rect = QRectF(plot_widget.sceneRect())
    legend = _plot_export_legend(plot_widget)
    gap = 4.0 if legend is not None else 0.0
    total_w = scene_rect.width()
    total_h = scene_rect.height()
    if legend is not None:
        total_w += legend.width() + gap
        total_h = max(total_h, float(legend.height()))
    return scene_rect, legend, max(1.0, total_w), max(1.0, total_h), gap


def _plot_export_hide_chrome(plot_widget):
    """Hide the A/F buttons and the cursor readout for the export."""
    item = plot_widget.getPlotItem()
    hidden = []
    for name in ("autoBtn", "fitBtn", "readoutText"):
        obj = getattr(item, name, None)
        if obj is not None and obj.isVisible():
            obj.setVisible(False)
            hidden.append(obj)
    reserved = bool(getattr(item, "_readout_row_reserved", False))
    if reserved:
        try:
            item.release_readout_row()
        except Exception:
            reserved = False
    return hidden, reserved


def _plot_export_restore_chrome(item, hidden, reserved) -> None:
    for obj in hidden:
        obj.setVisible(True)
    if reserved:
        try:
            item.reserve_readout_row()
        except Exception:
            pass


def _plot_export_disable_grids(plot_widget):
    """Turn off axis grid lines: the plot bitmap already contains them.

    Drawing the vector grid on top of the bitmap would double its opacity.
    """
    axes = []
    item = plot_widget.getPlotItem()
    for name in ("bottom", "left"):
        axis = item.getAxis(name)
        if axis is None:
            continue
        axes.append((axis, axis.grid))
        try:
            axis.setGrid(False)
        except Exception:
            pass
    return axes


def _plot_export_restore_grids(axes) -> None:
    for axis, value in axes:
        try:
            axis.setGrid(False if value is None else value)
        except Exception:
            pass


def _plot_export_data_bitmap(plot_widget, scale: float) -> QImage:
    """Rasterise the ViewBox area (data, image and grid) at `scale`."""
    rect = plot_widget.getPlotItem().getViewBox().sceneBoundingRect()
    width = max(1, int(round(rect.width() * scale)))
    height = max(1, int(round(rect.height() * scale)))
    image = QImage(
        width, height, QImage.Format.Format_ARGB32_Premultiplied
    )
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        plot_widget.scene().render(
            painter,
            QRectF(0, 0, width, height),
            rect,
            Qt.AspectRatioMode.IgnoreAspectRatio,
        )
    finally:
        painter.end()
    return image


def _plot_export_paint(plot_widget, painter, width: float, height: float) -> None:
    """Paint the plot at `width` x `height` device pixels.

    The plot area is a bitmap; axes, titles, tick text and the legend are
    painted by Qt's vector engines.
    """
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(
            QRectF(0, 0, width, height), QColor(255, 255, 255)
        )
        item = plot_widget.getPlotItem()
        viewbox = item.getViewBox()
        hidden, reserved = _plot_export_hide_chrome(plot_widget)
        axes = []
        viewbox_visible = viewbox.isVisible()
        try:
            scene_rect, legend, total_w, total_h, gap = _plot_export_layout(
                plot_widget
            )
            factor = min(width / total_w, height / total_h)
            plot_rect = QRectF(
                0.0, 0.0,
                scene_rect.width() * factor,
                scene_rect.height() * factor,
            )

            # 1. The plot area as one bitmap, at the highest useful density.
            bitmap_scale = max(_PDF_RASTER_SCALE, min(factor, 4.0))
            bitmap = _plot_export_data_bitmap(plot_widget, bitmap_scale)
            vb_rect = viewbox.sceneBoundingRect()
            target = QRectF(
                (vb_rect.x() - scene_rect.x()) * factor,
                (vb_rect.y() - scene_rect.y()) * factor,
                vb_rect.width() * factor,
                vb_rect.height() * factor,
            )
            painter.drawImage(target, bitmap)

            # 2. Everything else vectorially; hide the data items so they are
            #    not drawn a second time (and blown up into vector paths).
            #    The bitmap already contains the grid, so the vector pass must
            #    not paint it again (that would double its opacity).
            axes = _plot_export_disable_grids(plot_widget)
            viewbox.setVisible(False)
            try:
                plot_widget.scene().render(
                    painter, plot_rect, scene_rect,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                )
            finally:
                viewbox.setVisible(viewbox_visible)

            # 3. The image colour legend lives beside the plot in a widget of
            #    its own, outside the plot scene.
            if legend is not None:
                painter.save()
                try:
                    painter.translate(plot_rect.width() + gap * factor, 0.0)
                    painter.scale(factor, factor)
                    legend.render(painter, QPoint(0, 0))
                finally:
                    painter.restore()
        finally:
            _plot_export_restore_grids(axes)
            _plot_export_restore_chrome(item, hidden, reserved)
    finally:
        painter.restore()


def _plot_export_image(plot_widget, path, fmt: str,
                       scale: float = _RASTER_SCALE) -> bool:
    """Write the plot as PNG or JPEG."""
    _scene, _legend, total_w, total_h, _gap = _plot_export_layout(plot_widget)
    width = max(1, int(round(total_w * scale)))
    height = max(1, int(round(total_h * scale)))
    image = QImage(
        width, height, QImage.Format.Format_ARGB32_Premultiplied
    )
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        _plot_export_paint(plot_widget, painter, float(width), float(height))
    finally:
        painter.end()
    if fmt == "jpg":
        return bool(image.save(str(path), "JPG", 95))
    return bool(image.save(str(path), "PNG"))


def _plot_export_pdf(plot_widget, path) -> bool:
    """Write a PDF whose axes, text and legend stay editable."""
    _scene, _legend, total_w, total_h, _gap = _plot_export_layout(plot_widget)
    writer = QPdfWriter(str(path))
    writer.setResolution(_PDF_DPI)
    writer.setCreator("Smage")
    writer.setPageSize(
        QPageSize(
            QSizeF(total_w * 72.0 / 96.0, total_h * 72.0 / 96.0),
            QPageSize.Unit.Point,
        )
    )
    writer.setPageMargins(
        QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter
    )
    painter = QPainter(writer)
    try:
        if not painter.isActive():
            return False
        _plot_export_paint(
            plot_widget, painter,
            float(writer.width()), float(writer.height()),
        )
    finally:
        painter.end()
    return True


def _plot_export_safe_name(name: str) -> str:
    """A filename Windows accepts, without an extension."""
    text = " ".join(str(name or "").split())
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text.strip(" .") or "plot"


def _plot_export_save(plot_widget, parent=None,
                      suggested_name: str = "plot") -> str | None:
    """Ask for a file and save the plot; return the written path or None.

    The selected filter picks the format when the typed name has no
    extension; an explicit .pdf/.png/.jpg/.jpeg always wins.
    """
    from PySide6.QtWidgets import QFileDialog

    global _LAST_DIR
    start_dir = _LAST_DIR
    if not start_dir:
        start_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        )
    if not start_dir:
        start_dir = str(Path.home())
    base = _plot_export_safe_name(suggested_name)
    initial = str(Path(start_dir) / (base + ".pdf"))
    filters = ";;".join(item[0] for item in _FILTERS)
    path, selected = QFileDialog.getSaveFileName(
        parent, "Save Plot As", initial, filters
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
    if suffix == ".pdf":
        ok = _plot_export_pdf(plot_widget, path)
    else:
        ok = _plot_export_image(
            plot_widget, path, "jpg" if suffix in (".jpg", ".jpeg") else "png"
        )
    if not ok:
        raise RuntimeError(f"Could not write {path}")
    return path
