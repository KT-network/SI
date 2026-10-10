from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtMultimedia import (
    QAudioDevice,
    QAudioFormat,
    QAudioSink,
    QAudioSource,
    QMediaDevices,
)

try:
    from PySide6.QtMultimedia import QtAudio as AudioEnums
except ImportError:
    from PySide6.QtMultimedia import QAudio as AudioEnums

from si.audio_buffers import CaptureBuffer, PlaybackBuffer, StreamConverter
from si.domain import AudioMetrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    default: bool


def list_devices(inputs: bool) -> list[Device]:
    devices = QMediaDevices.audioInputs() if inputs else QMediaDevices.audioOutputs()
    return [Device(bytes(d.id()).hex(), d.description(), d.isDefault()) for d in devices]


def find_device(device_id: str, inputs: bool) -> QAudioDevice:
    for device in QMediaDevices.audioInputs() if inputs else QMediaDevices.audioOutputs():
        if bytes(device.id()).hex() == device_id:
            return device
    raise RuntimeError("所选音频设备已断开或不可用")


def sample_format(fmt: QAudioFormat) -> str:
    mapping = {
        QAudioFormat.SampleFormat.Int16: "int16",
        QAudioFormat.SampleFormat.Int32: "int32",
        QAudioFormat.SampleFormat.Float: "float32",
        QAudioFormat.SampleFormat.UInt8: "uint8",
    }
    if fmt.sampleFormat() not in mapping:
        raise RuntimeError("设备音频格式不受支持")
    return mapping[fmt.sampleFormat()]


@dataclass(frozen=True)
class AudioRequest:
    direction: str
    epoch: int
    input_device: str
    output_device: str
    capture: CaptureBuffer
    input_rate: int = 16000
    output_rate: int = 24000
    muted: bool = False
    volume: int = 80
    probe: bool = False


