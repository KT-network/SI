"""Exercise the application audio worker locally, discarding all captured audio."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication

from si.audio import AudioRequest, AudioWorker
from si.audio_buffers import CaptureBuffer
from si.diagnostics import configure_logging


class Probe(QObject):
    finished = Signal(object)

    def __init__(
        self,
        input_match: str,
        output_match: str,
        duration: float,
        playback: bool,
        pump_interval: int,
    ):
        super().__init__()
        self.input_match = input_match.lower()
        self.output_match = output_match.lower()
        self.duration = duration
        self.playback_test = playback
        self.pump_interval = pump_interval
        self.sent_samples = 0
        self.max_backlog_ms = 0
        self.worker = None
        self.captured = 0
        self.errors = []
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.drain)

    @Slot()
    def start(self):
        try:
            device_in = next(
                d
                for d in QMediaDevices.audioInputs()
                if self.input_match in d.description().lower()
            )
            device_out = next(
                d
                for d in QMediaDevices.audioOutputs()
                if self.output_match in d.description().lower()
            )
            self.worker = AudioWorker()
            self.worker.setParent(self)
            self.worker.failed.connect(lambda d, epoch, message: self.errors.append(message))
            self.worker.start_track(
                AudioRequest(
                    "downlink",
                    1,
                    bytes(device_in.id()).hex(),
                    bytes(device_out.id()).hex(),
                    CaptureBuffer(),
                    muted=not self.playback_test,
                    volume=0,
                )
            )
            track = self.worker.tracks.get("downlink")
            if track and not track.stopped:
                track.timer.setInterval(self.pump_interval)
            self.started_at = time.monotonic()
            self.timer.start()
            QTimer.singleShot(round(self.duration * 1000), self.finish)
        except Exception as exc:
            logging.getLogger("si.probe").exception("Audio probe failed")
            self.errors.append(str(exc) or "Matching device not found")
            self.finish()

    @Slot()
    def drain(self):
        track = self.worker.tracks.get("downlink") if self.worker else None
        if track:
            while frame := track.request.capture.take():
                self.captured += len(frame)
            if self.playback_test and not track.stopped:
                elapsed = min(self.duration, time.monotonic() - self.started_at)
                target_samples = int(elapsed * track.request.output_rate)
                samples = target_samples - self.sent_samples
                if samples > 0:
                    track.receive(b"\x00" * (samples * 2))
                    self.sent_samples = target_samples
                self.max_backlog_ms = max(
                    self.max_backlog_ms, int(track.playback.size * 1000 / track.bytes_per_second)
                )

    @Slot()
    def finish(self):
        self.timer.stop()
        self.drain()
        track = self.worker.tracks.get("downlink") if self.worker else None
        result = {
            "errors": self.errors,
            "captured_bytes": self.captured,
            "running": bool(track and not track.stopped),
        }
        if track and not track.stopped:
            result["input_processed_us"] = track.source.processedUSecs()
            result["output_processed_us"] = track.sink.processedUSecs()
            if self.playback_test:
                result["max_backlog_ms"] = self.max_backlog_ms
                result["received_ms"] = round(track.received_bytes * 1000 / track.bytes_per_second)
                result["written_ms"] = round(track.written_bytes * 1000 / track.bytes_per_second)
        if self.worker:
            self.worker.shutdown()
        self.finished.emit(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-match", default="CABLE-A")
    parser.add_argument("--output-match", default="Realtek")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--duration", type=float, default=1)
    parser.add_argument("--playback-test", action="store_true")
    parser.add_argument("--pump-interval", type=int, default=20)
    args = parser.parse_args()
    app = QApplication([])
    logs = configure_logging(Path("artifacts/audio-probe"), "DEBUG")
    logs.install_hooks()
    QMediaDevices.audioInputs()
    QMediaDevices.audioOutputs()
    thread = QThread()
    worker = Probe(
        args.input_match, args.output_match, args.duration, args.playback_test, args.pump_interval
    )
    worker.moveToThread(thread)
    thread.started.connect(worker.start)
    thread.finished.connect(worker.deleteLater)
    results = []

    def report(result):
        results.append(result)
        if args.result:
            args.result.write_text(json.dumps(result), encoding="utf-8")
        else:
            print(json.dumps(result), flush=True)

    worker.finished.connect(report)
    worker.finished.connect(thread.quit)
    thread.finished.connect(app.quit)
    thread.start()
    try:
        app.exec()
        thread.wait()
    finally:
        logs.close()
    return 0 if results and results[0]["running"] and not results[0]["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
