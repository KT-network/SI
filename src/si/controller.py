from __future__ import annotations

import logging
import re

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, Slot

from si.audio import AudioRequest, AudioWorker, find_device, list_devices
from si.audio_buffers import CaptureBuffer
from si.diagnostics import register_secret
from si.domain import ACTIVE_STATES, Direction, SessionConfig, Settings, State
from si.realtime import GENERATION_SIZE, NetworkService
from si.transcripts import TranscriptStore

logger = logging.getLogger(__name__)


class DuplexController(QObject):
    state_changed = Signal(str, str, str)
    metrics_changed = Signal(str, object)
    transcript_changed = Signal()
    audio_start = Signal(object)
    audio_stop = Signal(str, int)
    output_changed = Signal(str, bool, int)

    def __init__(
        self, settings: Settings, api_key: str = "", *, network: NetworkService | None = None
    ):
        super().__init__()
        self.settings = settings
        self.api_key = api_key
        register_secret(api_key)
        self.transcripts = TranscriptStore()
        self.states = {str(d): State.IDLE for d in Direction}
        self._generations = {str(d): 0 for d in Direction}
        self._epochs = {str(d): 0 for d in Direction}
        self._captures: dict[str, CaptureBuffer] = {}
        self._audio_errors: dict[str, str] = {}
        self._closed = False
        self._probe_epoch = 0
        self._last_outputs = {
            str(d): (settings.track(d).muted, settings.track(d).volume) for d in Direction
        }
        self.network = network or NetworkService()
        self.audio_thread = QThread()
        self.audio_thread.setObjectName("si-audio")
        self.audio_worker = AudioWorker()
        self.audio_worker.moveToThread(self.audio_thread)
        self.audio_thread.finished.connect(self.audio_worker.deleteLater)
        self.audio_start.connect(self.audio_worker.start_track, Qt.ConnectionType.QueuedConnection)
        self.audio_stop.connect(self.audio_worker.stop_track, Qt.ConnectionType.QueuedConnection)
        self.output_changed.connect(
            self.audio_worker.set_output, Qt.ConnectionType.QueuedConnection
        )
        self.audio_worker.failed.connect(self._audio_failed)
        self.audio_worker.started.connect(self._audio_started)
        self.audio_worker.metrics.connect(self._metrics)
        self.audio_worker.probe_finished.connect(self._probe_finished)
        signals = self.network.signals
        signals.state.connect(self._network_state)
        signals.ready.connect(self._ready)
        signals.event.connect(self._event)
        signals.audio.connect(self.audio_worker.receive, Qt.ConnectionType.QueuedConnection)
        signals.audio_done.connect(
            self.audio_worker.finish_audio, Qt.ConnectionType.QueuedConnection
        )
        self.audio_thread.start()

    def _set_state(self, direction: str, state: str, message: str = "") -> None:
        previous = self.states[direction]
        self.states[direction] = State(state)
        level = logging.ERROR if state == State.ERROR else logging.INFO
        logger.log(
            level,
            "[%s epoch=%s] State %s -> %s %s",
            direction,
            self._epochs[direction],
            previous,
            state,
            message,
        )
        self.state_changed.emit(direction, state, message)

    def _session_config(self, direction: str) -> SessionConfig:
        track = self.settings.track(direction)
        return SessionConfig(
            self.settings.region,
            self.settings.workspace_id.strip(),
            self.api_key.strip(),
            track.target_language,
            dict(track.phrases),
            voice=track.voice,
        )

    def validate(self, directions: list[str]) -> None:
        for direction in directions:
            if self.states[direction] in ACTIVE_STATES or self.states[direction] in (
                State.STOPPING,
                State.TESTING,
            ):
                raise ValueError("该方向正在运行或测试")
            config = self._session_config(direction)
            _ = config.url
            track = self.settings.track(direction)
            if not track.input_device or not track.output_device:
                raise ValueError("尚未选择完整的输入与输出设备")
            find_device(track.input_device, True)
            find_device(track.output_device, False)
        requested = set(directions) | {
            d for d, state in self.states.items() if state in ACTIVE_STATES
        }
        if len(requested) == 2:
            up, down = self.settings.uplink, self.settings.downlink
            if up.input_device == down.input_device:
                raise ValueError("上行与下行不能使用同一个输入设备")
            if up.output_device == down.output_device:
                raise ValueError("上行与下行不能使用同一个输出设备")
            inputs = {d.id: d.name for d in list_devices(True)}
            outputs = {d.id: d.name for d in list_devices(False)}
            up_name, down_name = (
                outputs.get(up.output_device, ""),
                inputs.get(down.input_device, ""),
            )

            def canonical(text: str) -> str:
                return (
                    re.sub(
                        r"\s+", " ", re.sub(r"\b(input|output)\b", "", text, flags=re.IGNORECASE)
                    )
                    .strip()
                    .lower()
                )

            if "cable" in up_name.lower() and canonical(up_name) == canonical(down_name):
                raise ValueError("上行输出与下行输入连接到同一条虚拟音频线，会形成翻译回流")

    def start(self, directions: list[str]) -> None:
        register_secret(self.api_key.strip())
        try:
            self.validate(directions)
        except (ValueError, RuntimeError):
            logger.error("Start rejected directions=%s", directions, exc_info=True)
            raise
        for direction in directions:
            self._generations[direction] += 1
            self._audio_errors.pop(direction, None)
            logger.info(
                "[%s] Start requested generation=%s language=%s",
                direction,
                self._generations[direction],
                self.settings.track(direction).target_language,
            )
            self._set_state(direction, State.CONNECTING)
            self.network.start(
                direction, self._generations[direction], self._session_config(direction)
            )

    def stop(self, direction: str, *, pause: bool = False) -> None:
        if self._closed:
            return
        state = self.states[direction]
        logger.info("[%s] %s requested", direction, "Pause" if pause else "Stop")
        self.audio_stop.emit(direction, 0)
        capture = self._captures.pop(direction, None)
        if capture:
            capture.close()
        if state in ACTIVE_STATES or state == State.STOPPING:
            self._audio_errors.pop(direction, None)
            self._set_state(direction, State.STOPPING)
            self.network.stop(direction, State.PAUSED if pause else State.IDLE)
        else:
            self._set_state(direction, State.PAUSED if pause else State.IDLE)

    def set_output(self, direction: str, muted: bool, volume: int) -> None:
        track = self.settings.track(direction)
        if self._last_outputs[direction][0] != muted:
            logger.info("[%s] Output muted=%s", direction, muted)
        logger.debug("[%s] Output volume=%s", direction, volume)
        track.muted = muted
        track.volume = max(0, min(100, volume))
        self._last_outputs[direction] = (track.muted, track.volume)
        self.output_changed.emit(direction, muted, track.volume)

    def probe(self, direction: str) -> None:
        if self.states[direction] in ACTIVE_STATES or self.states[direction] in (
            State.STOPPING,
            State.TESTING,
        ):
            raise ValueError("该方向正在运行")
        track = self.settings.track(direction)
        if not track.input_device or not track.output_device:
            raise ValueError("尚未选择完整的输入与输出设备")
        find_device(track.input_device, True)
        find_device(track.output_device, False)
        self._probe_epoch -= 1
        epoch = self._probe_epoch
        self._epochs[direction] = epoch
        logger.info("[%s epoch=%s] Local device test requested", direction, epoch)
        self._set_state(direction, State.TESTING)
        self.audio_start.emit(
            AudioRequest(
                direction,
                epoch,
                track.input_device,
                track.output_device,
                CaptureBuffer(),
                muted=track.muted,
                volume=track.volume,
                probe=True,
            )
        )

    def _accept(self, direction: str, epoch: int) -> bool:
        return (
            not self._closed
            and epoch > 0
            and epoch // GENERATION_SIZE == self._generations[direction]
        )

    @Slot(str, int, str, str)
    def _network_state(self, direction: str, epoch: int, state: str, message: str) -> None:
        if not self._accept(direction, epoch) or epoch < self._epochs[direction]:
            return
        if self.states[direction] == State.STOPPING and State(state) in ACTIVE_STATES:
            return
        if state == State.RUNNING:
            return
        old_epoch = self._epochs[direction]
        if old_epoch != epoch or state in (
            State.RECONNECTING,
            State.ERROR,
            State.IDLE,
            State.PAUSED,
        ):
            self.audio_stop.emit(direction, old_epoch)
            capture = self._captures.pop(direction, None)
            if capture:
                capture.close()
            if old_epoch > 0:
                self.transcripts.mark_interrupted(direction, old_epoch)
                self.transcript_changed.emit()
        self._epochs[direction] = epoch
        if state == State.ERROR:
            message = self._audio_errors.pop(direction, "") or message
        self._set_state(direction, state, message)

    @Slot(str, int, int, int, object)
    def _ready(
        self, direction: str, epoch: int, input_rate: int, output_rate: int, capture: CaptureBuffer
    ) -> None:
        if not self._accept(direction, epoch) or self.states[direction] == State.STOPPING:
            capture.close()
            return
        self._epochs[direction] = epoch
        self._captures[direction] = capture
        track = self.settings.track(direction)
        self.audio_start.emit(
            AudioRequest(
                direction,
                epoch,
                track.input_device,
                track.output_device,
                capture,
                input_rate,
                output_rate,
                track.muted,
                track.volume,
            )
        )

    @Slot(str, int, object)
    def _event(self, direction: str, epoch: int, event) -> None:
        if self._accept(direction, epoch) and epoch == self._epochs[direction]:
            self.transcripts.apply(direction, epoch, event)
            self.transcript_changed.emit()

    @Slot(str, int, object)
    def _metrics(self, direction: str, epoch: int, metrics) -> None:
        if not self._closed and epoch == self._epochs[direction]:
            self.metrics_changed.emit(direction, metrics)

    @Slot(str, int, str)
    def _audio_failed(self, direction: str, epoch: int, message: str) -> None:
        if self._closed or epoch != self._epochs[direction]:
            return
        if epoch < 0:
            self._set_state(direction, State.ERROR, message)
            return
        self._audio_errors[direction] = message
        self._set_state(direction, State.STOPPING, message)
        self.network.stop(direction, State.ERROR)

    @Slot(str, int)
    def _audio_started(self, direction: str, epoch: int) -> None:
        if (
            self._accept(direction, epoch)
            and epoch == self._epochs[direction]
            and self.states[direction] in ACTIVE_STATES
        ):
            self._set_state(direction, State.RUNNING)

    @Slot(str, int)
    def _probe_finished(self, direction: str, epoch: int) -> None:
        if epoch == self._epochs[direction] and self.states[direction] == State.TESTING:
            self._set_state(direction, State.IDLE)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        logger.info("Controller shutdown started")
        for capture in self._captures.values():
            capture.close()
        QMetaObject.invokeMethod(
            self.audio_worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection
        )
        self.audio_thread.quit()
        self.audio_thread.wait(3000)
        self.network.close()
        logger.info("Controller shutdown completed")
