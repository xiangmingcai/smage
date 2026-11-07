"""Reusable multi-select dialog (filter + tri-state + Shift range)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)


class _SelectAllFilter(QObject):
    """Select all text after a mouse click into the line edit."""

    def __init__(self, line_edit) -> None:
        super().__init__()
        self._line_edit = line_edit

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self._line_edit.selectAll)
        return False


def _pick_items_dialog(ctx, title, items, selected, placeholder='Filter...', reorder=False, summary=None) -> list | None:
    """Reusable multi-select dialog.

    Filter box, tri-state "Select all" over the currently visible rows,
    checkable rows with Shift-range toggling and a
    "<selected>/<available>" counter. With `reorder` the rows can be
    dragged into a new order, and `summary(checked_count)` supplies an
    extra line under the counter. Returns the checked labels in list
    order, or None when the dialog is cancelled.
    """
    selected = set(selected or [])
    result = {"value": None}

    dialog = QDialog(ctx.host)
    dialog.setWindowTitle(title)
    dialog.setMinimumSize(340, 420)
    dialog.resize(380, 560)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)

    filter_edit = QLineEdit()
    filter_edit.setPlaceholderText(placeholder)
    layout.addWidget(filter_edit)

    select_all = QCheckBox("Select all")
    select_all.setTristate(True)
    layout.addWidget(select_all)

    item_list = QListWidget()
    item_list.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection
    )
    if reorder:
        item_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        item_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        item_list.setDragEnabled(True)
        item_list.setToolTip("Drag rows to change their order")
    item_list.setStyleSheet(
        "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
    )
    for name in items:
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
        )
        item_list.addItem(item)
    layout.addWidget(item_list, 1)

    count_label = QLabel("")
    layout.addWidget(count_label)

    footer = QHBoxLayout()
    footer.addStretch()
    cancel_btn = QPushButton("Cancel")
    ok_btn = QPushButton("OK")
    cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    footer.addWidget(cancel_btn)
    footer.addWidget(ok_btn)
    layout.addLayout(footer)

    anchor = {"row": None}

    def visible_items():
        return [
            item_list.item(i)
            for i in range(item_list.count())
            if not item_list.item(i).isHidden()
        ]

    def update_counts() -> None:
        visible = visible_items()
        checked = sum(
            1 for item in visible
            if item.checkState() == Qt.CheckState.Checked
        )
        text = f"{checked}/{len(visible)}"
        if summary is not None:
            extra = summary(checked)
            if extra:
                text += f"    ·    {extra}"
        count_label.setText(text)
        if not visible or checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == len(visible):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        select_all.blockSignals(True)
        select_all.setCheckState(state)
        select_all.blockSignals(False)

    def set_visible_checked(checked: bool) -> None:
        target = (
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        item_list.blockSignals(True)
        for item in visible_items():
            item.setCheckState(target)
        item_list.blockSignals(False)
        update_counts()

    def on_select_all(_checked=False) -> None:
        visible = visible_items()
        if not visible:
            return
        all_checked = all(
            item.checkState() == Qt.CheckState.Checked for item in visible
        )
        set_visible_checked(not all_checked)

    def apply_filter(text: str) -> None:
        needle = text.strip().lower()
        item_list.blockSignals(True)
        for i in range(item_list.count()):
            item = item_list.item(i)
            item.setHidden(
                bool(needle) and needle not in item.text().lower()
            )
        item_list.blockSignals(False)
        update_counts()

    def on_item_clicked(item) -> None:
        row = item_list.row(item)
        modifiers = QApplication.keyboardModifiers()
        if (
            modifiers & Qt.KeyboardModifier.ShiftModifier
            and anchor["row"] is not None
        ):
            lo, hi = sorted((anchor["row"], row))
            state = item.checkState()
            item_list.blockSignals(True)
            for r in range(lo, hi + 1):
                other = item_list.item(r)
                if not other.isHidden():
                    other.setCheckState(state)
            item_list.blockSignals(False)
        anchor["row"] = row
        update_counts()

    def on_accept() -> None:
        result["value"] = [
            item_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(item_list.count())
            if item_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        dialog.accept()

    filter_edit.textChanged.connect(apply_filter)
    select_all.clicked.connect(on_select_all)
    item_list.itemClicked.connect(on_item_clicked)
    item_list.itemChanged.connect(lambda _item: update_counts())
    cancel_btn.clicked.connect(dialog.reject)
    ok_btn.clicked.connect(on_accept)

    update_counts()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return result["value"]
