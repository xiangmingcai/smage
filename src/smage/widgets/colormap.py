"""Colormap definitions, the picker and the custom-colormap editor."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QDialog, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from smage.core.settings import (
    _default_palette,
    _global_settings,
    _save_global_settings,
)
from smage.widgets.controls import (
    _NoWheelComboBox, _NoWheelDoubleSpinBox,
)


FIGURE_PALETTES = [
    "viridis", "plasma", "magma", "inferno", "cividis",
    "turbo", "jet", "coolwarm", "gray",
]
_COLORMAP_ALIASES = {"grayscale": "gray"}
_GRADIENT_CACHE = {}
_COLORMAP_GROUPS_CACHE = None

def _custom_colormaps() -> list:
    """User-defined colormaps (only the well-formed ones)."""
    maps = _global_settings().get("colormaps")
    if not isinstance(maps, list):
        return []
    out = []
    for entry in maps:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry.get("name")
            and isinstance(entry.get("stops"), list)
        ):
            out.append(entry)
    return out


def _normalize_stops(stops) -> list:
    """Validated ``[(position, (r, g, b)), ...]`` sorted by position."""
    out = []
    for item in stops or []:
        try:
            pos = float(item[0])
            rgb = tuple(int(max(0, min(255, int(c)))) for c in item[1][:3])
        except (TypeError, ValueError, IndexError):
            continue
        if len(rgb) != 3:
            continue
        out.append((min(1.0, max(0.0, pos)), rgb))
    out.sort(key=lambda stop: stop[0])
    return out


def _colormap_from_stops(stops):
    import pyqtgraph as pg

    points = _normalize_stops(stops)
    if len(points) < 2:
        raise ValueError("a colormap needs at least two stops")
    return pg.ColorMap(
        [pos for pos, _ in points], [list(rgb) for _, rgb in points]
    )


def _colormap_groups() -> list:
    """``[(group_title, [name, ...]), ...]`` for every selectable colormap."""
    import pyqtgraph as pg

    global _COLORMAP_GROUPS_CACHE
    if _COLORMAP_GROUPS_CACHE is None:
        try:
            mpl = sorted(pg.colormap.listMaps("matplotlib"))
        except Exception:
            mpl = []
        try:
            cet = sorted(n for n in pg.colormap.listMaps() if n not in mpl)
        except Exception:
            cet = []
        _COLORMAP_GROUPS_CACHE = [
            ("Standard", list(FIGURE_PALETTES)),
            ("Custom", []),
            ("Matplotlib", mpl),
            ("ColorCET", cet),
        ]
    groups = [(title, list(names)) for title, names in _COLORMAP_GROUPS_CACHE]
    groups[1] = ("Custom", [m["name"] for m in _custom_colormaps()])
    return groups


def _gradient_pixmap(lut, width: int, height: int):
    """Paint a horizontal colour bar from a lookup table."""
    import numpy as _np

    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#d8dee6"))
    if lut is not None and len(lut):
        lut = _np.asarray(lut)
        painter = QPainter(pixmap)
        for i in range(width):
            value = lut[min(i, len(lut) - 1)]
            painter.setPen(QColor(int(value[0]), int(value[1]), int(value[2])))
            painter.drawLine(i, 0, i, height - 1)
        painter.end()
    return pixmap


def _colormap_gradient_pixmap(name: str, width: int = 110, height: int = 14):
    """A small bar showing the colormap's colours, for list/preview widgets."""
    key = (name, width, height)
    cached = _GRADIENT_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        lut = _figure_colormap(name).getLookupTable(0.0, 1.0, max(2, width))
    except Exception:
        lut = None
    pixmap = _gradient_pixmap(lut, width, height)
    _GRADIENT_CACHE[key] = pixmap
    return pixmap


def _colormap_name_taken(name: str) -> bool:
    if not name:
        return False
    for _title, names in _colormap_groups():
        if name in names:
            return True
    return False


def _palette_names() -> list:
    """Standard colormaps followed by the user's custom ones."""
    names = list(FIGURE_PALETTES)
    for entry in _custom_colormaps():
        if entry["name"] not in names:
            names.append(entry["name"])
    return names


