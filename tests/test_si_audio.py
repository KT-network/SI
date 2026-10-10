import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PySide6 import QtMultimedia
from PySide6.QtCore import QObject
from PySide6.QtMultimedia import QAudioFormat

from si.audio import AudioRequest, AudioTrack
from si.audio_buffers import (
    CaptureBuffer,
    PlaybackBuffer,
    StreamConverter,
    decode_samples,
    encode_samples,
)


@pytest.mark.parametrize("fmt", ["int16", "int32", "float32", "uint8"])
def test_device_formats_round_trip_without_clipping_wraparound(fmt):
    samples = np.array([-1, -0.25, 0, 0.25, 1], dtype=np.float32)
    actual = decode_samples(encode_samples(samples, fmt, 2), fmt, 2)
    assert np.allclose(samples, actual, atol=0.01)
    assert actual[-1] > 0.99


def test_streaming_resampler_handles_arbitrary_fragment_boundaries():
    samples = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000).astype(np.float32) * 0.2
    stereo = encode_samples(samples, "float32", 2)
    converter = StreamConverter(48000, 16000, "float32", "int16", 2)
    chunks = [converter.feed(stereo[i : i + 1003]) for i in range(0, len(stereo), 1003)]
    chunks.append(converter.feed(b"", last=True))
    result = decode_samples(b"".join(chunks), "int16", 1)
    assert abs(len(result) - 16000) <= 1
    crossings = np.count_nonzero((result[:-1] <= 0) & (result[1:] > 0))
    assert abs(crossings - 440) <= 2
    assert np.max(np.abs(result)) < 0.22


def test_24k_translation_to_48k_stereo_and_partial_frame_validation():
    converter = StreamConverter(24000, 48000, "int16", "float32", output_channels=2)
    raw = encode_samples(np.ones(2400, dtype=np.float32) * 0.1, "int16")
    result = converter.feed(raw) + converter.feed(b"", last=True)
    assert len(result) == 4800 * 2 * 4
    channels = np.frombuffer(result, dtype="<f4").reshape(-1, 2)
    assert np.array_equal(channels[:, 0], channels[:, 1])
    broken = StreamConverter(16000, 16000, "int16", "int16")
    broken.feed(b"\x01")
    with pytest.raises(ValueError):
        broken.feed(b"", last=True)


def test_capture_buffer_is_bounded_closed_and_does_not_replay():
    buffer = CaptureBuffer(4)
    assert buffer.push(b"ab") and buffer.push(b"cd")
    assert not buffer.push(b"ef") and buffer.overflowed
    assert buffer.take() == b"ab"
    buffer.close()
    assert buffer.take() == b"" and not buffer.push(b"gh")


def test_capture_buffer_supports_a_producer_thread():
    buffer = CaptureBuffer(1000)
    producer = threading.Thread(target=lambda: [buffer.push(bytes([i])) for i in range(100)])
    producer.start()
    producer.join()
    assert b"".join(buffer.take() for _ in range(100)) == bytes(range(100))


def test_playback_handles_partial_device_writes_without_reordering():
    buffer = PlaybackBuffer(8)
    buffer.push(b"abcd")
    buffer.push(b"efgh")
    assert buffer.peek(6) == b"abcdef"
    buffer.consume(3)
    assert buffer.peek(8) == b"defgh"
    with pytest.raises(BufferError):
        buffer.push(b"ijkl")
    buffer.clear()
    assert buffer.size == 0 and buffer.peek(100) == b""


def test_audio_start_accepts_current_qt_runtime_error_enum(qapp, monkeypatch):
    from si import audio

    runtime_enums = getattr(QtMultimedia, "QtAudio", QtMultimedia.QAudio)
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    device = SimpleNamespace(preferredFormat=lambda: fmt, description=lambda: "Test device")
    sink, source = Mock(), Mock()
    for stream in (sink, source):
        stream.error.return_value = runtime_enums.Error.NoError
        stream.state.return_value = runtime_enums.State.IdleState
    monkeypatch.setattr(audio, "find_device", lambda *args: device)
    monkeypatch.setattr(audio, "QAudioSink", lambda *args: sink)
    monkeypatch.setattr(audio, "QAudioSource", lambda *args: source)
    parent = QObject()
    track = AudioTrack(AudioRequest("downlink", 1, "in", "out", CaptureBuffer()), parent)
    failures = []
    track.failed.connect(lambda *args: failures.append(args))
    try:
        track.start()
        track.audio_state()
        assert not track.stopped and not track.opening
        assert failures == []
        sink.reset.assert_not_called()
    finally:
        track.stop()


def test_playback_can_fill_device_capacity_after_a_delayed_timer(qapp):
    parent = QObject()
    track = AudioTrack(AudioRequest("downlink", 1, "in", "out", CaptureBuffer()), parent)
    track.bytes_per_second = 384000
    track.frame_bytes = 8
    track.silence = b"\x00" * 7680
    track.playback = PlaybackBuffer(384000 * 8)
    track.playback.push(b"x" * 38400)
    track.output_io = Mock()
    track.output_io.write.side_effect = len
    track.sink = Mock()
    track.sink.bytesFree.return_value = 23040
    try:
        track.pump()
        assert track.output_io.write.call_args.args[0] == b"x" * 23040
        assert track.playback.size == 15360
        assert track.written_bytes == 23040
    finally:
        track.stop()


def test_playback_counts_only_bytes_accepted_by_device(qapp):
    parent = QObject()
    track = AudioTrack(AudioRequest("downlink", 1, "in", "out", CaptureBuffer()), parent)
    track.bytes_per_second = 1000
    track.frame_bytes = 2
    track.silence = b"\x00" * 20
    track.playback = PlaybackBuffer(8000)
    track.playback.push(b"x" * 100)
    track.output_io = Mock()
    track.output_io.write.return_value = 12
    track.sink = Mock()
    track.sink.bytesFree.return_value = 60
    try:
        track.pump()
        assert track.playback.size == 88 and track.written_bytes == 12
    finally:
        track.stop()