class AudioTrack(QObject):
    failed = Signal(str, int, str)
    metrics = Signal(str, int, object)

    def __init__(self, request: AudioRequest, parent: QObject):
        super().__init__(parent)
        self.request = request
        self.source = None
        self.sink = None
        self.input_io = None
        self.output_io = None
        self.converter_out = None
        self.stopped = False
        self.opening = True
        self.muted = request.muted
        self.primed = False
        self.finished_audio = False
        self.underruns = 0
        self.was_playing = False
        self.last_metrics_log = 0.0
        self.received_bytes = 0
        self.written_bytes = 0
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.pump)
        self.metrics_timer = QTimer(self)
        self.metrics_timer.setInterval(100)
        self.metrics_timer.timeout.connect(self.report)

    def start(self) -> None:
        device_in = find_device(self.request.input_device, True)
        device_out = find_device(self.request.output_device, False)
        fmt_in = device_in.preferredFormat()
        fmt_out = device_out.preferredFormat()
        if not fmt_in.isValid() or not fmt_out.isValid():
            raise RuntimeError("设备没有可用的音频格式")
        logger.info(
            "[%s epoch=%s] Opening audio input=%s (%s Hz, %s channels, %s) output=%s (%s Hz, %s channels, %s) probe=%s",
            self.request.direction,
            self.request.epoch,
            device_in.description(),
            fmt_in.sampleRate(),
            fmt_in.channelCount(),
            sample_format(fmt_in),
            device_out.description(),
            fmt_out.sampleRate(),
            fmt_out.channelCount(),
            sample_format(fmt_out),
            self.request.probe,
        )
        self.converter_in = StreamConverter(
            fmt_in.sampleRate(),
            self.request.input_rate,
            sample_format(fmt_in),
            "int16",
            fmt_in.channelCount(),
        )
        self.format_out = fmt_out
        self.bytes_per_second = fmt_out.sampleRate() * fmt_out.bytesPerFrame()
        self.frame_bytes = fmt_out.bytesPerFrame()
        self._new_output_converter()
        self.playback = PlaybackBuffer(self.bytes_per_second * 8)
        self.silence = (b"\x80" if sample_format(fmt_out) == "uint8" else b"\x00") * max(
            self.frame_bytes,
            int(self.bytes_per_second * 0.02) // self.frame_bytes * self.frame_bytes,
        )
        self.sink = QAudioSink(device_out, fmt_out, self)
        self.sink.setBufferSize(int(self.bytes_per_second * 0.12))
        self.sink.setVolume(0 if self.muted else self.request.volume / 100)
        self.sink.stateChanged.connect(self.audio_state)
        self.output_io = self.sink.start()
        if self.output_io is None or self.sink.error() != AudioEnums.Error.NoError:
            raise RuntimeError(self.stream_error(self.sink, "输出", device_out.description()))
        self.source = QAudioSource(device_in, fmt_in, self)
        self.source.setBufferSize(int(fmt_in.sampleRate() * fmt_in.bytesPerFrame() * 0.08))
        self.source.stateChanged.connect(self.audio_state)
        self.input_io = self.source.start()
        if self.input_io is None or self.source.error() != AudioEnums.Error.NoError:
            raise RuntimeError(self.stream_error(self.source, "输入", device_in.description()))
        self.opening = False
        self.input_io.readyRead.connect(self.capture)
        self.timer.start()
        self.metrics_timer.start()
        logger.info("[%s epoch=%s] Audio started", self.request.direction, self.request.epoch)
        if self.request.probe:
            samples = np.arange(4800, dtype=np.float32) / self.request.output_rate
            tone = (
                0.08
                * np.sin(samples * 2 * np.pi * 440)
                * np.sin(np.linspace(0, np.pi, len(samples))) ** 2
            ).astype(np.float32)
            from si.audio_buffers import encode_samples

            self.receive(encode_samples(tone, "int16"))
            self.finish_audio()

    def _new_output_converter(self) -> None:
        self.converter_out = StreamConverter(
            self.request.output_rate,
            self.format_out.sampleRate(),
            "int16",
            sample_format(self.format_out),
            output_channels=self.format_out.channelCount(),
        )

    @Slot()
    def capture(self) -> None:
        if self.stopped:
            return
        try:
            frame = self.converter_in.feed(bytes(self.input_io.readAll()))
            if frame and not self.request.probe:
                self.request.capture.push(frame)
        except Exception as exc:
            self.fail(str(exc), exc_info=True)

    def receive(self, pcm: bytes) -> None:
        if self.stopped or self.muted:
            return
        try:
            self.finished_audio = False
            converted = self.converter_out.feed(pcm)
            self.received_bytes += len(converted)
            if self.playback.size + len(converted) > self.playback.max_bytes:
                logger.error(
                    "[%s epoch=%s] Playback overflow queued_ms=%s incoming_ms=%s received_ms=%s written_ms=%s free_bytes=%s state=%s error=%s",
                    self.request.direction,
                    self.request.epoch,
                    int(self.playback.size * 1000 / self.bytes_per_second),
                    int(len(converted) * 1000 / self.bytes_per_second),
                    int(self.received_bytes * 1000 / self.bytes_per_second),
                    int(self.written_bytes * 1000 / self.bytes_per_second),
                    self.sink.bytesFree(),
                    self.sink.state().name,
                    self.sink.error().name,
                )
            self.playback.push(converted)
        except Exception as exc:
            self.fail(str(exc), exc_info=True)

    def finish_audio(self) -> None:
        if self.stopped or self.muted:
            return
        try:
            tail = self.converter_out.feed(b"", last=True)
            self.received_bytes += len(tail)
            self.playback.push(tail)
            self._new_output_converter()
            self.finished_audio = True
        except Exception as exc:
            self.fail(str(exc), exc_info=True)

    @Slot()
    def pump(self) -> None:
        if self.stopped or self.output_io is None:
            return
        try:
            # Allow delayed timer ticks to catch up without changing playback speed.
            available = min(self.sink.bytesFree(), int(self.bytes_per_second * 0.12))
            available -= available % self.frame_bytes
            if available <= 0:
                return
            if (
                not self.primed
                and self.playback.size
                and (self.playback.size >= int(self.bytes_per_second * 0.08) or self.finished_audio)
            ):
                self.primed = True
            if self.primed and self.playback.size and not self.muted:
                chunk = self.playback.peek(available)
                written = self.output_io.write(chunk)
                if written < 0:
                    raise RuntimeError("输出设备写入失败")
                self.playback.consume(written)
                self.written_bytes += written
                self.was_playing = True
            else:
                if self.was_playing and not self.finished_audio and not self.muted:
                    self.underruns += 1
                self.was_playing = False
                self.primed = False
                if self.output_io.write(self.silence[: min(available, len(self.silence))]) < 0:
                    raise RuntimeError("输出设备已断开")
        except Exception as exc:
            self.fail(str(exc), exc_info=True)

    def set_output(self, muted: bool, volume: int) -> None:
        changed = self.muted != muted
        self.muted = muted
        if self.stopped:
            return
        self.sink.setVolume(0 if muted else max(0, min(100, volume)) / 100)
        if changed:
            self.playback.clear()
            self.primed = False
            self.was_playing = False
            self._new_output_converter()
            self.sink.reset()
            self.output_io = self.sink.start()
            if self.output_io is None or self.sink.error() != AudioEnums.Error.NoError:
                self.fail(self.stream_error(self.sink, "输出"))

    @staticmethod
    def stream_error(stream, direction: str, device_name: str = "") -> str:
        return (
            f"{direction}设备{' ' + device_name if device_name else ''}无法工作"
            f"（{stream.error().name}，{stream.state().name}）"
        )

    @Slot()
    def audio_state(self) -> None:
        if self.stopped or self.opening:
            return
        for direction, stream in (("输入", self.source), ("输出", self.sink)):
            if stream is not None and stream.error() not in (
                AudioEnums.Error.NoError,
                AudioEnums.Error.UnderrunError,
            ):
                self.fail(self.stream_error(stream, direction))
                return

    @Slot()
    def report(self) -> None:
        if not self.stopped:
            if time.monotonic() - self.last_metrics_log >= 5:
                self.last_metrics_log = time.monotonic()
                logger.info(
                    "[%s epoch=%s] Audio backlog_ms=%s underruns=%s received_ms=%s written_ms=%s",
                    self.request.direction,
                    self.request.epoch,
                    int(self.playback.size * 1000 / self.bytes_per_second),
                    self.underruns,
                    int(self.received_bytes * 1000 / self.bytes_per_second),
                    int(self.written_bytes * 1000 / self.bytes_per_second),
                )
            self.metrics.emit(
                self.request.direction,
                self.request.epoch,
                AudioMetrics(
                    self.converter_in.level,
                    int(self.playback.size * 1000 / self.bytes_per_second),
                    self.underruns,
                ),
            )

    def fail(self, message: str, *, exc_info: bool = False) -> None:
        if not self.stopped:
            logger.error(
                "[%s epoch=%s] Audio failed: %s",
                self.request.direction,
                self.request.epoch,
                message,
                exc_info=exc_info,
            )
            self.stop()
            self.failed.emit(self.request.direction, self.request.epoch, message)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        logger.info("[%s epoch=%s] Audio stopped", self.request.direction, self.request.epoch)
        self.timer.stop()
        self.metrics_timer.stop()
        self.request.capture.close()
        if self.source:
            self.source.stop()
        if self.sink:
            self.sink.reset()
        if hasattr(self, "playback"):
            self.playback.clear()


