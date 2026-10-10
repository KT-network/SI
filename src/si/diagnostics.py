from __future__ import annotations

import logging
import os
import queue
import re
import sys
import threading
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QtMsgType, qInstallMessageHandler

_secrets: set[str] = set()
_secret_lock = threading.Lock()
_session: LogSession | None = None


def register_secret(value: str) -> None:
    if value:
        with _secret_lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _secret_lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[\w.\-+/=]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    return re.sub(
        r"(?i)([\"']?(?:api[_-]?key|authorization)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
        r"\1[REDACTED]",
        text,
    )


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_directory() -> Path:
    if _session:
        return _session.path.parent
    return (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        / "logs"
    )


class LogSession:
    def __init__(
        self,
        directory: Path,
        level: str = "INFO",
        *,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 5,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory.resolve() / "si.log"
        self.file_handler = RotatingFileHandler(
            self.path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        self.file_handler.setFormatter(
            RedactingFormatter(
                "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.logger = logging.getLogger("si")
        self.previous_level = self.logger.level
        self.previous_propagate = self.logger.propagate
        self.logger.setLevel(level)
        self.logger.propagate = False
        self.queue_handler = QueueHandler(queue.Queue())
        # Redact before enqueueing, including formatted exception tracebacks.
        self.queue_handler.setFormatter(RedactingFormatter("%(message)s"))
        self.listener = QueueListener(self.queue_handler.queue, self.file_handler)
        self.listener.start()
        self.logger.addHandler(self.queue_handler)
        self._closed = False
        self._hooks_installed = False

    def install_hooks(self) -> None:
        if self._hooks_installed:
            return
        self._previous_sys_hook = sys.excepthook
        self._previous_thread_hook = threading.excepthook
        self._sys_hook = self._unhandled_exception
        self._thread_hook = self._unhandled_thread_exception
        sys.excepthook = self._sys_hook
        threading.excepthook = self._thread_hook
        self._previous_qt_handler = qInstallMessageHandler(self._qt_message)
        self._hooks_installed = True

    def _unhandled_exception(self, exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            self._previous_sys_hook(exc_type, exc_value, traceback)
            return
        logging.getLogger("si.exception").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, traceback)
        )

    @staticmethod
    def _unhandled_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        logging.getLogger("si.exception").critical(
            "Unhandled exception in thread %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    @staticmethod
    def _qt_message(kind, context, message: str) -> None:
        level = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }.get(kind, logging.INFO)
        logging.getLogger("si.qt").log(level, "%s: %s", context.category or "qt", message)

    def close(self) -> None:
        global _session
        if self._closed:
            return
        self._closed = True
        if self._hooks_installed:
            if sys.excepthook is self._sys_hook:
                sys.excepthook = self._previous_sys_hook
            if threading.excepthook is self._thread_hook:
                threading.excepthook = self._previous_thread_hook
            qInstallMessageHandler(self._previous_qt_handler)
        self.logger.removeHandler(self.queue_handler)
        self.listener.stop()
        self.queue_handler.close()
        self.file_handler.close()
        self.logger.setLevel(self.previous_level)
        self.logger.propagate = self.previous_propagate
        if _session is self:
            _session = None


def configure_logging(directory: Path | None = None, level: str = "INFO") -> LogSession:
    global _session
    directory = directory or log_directory()
    if _session:
        _session.close()
    register_secret(os.getenv("DASHSCOPE_API_KEY", ""))
    _session = LogSession(directory, level)
    return _session