def _figure_colormap(name: str):
    """Return a pyqtgraph ColorMap for a palette name, with a fallback."""
    import pyqtgraph as pg
    if not isinstance(name, str) or not name:
        name = "viridis"
    name = _COLORMAP_ALIASES.get(name, name)
    for entry in _custom_colormaps():
        if entry.get("name") == name:
            try:
                return _colormap_from_stops(entry.get("stops"))
            except Exception:
                break
    # Matplotlib maps must be requested from their own source: the default
    # lookup raises FileNotFoundError for them.
    for kwargs in ({}, {"source": "matplotlib"}):
        try:
            return pg.colormap.get(name, **kwargs)
        except Exception:
            continue
    return pg.ColorMap([0.0, 1.0], [(0, 0, 255), (255, 0, 0)])


class _CustomColormapDialog(QDialog):
    """Create a custom colormap from a list of colour stops."""

    def __init__(self, parent=None, start_name: str = "viridis") -> None:
        super().__init__(parent)
        self.setWindowTitle("New Colormap")
        self.resize(560, 470)
        self._entry = None
        self._rows = []  # [[position, [r, g, b]], ...]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.addWidget(QLabel("Name"), 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My colormap")
        form.addWidget(self.name_edit, 0, 1)
        form.addWidget(QLabel("Start from"), 1, 0)
        self.start_combo = _NoWheelComboBox()
        for name in _palette_names():
            self.start_combo.addItem(name, name)
        index = self.start_combo.findData(start_name)
        self.start_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addWidget(self.start_combo, 1, 1)
        lay.addLayout(form)

        self.preview = QLabel()
        self.preview.setFixedHeight(30)
        lay.addWidget(self.preview)

        lay.addWidget(QLabel("Stops (position 0–1 and colour)"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Position", "Color", ""])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(2, 90)
        self.table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        lay.addWidget(self.table, 1)

        add_btn = QPushButton("Add stop")
        add_btn.clicked.connect(self._add_stop)
        lay.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color: #c0392b;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("OK")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_btn)
        lay.addLayout(buttons)

        self.name_edit.textChanged.connect(lambda _t: self._validate())
        self.start_combo.currentIndexChanged.connect(lambda _i: self._load_start())
        self._load_start()

    # -- stops ---------------------------------------------------------
    def _load_start(self) -> None:
        name = self.start_combo.currentData()
        rows = []
        try:
            cmap = _figure_colormap(name)
            for pos, color in zip(list(cmap.pos), list(cmap.color)):
                rows.append([float(pos), [int(c) for c in list(color)[:3]]])
        except Exception:
            rows = []
        if len(rows) > 7:
            # Built-in maps expand to 256 stops; keep a handful of samples so
            # the editor stays usable (and the saved JSON stays small).
            lut = cmap.getLookupTable(0.0, 1.0, 256)
            rows = []
            for i in range(7):
                position = i / 6.0
                color = lut[int(round(position * 255))]
                rows.append([position, [int(c) for c in color[:3]]])
        if len(rows) < 2:
            rows = [[0.0, [0, 0, 0]], [1.0, [255, 255, 255]]]
        self._rows = rows
        self._rebuild_table()

    def _color_at(self, position: float):
        try:
            cmap = _colormap_from_stops(self._rows)
            lut = cmap.getLookupTable(0.0, 1.0, 256)
            index = int(round(max(0.0, min(1.0, position)) * 255))
            return [int(c) for c in lut[index][:3]]
        except Exception:
            return [0, 0, 0]

    def _paint_swatch(self, button, rgb) -> None:
        button.setStyleSheet(
            f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); "
            "border: 1px solid #6b7785; border-radius: 3px;"
        )

    def _rebuild_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for i, (position, rgb) in enumerate(self._rows):
            spin = _NoWheelDoubleSpinBox()
            spin.setRange(0.0, 1.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.05)
            spin.setValue(float(position))
            spin.valueChanged.connect(
                lambda value, row=i: self._set_position(row, value)
            )
            self.table.setCellWidget(i, 0, spin)

            swatch = QPushButton()
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.clicked.connect(lambda _=False, row=i: self._pick_color(row))
            self._paint_swatch(swatch, rgb)
            self.table.setCellWidget(i, 1, swatch)

            remove = QPushButton("Remove")
            remove.setEnabled(len(self._rows) > 2)
            remove.clicked.connect(lambda _=False, row=i: self._remove_stop(row))
            self.table.setCellWidget(i, 2, remove)
        self._update_preview()

    def _set_position(self, row, value) -> None:
        if 0 <= row < len(self._rows):
            self._rows[row][0] = float(value)
            self._update_preview()

    def _pick_color(self, row) -> None:
        if not (0 <= row < len(self._rows)):
            return
        rgb = self._rows[row][1]
        chosen = QColorDialog.getColor(
            QColor(rgb[0], rgb[1], rgb[2]), self, "Choose stop color"
        )
        if chosen.isValid():
            self._rows[row][1] = [chosen.red(), chosen.green(), chosen.blue()]
            self._rebuild_table()

    def _add_stop(self) -> None:
        rows = sorted(self._rows, key=lambda r: r[0])
        position = 0.5
        if len(rows) >= 2:
            gaps = [
                (rows[i + 1][0] - rows[i][0], i) for i in range(len(rows) - 1)
            ]
            _gap, i = max(gaps)
            position = (rows[i][0] + rows[i + 1][0]) / 2.0
        self._rows.append([position, self._color_at(position)])
        self._rows.sort(key=lambda r: r[0])
        self._rebuild_table()

    def _remove_stop(self, row) -> None:
        if len(self._rows) <= 2 or not (0 <= row < len(self._rows)):
            return
        del self._rows[row]
        self._rebuild_table()

    def _update_preview(self) -> None:
        try:
            lut = _colormap_from_stops(self._rows).getLookupTable(0.0, 1.0, 260)
        except Exception:
            lut = None
        self.preview.setPixmap(_gradient_pixmap(lut, 260, 26))
        self._validate()

    # -- validation ----------------------------------------------------
    def _validate(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            message = "Enter a name for the colormap."
        elif _colormap_name_taken(name):
            message = f"“{name}” is already used by another colormap."
        else:
            message = ""
        self.hint.setText(message)
        self.ok_btn.setEnabled(not message)

    def _accept(self) -> None:
        self._entry = {
            "name": self.name_edit.text().strip(),
            "stops": [
                [round(float(pos), 4), list(rgb)]
                for pos, rgb in _normalize_stops(self._rows)
            ],
        }
        self.accept()

    def result_entry(self) -> dict:
        return self._entry or {}


class _ColormapPicker(QWidget):
    """Searchable list of every colormap with swatches, preview and CRUD."""

    selectionChanged = Signal(str)

    def __init__(self, current: str = "", parent=None) -> None:
        super().__init__(parent)
        self._current = current or _default_palette()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search colormaps…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.reload())
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QSize(110, 14))
        self.list.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        self.list.currentItemChanged.connect(self._on_current)
        lay.addWidget(self.list, 1)

        preview_row = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setMinimumHeight(26)
        preview_row.addWidget(self.preview, 1)
        self.preview_name = QLabel("")
        self.preview_name.setStyleSheet("color: #5a6673;")
        preview_row.addWidget(self.preview_name)
        lay.addLayout(preview_row)

        buttons = QHBoxLayout()
        self.new_btn = QPushButton("New custom…")
        self.new_btn.clicked.connect(self._new_custom)
        self.delete_btn = QPushButton("Delete custom")
        self.delete_btn.clicked.connect(self._delete_custom)
        buttons.addWidget(self.new_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch()
        lay.addLayout(buttons)

        self.reload()

    # -- list ----------------------------------------------------------
    def selected(self) -> str:
        return self._current

    def set_selected(self, name: str) -> None:
        if name:
            self._current = name
            self.reload()

    def reload(self) -> None:
        query = self.search.text().strip().lower()
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for title, names in _colormap_groups():
            shown = [n for n in names if not query or query in n.lower()]
            if not shown:
                continue
            header = QListWidgetItem(title)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            header.setForeground(QColor("#8a94a0"))
            self.list.addItem(header)
            for name in shown:
                item = QListWidgetItem(_colormap_gradient_pixmap(name), name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                self.list.addItem(item)
                if name == self._current:
                    target = item
        self.list.blockSignals(False)
        if target is not None:
            self.list.setCurrentItem(target)
            self.list.scrollToItem(
                target, QAbstractItemView.ScrollHint.PositionAtCenter
            )
        else:
            self._show_preview(self._current)

    def _show_preview(self, name: str) -> None:
        try:
            lut = _figure_colormap(name).getLookupTable(0.0, 1.0, 260)
        except Exception:
            lut = None
        self.preview.setPixmap(_gradient_pixmap(lut, 260, 24))
        self.preview_name.setText(name)
        custom = {m["name"] for m in _custom_colormaps()}
        self.delete_btn.setEnabled(name in custom)

    def _on_current(self, current, _previous) -> None:
        name = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not name:
            return
        self._current = name
        self._show_preview(name)
        self.selectionChanged.emit(name)

    # -- custom colormaps ----------------------------------------------
    def _new_custom(self) -> None:
        dialog = _CustomColormapDialog(self, self._current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dialog.result_entry()
        if not entry.get("name"):
            return
        settings = _global_settings()
        settings.setdefault("colormaps", []).append(entry)
        _save_global_settings(settings)
        _GRADIENT_CACHE.clear()
        self._current = entry["name"]
        self.reload()
        self.selectionChanged.emit(self._current)

    def _delete_custom(self) -> None:
        name = self._current
        settings = _global_settings()
        maps = settings.setdefault("colormaps", [])
        if not any(m.get("name") == name for m in maps):
            return
        reply = QMessageBox.question(
            self,
            "Delete Colormap",
            f"Delete the custom colormap “{name}”?\n"
            "Plots using it will fall back to viridis.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        settings["colormaps"] = [m for m in maps if m.get("name") != name]
        _save_global_settings(settings)
        _GRADIENT_CACHE.clear()
        self._current = _default_palette()
        self.reload()
        self.selectionChanged.emit(self._current)


def _populate_palette_combo(ctx, combo, current) -> None:
    """Fill a palette combo with standard + custom maps and a 'More…' entry."""
    for name in _palette_names():
        if combo.findData(name) < 0:
            combo.addItem(name, name)
    ctx._add_more_colormaps(combo)
    index = combo.findData(current)
    if index < 0 and current:
        combo.insertItem(0, str(current), str(current))
        index = 0
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo._palette_previous = max(0, combo.currentIndex())


def _add_more_colormaps(ctx, combo) -> None:
    combo.addItem("More colormaps…", "__more__")
    combo._palette_previous = max(
        0, combo.currentIndex()
    ) if combo.currentIndex() >= 0 else 0
    combo.currentIndexChanged.connect(
        lambda index, c=combo: ctx._palette_more_guard(c, index)
    )


def _palette_more_guard(ctx, combo, index: int) -> None:
    """Keep the sentinel from reaching the caller's handler."""
    if combo.itemData(index) != "__more__":
        combo._palette_previous = index
        return
    previous = getattr(combo, "_palette_previous", 0)
    combo.blockSignals(True)
    combo.setCurrentIndex(max(0, previous))
    combo.blockSignals(False)
    QTimer.singleShot(0, lambda: ctx._palette_pick_more(combo))


def _palette_pick_more(ctx, combo) -> None:
    current = combo.itemData(combo.currentIndex())
    name = ctx._pick_colormap(combo, current)
    if not name:
        return
    index = combo.findData(name)
    if index < 0:
        index = max(0, combo.count() - 1)
        combo.insertItem(index, name, name)
    combo.setCurrentIndex(index)


def _pick_colormap(ctx, parent, current) -> str:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Choose Colormap")
    dialog.resize(620, 560)
    lay = QVBoxLayout(dialog)
    start = current if isinstance(current, str) else ""
    picker = _ColormapPicker(start or _default_palette(), dialog)
    lay.addWidget(picker, 1)
    buttons = QHBoxLayout()
    buttons.addStretch()
    cancel = QPushButton("Cancel")
    cancel.clicked.connect(dialog.reject)
    ok = QPushButton("OK")
    ok.setDefault(True)
    ok.clicked.connect(dialog.accept)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    lay.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    return picker.selected()


