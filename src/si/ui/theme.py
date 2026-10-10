from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def colors(dark: bool = False) -> dict[str, str]:
    if dark:
        return {
            "bg": "#171b1d",
            "surface": "#202527",
            "text": "#e8eeee",
            "muted": "#9ba9ad",
            "line": "#384144",
            "accent": "#24c7af",
            "hover": "#30383b",
            "input": "#282f32",
            "source": "#afbcc1",
            "danger": "#f17b80",
        }
    return {
        "bg": "#f4f6f7",
        "surface": "#ffffff",
        "text": "#202d32",
        "muted": "#6e7e85",
        "line": "#dce4e7",
        "accent": "#087f83",
        "hover": "#edf4f4",
        "input": "#ffffff",
        "source": "#61757d",
        "danger": "#c43d4d",
    }


def apply_theme(app: QApplication, dark: bool = False) -> None:
    app.setProperty("siDarkTheme", dark)
    c = colors(dark)
    palette = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "bg"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "bg"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "surface"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.Highlight, "accent"),
        (QPalette.ColorRole.HighlightedText, "surface"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
    ):
        palette.setColor(role, QColor(c[key]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["muted"])
    )
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: {c["text"]}; }}
        QMainWindow, QDialog {{ background: {c["bg"]}; }}
        QFrame#sidebar, QFrame#transcript {{ background: {c["surface"]}; border: 1px solid {c["line"]}; border-radius: 6px; }}
        QLabel#brand {{ font-size: 23px; font-weight: 700; }}
        QLabel#sectionTitle {{ font-size: 15px; font-weight: 600; }}
        QLabel#mutedText {{ color: {c["muted"]}; font-size: 12px; }}
        QLabel#badge {{ color: {c["accent"]}; font-size: 12px; }}
        QLabel#state {{ font-size: 12px; }}
        QFrame#divider {{ background: {c["line"]}; border: none; }}
        QComboBox, QLineEdit, QPlainTextEdit {{ background: {c["input"]}; border: 1px solid {c["line"]}; border-radius: 5px; padding: 6px 9px; selection-background-color: {c["accent"]}; min-height: 20px; }}
        QComboBox:disabled, QLineEdit:disabled {{ color: {c["muted"]}; background: {c["bg"]}; }}
        QComboBox:hover, QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {c["accent"]}; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{ image: none; }}
        QComboBox QAbstractItemView {{ background: {c["surface"]}; selection-background-color: {c["hover"]}; selection-color: {c["text"]}; border: 1px solid {c["line"]}; }}
        QPushButton, QToolButton {{ background: {c["surface"]}; border: 1px solid {c["line"]}; border-radius: 5px; padding: 6px 10px; min-height: 20px; }}
        QPushButton:hover, QToolButton:hover {{ background: {c["hover"]}; border-color: {c["accent"]}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c["muted"]}; border-color: {c["line"]}; }}
        QPushButton#primary {{ background: #087f83; border-color: #087f83; color: white; font-weight: 600; }}
        QPushButton#primary:disabled {{ background: {c["line"]}; color: {c["muted"]}; }}
        QToolButton:checked {{ background: {c["hover"]}; border-color: {c["accent"]}; color: {c["accent"]}; }}
        QCheckBox {{ spacing: 6px; background: transparent; }}
        QCheckBox::indicator {{ width: 15px; height: 15px; }}
        QCheckBox::indicator:unchecked {{ border: 1px solid {c["muted"]}; border-radius: 3px; background: {c["input"]}; }}
        QCheckBox::indicator:unchecked:hover {{ border-color: {c["accent"]}; }}
        QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {c["line"]}; }}
        QSlider::sub-page:horizontal {{ background: {c["accent"]}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {c["accent"]}; width: 12px; margin: -4px 0; border-radius: 6px; }}
        QProgressBar {{ border: none; background: {c["line"]}; border-radius: 2px; min-height: 4px; max-height: 4px; }}
        QProgressBar::chunk {{ background: {c["accent"]}; border-radius: 2px; }}
        QTabBar::tab {{ padding: 10px 16px; border-bottom: 2px solid transparent; color: {c["muted"]}; }}
        QTabBar::tab:selected {{ border-bottom-color: {c["accent"]}; color: {c["text"]}; }}
        QTabBar::tab:hover {{ background: {c["hover"]}; }}
        QListView {{ border: none; background: {c["surface"]}; outline: none; }}
        QStatusBar {{ background: {c["surface"]}; border-top: 1px solid {c["line"]}; }}
        QStatusBar::item {{ border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c["line"]}; min-height: 28px; border-radius: 3px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QToolTip {{ background: {c["surface"]}; border: 1px solid {c["line"]}; padding: 5px; }}
    """)
