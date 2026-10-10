from __future__ import annotations

import math

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from si.audio import Device
from si.domain import (
    ACTIVE_STATES,
    LANGUAGES,
    VOICES,
    AudioMetrics,
    Direction,
    State,
    TrackSettings,
)
from si.ui.icons import standard_icon

STATE_LABELS = {
    State.IDLE: "未启动",
    State.CONNECTING: "连接中",
    State.CONFIGURING: "配置会话",
    State.RUNNING: "翻译中",
    State.RECONNECTING: "重新连接",
    State.STOPPING: "正在停止",
    State.PAUSED: "已暂停",
    State.ERROR: "发生错误",
    State.TESTING: "设备测试中",
}


def tool_button(
    parent: QWidget, icon: QStyle.StandardPixmap, tooltip: str, *, checkable: bool = False
) -> QToolButton:
    button = QToolButton(parent)
    button.setProperty("standardPixmap", icon.value)
    button.setIcon(standard_icon(button, icon))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCheckable(checkable)
    button.setFixedSize(34, 32)
    return button


class ChoiceCombo(QComboBox):
    def paintEvent(self, event) -> None:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = QFontMetrics(self.font()).elidedText(
            self.currentText(), Qt.TextElideMode.ElideRight, max(10, self.width() - 43)
        )
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        icon = standard_icon(self, QStyle.StandardPixmap.SP_ArrowDown)
        icon.paint(painter, QRect(self.width() - 22, (self.height() - 10) // 2, 10, 10))


class DeviceCombo(ChoiceCombo):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(80)
        self.setMaxVisibleItems(12)
        self.currentIndexChanged.connect(lambda: self.setToolTip(self.currentText()))

    def populate(self, devices: list[Device], selected: str) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem("未选择", "")
        for device in devices:
            self.addItem(device.name, device.id)
            self.setItemData(self.count() - 1, device.name, Qt.ItemDataRole.ToolTipRole)
        index = self.findData(selected)
        if selected and index < 0:
            self.addItem("设备已断开", selected)
            index = self.count() - 1
        self.setCurrentIndex(max(0, index))
        self.blockSignals(False)
        self.setToolTip(self.currentText())


class TrackPanel(QWidget):
    start_requested = Signal(str)
    stop_requested = Signal(str)
    pause_requested = Signal(str)
    probe_requested = Signal(str)
    options_changed = Signal(str)
    output_changed = Signal(str, bool, int)

    def __init__(self, direction: str, settings: TrackSettings, parent=None):
        super().__init__(parent)
        self.direction = direction
        self.settings = settings
        self.state = State.IDLE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        heading = QHBoxLayout()
        title = QLabel("我的发言" if direction == Direction.UPLINK else "对方发言")
        title.setObjectName("sectionTitle")
        badge = QLabel("上行" if direction == Direction.UPLINK else "下行")
        badge.setObjectName("badge")
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(badge)
        layout.addLayout(heading)
        for attribute, label in (
            ("input_combo", "真实麦克风" if direction == Direction.UPLINK else "会议音频 · 采集端"),
            (
                "output_combo",
                "会议麦克风 · 写入端" if direction == Direction.UPLINK else "真实耳机 / 扬声器",
            ),
        ):
            row = QHBoxLayout()
            field_label = QLabel(label)
            field_label.setObjectName("mutedText")
            field_label.setFixedWidth(142)
            combo = DeviceCombo()
            setattr(self, attribute, combo)
            row.addWidget(field_label)
            row.addWidget(combo, 1)
            layout.addLayout(row)
        language_row = QHBoxLayout()
        language_row.setSpacing(6)
        language_label = QLabel("译为")
        language_label.setObjectName("mutedText")
        language_label.setFixedWidth(28)
        self.language = ChoiceCombo()
        self.language.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.language.setMinimumWidth(80)
        self.language.setMaximumWidth(100)
        self.language.setAccessibleName("翻译目标语言")
        for code, name in LANGUAGES:
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.target_language)))
        language_row.addWidget(language_label)
        language_row.addWidget(self.language, 1)
        voice_label = QLabel("音色")
        voice_label.setObjectName("mutedText")
        voice_label.setFixedWidth(28)
        self.voice = ChoiceCombo()
        self.voice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.voice.setMinimumWidth(140)
        self.voice.setMaxVisibleItems(8)
        self.voice.setAccessibleName("译音音色")
        for code, name, description in VOICES:
            self.voice.addItem(f"{name} {code}", code)
            self.voice.setItemData(self.voice.count() - 1, description, Qt.ItemDataRole.ToolTipRole)
        self.voice.setCurrentIndex(max(0, self.voice.findData(settings.voice)))
        self._update_voice_tooltip()
        self.voice.currentIndexChanged.connect(self._update_voice_tooltip)
        language_row.addWidget(voice_label)
        language_row.addWidget(self.voice, 2)
        layout.addLayout(language_row)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setAccessibleName("输入电平")
        if direction == Direction.DOWNLINK:
            badge.setStyleSheet("color: #19896b;")
            self.meter.setStyleSheet("QProgressBar::chunk { background: #22a274; }")
        layout.addWidget(self.meter)
        controls = QHBoxLayout()
        controls.setSpacing(7)
        self.start_button = tool_button(self, QStyle.StandardPixmap.SP_MediaPlay, "开始此方向")
        self.pause_button = tool_button(self, QStyle.StandardPixmap.SP_MediaPause, "暂停翻译")
        self.stop_button = tool_button(self, QStyle.StandardPixmap.SP_MediaStop, "停止此方向")
        self.mute_button = tool_button(
            self, QStyle.StandardPixmap.SP_MediaVolume, "译音静音", checkable=True
        )
        self.mute_button.setChecked(settings.muted)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(settings.volume)
        self.volume.setAccessibleName("译音音量")
        self.volume.setToolTip(f"译音音量 {settings.volume}%")
        self.probe_button = tool_button(
            self, QStyle.StandardPixmap.SP_MediaSkipForward, "测试输入电平与输出音"
        )
        for button in (self.start_button, self.pause_button, self.stop_button, self.mute_button):
            controls.addWidget(button)
        controls.addWidget(self.volume, 1)
        controls.addWidget(self.probe_button)
        layout.addLayout(controls)
        options = QHBoxLayout()
        self.source_check = QCheckBox("原文")
        self.translation_check = QCheckBox("译文")
        self.source_check.setChecked(settings.show_source)
        self.translation_check.setChecked(settings.show_translation)
        options.addWidget(self.source_check)
        options.addWidget(self.translation_check)
        options.addStretch()
        self.level_label = QLabel("— dB")
        self.level_label.setObjectName("mutedText")
        options.addWidget(self.level_label)
        layout.addLayout(options)
        self.status = QLabel(STATE_LABELS[State.IDLE])
        self.status.setObjectName("state")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(20)
        self.status.setMaximumHeight(38)
        layout.addWidget(self.status)
        self.start_button.clicked.connect(lambda: self.start_requested.emit(direction))
        self.pause_button.clicked.connect(lambda: self.pause_requested.emit(direction))
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(direction))
        self.probe_button.clicked.connect(lambda: self.probe_requested.emit(direction))
        for control in (self.input_combo, self.output_combo, self.language, self.voice):
            control.currentIndexChanged.connect(self._options)
        self.source_check.toggled.connect(self._options)
        self.translation_check.toggled.connect(self._options)
        self.mute_button.toggled.connect(self._output)
        self.volume.valueChanged.connect(self._output)
        self.set_state(State.IDLE)
        self._update_mute_icon()

    def _options(self) -> None:
        self.settings.input_device = self.input_combo.currentData() or ""
        self.settings.output_device = self.output_combo.currentData() or ""
        self.settings.target_language = self.language.currentData()
        self.settings.voice = self.voice.currentData()
        self.settings.show_source = self.source_check.isChecked()
        self.settings.show_translation = self.translation_check.isChecked()
        self.options_changed.emit(self.direction)

    def _update_voice_tooltip(self) -> None:
        description = self.voice.currentData(Qt.ItemDataRole.ToolTipRole)
        self.voice.setToolTip(f"{self.voice.currentText()} · {description}")

    def _output(self) -> None:
        self.settings.muted = self.mute_button.isChecked()
        self.settings.volume = self.volume.value()
        self.volume.setToolTip(f"译音音量 {self.settings.volume}%")
        self._update_mute_icon()
        self.output_changed.emit(self.direction, self.settings.muted, self.settings.volume)

    def _update_mute_icon(self) -> None:
        icon = (
            QStyle.StandardPixmap.SP_MediaVolumeMuted
            if self.mute_button.isChecked()
            else QStyle.StandardPixmap.SP_MediaVolume
        )
        self.mute_button.setProperty("standardPixmap", icon.value)
        self.mute_button.setIcon(standard_icon(self.mute_button, icon))

    def set_state(self, state: str, message: str = "") -> None:
        self.state = State(state)
        busy = self.state in ACTIVE_STATES or self.state in (State.STOPPING, State.TESTING)
        for control in (
            self.input_combo,
            self.output_combo,
            self.language,
            self.voice,
            self.probe_button,
        ):
            control.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.start_button.setToolTip("恢复翻译" if state == State.PAUSED else "开始此方向")
        self.pause_button.setEnabled(state == State.RUNNING)
        self.stop_button.setEnabled(busy or state == State.PAUSED)
        text = STATE_LABELS[self.state]
        if message:
            text += " · " + message
        self.status.setText(text[:130])
        self.status.setToolTip(text)
        self.status.setStyleSheet("color: #c43d4d;" if state == State.ERROR else "")
        if not busy:
            self.meter.setValue(0)
            self.level_label.setText("— dB")

    def set_metrics(self, metrics: AudioMetrics) -> None:
        db = 20 * math.log10(max(metrics.level, 1e-5))
        self.meter.setValue(max(0, min(100, int((db + 60) / 60 * 100))))
        self.level_label.setText(f"{db:.0f} dB" if db > -99 else "— dB")
        self.meter.setToolTip(f"播放缓存 {metrics.backlog_ms} ms · 欠载 {metrics.underruns}")

    def populate(self, inputs: list[Device], outputs: list[Device]) -> None:
        self.input_combo.populate(inputs, self.settings.input_device)
        self.output_combo.populate(outputs, self.settings.output_device)
