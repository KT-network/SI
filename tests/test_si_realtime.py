import asyncio
import base64
import json

import pytest
from websockets.asyncio.server import serve

from si.domain import SessionConfig
from si.protocol import ServiceError
from si.realtime import NetworkService, NetworkSignals, RealtimeClient, Runtime


def test_two_real_websocket_connections_handshake_stream_and_finish(qapp):
    async def scenario():
        received = []
        signals = NetworkSignals()
        runtimes = {}
        audio = {}
        texts = []
        ready = []
        signals.ready.connect(
            lambda d, epoch, rate_in, rate_out, buffer: (
                ready.append((d, rate_in, rate_out)),
                buffer.push(b"\x01\x00" if d == "uplink" else b"\x02\x00"),
            )
        )
        signals.audio.connect(lambda d, epoch, pcm: audio.setdefault(d, []).append(pcm))
        signals.event.connect(lambda d, epoch, event: texts.append((d, event)))
        signals.audio_done.connect(lambda d, epoch: runtimes[d].stop.set())

        async def handler(ws):
            await ws.send(json.dumps({"type": "session.created", "session": {}}))
            config = json.loads(await ws.recv())
            assert config["type"] == "session.update"
            assert config["session"]["output_modalities"] == ["text", "audio"]
            language = config["session"]["translation"]["language"]
            voice = config["session"]["audio"]["output"]["voice"]
            assert voice == ("Ethan" if language == "en" else "Theo Calm")
            assert "voice" not in config["session"]
            await ws.send(json.dumps({"type": "session.updated", "session": config["session"]}))
            frame = json.loads(await ws.recv())
            assert frame["type"] == "input_audio_buffer.append"
            pcm = base64.b64decode(frame["audio"])
            received.append((language, pcm))
            for event in (
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "item_id": "s",
                    "delta": language,
                },
                {"type": "response.audio_transcript.delta", "item_id": "t", "delta": "translated"},
                {"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()},
                {"type": "response.audio.done"},
            ):
                await ws.send(json.dumps(event))
            finish = json.loads(await ws.recv())
            assert finish["type"] == "session.finish"
            await ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "s",
                        "transcript": "final",
                    }
                )
            )
            await ws.send(json.dumps({"type": "session.finished"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            for direction, language, epoch in (("uplink", "en", 1), ("downlink", "zh", 2)):
                runtime = Runtime(
                    direction,
                    1,
                    SessionConfig(
                        "beijing",
                        "test-workspace",
                        "test-only",
                        language,
                        voice="Ethan" if direction == "uplink" else "Theo Calm",
                    ),
                    epoch=epoch,
                )
                runtimes[direction] = runtime
            await asyncio.wait_for(
                asyncio.gather(
                    *(RealtimeClient(r, signals, url=url).run() for r in runtimes.values())
                ),
                5,
            )
        assert sorted(received) == [("en", b"\x01\x00"), ("zh", b"\x02\x00")]
        assert audio == {"uplink": [b"\x01\x00"], "downlink": [b"\x02\x00"]}
        assert len(ready) == 2
        assert all(r.capture.closed for r in runtimes.values())
        assert len([e for _, e in texts if e.kind == "source" and e.final]) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("confirmed", [None, "Tina"])
def test_unconfirmed_voice_never_starts_recording(qapp, confirmed):
    async def scenario():
        signals = NetworkSignals()
        ready = []
        signals.ready.connect(lambda *args: ready.append(args))

        async def handler(ws):
            await ws.send(json.dumps({"type": "session.created"}))
            config = json.loads(await ws.recv())
            assert config["session"]["audio"]["output"]["voice"] == "Serena"
            await ws.send(
                json.dumps(
                    {
                        "type": "session.updated",
                        "session": {"audio": {"output": {"voice": confirmed}}},
                    }
                )
            )
            await ws.wait_closed()

        runtime = Runtime(
            "uplink", 1, SessionConfig("beijing", "test", "test-only", voice="Serena")
        )
        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            with pytest.raises(ServiceError, match="音色") as failure:
                await asyncio.wait_for(RealtimeClient(runtime, signals, url=url).run(), 3)
        assert not failure.value.retryable
        assert ready == [] and runtime.capture is None

    asyncio.run(scenario())


def test_configuration_rejection_never_starts_capture(qapp):
    async def scenario():
        signals = NetworkSignals()
        ready = []
        signals.ready.connect(lambda *args: ready.append(args))

        async def handler(ws):
            await ws.send(json.dumps({"type": "session.created"}))
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "type": "error",
                        "error": {"code": "invalid_parameter", "message": "wrong config"},
                    }
                )
            )
            await ws.wait_closed()

        runtime = Runtime("uplink", 1, SessionConfig("beijing", "test", "test-only"))
        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            with pytest.raises(ServiceError, match="invalid_parameter"):
                await asyncio.wait_for(RealtimeClient(runtime, signals, url=url).run(), 3)
        assert ready == [] and runtime.capture is None

    asyncio.run(scenario())


def test_invalid_wire_message_fails_cleanly():
    with pytest.raises(ServiceError):
        RealtimeClient._decode("not-json")
    with pytest.raises(ServiceError):
        RealtimeClient._decode("[]")


def test_stop_before_job_begins_still_notifies_idle(qapp):
    async def scenario():
        service = NetworkService.__new__(NetworkService)
        service.signals = NetworkSignals()
        service.runtimes = {}
        states = []
        service.signals.state.connect(lambda direction, epoch, state, message: states.append(state))
        await service._start("uplink", 1, SessionConfig("beijing", "test", "test-only"))
        await service._stop("uplink", "idle")
        assert states == ["idle"]
        assert service.runtimes["uplink"].task.done()

    asyncio.run(scenario())


def test_network_thread_redacts_terminal_error_and_stops(qapp, monkeypatch):
    import time

    from PySide6.QtTest import QTest

    async def reject(client):
        raise ServiceError("invalid credential test-only-secret")

    monkeypatch.setattr(RealtimeClient, "run", reject)
    service = NetworkService()
    states = []
    service.signals.state.connect(
        lambda direction, epoch, state, message: states.append((state, message))
    )
    try:
        service.start("uplink", 1, SessionConfig("beijing", "test", "test-only-secret"))
        deadline = time.monotonic() + 2
        while not any(state == "error" for state, _ in states) and time.monotonic() < deadline:
            QTest.qWait(10)
        assert states[-1][0] == "error"
        assert "test-only-secret" not in states[-1][1]
        assert len([state for state, _ in states if state == "connecting"]) == 1
    finally:
        service.close()
    assert not service.thread.is_alive()


def test_network_failure_is_logged_without_credentials(qapp, tmp_path, monkeypatch):
    import time

    from PySide6.QtTest import QTest

    from si.diagnostics import configure_logging

    async def reject(client):
        raise ServiceError("invalid credential network-log-secret")

    monkeypatch.setattr(RealtimeClient, "run", reject)
    logs = configure_logging(tmp_path)
    service = NetworkService()
    states = []
    service.signals.state.connect(lambda d, epoch, state, message: states.append(state))
    try:
        service.start("uplink", 1, SessionConfig("beijing", "test", "network-log-secret"))
        deadline = time.monotonic() + 2
        while "error" not in states and time.monotonic() < deadline:
            QTest.qWait(10)
        assert "error" in states
    finally:
        service.close()
        logs.close()
    text = logs.path.read_text(encoding="utf-8")
    assert "uplink" in text and "epoch=1000001" in text
    assert "Session failed" in text and "ServiceError" in text
    assert "network-log-secret" not in text
