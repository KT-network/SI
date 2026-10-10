from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from si.audio_buffers import CaptureBuffer
from si.diagnostics import register_secret
from si.domain import RealtimeEvent, SessionConfig, State
from si.protocol import (
    ServiceError,
    negotiated_rates,
    negotiated_voice,
    normalize,
    service_error,
)

GENERATION_SIZE = 1_000_000
logger = logging.getLogger(__name__)


class NetworkSignals(QObject):
    state = Signal(str, int, str, str)
    ready = Signal(str, int, int, int, object)
    event = Signal(str, int, object)
    audio = Signal(str, int, bytes)
    audio_done = Signal(str, int)


@dataclass
class Runtime:
    direction: str
    generation: int
    config: SessionConfig
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    capture: CaptureBuffer | None = None
    epoch: int = 0
    running: bool = False
    final_state: str = State.IDLE
    finished_notified: bool = False


class RealtimeClient:
    def __init__(
        self,
        runtime: Runtime,
        signals: NetworkSignals,
        *,
        connector=connect,
        url: str | None = None,
    ):
        self.runtime = runtime
        self.signals = signals
        self.connector = connector
        self.url = url

    def _state(self, state: str, message: str = "") -> None:
        r = self.runtime
        self.signals.state.emit(r.direction, r.epoch, state, message)

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("Expected event object")
            return event
        except (ValueError, TypeError) as exc:
            raise ServiceError("服务端返回了无效的消息") from exc

    async def _expect(self, ws, event_type: str) -> dict[str, Any]:
        async def receive():
            while True:
                event = self._decode(await ws.recv())
                if event.get("type") == "error":
                    raise service_error(event)
                if event.get("type") == event_type:
                    return event
                if event.get("type") == "session.finished":
                    raise ServiceError("会话在配置完成前已结束")

        return await asyncio.wait_for(receive(), timeout=12)

    async def run(self) -> None:
        r = self.runtime
        register_secret(r.config.api_key)
        uri = self.url or r.config.url
        logger.info(
            "[%s epoch=%s] Connecting region=%s language=%s voice=%s",
            r.direction,
            r.epoch,
            r.config.region,
            r.config.target_language,
            r.config.voice,
        )
        async with self.connector(
            uri,
            additional_headers={"Authorization": f"Bearer {r.config.api_key}"},
            open_timeout=10,
            close_timeout=1,
            ping_interval=20,
            ping_timeout=20,
            max_size=4 * 1024 * 1024,
            max_queue=16,
        ) as ws:
            created = await self._expect(ws, "session.created")
            logger.debug("[%s epoch=%s] Session created", r.direction, r.epoch)
            self._state(State.CONFIGURING)
            await ws.send(json.dumps(r.config.update_event(), ensure_ascii=False))
            updated = await self._expect(ws, "session.updated")
            voice = negotiated_voice(updated.get("session") or {}, r.config.voice)
            input_rate, output_rate = negotiated_rates(
                updated.get("session") or created.get("session", {})
            )
            logger.info(
                "[%s epoch=%s] Session configured input_rate=%s output_rate=%s voice=%s",
                r.direction,
                r.epoch,
                input_rate,
                output_rate,
                voice,
            )
            if r.stop.is_set():
                return
            r.capture = CaptureBuffer(max_bytes=input_rate)
            r.running = True
            self.signals.ready.emit(r.direction, r.epoch, input_rate, output_rate, r.capture)
            self._state(State.RUNNING)
            sender = asyncio.create_task(self._send(ws))
            receiver = asyncio.create_task(self._receive(ws))
            stop_waiter = asyncio.create_task(r.stop.wait())
            tasks = [sender, receiver, stop_waiter]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if r.stop.is_set():
                    sender.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender
                    await ws.send(json.dumps({"type": "session.finish"}))
                    logger.debug("[%s epoch=%s] Session finish sent", r.direction, r.epoch)
                    with contextlib.suppress(asyncio.TimeoutError, ConnectionClosed):
                        await asyncio.wait_for(receiver, timeout=2)
                else:
                    for task in (sender, receiver):
                        if task in done:
                            task.result()
                    raise ServiceError("实时会话已结束", retryable=True)
            finally:
                r.running = False
                r.capture.close()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _send(self, ws) -> None:
        r = self.runtime
        while not r.stop.is_set():
            if r.capture.overflowed:
                raise ServiceError("上传音频积压超过 500 毫秒，正在重新同步", retryable=True)
            frame = r.capture.take()
            if frame:
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(frame).decode("ascii"),
                        }
                    )
                )
            else:
                await asyncio.sleep(0.01)

    async def _receive(self, ws) -> None:
        r = self.runtime
        async for raw in ws:
            event = self._decode(raw)
            if event.get("type") == "session.finished":
                logger.info("[%s epoch=%s] Server session finished", r.direction, r.epoch)
                return
            normalized = normalize(event)
            if isinstance(normalized, bytes):
                if normalized and not r.stop.is_set():
                    self.signals.audio.emit(r.direction, r.epoch, normalized)
            elif isinstance(normalized, RealtimeEvent):
                if normalized.kind == "audio_done":
                    if not r.stop.is_set():
                        self.signals.audio_done.emit(r.direction, r.epoch)
                else:
                    self.signals.event.emit(r.direction, r.epoch, normalized)


