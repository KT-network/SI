from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from si.domain import Settings
from si.storage import credential_store_name
from si.ui.widgets import ChoiceCombo, tool_button


class PhraseTable(QWidget):
    def __init__(self, phrases: dict[str, str], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["原词", "译法"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)
        for source, target in phrases.items():
            self.add(source, target)
        actions = QHBoxLayout()
        add = QPushButton("新增术语")
        remove = tool_button(self, QStyle.StandardPixmap.SP_TrashIcon, "删除选中术语")
        actions.addWidget(add)
        actions.addStretch()
        actions.addWidget(remove)
        layout.addLayout(actions)
        add.clicked.connect(lambda: self.add())
        remove.clicked.connect(
            lambda: (
                self.table.removeRow(self.table.currentRow())
                if self.table.currentRow() >= 0
                else None
            )
        )

    def add(self, source: str = "", target: str = "") -> None:
        if self.table.rowCount() >= 128:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(source))
        self.table.setItem(row, 1, QTableWidgetItem(target))
        self.table.setCurrentCell(row, 0)

    def phrases(self) -> dict[str, str]:
        phrases = {}
        for row in range(self.table.rowCount()):
            source = self.table.item(row, 0).text().strip()
            target = self.table.item(row, 1).text().strip()
            if not source and not target:
                continue
            if not source or not target:
                raise ValueError("术语的原词和译法都需要填写")
            if len(source) > 200 or len(target) > 200:
                raise ValueError("单项术语不能超过 200 个字符")
            if source in phrases:
                raise ValueError(f"原词重复：{source}")
            phrases[source] = target
        return phrases


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        api_key: str,
        remember: bool,
        parent=None,
        *,
        records_only: bool = False,
        key_saved: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(650, 490)
        self.settings = settings
        self.initial_key = api_key.strip()
        self.key_saved = key_saved
        self.result_settings = settings
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        connection = QWidget()
        form = QFormLayout(connection)
        form.setContentsMargins(22, 22, 22, 22)
        form.setSpacing(15)
        self.region = ChoiceCombo()
        self.region.addItem("北京", "beijing")
        self.region.addItem("新加坡", "singapore")
        self.region.setCurrentIndex(max(0, self.region.findData(settings.region)))
        self.workspace = QLineEdit(settings.workspace_id)
        self.workspace.setMaxLength(120)
        self.key = QLineEdit(api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(512)
        show_key = QCheckBox("显示密钥")
        show_key.toggled.connect(
            lambda checked: self.key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        self.remember = QCheckBox(f"在 {credential_store_name()} 中保存密钥")
        self.remember.setChecked(remember)
        self.key_status = QLabel()
        self.key_status.setObjectName("mutedText")
        self.theme = ChoiceCombo()
        self.theme.addItem("浅色", "light")
        self.theme.addItem("深色", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        form.addRow("服务地域", self.region)
        form.addRow("业务空间 ID", self.workspace)
        form.addRow("API Key", self.key)
        form.addRow("", show_key)
        form.addRow("", self.remember)
        form.addRow("", self.key_status)
        form.addRow("主题", self.theme)
        model = QLabel("qwen3.8-livetranslate-flash-realtime")
        model.setObjectName("mutedText")
        form.addRow("模型", model)
        for signal in (
            self.key.textChanged,
            self.workspace.textChanged,
            self.region.currentIndexChanged,
            self.remember.toggled,
        ):
            signal.connect(self.update_key_status)
        self.update_key_status()
        tabs.addTab(connection, "连接与外观")
        records = QWidget()
        records_layout = QFormLayout(records)
        records_layout.setContentsMargins(22, 22, 22, 22)
        records_layout.setSpacing(20)
        self.export_checks = {}
        for direction, title in (("uplink", "我的发言"), ("downlink", "对方发言")):
            options = QWidget()
            row = QHBoxLayout(options)
            row.setContentsMargins(0, 0, 0, 0)
            original, translated = QCheckBox("导出原文"), QCheckBox("导出译文")
            original.setChecked(settings.track(direction).save_source)
            translated.setChecked(settings.track(direction).save_translation)
            row.addWidget(original)
            row.addWidget(translated)
            row.addStretch()
            self.export_checks[direction] = original, translated
            records_layout.addRow(title, options)
        tabs.addTab(records, "字幕导出")
        phrases = QTabWidget()
        self.up_phrases = PhraseTable(settings.uplink.phrases)
        self.down_phrases = PhraseTable(settings.downlink.phrases)
        phrases.addTab(self.up_phrases, "我的发言")
        phrases.addTab(self.down_phrases, "对方发言")
        tabs.addTab(phrases, "术语表")
        if records_only:
            tabs.setTabEnabled(0, False)
            tabs.setTabEnabled(2, False)
            tabs.setCurrentIndex(1)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_key_status(self) -> None:
        if not self.key.text().strip():
            text = "未设置密钥"
        elif not self.remember.isChecked():
            text = "密钥仅用于本次运行"
        elif (
            self.key_saved
            and self.key.text().strip() == self.initial_key
            and self.workspace.text().strip() == self.settings.workspace_id
            and self.region.currentData() == self.settings.region
        ):
            text = f"已保存到 {credential_store_name()}"
        else:
            text = "密钥尚未保存"
        self.key_status.setText(text)

    def accept(self) -> None:
        workspace = self.workspace.text().strip()
        if workspace and not all(c.isascii() and (c.isalnum() or c == "-") for c in workspace):
            QMessageBox.warning(self, "设置无效", "业务空间 ID 只能包含字母、数字和连字符")
            return
        try:
            up = replace(self.settings.uplink, phrases=self.up_phrases.phrases())
            down = replace(self.settings.downlink, phrases=self.down_phrases.phrases())
        except ValueError as exc:
            QMessageBox.warning(self, "术语无效", str(exc))
            return
        for direction, track in (("uplink", up), ("downlink", down)):
            source, translation = self.export_checks[direction]
            track.save_source, track.save_translation = source.isChecked(), translation.isChecked()
        self.result_settings = Settings(
            self.region.currentData(),
            workspace,
            self.theme.currentData(),
            up,
            down,
            remember_api_key=self.remember.isChecked(),
        )
        super().accept()
