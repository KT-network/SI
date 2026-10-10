from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QKeySequence,
    QPalette,
    QShortcut,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QListView,
    QMenu,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from si.domain import Direction, Settings
from si.transcripts import TranscriptStore
from si.ui.theme import colors


@dataclass(frozen=True)
class TranscriptRow:
    identity: tuple[str, int, str]
    header: str
    source: str
    translation: str
    direction: str
    notice: str = ""


class TranscriptModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[TranscriptRow] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole):
            return self.rows[index.row()]
        return None

    def update(self, rows: list[TranscriptRow]) -> None:
        old_ids = [r.identity for r in self.rows]
        new_ids = [r.identity for r in rows]
        if new_ids[: len(old_ids)] == old_ids:
            old_count = len(self.rows)
            if len(rows) > old_count:
                self.beginInsertRows(QModelIndex(), old_count, len(rows) - 1)
                self.rows.extend(rows[old_count:])
                self.endInsertRows()
            for i in range(old_count):
                if self.rows[i] != rows[i]:
                    self.rows[i] = rows[i]
                    self.dataChanged.emit(self.index(i), self.index(i))
        elif rows != self.rows:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()


class TranscriptDelegate(QStyledItemDelegate):
    def __init__(self, view: QListView):
        super().__init__(view)
        self.view = view
        self.dark = False

    @staticmethod
    def document(text: str, width: float, size: int, bold: bool = False) -> QTextDocument:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        doc.setDefaultFont(font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        doc.setPlainText(text)
        doc.setTextWidth(max(40, width))
        return doc

    def _documents(self, row: TranscriptRow, width: float):
        return [
            (self.document(text, width, size, bold), role)
            for text, size, bold, role in (
                (row.source, 13, False, "source"),
                (row.translation, 16, True, "text"),
                (row.notice, 12, False, "danger"),
            )
            if text
        ]

    def sizeHint(self, option, index) -> QSize:
        row = index.data(Qt.ItemDataRole.UserRole)
        width = self.view.viewport().width() - 48
        docs = self._documents(row, width)
        return QSize(100, int(47 + sum(doc.size().height() + 7 for doc, _ in docs)))

    def paint(self, painter, option, index) -> None:
        row = index.data(Qt.ItemDataRole.UserRole)
        c = colors(self.dark)
        painter.save()
        painter.setClipRect(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(c["hover"]))
        x, y = option.rect.left() + 24, option.rect.top() + 14
        width = option.rect.width() - 48
        painter.fillRect(
            QRectF(option.rect.left() + 10, y + 2, 3, 12),
            QColor("#129b9f" if row.direction == Direction.UPLINK else "#22a274"),
        )
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(c["muted"]))
        painter.drawText(QRectF(x, y, width, 18), Qt.AlignmentFlag.AlignLeft, row.header)
        y += 26
        for doc, role in self._documents(row, width):
            painter.save()
            painter.translate(x, y)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(QPalette.ColorRole.Text, QColor(c[role]))
            doc.documentLayout().draw(painter, context)
            painter.restore()
            y += doc.size().height() + 7
        painter.setPen(QColor(c["line"]))
        painter.drawLine(
            option.rect.left() + 24,
            option.rect.bottom(),
            option.rect.right() - 24,
            option.rect.bottom(),
        )
        painter.restore()


class SubtitleList(QListView):
    user_scrolled = Signal()

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() > 0:
            self.user_scrolled.emit()
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.doItemsLayout()


class TranscriptView(QWidget):
    follow_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.follow = True
        self.direction: str | None = None
        self.model = TranscriptModel(self)
        self.list = SubtitleList()
        self.list.setModel(self.model)
        self.delegate = TranscriptDelegate(self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.user_scrolled.connect(lambda: self.set_follow(False))
        self.list.verticalScrollBar().sliderPressed.connect(lambda: self.set_follow(False))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context_menu)
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.list)
        self.copy_shortcut.activated.connect(self.copy_selected)
        self.empty = QLabel("等待开始翻译")
        self.empty.setObjectName("mutedText")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QStackedLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.empty)
        layout.addWidget(self.list)
        self.stack = layout

    def set_follow(self, follow: bool) -> None:
        self.follow = follow
        self.follow_changed.emit(follow)
        if follow:
            self.list.scrollToBottom()

    def refresh(self, store: TranscriptStore, settings: Settings) -> None:
        rows = []
        for segment in store.rows(self.direction):
            track = settings.track(segment.direction)
            source = segment.source if track.show_source else ""
            translation = segment.translation if track.show_translation else ""
            if not (source or translation or segment.notice):
                continue
            state = (
                "未完整"
                if segment.incomplete
                else "已完成"
                if segment.source_final and segment.translation_final
                else "翻译中"
            )
            direction = "我的发言" if segment.direction == Direction.UPLINK else "对方发言"
            speaker = f" · 说话人 {segment.speaker}" if segment.speaker else ""
            rows.append(
                TranscriptRow(
                    segment.identity,
                    f"{segment.time}   {direction}{speaker}   ·   {state}",
                    source,
                    translation,
                    segment.direction,
                    segment.notice,
                )
            )
        self.model.update(rows)
        self.stack.setCurrentIndex(1 if rows else 0)
        all_hidden = all(
            not settings.track(d).show_source and not settings.track(d).show_translation
            for d in ([self.direction] if self.direction else list(Direction))
        )
        self.empty.setText("字幕已隐藏" if all_hidden else "等待开始翻译")
        if self.follow and rows:
            self.list.scrollToBottom()

    def copy_selected(self) -> None:
        index = self.list.currentIndex()
        if index.isValid():
            row = self.model.rows[index.row()]
            QApplication.clipboard().setText(
                "\n".join(s for s in (row.header, row.source, row.translation, row.notice) if s)
            )

    def context_menu(self, position) -> None:
        index = self.list.indexAt(position)
        if not index.isValid():
            return
        self.list.setCurrentIndex(index)
        menu = QMenu(self)
        action = menu.addAction("复制")
        action.triggered.connect(self.copy_selected)
        menu.exec(self.list.viewport().mapToGlobal(position))
