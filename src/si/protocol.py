from __future__ import annotations

import base64
from typing import Any

from si.domain import RealtimeEvent


class ServiceError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def service_error(event: dict[str, Any]) -> ServiceError:
    error = event.get("error", {})
    code = str(error.get("code", "service_error"))
    message = str(error.get("message", "服务端拒绝了请求"))[:300]
    retryable = any(
        word in code.lower()
        for word in ("rate_limit", "overload", "timeout", "server_error", "temporarily")
    )
    return ServiceError(f"{code}: {message}", retryable=retryable)


def negotiated_rates(session: dict[str, Any]) -> tuple[int, int]:
    audio = session.get("audio", {})
    rates = []
    for kind, default in (("input", 16000), ("output", 24000)):
        fmt = audio.get(kind, {}).get("format", {})
        if fmt.get("type", "pcm") != "pcm":
            raise ServiceError("服务端返回了不支持的音频格式")
        rate = fmt.get("sample_rate", default)
        if not isinstance(rate, int) or not 8000 <= rate <= 96000:
            raise ServiceError("服务端音频采样率无效")
        rates.append(rate)
    return rates[0], rates[1]


def negotiated_voice(session: dict[str, Any], requested: str) -> str:
    audio = session.get("audio")
    output = audio.get("output") if isinstance(audio, dict) else None
    voice = output.get("voice") if isinstance(output, dict) else None
    if not isinstance(voice, str) or not voice:
        raise ServiceError(f"服务端未确认音色 {requested}，请重新连接或查看日志")
    if voice != requested:
        raise ServiceError(f"服务端未采用音色 {requested}（实际为 {voice[:80]}），请更换音色后重试")
    return voice


def normalize(event: dict[str, Any]) -> RealtimeEvent | bytes | None:
    kind = event.get("type", "")
    common = {
        "item_id": str(event.get("item_id", "")),
        "event_id": str(event.get("event_id", "")),
        "response_id": str(event.get("response_id", "")),
    }
    if kind == "error":
        raise service_error(event)
    if kind == "response.audio.delta":
        try:
            return base64.b64decode(event.get("delta", ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise ServiceError("服务端返回了无效的音频数据") from exc
    if kind == "response.audio.done":
        return RealtimeEvent("audio_done", **common)
    if kind in (
        "conversation.item.input_audio_transcription.delta",
        "conversation.item.input_audio_transcription.completed",
    ):
        final = kind.endswith("completed")
        return RealtimeEvent(
            "source",
            text=str(event.get("transcript" if final else "delta", "")),
            final=final,
            **common,
        )
    if kind in (
        "response.audio_transcript.delta",
        "response.text.delta",
        "response.audio_transcript.done",
        "response.text.done",
    ):
        final = kind.endswith("done")
        text = event.get("transcript", event.get("text", "")) if final else event.get("delta", "")
        return RealtimeEvent("translation", text=str(text), final=final, **common)
    if kind in ("conversation.item.created", "response.output_item.added"):
        item = event.get("item", {})
        if item.get("role") not in (None, "assistant"):
            return None
        return RealtimeEvent(
            "link",
            item_id=str(item.get("id", event.get("item_id", ""))),
            previous_item_id=str(event.get("previous_item_id", item.get("previous_item_id", ""))),
            event_id=common["event_id"],
        )
    if kind == "input_audio_buffer.speech_started":
        return RealtimeEvent(
            "speech_start",
            speaker_id=str(event.get("speaker_id", "")),
            timestamp_ms=event.get("audio_start_ms"),
            **common,
        )
    if kind == "input_audio_buffer.speech_stopped":
        return RealtimeEvent("speech_stop", timestamp_ms=event.get("audio_end_ms"), **common)
    if kind == "conversation.item.input_audio_transcription.failed":
        return RealtimeEvent("source_failed", text="原文识别失败", **common)
    if kind == "response.done":
        response = event.get("response", {})
        if response.get("status") == "failed":
            details = response.get("status_details") or {}
            raise service_error({"error": details.get("error", details)})
        return RealtimeEvent(
            "response_done",
            response_id=str(response.get("id", "")),
            event_id=common["event_id"],
            data=response,
        )
    return None
