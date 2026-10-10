from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from si.audio import list_devices
from si.controller import DuplexController
from si.diagnostics import configure_logging, register_secret
from si.domain import MODEL
from si.storage import ConfigStore, CredentialStore
from si.ui.main_window import MainWindow
from si.ui.theme import apply_theme

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SI interpretation desktop")
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--log-dir", type=Path, help="Log directory")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("SI")
    app.setOrganizationName("SI")
    warning = ""
    try:
        logs = configure_logging(args.log_dir, args.log_level)
    except OSError:
        logs = configure_logging(Path(tempfile.gettempdir()) / "SI-logs", args.log_level)
        warning = "日志目录不可写，已改用临时目录"
    logs.install_hooks()
    logger.info(
        "Application started pid=%s python=%s model=%s",
        os.getpid(),
        platform.python_version(),
        MODEL,
    )
    logger.info("Log file: %s", logs.path)
    if warning:
        logger.warning(warning)
    try:
        return run_application(app, args, warning)
    except Exception:
        logger.exception("Application failed")
        if not args.smoke_test and not args.list_devices:
            QMessageBox.critical(None, "应用发生错误", f"详情已记录到日志：\n{logs.path}")
        return 1
    finally:
        logger.info("Application stopped")
        logs.close()


def run_application(app: QApplication, args: argparse.Namespace, warning: str = "") -> int:
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    app.setFont(font)
    if args.list_devices:
        from dataclasses import asdict

        inputs, outputs = list_devices(True), list_devices(False)
        logger.info("Device enumeration inputs=%d outputs=%d", len(inputs), len(outputs))
        print(
            json.dumps(
                {
                    "inputs": [asdict(d) for d in inputs],
                    "outputs": [asdict(d) for d in outputs],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    store = ConfigStore(args.config_path)
    settings = store.load()
    credentials = CredentialStore()
    try:
        api_key = credentials.load(settings)
    except Exception:
        logger.exception("Credential lookup failed")
        api_key = ""
        store.warning = "凭据读取失败，可在设置中重新填写密钥"
    register_secret(api_key)
    apply_theme(app, settings.theme == "dark")
    controller = DuplexController(settings, api_key)
    try:
        window = MainWindow(settings, store, credentials, api_key, controller=controller)
    except Exception:
        controller.close()
        raise
    window.show()
    logger.info("Main window shown")
    if warning:
        window.statusBar().showMessage(warning, 10000)

    def finish_smoke():
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        window.close()

    if args.smoke_test:
        QTimer.singleShot(600, finish_smoke)
    try:
        return app.exec()
    finally:
        window.close()
        controller.close()
