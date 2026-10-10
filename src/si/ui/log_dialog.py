from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QStyle,
    QVBoxLayout,
)

from si.ui.widgets import ChoiceCombo, tool_button

LOG_HEADER = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s"
)
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def filter_log(text: str, minimum: int) -> str:
    lines = []
    include = minimum == 0
    for line in text.splitlines():
        header = LOG_HEADER.match(line)
        if header:
            include = LEVELS[header.group(1)] >= minimum
        if include:
            lines.append(line)
    return "\n".join(lines)


class LogDialog(QDialog):
    def __init__(self, directory: Path, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.setWindowTitle("运行日志")
        self.resize(960, 580)
        self.setMinimumSize(700, 420)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.level = ChoiceCombo()
        self.level.addItem("全部级别", 0)
        self.level.addItem("警告及以上", 30)
        self.level.addItem("错误", 40)
        self.file = ChoiceCombo()
        self.file.addItem("当前日志", "si.log")
        for index in range(1, 6):
            if (directory / f"si.log.{index}").exists():
                self.file.addItem(f"历史日志 {index}", f"si.log.{index}")
        self.live = QCheckBox("实时刷新")
        self.live.setChecked(True)
        refresh = tool_button(self, QStyle.StandardPixmap.SP_BrowserReload, "刷新日志")
        self.copy_button = tool_button(
            self, QStyle.StandardPixmap.SP_FileDialogContentsView, "复制当前日志"
        )
        folder = tool_button(self, QStyle.StandardPixmap.SP_DirOpenIcon, "打开日志文件夹")
        toolbar.addWidget(self.file)
        toolbar.addWidget(self.level)
        toolbar.addStretch()
        toolbar.addWidget(self.live)
        for button in (refresh, self.copy_button, folder):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.path_label = QLabel()
        self.path_label.setObjectName("mutedText")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            self.path_label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.path_label)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text, 1)
        self.status = QLabel()
        self.status.setObjectName("mutedText")
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.file.currentIndexChanged.connect(self.refresh)
        self.level.currentIndexChanged.connect(self.refresh)
        self.live.toggled.connect(self.update_timer)
        refresh.clicked.connect(self.refresh)
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.text.toPlainText())
        )
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))))
        self.refresh()

    def refresh(self) -> None:
        path = self.directory / self.file.currentData()
        self.path_label.setText(str(path))
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                start = max(0, size - 256 * 1024)
                stream.seek(start)
                raw = stream.read().decode("utf-8", errors="replace")
                if start:
                    raw = raw.partition("\n")[2]
        except FileNotFoundError:
            raw = ""
            self.status.setText("日志文件尚未创建")
        except OSError as exc:
            self.status.setText(f"日志读取失败：{exc}")
            return
        else:
            self.status.setText("最近 256 KiB" if start else f"{size:,} 字节")
        text = filter_log(raw, self.level.currentData())
        if text != self.text.toPlainText():
            scroll = self.text.verticalScrollBar()
            follow = scroll.value() == scroll.maximum()
            position = scroll.value()
            self.text.setPlainText(text)
            scroll.setValue(scroll.maximum() if follow else position)

    def update_timer(self) -> None:
        if self.isVisible() and self.live.isChecked():
            self.timer.start()
        else:
            self.timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self.update_timer()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)