class NetworkService:
    def __init__(self):
        self.signals = NetworkSignals()
        self.loop = asyncio.new_event_loop()
        self.runtimes: dict[str, Runtime] = {}
        self._started = threading.Event()
        self._closed = False
        self.thread = threading.Thread(target=self._thread_main, name="si-network", daemon=False)
        self.thread.start()
        if not self._started.wait(3):
            raise RuntimeError("网络工作线程无法启动")

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.set_exception_handler(self._loop_exception)
        logger.debug("Network thread started")
        self._started.set()
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()
            logger.debug("Network thread stopped")

    @staticmethod
    def _loop_exception(loop, context) -> None:
        exception = context.get("exception")
        logger.error(
            "Unhandled asyncio error: %s",
            context.get("message", "unknown"),
            exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
        )

    def start(self, direction: str, generation: int, config: SessionConfig) -> None:
        register_secret(config.api_key)
        if not self._closed:
            asyncio.run_coroutine_threadsafe(self._start(direction, generation, config), self.loop)

    async def _start(self, direction: str, generation: int, config: SessionConfig) -> None:
        await self._stop(direction, State.IDLE)
        runtime = Runtime(direction, generation, config)
        self.runtimes[direction] = runtime
        runtime.task = asyncio.create_task(self._run(runtime))

    async def _run(self, runtime: Runtime) -> None:
        failure = ""
        failures = 0
        attempt = 0
        try:
            while not runtime.stop.is_set():
                attempt += 1
                runtime.epoch = runtime.generation * GENERATION_SIZE + attempt
                self.signals.state.emit(runtime.direction, runtime.epoch, State.CONNECTING, "")
                started = time.monotonic()
                try:
                    await RealtimeClient(runtime, self.signals).run()
                    break
                except (
                    ServiceError,
                    ConnectionClosed,
                    InvalidStatus,
                    OSError,
                    asyncio.TimeoutError,
                ) as exc:
                    if runtime.stop.is_set():
                        break
                    retryable = not isinstance(exc, ServiceError) or exc.retryable
                    if isinstance(exc, InvalidStatus):
                        retryable = exc.response.status_code in (429, 500, 502, 503, 504)
                    if (
                        isinstance(exc, ConnectionClosed)
                        and exc.rcvd
                        and exc.rcvd.code in (1008, 1003)
                    ):
                        retryable = False
                    failure = (
                        str(exc).replace(runtime.config.api_key, "[已隐藏]")[:300] or "连接中断"
                    )
                    if time.monotonic() - started > 30:
                        failures = 0
                    failures += 1
                    if not retryable or failures >= 5:
                        logger.error(
                            "[%s epoch=%s] Session failed attempts=%s retryable=%s: %s",
                            runtime.direction,
                            runtime.epoch,
                            failures,
                            retryable,
                            failure,
                            exc_info=True,
                        )
                        runtime.final_state = State.ERROR
                        break
                    delay = min(2 ** (failures - 1), 15) + random.uniform(0, 0.25)
                    logger.warning(
                        "[%s epoch=%s] Reconnecting attempt=%s delay=%.2fs: %s",
                        runtime.direction,
                        runtime.epoch,
                        failures,
                        delay,
                        failure,
                        exc_info=True,
                    )
                    self.signals.state.emit(
                        runtime.direction,
                        runtime.epoch,
                        State.RECONNECTING,
                        f"{failure} · {delay:.0f} 秒后重连",
                    )
                    try:
                        await asyncio.wait_for(runtime.stop.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception(
                "[%s epoch=%s] Unexpected session failure", runtime.direction, runtime.epoch
            )
            runtime.final_state = State.ERROR
            failure = str(exc).replace(runtime.config.api_key, "[已隐藏]")[:300]
        finally:
            runtime.running = False
            if runtime.capture:
                runtime.capture.close()
            self._notify_finished(runtime, failure)

    def _notify_finished(self, runtime: Runtime, failure: str = "") -> None:
        if not runtime.finished_notified:
            logger.info(
                "[%s epoch=%s] Session ended state=%s",
                runtime.direction,
                runtime.epoch,
                runtime.final_state,
            )
            runtime.finished_notified = True
            self.signals.state.emit(
                runtime.direction,
                runtime.epoch or runtime.generation * GENERATION_SIZE + 1,
                runtime.final_state,
                failure if runtime.final_state == State.ERROR else "",
            )

    def stop(self, direction: str, final_state: str = State.IDLE) -> None:
        if not self._closed:
            asyncio.run_coroutine_threadsafe(self._stop(direction, final_state), self.loop)

    async def _stop(self, direction: str, final_state: str) -> None:
        runtime = self.runtimes.get(direction)
        if runtime is None or runtime.task is None or runtime.task.done():
            return
        runtime.final_state = final_state
        runtime.stop.set()
        if runtime.capture:
            runtime.capture.close()
        if not runtime.running:
            runtime.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.task
        self._notify_finished(runtime)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        async def stop_all():
            await asyncio.gather(
                *(self._stop(direction, State.IDLE) for direction in list(self.runtimes))
            )

        future = asyncio.run_coroutine_threadsafe(stop_all(), self.loop)
        try:
            future.result(timeout=5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("网络线程尚未停止")
