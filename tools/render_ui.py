"""Render Widgets with synthetic captions; never starts capture or cloud sessions."""

from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows" if os.name == "nt" else "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

from si.controller import DuplexController
from si.domain import RealtimeEvent, Settings
from si.storage import ConfigStore, CredentialStore
from si.ui.icons import refresh_icons
from si.ui.log_dialog import LogDialog
from si.ui.main_window import MainWindow
from si.ui.settings_dialog import SettingsDialog
from si.ui.theme import apply_theme


def render() -> None:
    app = QApplication([])
    app.setApplicationName("SI.VisualQA")
    app.setOrganizationName("SI.VisualQA")
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    app.setFont(font)
    artifacts = Path(__file__).resolve().parent.parent / "artifacts"
    artifacts.mkdir(exist_ok=True)
    settings = Settings()
    settings.uplink.voice = "Katerina"
    settings.downlink.voice = "Theo Calm"
    controller = DuplexController(settings)
    try:
        window = MainWindow(
            settings,
            ConfigStore(artifacts / "visual-settings.json"),
            CredentialStore(),
            controller=controller,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        if window.tray:
            window.tray.hide()
        for i, (direction, source, translation, speaker) in enumerate(
            (
                (
                    "uplink",
                    "我们将在下周完成接口联调，并安排一次完整的双向会议测试。",
                    "We will finish the integration next week and schedule a full bidirectional meeting test.",
                    "0",
                ),
                (
                    "downlink",
                    "That works. Could you share the updated schedule after the meeting?",
                    "可以。会后能分享一下更新后的时间安排吗？",
                    "1",
                ),
                (
                    "uplink",
                    "好的，我会在会议结束后发送时间表。术语表也会一并更新。",
                    "Yes, I will send the schedule after the meeting and update the terminology list as well.",
                    "0",
                ),
            )
        ):
            epoch = 1
            source_id, translated_id = f"source-{i}", f"translation-{i}"
            for event in (
                RealtimeEvent("speech_start", source_id, speaker_id=speaker),
                RealtimeEvent("source", source_id, source, final=True),
                RealtimeEvent("link", translated_id, previous_item_id=source_id),
                RealtimeEvent("translation", translated_id, translation, final=True),
            ):
                controller.transcripts.apply(direction, epoch, event)
        for theme in ("light", "dark"):
            settings.theme = theme
            apply_theme(app, theme == "dark")
            refresh_icons(window)
            window.transcript_view.delegate.dark = theme == "dark"
            for width, height in ((1280, 720), (1600, 900), (1060, 670)):
                window.resize(width, height)
                window.show()
                window.mark_dirty()
                window.refresh_transcripts()
                app.processEvents()
                image = window.grab()
                image.save(str(artifacts / f"ui-test-data-{width}x{height}-{theme}.png"))
                assert window.transcript_view.list.horizontalScrollBar().maximum() == 0
                for panel in window.panels.values():
                    assert panel.status.geometry().bottom() <= panel.height()
                    widgets = [
                        w
                        for w in panel.findChildren(
                            QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
                        )
                        if w.isVisible()
                    ]
                    for i, first in enumerate(widgets):
                        for second in widgets[i + 1 :]:
                            assert not first.geometry().intersects(second.geometry()), (
                                f"Overlapping controls at {width}x{height}"
                            )
        dialog = SettingsDialog(settings, "", settings.remember_api_key, window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dialog.up_phrases.add("同声传译", "simultaneous interpretation")
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(artifacts / "settings-dark.png"))
        dialog.close()
        log_path = artifacts / "log-view-test-data"
        log_path.mkdir(exist_ok=True)
        (log_path / "si.log").write_text(
            "2026-10-09 12:00:00.000 INFO     [MainThread] si.main: Application started\n"
            "2026-10-09 12:00:01.000 ERROR    [Audio] si.audio: Output device OpenError\n"
            "Traceback (most recent call last):\n"
            '  File "audio.py", line 1, in start\n'
            "RuntimeError: Output device OpenError\n",
            encoding="utf-8",
        )
        log_dialog = LogDialog(log_path, window)
        log_dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        log_dialog.level.setCurrentIndex(2)
        log_dialog.resize(700, 420)
        log_dialog.show()
        app.processEvents()
        log_dialog.grab().save(str(artifacts / "logs-test-data-dark.png"))
        log_dialog.close()
        window.close()
        app.processEvents()
    finally:
        controller.close()
    print("Rendered six desktop views and one settings view; no audio or API sessions started.")


if __name__ == "__main__":
    render()
