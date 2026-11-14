"""Workflow tree: task cards, add-module picker, copy/paste, locking."""
from __future__ import annotations

from PySide6.QtCore import Signal

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from smage.runlog import runlog
from smage.widgets.resources import (
    _dot_pixmap,
    _lock_pixmap,
    _module_icon_pixmap,
)
from uuid import uuid4
import copy
from smage.data import columns as _data_columns
from smage.data import files as _data_files
from smage.data import stores as _data_stores
from smage.modules.import_ import page as _import__page
from smage.modules.rename import page as _rename_page


class _ModuleChip(QFrame):
    """One module node in the add-module mind map (icon + name pill)."""

    clicked = Signal()

    def __init__(self, mtype: str, name: str, color: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("moduleChip")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)
        self.setFixedHeight(30)
        self.setStyleSheet(
            "QFrame#moduleChip { background: #ffffff; border: 1px solid #cfd9e6;"
            " border-radius: 15px; } "
            f"QFrame#moduleChip:hover {{ border-color: {color};"
            " background: #f2f8ff; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 9, 2)
        lay.setSpacing(5)
        icon = QLabel()
        pixmap = _module_icon_pixmap(mtype, 16)
        if pixmap is not None:
            icon.setPixmap(pixmap)
            icon.setFixedSize(16, 16)
        else:
            icon.setPixmap(_dot_pixmap(color))
            icon.setFixedSize(12, 12)
        # Clicks belong to the pill: children must not swallow or forward them.
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(icon)
        label = QLabel(name)
        label.setStyleSheet("color: #2c3a48; font-size: 9pt;")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(label)

    def mousePressEvent(self, event) -> None:
        # Accept the press so it does not bubble up to the overlay, which
        # would dismiss the map before the release can produce a click.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _AddModuleOverlay(QWidget):
    """Full-window mind map shown when a task's "+" button is pressed.

    A short line leaves the button, fans out into one horizontal line per
    module group, and each group's modules follow as a chain of pills. Picking
    a pill or clicking anywhere else closes the map.
    """

    STUB = 100         # horizontal line leaving the "+"
    FAN_DX = 22        # horizontal span of the diagonal fan
    LABEL_STUB = 16    # short horizontal lead-in before a category name
    LABEL_GAP = 6      # gap between a category name and its connector lines
    CHIP_GAP = 10      # short connector between two pills
    CHIP_H = 30
    LINE_GAP = 10      # connector between wrapped pill lines
    ROW_GAP = 12

    def __init__(self, window, anchor: QPoint, on_choose) -> None:
        super().__init__(window)
        self._on_choose = on_choose
        self._anchor = anchor
        self._rows = []
        self.setGeometry(window.rect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # A resized window would leave the map anchored to a stale position.
        window.installEventFilter(self)
        self._label_font = QFont(self.font())
        self._label_font.setPointSizeF(9.0)
        self._label_font.setBold(True)
        self._build()

    # ----- layout ------------------------------------------------------
    def _build(self) -> None:
        metrics = QFontMetrics(self._label_font)
        left = self._anchor.x() + self.STUB + self.FAN_DX + 10
        right_limit = self.width() - 20
        left = max(12, min(left, max(12, right_limit - 260)))

        groups = []
        for title, kinds in ADD_MODULE_GROUPS:
            row_defs = ADD_MODULE_MAP_ROWS.get(title) or [list(kinds)]
            rows_types = [list(row) for row in row_defs]
            chips = []
            for row_types in rows_types:
                for mtype in row_types:
                    name, color = TASK_TYPES[mtype]
                    chip = _ModuleChip(mtype, name, color, self)
                    chip.clicked.connect(lambda t=mtype: self._choose(t))
                    chips.append(chip)
            groups.append((title, rows_types, chips))

        # All chip chains start at the same x, so short category names get a
        # longer connector line and every row's first module lines up.
        widest_label = max(
            (metrics.horizontalAdvance(title) + 10 for title, _r, _c in groups),
            default=0,
        )
        branch_x = left + widest_label + 6
        x0 = branch_x + 12

        rows = []
        total_h = 0
        for title, rows_types, chips in groups:
            label_w = metrics.horizontalAdvance(title) + 10
            lines = []
            cursor = x0
            chip_index = 0
            for row_types in rows_types:
                # A defined group row always starts its own line.
                lines.append([])
                cursor = x0
                for _mtype in row_types:
                    chip = chips[chip_index]
                    chip_index += 1
                    width = max(98, chip.sizeHint().width())
                    if cursor + width > right_limit and lines[-1]:
                        lines.append([])
                        cursor = x0
                    lines[-1].append((chip, cursor, width))
                    cursor += width + self.CHIP_GAP
            row_h = len(lines) * self.CHIP_H + (len(lines) - 1) * self.LINE_GAP
            rows.append({
                "title": title,
                "label_x": left,
                "label_w": label_w,
                "branch_x": branch_x,
                "x0": x0,
                "lines": lines,
                "h": row_h,
                "y": 0,
            })
            total_h += row_h + self.ROW_GAP
        total_h -= self.ROW_GAP

        top = self._anchor.y() - total_h / 2.0
        top = max(10.0, min(top, self.height() - total_h - 10.0))
        y = top
        for row in rows:
            row["y"] = y
            for line_index, line in enumerate(row["lines"]):
                cy = y + line_index * (self.CHIP_H + self.LINE_GAP)
                for chip, x, width in line:
                    chip.setGeometry(
                        int(x), int(cy), int(width), self.CHIP_H
                    )
                    chip.show()
            y += row["h"] + self.ROW_GAP
        self._rows = rows

    # ----- painting ----------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(10, 18, 30, 56))

        pen = QPen(QColor(108, 138, 172, 215))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        ax, ay = self._anchor.x(), self._anchor.y()
        stub_end = QPointF(ax + self.STUB, ay)
        painter.drawLine(QPointF(ax, ay), stub_end)

        for row in self._rows:
            first_y = row["y"] + self.CHIP_H / 2.0
            fan_end = QPointF(row["label_x"] - self.LABEL_STUB, first_y)
            painter.drawLine(stub_end, fan_end)
            # Short horizontal lead-in so every category name sits on a line,
            # with a space-sized gap before the text.
            painter.drawLine(
                fan_end, QPointF(row["label_x"] - self.LABEL_GAP, first_y)
            )

            painter.setFont(self._label_font)
            painter.setPen(QColor("#46586d"))
            painter.drawText(
                QRect(
                    int(row["label_x"]), int(first_y - 10),
                    int(row["label_w"]), 20,
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                row["title"],
            )
            painter.setPen(pen)

            for line_index, line in enumerate(row["lines"]):
                cy = row["y"] + line_index * (self.CHIP_H + self.LINE_GAP) \
                    + self.CHIP_H / 2.0
                if not line:
                    continue
                text_end = row["label_x"] + row["label_w"] - 10
                if len(row["lines"]) == 1:
                    painter.drawLine(
                        QPointF(text_end + self.LABEL_GAP, cy),
                        QPointF(line[0][1] - 2, cy),
                    )
                else:
                    # Split group: a bracket right after the label feeds every
                    # line of the group.
                    branch_x = row["branch_x"]
                    if line_index == 0:
                        last_cy = row["y"] + (len(row["lines"]) - 1) * (
                            self.CHIP_H + self.LINE_GAP
                        ) + self.CHIP_H / 2.0
                        painter.drawLine(
                            QPointF(text_end + self.LABEL_GAP, cy),
                            QPointF(branch_x, cy),
                        )
                        painter.drawLine(
                            QPointF(branch_x, cy), QPointF(branch_x, last_cy)
                        )
                    painter.drawLine(
                        QPointF(branch_x, cy), QPointF(line[0][1] - 2, cy)
                    )
                for index in range(len(line) - 1):
                    chip, x, width = line[index]
                    next_x = line[index + 1][1]
                    painter.drawLine(
                        QPointF(x + width, cy), QPointF(next_x - 2, cy)
                    )

    # ----- interaction -------------------------------------------------
    def _choose(self, mtype: str) -> None:
        callback = self._on_choose
        self._dismiss()
        callback(mtype)

    def _dismiss(self) -> None:
        window = self.parentWidget()
        if window is not None:
            # Drop the window's reference before the C++ object goes away,
            # otherwise the next "+" click would touch a deleted widget.
            if getattr(window, "_add_module_overlay", None) is self:
                window._add_module_overlay = None
            window.removeEventFilter(self)
        self.close()
        self.deleteLater()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._dismiss()
            return False
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dismiss()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._dismiss()
            return
        super().keyPressEvent(event)


TASK_TYPES = {
    "import": ("Data Import", "#9aa0a6"),
    "gating": ("Gating", "#f4b400"),
    "figure": ("Figure", "#34a853"),
    "heatmap": ("Heatmap", "#00838f"),
    "scale": ("Scale", "#4285f4"),
    "calculation": ("Calculation", "#6d4c41"),
    "rename": ("Rename", "#8e9aab"),
    "unmix": ("Unmix", "#7b61ff"),
    "segmentation": ("Segmentation", "#00acc1"),
    "region": ("Region", "#00897b"),
    "compensation": ("Compensation", "#fb8c00"),
    "illumination": ("Illumination Correction", "#8d6e63"),
    "feature": ("Feature Down", "#ec407a"),
    "feature_up": ("Feature Up", "#c2185b"),
    "gate2col": ("Gate2Col", "#455a64"),
    "subset": ("Subset", "#5c6bc0"),
    "subsample": ("Subsample", "#78909c"),
    "dimred": ("Dimension Reduction", "#ab47bc"),
    "cluster": ("Cluster", "#ef5350"),
    "region_size": ("Region Size", "#26a69a"),
    "data_clean": ("Data Clean", "#ff7043"),
    "batch_align": ("Batch Alignment", "#7e57c2"),
    "export": ("Export", "#66bb6a"),
}


ADD_MODULE_GROUPS = [
    ("Correct & transform", [
        "unmix",
        "scale",
        "compensation",
        "rename",
        "calculation",
        "illumination",
        "data_clean",
        "batch_align",
    ]),
    ("Segment & quantify", [
        "segmentation",
        "feature",
        "gate2col",
        "feature_up",
    ]),
    ("Gate & select", [
        "gating",
        "region",
        "subset",
        "subsample",
    ]),
    ("Analyse", [
        "dimred",
        "cluster",
        "region_size",
    ]),
    ("Present & export", [
        "figure",
        "heatmap",
        "export",
    ]),
]


ADD_MODULE_MAP_ROWS = {
    "Correct & transform": [
        ["unmix", "scale", "compensation", "rename"],
        ["calculation", "illumination", "data_clean", "batch_align"],
    ],
}


ADDABLE_TYPES = [kind for _title, kinds in ADD_MODULE_GROUPS for kind in kinds]


ROOT_COLOR = "#5f6368"


FALLBACK_COLOR = "#a142f4"


class _PinnedFirstTabBar(QTabBar):
    """A tab bar whose first tab can be selected but not dragged to reorder."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pressed_index = -1

    def mousePressEvent(self, event) -> None:
        self._pressed_index = self.tabAt(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._pressed_index = -1

    def mouseMoveEvent(self, event) -> None:
        if self._pressed_index == 0:
            return  # block dragging the pinned first (Workflow) tab
        super().mouseMoveEvent(event)


def _task_color(task_type: str) -> str:
    """Return the color for a task type."""
    if task_type == "root":
        return ROOT_COLOR
    return TASK_TYPES.get(task_type, ("Task", FALLBACK_COLOR))[1]


MODULE_KINDS = {
    "gating": "map",
    "scale": "map",
    "calculation": "map",
    "rename": "map",
    "compensation": "map",
    "illumination": "map",
    # Unmix can now generate image pyramids ("Unmix for image"), which locks
    # its upstream modules; the parameter-only behaviour still works.
    "unmix": "generate",
    "segmentation": "generate",
    "region": "map",
    "figure": "visualize",
    "heatmap": "visualize",
    "feature": "generate",
    "gate2col": "generate",
    # Placeholder modules (no implementation yet).
    "feature_up": "generate",
    "subset": "map",
    "subsample": "map",
    "dimred": "generate",
    "cluster": "generate",
    # Region Size stores a report table; Calculate marks it as generated so
    # the upstream Region/data modules lock until the results are cleared.
    "region_size": "generate",
    "data_clean": "generate",
    "batch_align": "map",
    # Export only reads the data and writes copies outside the project; it
    # never changes what flows downstream, so it does not lock anything.
    "export": "visualize",
}


def _task_kind(task_type: str) -> str:
    """Return a task's kind: import / map / generate / visualize."""
    if task_type == "import":
        return "import"
    return MODULE_KINDS.get(task_type, "map")


def _count_tasks(tasks) -> int:
    """Number of tasks in a workflow tree (modules of every depth)."""
    total = 0
    stack = list(tasks or [])
    while stack:
        task = stack.pop()
        total += 1
        stack.extend(task.get("children") or [])
    return total


def _task_is_locked(task: dict) -> bool:
    """True if the task is locked, manually or by saved downstream data.

    A manual lock (``task["locked"]``) is persisted with the project and has
    no permanent unlock: it can only be lifted for one session from the module
    toolbar. Saving generated output (for example segmentation masks) locks
    every module *above* it the same way; the generate module itself is never
    locked by that rule, and neither is anything below it.
    """
    if task.get("locked"):
        return True

    def walk(node) -> bool:
        for child in node.get("children", []):
            if (
                _task_kind(child.get("type", "")) == "generate"
                and child.get("output", {}).get("saved")
            ):
                return True
            if walk(child):
                return True
        return False

    return walk(task)



class WorkflowTreeMixin:
    def _task_locked(self, task_def) -> bool:
        """Effective lock: manual/automatic, unless unlocked for this session."""
        if not task_def:
            return False
        task_id = str(task_def.get("id") or "")
        if task_id and task_id in getattr(self, "_session_unlocked", ()):
            return False
        return _task_is_locked(task_def)

    def _find_task_by_id(self, task_id: str | None):
        """Return the workflow task dict with the given id, or None."""
        if self.project is None or not task_id:
            return None

        def walk(tasks):
            for task in tasks:
                if task.get("id") == task_id:
                    return task
                found = walk(task.get("children", []))
                if found is not None:
                    return found
            return None

        return walk(self.project.workflow.get("tasks", []))

    def _build_workflow_tab(self) -> None:
        workflow_panel = QWidget()
        wl = QVBoxLayout(workflow_panel)
        wl.setContentsMargins(8, 8, 8, 8)

        self.workflow_tree = QTreeWidget()
        self.workflow_tree.setHeaderHidden(True)
        self.workflow_tree.setIndentation(22)
        # Remember which nodes the user collapsed, so a rebuild (adding a
        # module, saving, reopening the project) never re-expands everything.
        self.workflow_tree.itemExpanded.connect(
            lambda item: self._on_tree_toggled(item, True)
        )
        self.workflow_tree.itemCollapsed.connect(
            lambda item: self._on_tree_toggled(item, False)
        )
        wl.addWidget(self.workflow_tree)

        self.tabs.addTab(workflow_panel, "Workflow")
        self._refresh_workflow_tree()

    def _task_item_widget(
        self, name: str, color: str, is_root: bool = False,
        on_add_child=None, on_actions=None, task_type: str | None = None,
        task_id: str | None = None, locked: bool = False,
    ) -> QWidget:
        """Build a card (icon + name + action buttons) for one task."""
        card = QFrame()
        card.setObjectName("taskCard")
        card.setStyleSheet(
            "QFrame#taskCard { background: #ffffff; "
            "border: 1px solid #dbe3ec; border-radius: 6px; }"
        )

        lay = QHBoxLayout(card)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        icon = QLabel()
        icon_pixmap = _module_icon_pixmap(task_type or "", 18)
        if icon_pixmap is not None:
            icon.setPixmap(icon_pixmap)
            icon.setFixedSize(18, 18)
        else:
            icon.setPixmap(_dot_pixmap(color))
            icon.setFixedSize(16, 16)
        lay.addWidget(icon)

        title = QLabel(name)
        if locked:
            title.setStyleSheet("color: #9aa0a6;")
        lay.addWidget(title)
        lay.addSpacing(12)

        if not is_root:
            buttons = QHBoxLayout()
            buttons.setSpacing(2)
            buttons.setContentsMargins(0, 0, 0, 0)

            open_btn = QPushButton("Open")
            open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_btn.clicked.connect(
                lambda _=False, tid=task_id, t=task_type, n=name: self._open_module(tid, t, n)
            )
            buttons.addWidget(open_btn)

            actions_btn = QPushButton("Actions ▾")
            actions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_actions is not None:
                actions_btn.clicked.connect(on_actions)
            buttons.addWidget(actions_btn)

            add_btn = QPushButton("+")
            add_btn.setFixedWidth(36)
            add_btn.setToolTip("Add child module")
            add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_add_child is not None:
                add_btn.clicked.connect(
                    lambda _=False, button=add_btn: on_add_child(button)
                )
            buttons.addWidget(add_btn)

            lay.addLayout(buttons)

            # Storage used by this module's own folder (hidden when it has none).
            size_label = QLabel()
            size_label.setStyleSheet("color: #6b7785; font-size: 8.5pt;")
            size_label.setToolTip("Disk space used by this module's folder")
            size_label.setVisible(False)
            lay.addWidget(size_label)
            if task_id:
                self._register_size_label(task_id, size_label)

        if locked:
            lock_icon = QLabel()
            lock_icon.setPixmap(_lock_pixmap())
            lock_icon.setFixedSize(14, 14)
            lock_icon.setToolTip("Locked — a downstream generate module has saved data")
            lay.addWidget(lock_icon)

        # Wrap the card so it only takes its content width (left-aligned),
        # with a little vertical margin so cards don't touch.
        container = QWidget()
        wrap = QVBoxLayout(container)
        wrap.setContentsMargins(0, 2, 0, 2)
        wrap.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
        return container

    def _show_actions_menu(self, task_def: dict, parent_task_def) -> None:
        """Show the task actions menu."""
        menu = self._build_actions_menu(task_def, parent_task_def)
        menu.exec(QCursor.pos())

    def _build_actions_menu(self, task_def: dict, parent_task_def) -> QMenu:
        """Build the task actions menu without showing it.

        Locking only stops a module from *saving* its changes; the workflow
        structure can still be edited (delete, paste, add children).
        """
        menu = QMenu(self)
        # Data Import is the fixed root of the workflow: it cannot be copied,
        # pasted over or deleted. The Paste actions stay: they are how a
        # copied module or branch becomes a top-level module.
        is_import = task_def.get("type") == "import"

        if not is_import:
            copy_action = QAction("Copy", self)
            copy_action.triggered.connect(lambda: self._copy_task(task_def, False))
            menu.addAction(copy_action)

            copy_branch_action = QAction("Copy branch", self)
            copy_branch_action.triggered.connect(
                lambda: self._copy_task(task_def, True)
            )
            menu.addAction(copy_branch_action)

            menu.addSeparator()

        paste_action = QAction("Paste", self)
        paste_action.triggered.connect(lambda: self._paste_task(task_def, False))
        menu.addAction(paste_action)

        paste_branch_action = QAction("Paste branch", self)
        paste_branch_action.triggered.connect(
            lambda: self._paste_task(task_def, True)
        )
        menu.addAction(paste_branch_action)

        menu.addSeparator()

        rename_action = QAction("Rename", self)
        rename_action.triggered.connect(lambda: _rename_page._rename_task(self._ctx, task_def))
        menu.addAction(rename_action)

        # Manual lock: a locked module opens as a temporary draft and can only
        # be unlocked for one session from its toolbar.
        lock_action = QAction("Lock", self)
        lock_action.setEnabled(not _task_is_locked(task_def))
        lock_action.setToolTip(
            "Lock this module: it opens as a temporary draft until unlocked "
            "for a session"
        )
        lock_action.triggered.connect(lambda: self._lock_task(task_def))
        menu.addAction(lock_action)

        if not is_import:
            delete_action = QAction("Delete", self)
            delete_action.triggered.connect(
                lambda: self._delete_task(task_def, parent_task_def, False)
            )
            menu.addAction(delete_action)

            delete_branch_action = QAction("Delete branch", self)
            delete_branch_action.triggered.connect(
                lambda: self._delete_task(task_def, parent_task_def, True)
            )
            menu.addAction(delete_branch_action)
        return menu

    def _copy_task(self, task_def: dict, with_branch: bool = False) -> None:
        """Copy a single task (or, with_branch, its whole branch) to the clipboard."""
        copied = copy.deepcopy(task_def)
        if not with_branch:
            copied["children"] = []
        _data_stores._attach_gating_stores(self._ctx, copied)
        self._clipboard = copied

    def _reset_pasted_outputs(self, task: dict) -> None:
        """Start a pasted subtree from settings only — no generated results.

        Generated data lives in ``Module/<task id>/`` and is never copied, so
        the copy must not carry the records that describe it: a generate module
        that still claims saved output would lock its new ancestors for files
        that do not exist, and its output/log tables would list data nobody can
        read. Settings (file selections, rules, keys, features…) are kept, so
        every pasted module opens fully editable.
        """
        for node in self._walk_tasks(task):
            params = node.get("params")
            if isinstance(params, dict):
                params.pop("log", None)
            # A copy of a locked module is a fresh configuration; it starts
            # unlocked so it can actually be saved.
            node.pop("locked", None)
            if _task_kind(node.get("type", "")) != "generate":
                continue
            node["output"] = {"saved": False}
            if not isinstance(params, dict):
                continue
            if "outputs" in params:
                params["outputs"] = {}
            if node.get("type") == "segmentation":
                # Per-file mask records and their signatures are generated too.
                if "masks" in params:
                    params["masks"] = {}
                if "applied" in params:
                    params["applied"] = {}

    def _remap_pasted_paths(self, task: dict, mapping: dict) -> None:
        """Rewrite ``Module/<old id>/…`` references in a pasted subtree.

        A module's file selections, generated outputs, matches and per-file
        overrides are stored as paths into the module folders of its upstream
        tasks. When those upstream tasks are pasted along with it they get new
        ids, so the paths must follow — otherwise the copy points at the
        original branch (or at nothing, once that branch is deleted).
        """
        for node in self._walk_tasks(task):
            params = node.get("params")
            if isinstance(params, dict):
                node["params"] = self._remap_path_value(params, mapping)

    def _remap_path_value(self, value, mapping: dict):
        """Recursively rewrite module-folder paths in params (keys and values)."""
        if isinstance(value, dict):
            return {
                self._remap_path_text(key, mapping): self._remap_path_value(item, mapping)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._remap_path_value(item, mapping) for item in value]
        if isinstance(value, str):
            return self._remap_path_text(value, mapping)
        return value

    def _remap_path_text(self, text: str, mapping: dict) -> str:
        if not text or "Module" not in text:
            return text
        for old, new in mapping.items():
            # Absolute and project-relative paths both contain this fragment.
            text = text.replace(f"Module\\{old}\\", f"Module\\{new}\\")
            text = text.replace(f"Module/{old}/", f"Module/{new}/")
        return text

    def _remap_pasted_refs(self, task: dict, mapping: dict) -> None:
        """Point pasted modules at the pasted copies of their inputs.

        Gate2Col rules reference the Gating task (and gate id) they convert.
        When the Gating task is part of the pasted subtree it gets a new id,
        so the rules must follow it. A Gate2Col pasted under a different
        Gating task is re-attached through the gate id it still carries.
        """
        stack = [task]
        while stack:
            node = stack.pop()
            stack.extend(node.get("children", []))
            if node.get("type") != "gate2col":
                continue
            rules = (node.get("params") or {}).get("rules") or []
            chain = _data_files._task_chain(self._ctx, node.get("id"))
            gating_tasks = [
                t for t in chain[:-1]
                if t.get("type") in ("gating", "region")
            ]
            known = {t.get("id") for t in gating_tasks}
            gate_owner = {}
            for owner in reversed(gating_tasks):  # nearest ancestor first
                for gate in _data_stores._gating_gates_for(self._ctx, owner):
                    gate_owner.setdefault(gate.get("id"), owner.get("id"))
            for rule in rules:
                current = rule.get("gate_task")
                if current and str(current) in mapping:
                    current = mapping[str(current)]
                    rule["gate_task"] = current
                if current in known:
                    continue
                gate_id = rule.get("gate_id")
                if gate_id and gate_id in gate_owner:
                    rule["gate_task"] = gate_owner[gate_id]

    def _paste_task(self, task_def: dict, with_branch: bool = False) -> None:
        """Paste a single task (or, with_branch, its whole branch) as a child."""
        if self._clipboard is None:
            return
        new_task = copy.deepcopy(self._clipboard)
        if not with_branch:
            new_task["children"] = []
        mapping = {}
        self._reassign_ids(new_task, mapping)
        self._reset_pasted_outputs(new_task)
        self._remap_pasted_paths(new_task, mapping)
        task_def.setdefault("children", []).append(new_task)
        self._remap_pasted_refs(new_task, mapping)
        _data_stores._restore_gating_stores(self._ctx, new_task, mapping)
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()

    def _reassign_ids(self, task: dict, mapping: dict | None = None) -> None:
        """Give a task (and its subtree) fresh unique ids.

        `mapping` collects ``{old id: new id}`` so cross-module references in
        the pasted subtree can be rewritten.
        """
        old = task.get("id")
        new = f"{task.get('type', 'task')}-{uuid4().hex[:8]}"
        task["id"] = new
        if mapping is not None and old:
            mapping[str(old)] = new
        for child in task.get("children", []):
            self._reassign_ids(child, mapping)

    def _collect_task_ids(self, task_def: dict) -> list:
        """Return the ids of a task and every descendant (pre-order)."""
        ids = []
        stack = [task_def]
        while stack:
            node = stack.pop()
            if node.get("id"):
                ids.append(node.get("id"))
            stack.extend(node.get("children", []))
        return ids

    def _cleanup_module_data(self, task_ids) -> None:
        """Delete the per-module data folders of the given task ids."""
        if self.project is None:
            return
        import shutil

        for tid in task_ids:
            self._module_sizes.pop(str(tid), None)
            self._size_labels.pop(str(tid), None)
            try:
                shutil.rmtree(
                    self.project.path / "Module" / str(tid), ignore_errors=True
                )
            except Exception:  # noqa: BLE001
                pass

        self._refresh_storage_usage()

    def _delete_task(self, task_def: dict, parent_task_def, with_branch: bool = False) -> None:
        """Delete a single task (or, with_branch, its whole branch).

        Deleting a generate module releases the lock it placed on the modules
        above it and removes its persisted data (masks); the workflow tree is
        rebuilt afterwards, so their Save buttons come back immediately.
        """
        name = task_def.get("name", "task")
        message = (
            f"Delete branch '{name}' and its sub-modules?"
            if with_branch
            else f"Delete '{name}'?"
        )
        reply = QMessageBox.question(
            self,
            "Delete",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        siblings = (
            self.project.workflow.get("tasks", [])
            if parent_task_def is None
            else parent_task_def.get("children", [])
        )
        if task_def not in siblings:
            return

        index = siblings.index(task_def)
        if with_branch:
            removed_ids = self._collect_task_ids(task_def)
            del siblings[index]
        else:
            # Remove only this task; promote its children into its place.
            removed_ids = [task_def.get("id")]
            siblings[index:index + 1] = task_def.get("children", [])

        self._cleanup_module_data([i for i in removed_ids if i])
        # The deleted task changed the stage chain of everything below it.
        _data_columns._bump_stages_revision(self._ctx)
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()
        self._refresh_locked_pages()

    def _refresh_locked_pages(self) -> None:
        """Rebuild the open Data Import tab when its lock state changed.

        The Data Import page keeps no unsaved edits, so rebuilding is safe and
        makes a released lock (e.g. after deleting a Segmentation module)
        take effect immediately.
        """
        task = _data_files._import_task(self._ctx)
        if task is None or self.project is None:
            return
        task_id = task.get("id")
        for index in range(1, self.tabs.count()):
            page = self.tabs.widget(index)
            if page.property("task_id") != task_id:
                continue
            if getattr(page, "_import_locked_state", None) == _import__page._import_locked(self._ctx):
                return
            fresh = _import__page._build_data_import_page(self._ctx)
            fresh.setProperty("task_id", task_id)
            title = self.tabs.tabText(index)
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, fresh, title)
            self.tabs.setCurrentIndex(index)
            return

    def _add_task(self, parent, name: str, color: str, is_root: bool = False,
                  on_add_child=None, on_actions=None, task_type: str | None = None,
                  task_id: str | None = None, locked: bool = False):
        """Add a task card to the tree and return its item."""
        item = QTreeWidgetItem([""])
        if task_id:
            item.setData(0, Qt.ItemDataRole.UserRole, str(task_id))
        if parent is None:
            self.workflow_tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self.workflow_tree.setItemWidget(
            item, 0, self._task_item_widget(
                name, color, is_root, on_add_child, on_actions, task_type, task_id,
                locked,
            )
        )
        return item

    def _on_tree_toggled(self, item, expanded: bool) -> None:
        """Record a node's expanded/collapsed state for this project."""
        if self._tree_restoring:
            return
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not task_id:
            return
        self._tree_expanded[str(task_id)] = bool(expanded)
        if self.project is not None:
            self.project.save_ui_state({"expanded": self._tree_expanded})

    def _load_tree_state(self) -> None:
        """Reload the tree expansion state from the current project."""
        if self.project is None:
            self._tree_expanded = {}
            return
        state = self.project.load_ui_state()
        expanded = state.get("expanded")
        self._tree_expanded = dict(expanded) if isinstance(expanded, dict) else {}

    def _refresh_workflow_tree(self) -> None:
        """Rebuild the workflow tree, keeping each node's expansion state."""
        if self.project is not self._tree_project:
            # A different project is open: pick up its saved layout.
            self._tree_project = self.project
            self._load_tree_state()
        force = set(self._tree_force_expand)
        self._tree_force_expand = set()
        live = set()
        self._tree_restoring = True
        try:
            self.workflow_tree.clear()
            self._size_labels.clear()
            root = self._add_task(None, "Workflow", ROOT_COLOR, is_root=True)
            root.setExpanded(True)
            if self.project is None:
                return
            for task in self.project.workflow.get("tasks", []):
                self._add_task_from_definition(root, task, None, force, live)
            # Drop remembered states of nodes that no longer exist.
            for task_id in list(self._tree_expanded):
                if task_id not in live:
                    self._tree_expanded.pop(task_id, None)
        finally:
            self._tree_restoring = False

    def _add_task_from_definition(self, parent, task_def: dict, parent_task_def=None,
                                  force=None, live=None):
        """Add a task card (and its children) from a workflow definition dict."""
        task_type = task_def.get("type", "")
        name = task_def.get("name") or TASK_TYPES.get(task_type, ("Task",))[0]
        color = _task_color(task_type)
        item = self._add_task(
            parent,
            name,
            color,
            on_add_child=lambda button=None, t=task_def: self._show_add_module_picker(t, button),
            on_actions=lambda checked=False, t=task_def, p=parent_task_def: self._show_actions_menu(t, p),
            task_type=task_type,
            task_id=task_def.get("id", ""),
            locked=self._task_locked(task_def),
        )
        task_id = task_def.get("id", "")
        if live is not None and task_id:
            live.add(str(task_id))
        expanded = bool(self._tree_expanded.get(str(task_id), True))
        if force and str(task_id) in force:
            expanded = True
            # Revealing a newly added node is a deliberate user action, so it
            # becomes the remembered state too.
            self._tree_expanded[str(task_id)] = True
        item.setExpanded(expanded)
        for child in task_def.get("children", []):
            self._add_task_from_definition(item, child, task_def, force, live)

    def _show_add_module_picker(self, parent_task_def: dict, anchor=None) -> None:
        """Open the mind-map module picker next to the "+" that was pressed."""
        if anchor is not None:
            point = anchor.mapTo(
                self, QPoint(anchor.width(), anchor.height() // 2)
            )
        else:
            point = self.mapFromGlobal(QCursor.pos())
        existing = getattr(self, "_add_module_overlay", None)
        if existing is not None:
            try:
                existing._dismiss()
            except RuntimeError:
                # The wrapper outlived its C++ widget; just drop it.
                pass
            self._add_module_overlay = None
        overlay = _AddModuleOverlay(
            self, point,
            lambda mtype: self._add_module(parent_task_def, mtype, None),
        )
        self._add_module_overlay = overlay
        overlay.show()
        overlay.raise_()
        overlay.setFocus()

    def _add_module(self, parent_task_def: dict, mtype: str, dialog=None) -> None:
        """Add a placeholder module of the given type as a child task."""
        name, _ = TASK_TYPES[mtype]
        new_task = {
            "id": f"{mtype}-{uuid4().hex[:8]}",
            "type": mtype,
            "name": name,
            "params": {},
            "children": [],
        }
        if _task_kind(mtype) == "generate":
            new_task["output"] = {"saved": False}
        parent_task_def.setdefault("children", []).append(new_task)
        runlog.info(
            "module.add", id=new_task["id"], type=mtype, name=name,
            parent=str(parent_task_def.get("id") or ""),
        )
        # Reveal the new module without expanding anything else: only its
        # ancestor path is forced open, every other node keeps its state.
        force = {str(new_task["id"])}
        parent_id = parent_task_def.get("id")
        if parent_id:
            force |= {str(t.get("id")) for t in _data_files._task_chain(self._ctx, parent_id)}
        self._tree_force_expand = force
        if self.project is not None:
            self.project.save()
        self._refresh_workflow_tree()
        if dialog is not None:
            dialog.accept()

    def _lock_task(self, task_def: dict) -> None:
        """Persistently lock a task (manual lock from the + map)."""
        if not isinstance(task_def, dict) or task_def.get("locked"):
            return
        name = task_def.get("name") or "Module"
        reply = QMessageBox.question(
            self, "Lock Module",
            f"Lock '{name}'?\n\n"
            "A locked module opens as a temporary draft: changes are kept in "
            "a temporary folder and are not saved until you unlock the module "
            "for a session from its toolbar.\n\n"
            "Lock this module?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        task_id = str(task_def.get("id") or "")
        # An open page must be closed first; its own Close path runs the
        # unsaved-changes prompt, and cancelling there cancels the lock.
        for index in range(1, self.tabs.count()):
            page = self.tabs.widget(index)
            if str(page.property("task_id")) != task_id:
                continue
            self.tabs.setCurrentIndex(index)
            close_btn = next(
                (b for b in page.findChildren(QPushButton)
                 if b.text() == "Close"),
                None,
            )
            if close_btn is not None:
                close_btn.click()
            else:
                self._close_module_page(page)
            QApplication.processEvents()
            if self.tabs.indexOf(page) >= 0:
                return  # the page stayed open (user cancelled)
            break
        task_def["locked"] = True
        if self.project is not None:
            self.project.save()
        runlog.action(
            "module.lock", id=task_id,
            type=str(task_def.get("type") or ""),
        )
        self._refresh_workflow_tree()

    def _walk_tasks(self, node):
        yield node
        for child in node.get("children", []):
            yield from self._walk_tasks(child)
