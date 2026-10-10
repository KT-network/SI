import logging
import sys
import threading

from PySide6.QtCore import qWarning

from si.diagnostics import LogSession, configure_logging, log_directory, register_secret


def test_log_redacts_messages_arguments_and_exception_tracebacks(tmp_path):
    session = configure_logging(tmp_path, "DEBUG")
    secret = "private-test-credential-123"
    register_secret(secret)
    try:
        logger = logging.getLogger("si.test")
        logger.info("Chinese message: 设备已连接; key=%s", secret)
        logger.debug("Authorization: Bearer unknown-token-456")
        logger.info('api_key="another-credential-789"')
        logger.info("unexpected token sk-unknowncredential")
        try:
            raise RuntimeError(f"server rejected {secret}")
        except RuntimeError:
            logger.exception("Connection failed")
    finally:
        session.close()
    text = session.path.read_text(encoding="utf-8")
    assert "设备已连接" in text
    assert "Traceback" in text and "RuntimeError" in text
    assert "si.test" in text and "MainThread" in text
    for value in (secret, "unknown-token-456", "another-credential-789", "sk-unknowncredential"):
        assert value not in text
    assert "[REDACTED]" in text


def test_rotation_bounds_retained_files_and_close_flushes_queue(tmp_path):
    session = LogSession(tmp_path, max_bytes=500, backup_count=2)
    logger = logging.getLogger("si.test")
    try:
        for index in range(40):
            logger.info("Entry %02d %s", index, "x" * 60)
    finally:
        session.close()
    assert len(list(tmp_path.glob("si.log*"))) == 3
    assert "Entry 39" in session.path.read_text(encoding="utf-8")
    assert "Entry 00" not in "".join(
        p.read_text(encoding="utf-8") for p in tmp_path.glob("si.log*")
    )
    assert session.listener._thread is None


def test_qt_python_and_thread_exceptions_are_logged_and_hooks_restored(qapp, tmp_path):
    previous_sys_hook = sys.excepthook
    previous_thread_hook = threading.excepthook
    session = configure_logging(tmp_path)
    session.install_hooks()
    register_secret("hook-private-credential")
    try:
        qWarning("Qt warning with hook-private-credential")
        try:
            raise ValueError("GUI exception with hook-private-credential")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        def fail():
            raise RuntimeError("Worker exception with hook-private-credential")

        thread = threading.Thread(target=fail, name="diagnostics-test-worker")
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        session.close()
    assert sys.excepthook is previous_sys_hook
    assert threading.excepthook is previous_thread_hook
    text = session.path.read_text(encoding="utf-8")
    assert "Qt warning" in text and "si.qt" in text
    assert "GUI exception" in text and "ValueError" in text
    assert "diagnostics-test-worker" in text and "Worker exception" in text
    assert "hook-private-credential" not in text


def test_reconfiguration_has_no_duplicate_handlers_and_respects_level(tmp_path):
    first = configure_logging(tmp_path / "first", "DEBUG")
    second = configure_logging(tmp_path / "second", "WARNING")
    try:
        assert log_directory() == second.path.parent
        logging.getLogger("si.test").info("Filtered message")
        logging.getLogger("si.test").warning("One warning")
    finally:
        second.close()
        first.close()
    text = second.path.read_text(encoding="utf-8")
    assert "Filtered message" not in text
    assert text.count("One warning") == 1
    assert first.listener._thread is None