class AudioWorker(QObject):
    started = Signal(str, int)
    failed = Signal(str, int, str)
    metrics = Signal(str, int, object)
    probe_finished = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.tracks: dict[str, AudioTrack] = {}

    @Slot(object)
    def start_track(self, request: AudioRequest) -> None:
        self.stop_track(request.direction, 0)
        track = AudioTrack(request, self)
        self.tracks[request.direction] = track
        track.failed.connect(self.failed)
        track.metrics.connect(self.metrics)
        try:
            track.start()
            if track.stopped:
                return
            self.started.emit(request.direction, request.epoch)
            if request.probe:
                QTimer.singleShot(
                    3000, lambda: self._finish_probe(request.direction, request.epoch)
                )
        except Exception as exc:
            logger.exception(
                "[%s epoch=%s] Cannot open audio devices", request.direction, request.epoch
            )
            self.stop_track(request.direction, request.epoch)
            self.failed.emit(request.direction, request.epoch, str(exc))

    def _finish_probe(self, direction: str, epoch: int) -> None:
        track = self.tracks.get(direction)
        if track and track.request.epoch == epoch and track.request.probe:
            self.stop_track(direction, epoch)
            self.probe_finished.emit(direction, epoch)

    @Slot(str, int)
    def stop_track(self, direction: str, epoch: int) -> None:
        track = self.tracks.get(direction)
        if track and (not epoch or track.request.epoch == epoch):
            self.tracks.pop(direction)
            track.stop()
            track.deleteLater()

    @Slot(str, int, bytes)
    def receive(self, direction: str, epoch: int, pcm: bytes) -> None:
        track = self.tracks.get(direction)
        if track and track.request.epoch == epoch:
            track.receive(pcm)

    @Slot(str, int)
    def finish_audio(self, direction: str, epoch: int) -> None:
        track = self.tracks.get(direction)
        if track and track.request.epoch == epoch:
            track.finish_audio()

    @Slot(str, bool, int)
    def set_output(self, direction: str, muted: bool, volume: int) -> None:
        track = self.tracks.get(direction)
        if track:
            track.set_output(muted, volume)

    @Slot()
    def shutdown(self) -> None:
        for direction in list(self.tracks):
            self.stop_track(direction, 0)
