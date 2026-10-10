from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QObject
from PySide6.QtMultimedia import QAudioFormat

from si.audio import AudioEnums, AudioRequest, AudioTrack, AudioWorker
from si.audio_buffers import CaptureBuffer, PlaybackBuffer
from si.controller import DuplexController
from si.domain import RealtimeEvent, Settings, State
from si.realtime import GENERATION_SIZE, NetworkSignals


def test_old_audio_cannot_enter_a_new_track(qapp):
    worker = AudioWorker()
    track = SimpleNamespace(request=SimpleNamespace(epoch=22), receive=Mock(), finish_audio=Mock())
    worker.tracks["uplink"] = track
    worker.receive("uplink", 21, b"old")
    worker.finish_audio("uplink", 21)
    track.receive.assert_not_called()
    track.finish_audio.assert_not_called()
    worker.receive("uplink", 22, b"new")
    track.receive.assert_called_once_with(b"new")


def test_mute_clears_software_and_device_buffers(qapp):
    parent = QObject()
    track = AudioTrack(AudioRequest("uplink", 1, "in", "out", CaptureBuffer()), parent)
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    track.format_out = fmt
    track.playback = PlaybackBuffer(1000)
    track.playback.push(b"old")
    track.sink = Mock()
    track.sink.error.return_value = AudioEnums.Error.NoError
    track.set_output(True, 80)
    assert track.playback.size == 0
    track.sink.reset.assert_called_once()
    track.sink.setVolume.assert_called_once_with(0)
    track.receive(b"\x01\x00")
    assert track.playback.size == 0
    track.stop()


def test_controller_rejects_late_text_and_closes_worker_threads(qapp):
    network = Mock(signals=NetworkSignals())
    controller = DuplexController(Settings(), "test-only", network=network)
    try:
        controller._generations["uplink"] = 2
        epoch = 2 * GENERATION_SIZE + 1
        controller._epochs["uplink"] = epoch
        controller._event("uplink", GENERATION_SIZE + 1, RealtimeEvent("source", "s", "old"))
        assert controller.transcripts.rows() == []
        controller._event("uplink", epoch, RealtimeEvent("source", "s", "new"))
        assert controller.transcripts.rows()[0].source == "new"
        controller.states["uplink"] = State.STOPPING
        capture = CaptureBuffer()
        controller._ready("uplink", epoch, 16000, 24000, capture)
        assert capture.closed
    finally:
        controller.close()
    assert not controller.audio_thread.isRunning()
    network.close.assert_called_once()


def test_running_requires_successful_audio_start(qapp):
    network = Mock(signals=NetworkSignals())
    controller = DuplexController(Settings(), "test-only", network=network)
    try:
        controller._generations["downlink"] = 1
        epoch = GENERATION_SIZE + 1
        controller._epochs["downlink"] = epoch
        controller.states["downlink"] = State.CONFIGURING
        controller._network_state("downlink", epoch, State.RUNNING, "")
        assert controller.states["downlink"] == State.CONFIGURING
        controller._audio_started("downlink", epoch - 1)
        assert controller.states["downlink"] == State.CONFIGURING
        controller._audio_started("downlink", epoch)
        assert controller.states["downlink"] == State.RUNNING
    finally:
        controller.close()


def test_controller_carries_each_voice_to_its_own_session(qapp):
    settings = Settings(workspace_id="workspace")
    settings.uplink.voice, settings.downlink.voice = "Ethan", "Serena"
    controller = DuplexController(settings, "test-only", network=Mock(signals=NetworkSignals()))
    try:
        assert controller._session_config("uplink").voice == "Ethan"
        assert controller._session_config("downlink").voice == "Serena"
    finally:
        controller.close()
