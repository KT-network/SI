from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices,QIcon
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from si.audio import Device, list_devices
from si.controller import DuplexController
from si.diagnostics import log_directory, register_secret
from si.domain import ACTIVE_STATES, Direction, Settings, State
from si.storage import ConfigStore, CredentialStore
from si.ui.icons import refresh_icons, standard_icon
from si.ui.log_dialog import LogDialog
from si.ui.settings_dialog import SettingsDialog
from si.ui.theme import apply_theme
from si.ui.transcript_view import TranscriptView
from si.ui.widgets import STATE_LABELS, TrackPanel, tool_button

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        store: ConfigStore,
        credentials: CredentialStore,
        api_key: str = "",
        *,
        controller: DuplexController | None = None,
    ):
        super().__init__()
        self.settings = settings
        self.store = store
        self.credentials = credentials
        self.controller = controller or DuplexController(settings, api_key)
        self._dirty = True
        self._closing = False
        self._last_revision = -1
        self._metrics = {}
        self.tray = None
        self._log_dialog = None
        self.setWindowTitle("同传 SI")
        # self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.setWindowIcon(QIcon(str(Path.cwd() / "res" / "icon.png")))
        self.resize(1280, 780)
        self.setMinimumSize(1060, 670)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(16)
        header = QHBoxLayout()
        brand = QLabel("同传 SI")
        brand.setObjectName("brand")
        header.addWidget(brand)
        self.global_state = QLabel("未启动")
        self.global_state.setObjectName("mutedText")
        header.addSpacing(18)
        header.addWidget(self.global_state)
        header.addStretch()
        self.start_all = QPushButton("开始双向")
        self.start_all.setProperty("standardPixmap", QStyle.StandardPixmap.SP_MediaPlay.value)
        self.start_all.setProperty("lightIcon", True)
        self.start_all.setIcon(
            standard_icon(self.start_all, QStyle.StandardPixmap.SP_MediaPlay, light=True)
        )
        self.start_all.setObjectName("primary")
        self.start_all.setFixedHeight(36)
        self.stop_all = tool_button(self, QStyle.StandardPixmap.SP_MediaStop, "全部停止")
        self.emergency = tool_button(
            self, QStyle.StandardPixmap.SP_MediaVolumeMuted, "紧急静音", checkable=True
        )
        self.settings_button = tool_button(
            self, QStyle.StandardPixmap.SP_FileDialogDetailedView, "设置"
        )
        self.logs_button = tool_button(
            self, QStyle.StandardPixmap.SP_FileDialogInfoView, "查看运行日志"
        )
        for button in (
            self.start_all,
            self.stop_all,
            self.emergency,
            self.logs_button,
            self.settings_button,
        ):
            header.addWidget(button)
        layout.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(390)
        sidebar.setMaximumWidth(490)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 16, 18, 16)
        sidebar_layout.setSpacing(13)
        sidebar_heading = QHBoxLayout()
        route_title = QLabel("音频路由")
        route_title.setObjectName("sectionTitle")
        self.refresh_button = tool_button(
            self, QStyle.StandardPixmap.SP_BrowserReload, "刷新音频设备"
        )
        sidebar_heading.addWidget(route_title)
        sidebar_heading.addStretch()
        sidebar_heading.addWidget(self.refresh_button)
        sidebar_layout.addLayout(sidebar_heading)
        self.panels = {}
        for i, direction in enumerate(Direction):
            if i:
                line = QFrame()
                line.setObjectName("divider")
                line.setFixedHeight(1)
                sidebar_layout.addWidget(line)
            panel = TrackPanel(str(direction), settings.track(direction))
            self.panels[str(direction)] = panel
            sidebar_layout.addWidget(panel)
            panel.start_requested.connect(self.start_direction)
            panel.stop_requested.connect(self.controller.stop)
            panel.pause_requested.connect(lambda d: self.controller.stop(d, pause=True))
            panel.probe_requested.connect(self.probe_direction)
            panel.output_changed.connect(self.controller.set_output)
            panel.output_changed.connect(self.schedule_save)
            panel.output_changed.connect(self.sync_emergency)
            panel.options_changed.connect(self.options_changed)
        sidebar_layout.addStretch(1)
        self.connection_label = QLabel("连接未配置")
        self.connection_label.setObjectName("mutedText")
        self.connection_label.setWordWrap(True)
        sidebar_layout.addWidget(self.connection_label)
        splitter.addWidget(sidebar)
        transcript = QFrame()
        transcript.setObjectName("transcript")
        transcript.setMinimumWidth(460)
        transcript_layout = QVBoxLayout(transcript)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        transcript_layout.setSpacing(0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(14, 8, 14, 6)
        self.tabs = QTabBar()
        self.tabs.addTab("双向")
        self.tabs.addTab("我的发言")
        self.tabs.addTab("对方发言")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        toolbar.addWidget(self.tabs)
        toolbar.addStretch()
        self.follow_button = tool_button(
            self, QStyle.StandardPixmap.SP_ArrowDown, "自动跟随字幕", checkable=True
        )
        self.follow_button.setChecked(True)
        self.clear_button = tool_button(self, QStyle.StandardPixmap.SP_TrashIcon, "清空当前字幕")
        self.export_button = tool_button(
            self, QStyle.StandardPixmap.SP_DialogSaveButton, "导出字幕"
        )
        for button in (self.follow_button, self.clear_button, self.export_button):
            toolbar.addWidget(button)
        transcript_layout.addLayout(toolbar)
        self.transcript_view = TranscriptView()
        transcript_layout.addWidget(self.transcript_view, 1)
        splitter.addWidget(transcript)
        splitter.setSizes([420, 820])
        layout.addWidget(splitter, 1)
        self.route_status = QLabel("上行 未启动     下行 未启动")
        self.route_status.setObjectName("mutedText")
        self.queue_status = QLabel("播放缓存 0 ms")
        self.queue_status.setObjectName("mutedText")
        self.usage_status = QLabel("输入 0 / 输出 0 tokens")
        self.usage_status.setObjectName("mutedText")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addWidget(self.route_status, 1)
        self.statusBar().addPermanentWidget(self.queue_status)
        self.statusBar().addPermanentWidget(self.usage_status)
        self.start_all.clicked.connect(self.start_both)
        self.stop_all.clicked.connect(self.stop_both)
        self.settings_button.clicked.connect(self.open_settings)
        self.logs_button.clicked.connect(self.show_logs)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.emergency.toggled.connect(self.emergency_mute)
        self.tabs.currentChanged.connect(self.set_filter)
        self.follow_button.toggled.connect(self.transcript_view.set_follow)
        self.transcript_view.follow_changed.connect(self.follow_button.setChecked)
        self.clear_button.clicked.connect(self.clear_transcripts)
        self.export_button.clicked.connect(self.export_transcripts)
        self.controller.state_changed.connect(self.on_state)
        self.controller.metrics_changed.connect(self.on_metrics)
        self.controller.transcript_changed.connect(self.mark_dirty)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(100)
        self.refresh_timer.timeout.connect(self.refresh_transcripts)
        self.refresh_timer.start()
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.save_settings)
        self.device_manager = QMediaDevices(self)
        self.device_manager.audioInputsChanged.connect(self.refresh_devices)
        self.device_manager.audioOutputsChanged.connect(self.refresh_devices)
        self.refresh_devices()
        self.sync_emergency()
        self.update_toolbar()
        self._setup_tray()
        self.transcript_view.delegate.dark = settings.theme == "dark"
        if store.warning:
            self.statusBar().showMessage(store.warning, 8000)

    @staticmethod
    def _physical(devices: list[Device]) -> str:
        candidates = [
            d
            for d in devices
            if not any(
                word in d.name.lower() for word in ("cable", "virtual", "stereo mix", "立体声混音")
            )
        ]
        return next((d.id for d in candidates if d.default), candidates[0].id if candidates else "")

    @staticmethod
    def _cable(devices: list[Device], letter: str) -> str:
        return next(
            (
                d.id
                for d in devices
                if f"cable{letter}" in d.name.lower().replace("-", "").replace(" ", "")
            ),
            "",
        )

    def refresh_devices(self) -> None:
        inputs, outputs = list_devices(True), list_devices(False)
        logger.info("Audio device list refreshed inputs=%s outputs=%s", len(inputs), len(outputs))
        self.settings.uplink.input_device = self.settings.uplink.input_device or self._physical(
            inputs
        )
        self.settings.uplink.output_device = self.settings.uplink.output_device or self._cable(
            outputs, "a"
        )
        self.settings.downlink.input_device = self.settings.downlink.input_device or self._cable(
            inputs, "b"
        )
        self.settings.downlink.output_device = (
            self.settings.downlink.output_device or self._physical(outputs)
        )
        for panel in self.panels.values():
            panel.populate(inputs, outputs)
        self.schedule_save()

    def start_direction(self, direction: str) -> None:
        try:
            self.controller.start([direction])
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "无法开始翻译", str(exc))

    def start_both(self) -> None:
        directions = [d for d in self.panels if self.controller.states[d] not in ACTIVE_STATES]
        if not directions:
            return
        try:
            self.controller.start(directions)
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "无法开始双向翻译", str(exc))

    def stop_both(self) -> None:
        for direction in self.panels:
            self.controller.stop(direction)

    def probe_direction(self, direction: str) -> None:
        try:
            self.controller.probe(direction)
        except (ValueError, RuntimeError) as exc:
            logger.warning("[%s] Device test rejected: %s", direction, exc)
            QMessageBox.warning(self, "无法测试设备", str(exc))

    def emergency_mute(self, checked: bool) -> None:
        logger.info("Emergency mute=%s", checked)
        for panel in self.panels.values():
            panel.mute_button.setChecked(checked)

    def sync_emergency(self) -> None:
        self.emergency.blockSignals(True)
        self.emergency.setChecked(
            all(panel.mute_button.isChecked() for panel in self.panels.values())
        )
        self.emergency.blockSignals(False)

    def options_changed(self) -> None:
        self._dirty = True
        self.schedule_save()

    def schedule_save(self) -> None:
        if not self._closing:
            self.save_timer.start()

    def save_settings(self) -> None:
        try:
            self.store.save(self.settings)
        except OSError:
            logger.exception("Configuration save failed")
            self.statusBar().showMessage("设置保存失败", 8000)

    def on_state(self, direction: str, state: str, message: str) -> None:
        self.panels[direction].set_state(state, message)
        if state not in ACTIVE_STATES and state != State.TESTING:
            self._metrics.pop(direction, None)
        self.update_toolbar()

    def on_metrics(self, direction: str, metrics) -> None:
        self.panels[direction].set_metrics(metrics)
        self._metrics[direction] = metrics
        self.queue_status.setText(
            f"播放缓存 {max((m.backlog_ms for m in self._metrics.values()), default=0)} ms"
        )

    def update_toolbar(self) -> None:
        states = self.controller.states
        busy = any(
            s in ACTIVE_STATES or s in (State.STOPPING, State.TESTING) for s in states.values()
        )
        self.stop_all.setEnabled(busy or State.PAUSED in states.values())
        self.start_all.setEnabled(
            not all(s in ACTIVE_STATES for s in states.values())
            and not any(s in (State.STOPPING, State.TESTING) for s in states.values())
        )
        self.settings_button.setEnabled(not busy)
        running = [d for d, s in states.items() if s == State.RUNNING]
        if len(running) == 2:
            overall = "双向翻译中"
        elif running:
            overall = "上行翻译中" if running[0] == Direction.UPLINK else "下行翻译中"
        elif State.ERROR in states.values():
            overall = "连接异常"
        elif busy:
            overall = "处理中"
        elif State.PAUSED in states.values():
            overall = "已暂停"
        else:
            overall = "未启动"
        self.global_state.setText(overall)
        self.route_status.setText(
            f"上行 {STATE_LABELS[states['uplink']]}     下行 {STATE_LABELS[states['downlink']]}"
        )
        configured = bool(self.settings.workspace_id and self.controller.api_key)
        self.connection_label.setText(
            f"{'北京' if self.settings.region == 'beijing' else '新加坡'} · Qwen3.8"
            if configured
            else "连接未配置"
        )
        self.queue_status.setText(
            f"播放缓存 {max((m.backlog_ms for m in self._metrics.values()), default=0)} ms"
        )

    def mark_dirty(self) -> None:
        self._dirty = True

    def refresh_transcripts(self) -> None:
        store = self.controller.transcripts
        if self._dirty or self._last_revision != store.revision:
            self.transcript_view.refresh(store, self.settings)
            self.usage_status.setText(
                f"输入 {store.input_tokens:,} / 输出 {store.output_tokens:,} tokens"
            )
            self._last_revision = store.revision
            self._dirty = False

    def set_filter(self, index: int) -> None:
        self.transcript_view.direction = (None, "uplink", "downlink")[index]
        self._dirty = True
        self.refresh_transcripts()

    def clear_transcripts(self) -> None:
        logger.info("Subtitle history cleared")
        self.controller.transcripts.clear()
        self._dirty = True
        self.refresh_transcripts()

    def export_transcripts(self) -> None:
        if not any(
            t.save_source or t.save_translation
            for t in (self.settings.uplink, self.settings.downlink)
        ):
            self.open_settings(records_only=True)
            if not any(
                t.save_source or t.save_translation
                for t in (self.settings.uplink, self.settings.downlink)
            ):
                return
        name = f"同传字幕-{datetime.now():%Y%m%d-%H%M%S}.txt"
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出字幕", str(Path.home() / "Documents" / name), "文本文件 (*.txt)"
        )
        if not filename:
            return
        try:
            count = self.controller.transcripts.export(Path(filename), self.settings)
            logger.info("Subtitles exported segments=%s", count)
            self.statusBar().showMessage(f"已导出 {count} 段字幕", 6000)
        except (OSError, ValueError) as exc:
            logger.exception("Subtitle export failed")
            QMessageBox.warning(self, "导出失败", str(exc))

    def open_settings(self, records_only: bool = False) -> None:
        try:
            saved_key = self.credentials.read_saved(self.settings)
            key_saved = bool(saved_key) and saved_key == self.controller.api_key
        except Exception:
            logger.exception("Credential lookup for settings failed")
            key_saved = False
        dialog = SettingsDialog(
            self.settings,
            self.controller.api_key,
            self.settings.remember_api_key,
            self,
            records_only=records_only,
            key_saved=key_saved,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_settings
        if records_only:
            for direction in self.panels:
                target = self.settings.track(direction)
                selected = updated.track(direction)
                target.save_source = selected.save_source
                target.save_translation = selected.save_translation
            self.save_settings()
            return
        api_key = dialog.key.text().strip()
        register_secret(api_key)
        try:
            if updated.remember_api_key and api_key:
                self.credentials.save(updated, api_key)
            else:
                self.credentials.delete(updated)
            self.store.save(updated)
        except Exception:
            logger.exception("Settings or credential save failed")
            QMessageBox.warning(self, "保存失败", "设置或凭据保存失败，原设置仍然有效")
            return
        self.settings = updated
        self.controller.settings = updated
        self.controller.api_key = api_key
        logger.info(
            "Settings updated region=%s theme=%s credential_configured=%s credential_persisted=%s",
            updated.region,
            updated.theme,
            bool(api_key),
            bool(api_key) and updated.remember_api_key,
        )
        for direction, panel in self.panels.items():
            panel.settings = updated.track(direction)
        apply_theme(QApplication.instance(), updated.theme == "dark")
        refresh_icons(self)
        self.transcript_view.delegate.dark = updated.theme == "dark"
        self._dirty = True
        self.update_toolbar()
        self.refresh_transcripts()
        self.statusBar().showMessage(
            "设置和密钥已保存"
            if api_key and updated.remember_api_key
            else "设置已保存，密钥仅用于本次运行"
            if api_key
            else "设置已保存",
            6000,
        )

    def open_logs(self) -> None:
        try:
            directory = log_directory()
            directory.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                raise OSError("系统无法打开日志文件夹")
        except OSError:
            logger.exception("Cannot open log directory")
            QMessageBox.warning(
                self, "无法打开日志", "日志文件夹无法打开，请查看使用说明中的日志位置"
            )

    def show_logs(self) -> None:
        if self._log_dialog is None:
            self._log_dialog = LogDialog(log_directory(), self)
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("同传 SI")
        menu = QMenu(self)
        menu.addAction("显示窗口", self.show_window)
        menu.addAction("隐藏窗口", self.hide)
        menu.addSeparator()
        menu.addAction("紧急静音", lambda: self.emergency.setChecked(True))
        menu.addAction("全部停止", self.stop_both)
        menu.addAction("查看运行日志", self.show_logs)
        menu.addAction("打开日志文件夹", self.open_logs)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: (
                self.show_window()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )
        )
        self.tray.show()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        logger.info("Window close requested")
        self.refresh_timer.stop()
        self.save_timer.stop()
        self.save_settings()
        try:
            self.controller.close()
        finally:
            if self.tray:
                self.tray.hide()
        event.accept()
