from pathlib import Path

import pytest

from si.domain import RealtimeEvent, Settings
from si.transcripts import TranscriptStore


def test_late_link_merges_original_and_translation_without_final_duplication():
    store = TranscriptStore()
    store.apply("uplink", 1, RealtimeEvent("translation", "t", "Hel", response_id="r"))
    store.apply("uplink", 1, RealtimeEvent("source", "s", "你好"))
    store.apply("uplink", 1, RealtimeEvent("link", "t", previous_item_id="s"))
    store.apply("uplink", 1, RealtimeEvent("translation", "t", "lo", response_id="r"))
    store.apply("uplink", 1, RealtimeEvent("translation", "t", "Hello.", final=True))
    store.apply("uplink", 1, RealtimeEvent("source", "s", "你好。", final=True))
    store.apply("uplink", 1, RealtimeEvent("speech_start", "s", speaker_id="0", timestamp_ms=100))
    rows = store.rows()
    assert len(rows) == 1
    assert rows[0].source == "你好。" and rows[0].translation == "Hello."
    assert rows[0].source_final and rows[0].translation_final
    assert rows[0].speaker == "0" and rows[0].start_ms == 100


def test_direction_epoch_and_duplicate_event_isolation():
    store = TranscriptStore()
    event = RealtimeEvent("source", "same", "Hi", event_id="event")
    store.apply("uplink", 1, event)
    store.apply("uplink", 1, event)
    store.apply("downlink", 1, event)
    store.apply("uplink", 2, event)
    assert len(store.rows()) == 3
    assert all(s.source == "Hi" for s in store.rows())


def test_multiple_translation_items_and_usage_deduplication():
    store = TranscriptStore()
    for item, text in (("t1", "Hello "), ("t2", "world")):
        store.apply("uplink", 1, RealtimeEvent("link", item, previous_item_id="s"))
        store.apply("uplink", 1, RealtimeEvent("translation", item, text, final=True))
    assert store.rows()[0].translation == "Hello world"
    usage = RealtimeEvent(
        "response_done", response_id="r", data={"usage": {"input_tokens": 10, "output_tokens": 20}}
    )
    store.apply("uplink", 1, usage)
    store.apply("uplink", 1, usage)
    assert (store.input_tokens, store.output_tokens) == (10, 20)


def test_history_is_bounded_and_interruption_is_visible():
    store = TranscriptStore(limit=2)
    for i in range(3):
        store.apply("uplink", 1, RealtimeEvent("source", str(i), str(i)))
    assert [s.source for s in store.rows()] == ["1", "2"]
    store.mark_interrupted("uplink", 1)
    assert all(s.incomplete for s in store.rows())


def test_export_requires_explicit_permission_and_final_content(tmp_path: Path):
    store = TranscriptStore()
    settings = Settings()
    store.apply("uplink", 1, RealtimeEvent("source", "s", "原文", final=True))
    store.apply("uplink", 1, RealtimeEvent("link", "t", previous_item_id="s"))
    store.apply("uplink", 1, RealtimeEvent("translation", "t", "translation", final=True))
    with pytest.raises(ValueError):
        store.export(tmp_path / "no.txt", settings)
    assert not (tmp_path / "no.txt").exists()
    settings.uplink.save_translation = True
    assert store.export(tmp_path / "yes.txt", settings) == 1
    text = (tmp_path / "yes.txt").read_text(encoding="utf-8-sig")
    assert "translation" in text and "原文" not in text
