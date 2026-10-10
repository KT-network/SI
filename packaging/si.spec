# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
version = os.environ.get(
    "SI_BUILD_VERSION", tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
)
icon_suffix = ".icns" if sys.platform == "darwin" else ".ico"
icon = root / "build" / f"si{icon_suffix}"

analysis = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "src" / "si" / "res" / "icon.png"), "si/res")]
    + collect_data_files("qtawesome")
    + collect_data_files("certifi"),
    hiddenimports=["PySide6.QtSvg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "packaging" / "runtime_hook.py")],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform not in ("win32", "darwin") or os.environ.get("SI_BUILD_CONSOLE") == "1",
    icon=str(icon) if sys.platform in ("win32", "darwin") else None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="SI",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="SI.app",
        icon=str(icon),
        bundle_identifier="org.ktnetwork.si",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "15.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "同传 SI 需要采集麦克风或虚拟音频设备以进行实时翻译。",
        },
    )
