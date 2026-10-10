"""Build, smoke-test, and archive a native SI desktop application."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def release_version(value: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"v?(\d+\.\d+\.\d+)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", value
    )
    if not match:
        raise ValueError("版本格式应为 v0.1.0 或 v0.1.0-rc.1")
    return value if value.startswith("v") else f"v{value}", match.group(1)


def native_target() -> tuple[str, str]:
    systems = {"win32": "windows", "linux": "ubuntu", "darwin": "macos"}
    architectures = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    try:
        return systems[sys.platform], architectures[platform.machine().lower()]
    except KeyError as exc:
        raise ValueError("仅支持 Windows、Linux、macOS 的 x64 / arm64 原生构建") from exc


def make_icon() -> None:
    if sys.platform not in ("win32", "darwin"):
        return
    from PIL import Image

    suffix = ".icns" if sys.platform == "darwin" else ".ico"
    path = ROOT / "build" / f"si{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / "src" / "si" / "res" / "icon.png") as source:
        image = source.convert("RGBA")
        side = 1024 if sys.platform == "darwin" else 256
        scale = side / max(image.size)
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
        canvas = Image.new("RGBA", (side, side))
        canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        canvas.save(path)


def smoke_test(executable: Path) -> None:
    # A different working directory also verifies that bundled resources can be found.
    with tempfile.TemporaryDirectory(prefix="si-smoke-") as directory:
        scratch = Path(directory)
        screenshot = scratch / "smoke.png"
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        # CI does not have an unlocked desktop keychain or a Secret Service session.
        env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
        env.pop("DASHSCOPE_API_KEY", None)
        env.pop("DASHSCOPE_WORKSPACE_ID", None)
        result = subprocess.run(
            [
                str(executable),
                "--smoke-test",
                "--config-path", str(scratch / "settings.json"),
                "--log-dir", str(scratch / "logs"),
                "--screenshot", str(screenshot),
            ],
            cwd=scratch,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        log_path = scratch / "logs" / "si.log"
        log = log_path.read_text(encoding="utf-8") if log_path.exists() else "未生成应用日志"
        if result.returncode:
            raise RuntimeError(
                f"打包程序启动失败（退出码 {result.returncode}）：\n"
                f"{result.stdout}\n{result.stderr}\n{log}"
            )
        if not screenshot.is_file() or not screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("打包程序未能生成界面截图，启动检查失败")
        if "Application stopped" not in log or " ERROR " in log or " CRITICAL " in log:
            raise RuntimeError(f"打包程序启动或退出异常：\n{log}")
    print("Packaged application smoke test passed.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"])
    args = parser.parse_args()
    tag, bundle_version = release_version(args.version)
    system, arch = native_target()
    make_icon()
    env = os.environ.copy()
    env["SI_BUILD_VERSION"] = bundle_version
    if sys.platform == "win32":
        # Prefer Windows' ICU/UCRT over unrelated copies supplied by tools on PATH.
        system32 = Path(os.environ["SystemRoot"]) / "System32"
        env["PATH"] = str(system32) + os.pathsep + env.get("PATH", "")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "packaging" / "si.spec")],
        cwd=ROOT,
        env=env,
        check=True,
    )

    dist = ROOT / "dist"
    executable = (
        dist / "SI.app" / "Contents" / "MacOS" / "SI" if system == "macos"
        else dist / "SI" / ("SI.exe" if system == "windows" else "SI")
    )
    smoke_test(executable)
    output = dist / "release"
    output.mkdir(parents=True, exist_ok=True)
    name = f"SI-{tag}-{system}-{arch}"
    with tempfile.TemporaryDirectory(prefix="si-package-") as directory:
        stage = Path(directory)
        package = stage / "SI"
        if system == "macos":
            package.mkdir()
            shutil.copytree(dist / "SI.app", package / "SI.app", symlinks=True)
        else:
            shutil.copytree(dist / "SI", package, symlinks=True)
        shutil.copy2(ROOT / "LICENSE", package / "LICENSE")
        shutil.copy2(ROOT / "packaging" / "README.txt", package / "README.txt")
        if system == "windows":
            archive = Path(shutil.make_archive(str(output / name), "zip", stage, "SI"))
        elif system == "macos":
            archive = output / f"{name}.zip"
            subprocess.run(
                ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(package), str(archive)],
                check=True,
            )
        else:
            archive = output / f"{name}.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(package, arcname="SI")
    print(f"Release archive: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
