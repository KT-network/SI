from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from si.domain import Direction, RealtimeEvent, Settings


@dataclass
class Segment:
    direction: str
    epoch: int
    source_id: str
    time: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    source: str = ""
    source_final: bool = False
    translations: dict[str, str] = field(default_factory=dict)
    translation_finals: set[str] = field(default_factory=set)
    responses: set[str] = field(default_factory=set)
    speaker: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    incomplete: bool = False
    notice: str = ""

    @property
    def identity(self) -> tuple[str, int, str]:
        return self.direction, self.epoch, self.source_id

    @property
    def translation(self) -> str:
        return "".join(self.translations.values())

    @property
    def translation_final(self) -> bool:
        return bool(self.translations) and self.translation_finals.issuperset(self.translations)


class TranscriptStore:
    def __init__(self, limit: int = 300):
        self.limit = limit
        self.segments: OrderedDict[tuple[str, int, str], Segment] = OrderedDict()
        self.links: dict[tuple[str, int, str], str] = {}
        self._seen: OrderedDict[tuple[str, int, str], None] = OrderedDict()
        self._usage_seen: OrderedDict[tuple[str, int, str], None] = OrderedDict()
        self.input_tokens = 0
        self.output_tokens = 0
        self.revision = 0

    def _segment(self, direction: str, epoch: int, item_id: str) -> Segment:
        key = direction, epoch, item_id
        if key not in self.segments:
            self.segments[key] = Segment(direction, epoch, item_id)
        return self.segments[key]

    def apply(self, direction: str, epoch: int, event: RealtimeEvent) -> None:
        if event.event_id:
            seen_key = direction, epoch, event.event_id
            if seen_key in self._seen:
                return
            self._seen[seen_key] = None
            if len(self._seen) > 2048:
                self._seen.popitem(last=False)
        if event.kind == "response_done":
            response_key = direction, epoch, event.response_id
            if event.response_id and response_key not in self._usage_seen:
                self._usage_seen[response_key] = None
                if len(self._usage_seen) > 2048:
                    self._usage_seen.popitem(last=False)
                usage = event.data.get("usage") or {}
                self.input_tokens += int(usage.get("input_tokens", 0) or 0)
                self.output_tokens += int(usage.get("output_tokens", 0) or 0)
            if event.data.get("status", "completed") != "completed":
                for segment in self.segments.values():
                    if (
                        segment.direction == direction
                        and segment.epoch == epoch
                        and event.response_id in segment.responses
                    ):
                        segment.incomplete = True
            self.revision += 1
            return
        if not event.item_id:
            return
        key = direction, epoch, event.item_id
        if event.kind == "link":
            if not event.previous_item_id or event.previous_item_id == event.item_id:
                return
            self.links[key] = event.previous_item_id
            target = self._segment(direction, epoch, event.previous_item_id)
            orphan = self.segments.pop(key, None)
            if orphan and orphan is not target:
                target.translations.update(orphan.translations)
                target.translation_finals.update(orphan.translation_finals)
                target.responses.update(orphan.responses)
                target.incomplete |= orphan.incomplete
        elif event.kind == "translation":
            source_id = self.links.get(key, event.item_id)
            segment = self._segment(direction, epoch, source_id)
            if event.response_id:
                segment.responses.add(event.response_id)
            if event.final:
                segment.translations[event.item_id] = event.text
                segment.translation_finals.add(event.item_id)
            elif event.item_id not in segment.translation_finals:
                segment.translations[event.item_id] = (
                    segment.translations.get(event.item_id, "") + event.text
                )
        else:
            segment = self._segment(direction, epoch, event.item_id)
            if event.kind == "source":
                if event.final:
                    segment.source = event.text
                    segment.source_final = True
                elif not segment.source_final:
                    segment.source += event.text
            elif event.kind == "speech_start":
                segment.speaker = event.speaker_id
                segment.start_ms = event.timestamp_ms
            elif event.kind == "speech_stop":
                segment.end_ms = event.timestamp_ms
            elif event.kind == "source_failed":
                segment.incomplete = True
                segment.notice = event.text
        self._trim()
        self.revision += 1

    def _trim(self) -> None:
        while len(self.segments) > self.limit:
            old_key, _ = self.segments.popitem(last=False)
            self.links = {
                k: v
                for k, v in self.links.items()
                if k[:2] != old_key[:2] or (k[2] != old_key[2] and v != old_key[2])
            }

    def mark_interrupted(self, direction: str, epoch: int) -> None:
        for segment in self.segments.values():
            if (
                segment.direction == direction
                and segment.epoch == epoch
                and not (segment.source_final and segment.translation_final)
            ):
                segment.incomplete = True
        self.revision += 1

    def rows(self, direction: str | None = None) -> list[Segment]:
        return [
            s
            for s in self.segments.values()
            if (direction is None or s.direction == direction)
            and (s.source or s.translation or s.notice)
        ]

    def clear(self) -> None:
        self.segments.clear()
        self.links.clear()
        self._seen.clear()
        self.revision += 1

    def export(self, path: Path, settings: Settings) -> int:
        lines = []
        count = 0
        for segment in self.rows():
            track = settings.track(segment.direction)
            source = segment.source if track.save_source and segment.source_final else ""
            translation = (
                segment.translation if track.save_translation and segment.translation_final else ""
            )
            if not (source or translation):
                continue
            direction = "我的发言" if segment.direction == Direction.UPLINK else "对方发言"
            suffix = " · 未完整" if segment.incomplete else ""
            lines.append(f"[{segment.time}] {direction}{suffix}")
            if source:
                lines.append(f"原文：{source}")
            if translation:
                lines.append(f"译文：{translation}")
            lines.append("")
            count += 1
        if not count:
            raise ValueError("没有已完成且允许导出的字幕")
        path.write_text("\n".join(lines), encoding="utf-8-sig")
        return count
