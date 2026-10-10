import qtawesome
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QAbstractButton, QApplication, QStyle, QWidget

from si.ui.theme import colors


def standard_icon(widget: QWidget, name: QStyle.StandardPixmap, *, light: bool = False) -> QIcon:
    names = {
        QStyle.StandardPixmap.SP_MediaPlay: "mdi6.play",
        QStyle.StandardPixmap.SP_MediaPause: "mdi6.pause",
        QStyle.StandardPixmap.SP_MediaStop: "mdi6.stop",
        QStyle.StandardPixmap.SP_MediaVolume: "mdi6.volume-high",
        QStyle.StandardPixmap.SP_MediaVolumeMuted: "mdi6.volume-off",
        QStyle.StandardPixmap.SP_MediaSkipForward: "mdi6.headphones",
        QStyle.StandardPixmap.SP_BrowserReload: "mdi6.refresh",
        QStyle.StandardPixmap.SP_FileDialogDetailedView: "mdi6.cog-outline",
        QStyle.StandardPixmap.SP_ArrowDown: "mdi6.chevron-down",
        QStyle.StandardPixmap.SP_TrashIcon: "mdi6.trash-can-outline",
        QStyle.StandardPixmap.SP_DialogSaveButton: "mdi6.download",
        QStyle.StandardPixmap.SP_FileDialogInfoView: "mdi6.file-document-outline",
        QStyle.StandardPixmap.SP_FileDialogContentsView: "mdi6.content-copy",
        QStyle.StandardPixmap.SP_DirOpenIcon: "mdi6.folder-open-outline",
    }
    if name not in names:
        return widget.style().standardIcon(name)
    app = QApplication.instance()
    theme = colors(bool(app.property("siDarkTheme")) if app else False)
    color = QColor("white" if light else theme["text"])
    disabled = QColor(theme["muted"])
    return qtawesome.icon(names[name], color=color, color_disabled=disabled)


def refresh_icons(widget: QWidget) -> None:
    for button in widget.findChildren(QAbstractButton):
        name = button.property("standardPixmap")
        if name is not None:
            button.setIcon(
                standard_icon(
                    button, QStyle.StandardPixmap(name), light=bool(button.property("lightIcon"))
                )
            )
