from si.ui.log_dialog import LogDialog, filter_log


def test_error_filter_keeps_complete_exception_traceback():
    text = (
        "2026-10-09 12:00:00.000 INFO     [MainThread] si: Started\n"
        "2026-10-09 12:00:01.000 ERROR    [Audio] si.audio: Open failed\n"
        "Traceback (most recent call last):\n"
        "  File 'audio.py', line 1\n"
        "RuntimeError: OpenError\n"
        "2026-10-09 12:00:02.000 INFO     [MainThread] si: Stopped\n"
    )
    result = filter_log(text, 40)
    assert "Traceback" in result and "RuntimeError: OpenError" in result
    assert "Started" not in result and "Stopped" not in result


def test_log_view_refresh_and_history(qapp, tmp_path):
    (tmp_path / "si.log").write_text("Current log", encoding="utf-8")
    (tmp_path / "si.log.1").write_text("Previous log", encoding="utf-8")
    dialog = LogDialog(tmp_path)
    assert dialog.text.toPlainText() == "Current log"
    (tmp_path / "si.log").write_text("New log", encoding="utf-8")
    dialog.refresh()
    assert dialog.text.toPlainText() == "New log"
    dialog.file.setCurrentIndex(1)
    assert dialog.text.toPlainText() == "Previous log"
    dialog.copy_button.click()
    assert qapp.clipboard().text() == "Previous log"
    dialog.close()
