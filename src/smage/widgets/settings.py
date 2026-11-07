"""Global settings dialog and its application to the running window."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

from smage.core.settings import (
    APP_STYLE,
    GLOBAL_DEFAULT_SETTINGS,
    _app_stylesheet,
    _global_settings,
    _plot_background,
    _save_global_settings,
    _setting,
)
from smage.widgets.colormap import _ColormapPicker
from smage.widgets.controls import _NoWheelComboBox


class _GlobalSettingsDialog(QDialog):
    """Global Settings: left tab list + right pages, applied on OK."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Global Settings")
        self.resize(840, 620)
        self._draft = copy.deepcopy(_global_settings())

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(10)

        self.tabs = QListWidget()
        self.tabs.setFixedWidth(160)
        self.tabs.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
            "QListWidget::item { padding: 7px 9px; }"
            "QListWidget::item:selected { background: #e1ecf9; color: #1a4a76; }"
        )
        body.addWidget(self.tabs)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        self.tabs.addItem("Appearance")
        self.tabs.addItem("Parameters")
        self.tabs.addItem("Gating")
        self.stack.addWidget(self._appearance_page())
        self.stack.addWidget(self._parameters_page())
        self.stack.addWidget(self._gating_page())
        self.tabs.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.tabs.setCurrentRow(0)

        footer = QHBoxLayout()
        restore = QPushButton("Restore Defaults")
        restore.clicked.connect(self._restore_defaults)
        footer.addWidget(restore)
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self._ok)
        footer.addWidget(cancel)
        footer.addWidget(ok)
        root.addLayout(footer)

    # -- pages ---------------------------------------------------------
    def _page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        return page

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #6b7785;")
        label.setWordWrap(True)
        return label

    def _appearance_page(self) -> QWidget:
        page = self._page()
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        appearance = self._draft["appearance"]
        form.addWidget(QLabel("Interface font size"), 0, 0)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 14)
        self.font_size.setSuffix(" pt")
        self.font_size.setValue(int(appearance.get("font_size", 9) or 9))
        form.addWidget(self.font_size, 0, 1)
        form.addWidget(
            self._hint("Menus, labels and tables. Takes effect immediately."),
            1, 0, 1, 2,
        )

        form.addWidget(QLabel("UI density"), 2, 0)
        self.density = _NoWheelComboBox()
        self.density.addItem("Normal", "normal")
        self.density.addItem("Compact", "compact")
        index = self.density.findData(appearance.get("density", "normal"))
        self.density.setCurrentIndex(index if index >= 0 else 0)
        form.addWidget(self.density, 2, 1)
        form.addWidget(
            self._hint("Compact shrinks tree and table rows so more fits on screen."),
            3, 0, 1, 2,
        )

        form.addWidget(QLabel("Plot background"), 4, 0)
        self.plot_background = _NoWheelComboBox()
        self.plot_background.addItem("White", "white")
        self.plot_background.addItem("Light grey", "grey")
        index = self.plot_background.findData(
            appearance.get("plot_background", "white")
        )
        self.plot_background.setCurrentIndex(index if index >= 0 else 0)
        form.addWidget(self.plot_background, 4, 1)
        form.addWidget(
            self._hint("Canvas colour of every plot. The light grey is easier on the eyes."),
            5, 0, 1, 2,
        )

        self.remember_geometry = QCheckBox("Remember window size and position")
        self.remember_geometry.setChecked(
            bool(appearance.get("remember_geometry", True))
        )
        form.addWidget(self.remember_geometry, 6, 0, 1, 2)

        page.layout().addLayout(form)
        page.layout().addStretch()
        return page

    def _parameters_page(self) -> QWidget:
        page = self._page()
        layout = page.layout()
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        plot = self._draft["plot"]

        def add_spin(row, label, hint, value, low, high, step):
            form.addWidget(QLabel(label), row * 2, 0)
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setGroupSeparatorShown(True)
            spin.setValue(int(value))
            form.addWidget(spin, row * 2, 1)
            form.addWidget(self._hint(hint), row * 2 + 1, 0, 1, 2)
            return spin

        self.max_points = add_spin(
            0, "Scatter max points",
            "Larger files are sampled down to this many points per scatter plot.",
            plot.get("max_points", 50000), 1000, 500000, 5000,
        )
        self.nxn_points = add_spin(
            1, "N×N plot max points",
            "Reserved for the future N×N plot grid: many small scatters at once, "
            "so it keeps fewer points. Not used yet. Follows Scatter max points ÷ 10 "
            "until you edit it here.",
            plot.get("nxn_max_points", 5000), 500, 100000, 1000,
        )
        self.density_grid = add_spin(
            2, "Density grid",
            "Bins per axis used to estimate point density for colouring. "
            "Higher is finer but slower.",
            plot.get("density_grid", 200), 50, 500, 10,
        )
        layout.addLayout(form)

        # N×N follows the scatter limit until the user edits it.
        def on_max_points(value):
            old_follow = max(500, int(self.max_points_previous / 10))
            if self.nxn_points.value() == old_follow:
                self.nxn_points.setValue(max(500, int(value / 10)))
            self.max_points_previous = value

        self.max_points_previous = self.max_points.value()
        self.max_points.valueChanged.connect(on_max_points)

        layout.addWidget(QLabel("Default colormap"))
        self.picker = _ColormapPicker(plot.get("default_colormap", "viridis"))
        layout.addWidget(self.picker, 1)
        layout.addWidget(self._hint(
            "Used when a new plot is created. Any colormap can still be chosen "
            "per plot; 'More colormaps…' in a plot's Palette list opens this same list."
        ))
        return page

    def _gating_page(self) -> QWidget:
        page = self._page()
        layout = page.layout()
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        gating = self._draft.get("gating") or {}

        form.addWidget(QLabel("Col2Gate category max"), 0, 0)
        self.col2gate_max = QSpinBox()
        self.col2gate_max.setRange(1, 100000)
        self.col2gate_max.setSingleStep(10)
        self.col2gate_max.setGroupSeparatorShown(True)
        self.col2gate_max.setValue(int(gating.get("col2gate_cate_max", 100)))
        form.addWidget(self.col2gate_max, 0, 1)
        form.addWidget(self._hint(
            "The Col2Gate tool refuses to turn a column into gates when it has "
            "more distinct values than this, so a continuous channel cannot "
            "create thousands of gates."
        ), 1, 0, 1, 2)
        layout.addLayout(form)
        layout.addStretch()
        return page

    # -- actions -------------------------------------------------------
    def _restore_defaults(self) -> None:
        defaults = copy.deepcopy(GLOBAL_DEFAULT_SETTINGS)
        if self.tabs.currentRow() == 0:
            appearance = defaults["appearance"]
            self.font_size.setValue(int(appearance["font_size"]))
            self.density.setCurrentIndex(
                max(0, self.density.findData(appearance["density"]))
            )
            self.plot_background.setCurrentIndex(
                max(0, self.plot_background.findData(appearance["plot_background"]))
            )
            self.remember_geometry.setChecked(bool(appearance["remember_geometry"]))
        elif self.tabs.currentRow() == 1:
            plot = defaults["plot"]
            self.max_points.setValue(int(plot["max_points"]))
            self.nxn_points.setValue(int(plot["nxn_max_points"]))
            self.density_grid.setValue(int(plot["density_grid"]))
            self.picker.set_selected(plot["default_colormap"])
        else:
            gating = defaults["gating"]
            self.col2gate_max.setValue(int(gating["col2gate_cate_max"]))

    def _ok(self) -> None:
        live = _global_settings()
        live["appearance"].update({
            "font_size": int(self.font_size.value()),
            "density": self.density.currentData() or "normal",
            "plot_background": self.plot_background.currentData() or "white",
            "remember_geometry": bool(self.remember_geometry.isChecked()),
        })
        live["plot"].update({
            "max_points": int(self.max_points.value()),
            "nxn_max_points": int(self.nxn_points.value()),
            "density_grid": int(self.density_grid.value()),
            "default_colormap": self.picker.selected(),
        })
        live.setdefault("gating", {})["col2gate_cate_max"] = int(
            self.col2gate_max.value()
        )
        _save_global_settings(live)
        self.accept()


