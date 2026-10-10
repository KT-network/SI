import base64

import pytest

from si.domain import VOICE_IDS, SessionConfig
from si.protocol import ServiceError, negotiated_rates, negotiated_voice, normalize


def test_38_configuration_and_workspace_endpoint():
    config = SessionConfig("beijing", "workspace-123", "test-key", "en", {"同传": "interpretation"})
    assert (
        config.url
        == "wss://workspace-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-livetranslate-flash-realtime"
    )
    session = config.update_event()["session"]
    assert session["output_modalities"] == ["text", "audio"]
    assert session["audio"]["output"]["voice"] == "Tina"
    assert "voice" not in session
    assert session["translation"]["corpus"]["phrases"] == {"同传": "interpretation"}
    assert "modalities" not in session
    assert "input_audio_transcription" not in session
    assert "test-key" not in repr(config)


@pytest.mark.parametrize("voice", sorted(VOICE_IDS))
def test_voice_uses_the_nested_38_audio_configuration(voice):
    config = SessionConfig("beijing", "workspace", "test-only", voice=voice)
    session = config.update_event()["session"]
    assert session["audio"] == {"output": {"voice": voice}}
    assert negotiated_voice(session, voice) == voice


def test_unknown_voice_is_rejected_before_connecting():
    config = SessionConfig("beijing", "workspace", "test-only", voice="unsupported")
    with pytest.raises(ValueError, match="音色"):
        _ = config.url
    with pytest.raises(ValueError, match="音色"):
        config.update_event()


@pytest.mark.parametrize("workspace", ["", "a/b", "a?token=secret", "中文", "space id"])
def test_workspace_cannot_inject_a_host_or_query(workspace):
    with pytest.raises(ValueError):
        SessionConfig("beijing", workspace, "key").url


def test_events_use_delta_then_full_final_text():
    source = normalize(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": "s",
            "delta": "你好",
        }
    )
    assert source.kind == "source" and source.text == "你好" and not source.final
    final = normalize(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "s",
            "transcript": "你好。",
        }
    )
    assert final.final and final.text == "你好。"
    translated = normalize({"type": "response.audio_transcript.delta", "delta": "Hello"})
    assert translated.kind == "translation" and translated.text == "Hello"
    assert (
        normalize({"type": "response.audio_transcript.text", "text": "old", "stash": "old"}) is None
    )


def test_audio_is_validated_and_format_is_negotiated():
    assert (
        normalize({"type": "response.audio.delta", "delta": base64.b64encode(b"\x00\x00").decode()})
        == b"\x00\x00"
    )
    with pytest.raises(ServiceError):
        normalize({"type": "response.audio.delta", "delta": "bad base64!"})
    assert negotiated_rates(
        {
            "audio": {
                "input": {"format": {"sample_rate": 16000}},
                "output": {"format": {"sample_rate": 24000}},
            }
        }
    ) == (16000, 24000)
    with pytest.raises(ServiceError):
        negotiated_rates({"audio": {"input": {"format": {"type": "opus"}}}})


def test_fatal_and_retryable_server_failures_are_distinct():
    with pytest.raises(ServiceError) as fatal:
        normalize({"type": "error", "error": {"code": "invalid_api_key", "message": "invalid"}})
    assert not fatal.value.retryable
    with pytest.raises(ServiceError) as retry:
        normalize(
            {"type": "error", "error": {"code": "rate_limit_exceeded", "message": "slow down"}}
        )
    assert retry.value.retryable


def test_speaker_and_translation_link_are_normalized():
    speaker = normalize(
        {
            "type": "input_audio_buffer.speech_started",
            "item_id": "source",
            "speaker_id": 0,
            "audio_start_ms": 123,
        }
    )
    assert speaker.speaker_id == "0" and speaker.timestamp_ms == 123
    link = normalize(
        {
            "type": "conversation.item.created",
            "item": {"id": "translated", "role": "assistant"},
            "previous_item_id": "source",
        }
    )
    assert link.item_id == "translated" and link.previous_item_id == "source"


def test_user_item_does_not_link_consecutive_source_segments():
    assert (
        normalize(
            {
                "type": "conversation.item.created",
                "item": {"id": "source-2", "role": "user"},
                "previous_item_id": "source-1",
            }
        )
        is None
    )
