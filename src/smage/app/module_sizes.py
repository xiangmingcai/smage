"""Module folder sizes and resource readouts."""
from __future__ import annotations

from pathlib import Path
from smage.core.formatting import _format_bytes
from smage.widgets.resources import (
    _folder_size,
    _process_memory_bytes,
    _system_memory_bytes,
)


class ModuleSizesMixin:
    def _register_size_label(self, task_id, label) -> None:
        key = str(task_id)
        self._size_labels[key] = label
        if key in self._module_sizes:
            self._apply_module_size(key)
        else:
            self._schedule_module_size(key)

    def _schedule_module_size(self, task_id) -> None:
        """Measure a module folder in the background (cached until it saves)."""
        path = self._module_size_path(task_id)
        if path is None or not Path(path).is_dir():
            self._forget_module_size(task_id)
            return
        key = str(task_id)
        if key in self._size_pending:
            return
        self._size_pending.add(key)
        self._start_size_worker()
        self._size_jobs.put((key, str(path)))

    def _start_size_worker(self) -> None:
        import queue as queue_mod
        import threading

        if self._size_worker is not None:
            return
        if self._size_jobs is None:
            self._size_jobs = queue_mod.Queue()
            self._size_results = queue_mod.Queue()

        def run() -> None:
            while True:
                job = self._size_jobs.get()
                if job is None:
                    break
                task_id, path = job
                try:
                    size, count = _folder_size(path)
                except Exception:  # noqa: BLE001
                    size, count = 0, 0
                self._size_results.put((task_id, size, count))

        self._size_worker = threading.Thread(target=run, daemon=True)
        self._size_worker.start()
        self._size_timer.start()

    def _poll_module_sizes(self) -> None:
        import queue as queue_mod

        if self._size_results is None:
            return
        changed = False
        while True:
            try:
                task_id, size, count = self._size_results.get_nowait()
            except queue_mod.Empty:
                break
            self._size_pending.discard(task_id)
            self._module_sizes[task_id] = (size, count)
            self._store_module_size(task_id, size, count)
            self._apply_module_size(task_id)
            changed = True
        if changed:
            if self.project is not None:
                self.project.save()
            self._refresh_storage_usage()

    def _load_stored_sizes(self) -> None:
        """Read the per-task folder sizes persisted in the project file."""
        self._module_sizes.clear()
        if self.project is None:
            return

        def walk(task) -> None:
            storage = task.get("storage")
            if isinstance(storage, dict) and "bytes" in storage:
                try:
                    size = int(storage.get("bytes") or 0)
                    count = int(storage.get("files") or 0)
                except (TypeError, ValueError):
                    size = count = None
                if size is not None and task.get("id"):
                    self._module_sizes[str(task.get("id"))] = (size, count)
            for child in task.get("children") or []:
                walk(child)

        for task in self.project.workflow.get("tasks", []):
            walk(task)

    def _store_module_size(self, task_id, size, count) -> None:
        """Remember a module's folder size in its task definition."""
        task = self._find_task_by_id(task_id)
        if task is None:
            return
        task["storage"] = {"bytes": int(size), "files": int(count)}

    def _forget_module_size(self, task_id) -> None:
        """Drop a stored size whose folder no longer exists."""
        key = str(task_id)
        changed = key in self._module_sizes
        self._module_sizes.pop(key, None)
        if changed:
            self._refresh_storage_usage()
        if self.project is None:
            return
        task = self._find_task_by_id(key)
        if task is not None and "storage" in task:
            task.pop("storage", None)
            self.project.save()

    def _apply_module_size(self, task_id) -> None:
        label = self._size_labels.get(str(task_id))
        entry = self._module_sizes.get(str(task_id))
        if label is None or entry is None:
            return
        size, count = entry
        text = _format_bytes(size)
        label.setText(text)
        detail = f"{text} · {count:,} file(s)"
        path = self._module_size_path(task_id)
        if path is not None:
            detail += f"\n{path}"
        label.setToolTip(detail)
        label.setVisible(True)

    def _refresh_storage_usage(self) -> None:
        total = sum(size for size, _count in self._module_sizes.values())
        self._storage_total = total
        label = getattr(self, "_storage_label", None)
        if label is None:
            return
        label.setText(
            f"Storage {_format_bytes(total)}" if self._module_sizes
            else "Storage —"
        )
        if self.project is not None:
            label.set_tooltip(
                f"Disk space used by this project: {_format_bytes(total)}\n"
                f"{self.project.path}"
            )

    def _refresh_memory_usage(self) -> None:
        label = getattr(self, "_ram_label", None)
        working = _process_memory_bytes()
        if label is None:
            return
        label.setText(f"RAM {_format_bytes(working)}" if working else "RAM —")
        system = _system_memory_bytes()
        if working and system:
            total, available = system
            label.set_tooltip(
                f"Smage memory: {_format_bytes(working)}\n"
                f"System memory: {_format_bytes(available)} free "
                f"of {_format_bytes(total)}"
            )
        else:
            label.set_tooltip("Smage process working set")
