"""Check voice configuration acknowledgements without recording or uploading audio."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace

from PySide6.QtWidgets import QApplication
from websockets.asyncio.client import connect

from si.diagnostics import redact, register_secret
from si.domain import VOICE_IDS, SessionConfig
from si.protocol import negotiated_voice, service_error
from si.storage import ConfigStore, CredentialStore


async def check(voices: list[str]) -> None:
    settings = ConfigStore().load()
    key = CredentialStore().load(settings)
    register_secret(key)
    config = SessionConfig(settings.region, settings.workspace_id, key)
    for index, voice in enumerate(voices):
        if index:
            await asyncio.sleep(6.5)
        async with connect(
            config.url,
            additional_headers={"Authorization": f"Bearer {key}"},
            open_timeout=10,
            close_timeout=1,
        ) as ws:
            await asyncio.wait_for(ws.recv(), 12)
            event = replace(config, voice=voice).update_event()
            await ws.send(json.dumps(event))
            while True:
                response = json.loads(await asyncio.wait_for(ws.recv(), 12))
                if response.get("type") == "error":
                    raise service_error(response)
                if response.get("type") == "session.updated":
                    confirmed = negotiated_voice(response.get("session") or {}, voice)
                    print(voice, "confirmed:", confirmed)
                    break
            if response.get("type") == "session.updated":
                await ws.send(json.dumps({"type": "session.finish"}))
                while True:
                    response = json.loads(await asyncio.wait_for(ws.recv(), 12))
                    if response.get("type") in ("session.finished", "error"):
                        break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("voices", nargs="+", choices=sorted(VOICE_IDS), help="Voice IDs to check")
    args = parser.parse_args()
    app = QApplication([])
    app.setApplicationName("SI")
    app.setOrganizationName("SI")
    try:
        asyncio.run(check(args.voices))
    except Exception as exc:
        raise SystemExit(redact(str(exc))) from None
