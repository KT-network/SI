import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QSystemTrayIcon

from si.audio import Device
from si.domain import RealtimeEvent, Settings, State
from si.storage import ConfigStore, CredentialStore
from si.transcripts import TranscriptStore
from si.ui.main_window import MainWindow
from si.ui.settings_dialog import SettingsDialog
from si.ui.theme import apply_theme


class FakeController(QObject):
    state_changed = Signal(str, str, str)
    metrics_changed = Signal(str, object)
    transcript_changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.api_key = ""
        self.transcripts = TranscriptStore()
        self.states = {"uplink": State.IDLE, "downlink": State.IDLE}
        self.calls = []
        self.closed = False

    def start(self, directions):
        self.calls.append(("start", directions))

    def stop(self, direction, *, pause=False):
        self.calls.append(("stop", direction, pause))

    def set_output(self, direction, muted, volume):
        self.calls.append(("output", direction, muted, volume))

    def probe(self, direction):
        self.calls.append(("probe", direction))

    def close(self):
        self.closed = True


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from si.ui import main_window

    monkeypatch.setattr(
        main_window,
        "list_devices",
        lambda inputs: (
            [Device("mic", "真实麦克风", True), Device("b", "CABLE-B Output", False)]
            if inputs
            else [Device("a", "CABLE-A Input", False), Device("speaker", "真实耳机", True)]
        ),
    )
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    settings = Settings()
    controller = FakeController(settings)
    apply_theme(qapp)
    view = MainWindow(
        settings, ConfigStore(tmp_path / "settings.json"), CredentialStore(), controller=controller
    )
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.show()
    qapp.processEvents()
    yield view
    view.close()
    qapp.processEvents()


def add_subtitles(window):
    store = window.controller.transcripts
    for direction in ("uplink", "downlink"):
        store.apply(direction, 1, RealtimeEvent("source", "s", "原文", final=True))
        store.apply(direction, 1, RealtimeEvent("link", "t", previous_item_id="s"))
        store.apply(direction, 1, RealtimeEvent("translation", "t", "Translation", final=True))
    window.mark_dirty()
    window.refresh_transcripts()


def test_display_toggles_never_change_voice_or_connections(window, qapp):
    add_subtitles(window)
    assert len(window.transcript_view.model.rows) == 2
    QTest.mouseClick(window.panels["uplink"].source_check, Qt.MouseButton.LeftButton)
    window.refresh_transcripts()
    rows = window.transcript_view.model.rows
    assert rows[0].source == "" and rows[0].translation == "Translation"
    assert rows[1].source == "原文"
    assert window.controller.calls == []
    window.tabs.setCurrentIndex(1)
    assert len(window.transcript_view.model.rows) == 1


def test_emergency_mute_routes_to_both_tracks(window):
    window.emergency.setChecked(True)
    assert all(p.settings.muted for p in window.panels.values())
    assert ("output", "uplink", True, 80) in window.controller.calls
    assert ("output", "downlink", True, 80) in window.controller.calls
    window.panels["uplink"].mute_button.setChecked(False)
    assert not window.emergency.isChecked()


def test_transport_controls_and_running_state(window):
    window.start_both()
    assert window.controller.calls[-1] == ("start", ["uplink", "downlink"])
    window.controller.states["uplink"] = State.RUNNING
    window.controller.state_changed.emit("uplink", State.RUNNING, "")
    assert not window.panels["uplink"].input_combo.isEnabled()
    assert not window.panels["uplink"].voice.isEnabled()
    assert window.panels["downlink"].voice.isEnabled()
    assert window.panels["uplink"].pause_button.isEnabled()
    assert not window.settings_button.isEnabled()
    window.stop_both()
    assert window.controller.calls[-2:] == [("stop", "uplink", False), ("stop", "downlink", False)]


def test_voice_choices_are_independent_and_survive_window_reload(window, qapp):
    up, down = window.panels["uplink"], window.panels["downlink"]
    up.voice.setCurrentIndex(up.voice.findData("Ethan"))
    down.voice.setCurrentIndex(down.voice.findData("Theo Calm"))
    assert window.settings.uplink.voice == "Ethan"
    assert window.settings.downlink.voice == "Theo Calm"
    assert "舒缓" in down.voice.toolTip()
    assert window.controller.calls == []
    QTest.qWait(window.save_timer.interval() + 100)
    loaded = window.store.load()
    assert loaded.uplink.voice == "Ethan" and loaded.downlink.voice == "Theo Calm"
    from si.ui.widgets import TrackPanel

    restored = TrackPanel("downlink", loaded.downlink)
    assert restored.voice.currentData() == "Theo Calm"
    restored.deleteLater()


