from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import keyring
from PySide6.QtCore import QStandardPaths

from si.diagnostics import register_secret
from si.domain import LANGUAGES, REGIONS, VOICE_IDS, Settings

logger = logging.getLogger(__name__)


def credential_store_name() -> str:
    if sys.platform == "win32":
        return "Windows 凭据管理器"
    if sys.platform == "darwin":
        return "macOS 钥匙串"
    return "系统密钥环"


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = (
            path
            or Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
            )
            / "settings.json"
        )
        self.warning = ""

    def load(self) -> Settings:
        settings = Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("配置格式无效")
            settings.region = raw.get("region") if raw.get("region") in REGIONS else "beijing"
            workspace = raw.get("workspace_id", "")
            settings.workspace_id = workspace if isinstance(workspace, str) else ""
            settings.theme = "dark" if raw.get("theme") == "dark" else "light"
            if isinstance(raw.get("remember_api_key"), bool):
                settings.remember_api_key = raw["remember_api_key"]
            for name in ("uplink", "downlink"):
                values = raw.get(name, {})
                if not isinstance(values, dict):
                    continue
                track = getattr(settings, name)
                for key in ("input_device", "output_device"):
                    if isinstance(values.get(key), str):
                        setattr(track, key, values[key])
                if values.get("target_language") in dict(LANGUAGES):
                    track.target_language = values["target_language"]
                voice = values.get("voice")
                if isinstance(voice, str) and voice in VOICE_IDS:
                    track.voice = voice
                for key in (
                    "show_source",
                    "show_translation",
                    "save_source",
                    "save_translation",
                    "muted",
                ):
                    if isinstance(values.get(key), bool):
                        setattr(track, key, values[key])
                if isinstance(values.get("volume"), int):
                    track.volume = max(0, min(100, values["volume"]))
                phrases = values.get("phrases", {})
                if isinstance(phrases, dict) and all(
                    isinstance(k, str) and isinstance(v, str) for k, v in phrases.items()
                ):
                    track.phrases = dict(list(phrases.items())[:128])
        except FileNotFoundError:
            logger.info("Configuration not found; using defaults")
        except (OSError, ValueError, TypeError):
            logger.exception("Configuration read failed; using defaults")
            self.warning = "配置读取失败，已使用默认设置"
        settings.workspace_id = settings.workspace_id or os.getenv("DASHSCOPE_WORKSPACE_ID", "")
        logger.debug(
            "Configuration loaded path=%s region=%s theme=%s",
            self.path,
            settings.region,
            settings.theme,
        )
        return settings

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(payload)
            os.replace(temporary, self.path)
            logger.debug("Configuration saved path=%s", self.path)
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()


class CredentialStore:
    SERVICE = "SI.QwenRealtime"

    @staticmethod
    def account(settings: Settings) -> str:
        return f"{settings.region}:{settings.workspace_id}"

    def load(self, settings: Settings) -> str:
        saved = self.read_saved(settings) if settings.remember_api_key else ""
        api_key = saved or os.getenv("DASHSCOPE_API_KEY", "")
        register_secret(api_key)
        logger.info(
            "Credential loaded source=%s",
            "keyring" if saved else "environment" if api_key else "none",
        )
        return api_key

    def read_saved(self, settings: Settings) -> str:
        api_key = keyring.get_password(self.SERVICE, self.account(settings)) or ""
        register_secret(api_key)
        return api_key

    def save(self, settings: Settings, api_key: str) -> None:
        register_secret(api_key)
        keyring.set_password(self.SERVICE, self.account(settings), api_key)
        if self.read_saved(settings) != api_key:
            raise RuntimeError(f"密钥写入后校验失败，未能从 {credential_store_name()} 读回")
        logger.info("Credential saved to system keyring and verified")

    def delete(self, settings: Settings) -> None:
        try:
            keyring.delete_password(self.SERVICE, self.account(settings))
            logger.info("Credential removed from system keyring")
        except keyring.errors.PasswordDeleteError:
            pass
