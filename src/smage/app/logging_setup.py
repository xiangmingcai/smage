"""Qt message handler and uncaught-exception logging."""
from __future__ import annotations

from datetime import datetime
from smage.core.settings import _settings_dir
from smage.runlog import runlog
import sys


def _install_qt_message_handler() -> None:
    """Route Qt warnings/criticals into the session log (deduplicated)."""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return
    seen = set()

    def _dedup_key(mode, text):
        import re

        # Object addresses (0x…) differ per widget instance but the message is
        # the same, so they are removed before deduplicating.
        normalized = re.sub(r"0x[0-9a-fA-F]+", "0x…", text)
        return (int(mode), normalized[:160])

    def handler(mode, context, message) -> None:
        try:
            if mode in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
                return
            text = str(message).strip()
            if not text:
                return
            key = _dedup_key(mode, text)
            if key in seen or len(seen) > 200:
                return
            seen.add(key)
            level = "error" if mode in (
                QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg
            ) else "warn"
            getattr(runlog, level)(
                "qt.message", text=text,
                source=str(getattr(context, "file", "") or ""),
                line=int(getattr(context, "line", 0) or 0),
            )
        except Exception:
            pass

    qInstallMessageHandler(handler)


def _install_excepthook() -> None:
    """Log uncaught exceptions instead of losing them in a windowless app."""

    def hook(exc_type, exc, tb):
        try:
            runlog.error("exception", exc=exc)
        except Exception:
            pass
        try:
            _settings_dir().mkdir(parents=True, exist_ok=True)
            import traceback

            with (_settings_dir() / "error.log").open("a", encoding="utf-8") as fh:
                fh.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n")
                fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except OSError:
            pass

    sys.excepthook = hook