@pytest.mark.parametrize(
    "state",
    [
        State.CONNECTING,
        State.CONFIGURING,
        State.RUNNING,
        State.RECONNECTING,
        State.STOPPING,
        State.TESTING,
    ],
)
def test_voice_is_locked_until_the_track_can_start_a_new_session(window, state):
    panel = window.panels["uplink"]
    panel.set_state(state)
    assert not panel.voice.isEnabled()
    panel.set_state(State.PAUSED)
    assert panel.voice.isEnabled()


def test_long_text_wraps_at_narrow_desktop_sizes(window, qapp):
    store = window.controller.transcripts
    store.apply("uplink", 1, RealtimeEvent("source", "long", "原文" * 100, final=True))
    store.apply("uplink", 1, RealtimeEvent("link", "translation", previous_item_id="long"))
    store.apply(
        "uplink", 1, RealtimeEvent("translation", "translation", "LongestWord" * 60, final=True)
    )
    window.resize(1060, 670)
    window.mark_dirty()
    window.refresh_transcripts()
    qapp.processEvents()
    model = window.transcript_view.model
    assert model.rowCount() == 1
    assert window.transcript_view.list.horizontalScrollBar().maximum() == 0
    assert window.panels["downlink"].status.geometry().bottom() < window.panels["downlink"].height()
    assert window.grab().width() > 0


def test_settings_use_widgets_and_structured_terms(qapp):
    settings = Settings()
    dialog = SettingsDialog(settings, "", False)
    dialog.workspace.setText("workspace-123")
    dialog.up_phrases.add("千问", "Qwen")
    dialog.export_checks["uplink"][1].setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_settings.uplink.phrases == {"千问": "Qwen"}
    assert dialog.result_settings.uplink.save_translation
    assert not settings.uplink.save_translation


def test_settings_remember_preference_survives_dialog_save(qapp):
    settings = Settings()
    assert settings.remember_api_key
    dialog = SettingsDialog(settings, "test-key", settings.remember_api_key)
    assert dialog.remember.isChecked()
    dialog.remember.setChecked(False)
    dialog.accept()
    assert not dialog.result_settings.remember_api_key


def test_key_status_reflects_pending_changes(qapp):
    dialog = SettingsDialog(
        Settings(workspace_id="workspace"), "saved-test-key", True, key_saved=True
    )
    assert dialog.key_status.text() == "已保存到 Windows 凭据管理器"
    dialog.key.setText("changed-test-key")
    assert dialog.key_status.text() == "密钥尚未保存"
    dialog.remember.setChecked(False)
    assert dialog.key_status.text() == "密钥仅用于本次运行"


def test_settings_save_key_can_be_loaded_by_a_new_controller(window, monkeypatch):
    from PySide6.QtCore import QTimer

    from si import storage
    from si.ui import main_window

    vault = {}
    monkeypatch.setattr(
        storage.keyring,
        "set_password",
        lambda service, account, key: vault.update({(service, account): key}),
    )
    monkeypatch.setattr(
        storage.keyring, "get_password", lambda service, account: vault.get((service, account))
    )

    class AutoSaveDialog(SettingsDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self.workspace.setText("workspace-saved")
            self.key.setText("gui-persisted-test-key")
            QTimer.singleShot(0, self.accept)

    monkeypatch.setattr(main_window, "SettingsDialog", AutoSaveDialog)
    window.open_settings()
    loaded = window.store.load()
    assert loaded.remember_api_key
    assert CredentialStore().load(loaded) == "gui-persisted-test-key"
    assert "gui-persisted-test-key" not in window.store.path.read_text(encoding="utf-8")
    assert window.statusBar().currentMessage() == "设置和密钥已保存"


def test_export_dialog_locks_connection_and_terms(qapp):
    from PySide6.QtWidgets import QTabWidget

    dialog = SettingsDialog(Settings(), "test-only", False, records_only=True)
    tabs = dialog.findChild(QTabWidget)
    assert tabs.currentIndex() == 1
    assert not tabs.isTabEnabled(0) and not tabs.isTabEnabled(2)


def test_log_button_shows_active_log_and_traceback(window, tmp_path, qapp):
    import logging

    from si.diagnostics import configure_logging

    logs = configure_logging(tmp_path / "diagnostics")
    try:
        try:
            raise RuntimeError("Device OpenError")
        except RuntimeError:
            logging.getLogger("si.test").exception("Cannot start audio")
        logs.queue_handler.queue.join()
        QTest.mouseClick(window.logs_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        dialog = window._log_dialog
        assert dialog.isVisible()
        assert dialog.path_label.text() == str(logs.path)
        assert "Cannot start audio" in dialog.text.toPlainText()
        assert "RuntimeError: Device OpenError" in dialog.text.toPlainText()
        assert window.logs_button.toolTip() == "查看运行日志"
        dialog.close()
        assert not dialog.timer.isActive()
    finally:
        logs.close()
