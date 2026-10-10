import json

import pytest

from si.domain import Settings
from si.storage import ConfigStore, CredentialStore


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "Windows 凭据管理器"), ("darwin", "macOS 钥匙串"), ("linux", "系统密钥环")],
)
def test_credential_store_name_matches_platform(monkeypatch, platform, expected):
    from si import storage

    monkeypatch.setattr(storage.sys, "platform", platform)
    assert storage.credential_store_name() == expected


def test_configuration_round_trip_never_persists_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID", raising=False)
    store = ConfigStore(tmp_path / "settings.json")
    settings = Settings(workspace_id="workspace", theme="dark")
    settings.uplink.input_device = "stable-id"
    settings.uplink.phrases = {"千问": "Qwen"}
    settings.downlink.save_translation = True
    settings.uplink.voice = "Ethan"
    settings.downlink.voice = "Theo Calm"
    settings.remember_api_key = False
    store.save(settings)
    assert store.load() == settings
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert "api_key" not in raw
    assert list(tmp_path.glob("*.tmp")) == []


def test_invalid_configuration_recovers_to_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID", raising=False)
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    store = ConfigStore(path)
    assert store.load() == Settings()
    assert store.warning


def test_external_config_values_are_checked(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "region": "bad",
                "uplink": {"volume": 999, "show_source": "false", "phrases": {"wrong": 12}},
            }
        ),
        encoding="utf-8",
    )
    settings = ConfigStore(path).load()
    assert settings.region == "beijing" and settings.uplink.volume == 100
    assert settings.uplink.show_source and settings.uplink.phrases == {}


@pytest.mark.parametrize("voice", [None, "unsupported", "", 42, [], {}])
def test_missing_or_invalid_voices_use_default_without_losing_other_settings(tmp_path, voice):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"uplink": {"voice": voice, "target_language": "ja"}}),
        encoding="utf-8",
    )
    settings = ConfigStore(path).load()
    assert settings.uplink.voice == settings.downlink.voice == "Tina"
    assert settings.uplink.target_language == "ja"


def test_saved_key_survives_reload_and_takes_precedence_over_environment(tmp_path, monkeypatch):
    from si import storage

    vault = {}
    monkeypatch.setattr(
        storage.keyring,
        "set_password",
        lambda service, account, key: vault.update({(service, account): key}),
    )
    monkeypatch.setattr(
        storage.keyring, "get_password", lambda service, account: vault.get((service, account))
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-environment-key")
    settings = Settings(workspace_id="workspace")
    credentials = CredentialStore()
    credentials.save(settings, "new-saved-key")
    config = ConfigStore(tmp_path / "settings.json")
    config.save(settings)
    assert CredentialStore().load(config.load()) == "new-saved-key"
    assert "new-saved-key" not in config.path.read_text(encoding="utf-8")


def test_key_write_must_be_verified_and_memory_only_mode_skips_vault(monkeypatch):
    from si import storage

    monkeypatch.setattr(storage.keyring, "set_password", lambda *args: None)
    monkeypatch.setattr(storage.keyring, "get_password", lambda *args: None)
    with pytest.raises(RuntimeError, match="校验失败"):
        CredentialStore().save(Settings(), "new-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-key")
    monkeypatch.setattr(
        storage.keyring, "get_password", lambda *args: pytest.fail("Vault must not be read")
    )
    settings = Settings(remember_api_key=False)
    assert CredentialStore().load(settings) == "environment-key"
