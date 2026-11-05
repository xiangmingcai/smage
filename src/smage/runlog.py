"""Per-session diagnostic log.

Every time a project is opened, Smage starts a new log file inside
``<project>/log/``.  The file records what the user did (actions), what the
application did (module/project lifecycle, renders, long operations) and any
error with a full traceback, so a user can send the file back when something
goes wrong.

The log is written by a small background thread so the UI never waits for
disk I/O.  When no project is open nothing is written (a session log belongs
to a project).  Files are capped at ``MAX_BYTES`` (a ``_part2`` file continues
the session) and only the newest ``MAX_FILES`` files of each project are kept.
"""
from __future__ import annotations

import itertools
import os
import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIRNAME = "log"
FILE_PREFIX = "smage_"
MAX_FILES = 20
MAX_BYTES = 20 * 1024 * 1024
MAX_FIELD_CHARS = 300
MAX_LINE_CHARS = 2000
MAX_TRACEBACK_CHARS = 12000
FLUSH_INTERVAL = 0.5

_LEVELS = {
    "info": "INFO",
    "action": "ACTION",
    "warn": "WARN",
    "error": "ERROR",
}


def _format_value(value) -> str:
    """Render one log field as ``key=value`` friendly text."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value:  # NaN
            return "nan"
        return f"{value:.6g}"
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_format_value(v) for v in value)
        return f"[{inner}]" if len(inner) <= MAX_FIELD_CHARS else f"[{inner[:MAX_FIELD_CHARS]}…]"
    if isinstance(value, dict):
        inner = " ".join(f"{k}={_format_value(v)}" for k, v in value.items())
        return "{" + inner[:MAX_FIELD_CHARS] + ("…}" if len(inner) > MAX_FIELD_CHARS else "}")
    text = str(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > MAX_FIELD_CHARS:
        text = text[:MAX_FIELD_CHARS] + "…"
    if text == "" or any(ch.isspace() or ch in '="' for ch in text):
        text = '"' + text.replace('"', "'") + '"'
    return text


class SessionLog:
    """Thread-safe, per-project session log with a background writer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self._file = None
        self._path: Path | None = None
        self._dir: Path | None = None
        self._bytes = 0
        self._part = 1
        self._counter = itertools.count(1)
        self._started = time.time()

    # -- lifecycle ---------------------------------------------------------
    @property
    def path(self) -> Path | None:
        """The current log file (None while no project is open)."""
        return self._path

    @property
    def active(self) -> bool:
        return self._queue is not None

    def start(self, project_path, env: dict | None = None) -> None:
        """Start (or switch to) a session log for the given project folder."""
        project_dir = Path(project_path)
        log_dir = project_dir / LOG_DIRNAME
        with self._lock:
            if self._path is not None and self._dir == log_dir:
                return  # already logging this project
            if self._path is not None:
                self._emit("info", "project.switch", {"path": str(project_dir)})
            self._shutdown_locked()
            if not self._open_locked(log_dir):
                return
        self.info(
            "app.start",
            project=str(project_dir),
            version=str((env or {}).get("version", "")),
            python=str((env or {}).get("python", "")),
            pyside=str((env or {}).get("pyside", "")),
            qt=str((env or {}).get("qt", "")),
            os=str((env or {}).get("os", "")),
            screen=str((env or {}).get("screen", "")),
            pid=os.getpid(),
        )

    def stop(self, reason: str = "") -> None:
        """Record the end of the session and close the file."""
        if self._queue is None:
            return
        self.info(
            "app.stop", reason=reason or "closed",
            seconds=round(time.time() - self._started, 3),
        )
        with self._lock:
            self._shutdown_locked()
        self._started = time.time()

    # -- public logging ----------------------------------------------------
    def info(self, category: str, **fields) -> None:
        self._emit("info", category, fields)

    def action(self, category: str, **fields) -> None:
        self._emit("action", category, fields)

    def warn(self, category: str, **fields) -> None:
        self._emit("warn", category, fields)

    def error(self, category: str = "exception", exc: BaseException | None = None,
              **fields) -> None:
        """Log an error; an exception is stored with its full traceback."""
        block = None
        if exc is not None:
            text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            fields.setdefault("error", f"{type(exc).__name__}: {exc}")
            block = text[-MAX_TRACEBACK_CHARS:]
        self._emit("error", category, fields, block)

    # -- internals ---------------------------------------------------------
    def _emit(self, level: str, category: str, fields: dict,
              block: str | None = None) -> None:
        if self._queue is None:
            return
        line = self._format(level, category, **fields)
        if block:
            # Tracebacks stay multi-line (and readable) after the header.
            indented = chr(10).join(
                "    " + part for part in block.rstrip().splitlines()
            )
            line = line + chr(10) + indented
        self._queue.put(line)

    def _format(self, level: str, category: str, **fields) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        parts = [
            now,
            f"[{_LEVELS.get(level, 'INFO'):<6}]",
            f"#{next(self._counter):05d}",
            category,
        ]
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={_format_value(value)}")
        line = " ".join(parts)
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "…"
        return line

    @staticmethod
    def _session_files(log_dir: Path) -> list[Path]:
        try:
            files = [
                p for p in log_dir.iterdir()
                if p.is_file() and p.name.startswith(FILE_PREFIX)
                and p.suffix == ".log"
            ]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def _prune(self, log_dir: Path) -> None:
        """Keep only the newest MAX_FILES-1 files before adding a new one."""
        files = self._session_files(log_dir)
        for old in files[: max(0, len(files) - (MAX_FILES - 1))]:
            try:
                old.unlink()
            except OSError:
                pass

    def _open_locked(self, log_dir: Path) -> bool:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._prune(log_dir)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._path = log_dir / f"{FILE_PREFIX}{stamp}_p{os.getpid()}.log"
            self._file = open(self._path, "a", encoding="utf-8", newline="\n")
        except OSError:
            self._path = None
            self._file = None
            self._dir = None
            return False
        self._dir = log_dir
        self._bytes = 0
        self._part = 1
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()
        return True

    def _writer(self) -> None:
        """Consume queued lines; flush periodically so a crash loses little."""
        q = self._queue
        while q is not None:
            try:
                line = q.get(timeout=FLUSH_INTERVAL)
            except queue.Empty:
                self._flush()
                continue
            if line is None:
                self._flush()
                return
            self._write_line_locked(line)

    def _flush(self) -> None:
        fh = self._file
        if fh is not None:
            try:
                fh.flush()
            except (OSError, ValueError):
                pass

    def _write_line_locked(self, line: str) -> None:
        fh = self._file
        if fh is None:
            return
        if self._bytes > MAX_BYTES:
            self._rotate_locked()
            fh = self._file
            if fh is None:
                return
        try:
            fh.write(line + "\n")
            self._bytes += len(line) + 1
        except (OSError, ValueError):
            pass

    def _rotate_locked(self) -> None:
        """Continue the session in a ``_partN`` file after MAX_BYTES."""
        if self._path is None:
            return
        self._flush()
        try:
            self._file.close()
        except (OSError, ValueError):
            pass
        self._part += 1
        part = self._path.with_name(
            f"{self._path.stem}_part{self._part}.log"
        )
        try:
            self._file = open(part, "a", encoding="utf-8", newline="\n")
            self._bytes = 0
        except OSError:
            self._file = None

    def _shutdown_locked(self) -> None:
        q = self._queue
        thread = self._thread
        self._queue = None
        self._thread = None
        if q is not None:
            q.put(None)
        if thread is not None:
            thread.join(timeout=1.5)
        fh = self._file
        self._file = None
        self._path = None
        self._dir = None
        if fh is not None:
            try:
                fh.flush()
                fh.close()
            except (OSError, ValueError):
                pass


# The one session log used by the application.
runlog = SessionLog()