def _open_settings(ctx) -> None:
    dialog = _GlobalSettingsDialog(ctx.host)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    ctx._apply_appearance_settings()
    ctx._refresh_plots_for_settings()


def _apply_appearance_settings(ctx) -> None:
    """Re-apply font, row density and plot colours from the settings."""
    import pyqtgraph as pg

    app = QApplication.instance()
    if app is None:
        return
    font = app.font()
    font.setPointSize(int(_setting("appearance", "font_size", 9) or 9))
    app.setFont(font)
    # Qt only applies the application font to widgets created afterwards,
    # so push it through the window (children inherit it).
    ctx.setFont(font)
    app.setStyleSheet(_app_stylesheet())
    pg.setConfigOption("background", _plot_background())
    for plot in ctx.findChildren(pg.PlotWidget):
        plot.setBackground(_plot_background())


def _refresh_plots_for_settings(ctx) -> None:
    """Re-render open plots so changed limits/colours show up right away."""
    for index in range(ctx.tabs.count()):
        page = ctx.tabs.widget(index)
        try:
            if hasattr(page, "_fig_plots") and hasattr(page, "_fig_cards"):
                ctx._figure_refresh_all(page)
            elif hasattr(page, "_gating_plot_widget"):
                ctx._gating_render(page)
            elif hasattr(page, "_area_plot"):
                ctx._area_render_plot(page)
        except Exception:
            continue


