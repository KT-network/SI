from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Direction(StrEnum):
    UPLINK = "uplink"
    DOWNLINK = "downlink"


class State(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONFIGURING = "configuring"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    PAUSED = "paused"
    ERROR = "error"
    TESTING = "testing"


ACTIVE_STATES = {State.CONNECTING, State.CONFIGURING, State.RUNNING, State.RECONNECTING}
MODEL = "qwen3.8-livetranslate-flash-realtime"
REGIONS = {"beijing": "cn-beijing", "singapore": "ap-southeast-1"}
LANGUAGES = (
    ("zh", "中文"),
    ("en", "英语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("de", "德语"),
    ("fr", "法语"),
    ("es", "西班牙语"),
    ("it", "意大利语"),
    ("pt", "葡萄牙语"),
    ("ru", "俄语"),
    ("th", "泰语"),
    ("id", "印度尼西亚语"),
    ("ar", "阿拉伯语"),
    ("vi", "越南语"),
    ("tr", "土耳其语"),
    ("fi", "芬兰语"),
    ("pl", "波兰语"),
    ("hi", "印地语"),
    ("nl", "荷兰语"),
    ("cs", "捷克语"),
    ("ur", "乌尔都语"),
    ("tl", "菲律宾语"),
    ("sv", "瑞典语"),
    ("da", "丹麦语"),
    ("he", "希伯来语"),
    ("is", "冰岛语"),
    ("ms", "马来语"),
    ("no", "挪威语"),
    ("fa", "波斯语"),
)
# These presets were acknowledged by the 3.8 API with no audio input.
VOICES = (
    ("Tina", "甜甜", "甜美温暖（默认）"),
    ("Serena", "苏瑶", "温柔自然"),
    ("Ethan", "晨煦", "阳光温暖"),
    ("Maia", "四月", "知性温柔"),
    ("Katerina", "卡捷琳娜", "成熟、韵律丰富"),
    ("Ryan", "甜茶", "节奏鲜明、富有张力"),
    ("Theo Calm", "予安", "平静舒缓"),
)
VOICE_IDS = frozenset(voice for voice, _, _ in VOICES)


@dataclass
class TrackSettings:
    input_device: str = ""
    output_device: str = ""
    target_language: str = "en"
    show_source: bool = True
    show_translation: bool = True
    save_source: bool = False
    save_translation: bool = False
    muted: bool = False
    volume: int = 80
    phrases: dict[str, str] = field(default_factory=dict)
    voice: str = "Tina"


@dataclass
class Settings:
    region: str = "beijing"
    workspace_id: str = ""
    theme: str = "light"
    uplink: TrackSettings = field(default_factory=TrackSettings)
    downlink: TrackSettings = field(default_factory=lambda: TrackSettings(target_language="zh"))
    remember_api_key: bool = True

    def track(self, direction: Direction | str) -> TrackSettings:
        return self.uplink if direction == Direction.UPLINK else self.downlink


@dataclass(frozen=True)
class SessionConfig:
    region: str
    workspace_id: str
    api_key: str = field(repr=False)
    target_language: str = "en"
    phrases: dict[str, str] = field(default_factory=dict)
    voice: str = "Tina"

    @property
    def url(self) -> str:
        if self.region not in REGIONS:
            raise ValueError("不支持的服务地域")
        if not self.workspace_id or not all(
            c.isascii() and (c.isalnum() or c == "-") for c in self.workspace_id
        ):
            raise ValueError("业务空间 ID 只能包含字母、数字和连字符")
        if not self.api_key.strip():
            raise ValueError("尚未设置 API Key")
        if self.target_language not in dict(LANGUAGES):
            raise ValueError("不支持的语音输出语言")
        if self.voice not in VOICE_IDS:
            raise ValueError("不支持的译音音色")
        return f"wss://{self.workspace_id}.{REGIONS[self.region]}.maas.aliyuncs.com/api-ws/v1/realtime?model={MODEL}"

    def update_event(self) -> dict[str, Any]:
        if self.voice not in VOICE_IDS:
            raise ValueError("不支持的译音音色")
        translation: dict[str, Any] = {"language": self.target_language}
        if self.phrases:
            translation["corpus"] = {"phrases": self.phrases}
        return {
            "type": "session.update",
            "session": {
                "output_modalities": ["text", "audio"],
                "audio": {"output": {"voice": self.voice}},
                "translation": translation,
            },
        }


@dataclass(frozen=True)
class RealtimeEvent:
    kind: str
    item_id: str = ""
    text: str = ""
    final: bool = False
    previous_item_id: str = ""
    speaker_id: str = ""
    response_id: str = ""
    event_id: str = ""
    timestamp_ms: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioMetrics:
    level: float = 0.0
    backlog_ms: int = 0
    underruns: int = 0
