import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("SI.Tests")
    app.setOrganizationName("SI.Tests")
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    app.setFont(font)
    yield app
