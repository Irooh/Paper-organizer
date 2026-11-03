"""Small, native-widget-friendly light and dark palettes."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_theme(name: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    dark = name == "Dark"
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#171b24" if dark else "#f4f6fa",
        QPalette.ColorRole.WindowText: "#e8edf6" if dark else "#202838",
        QPalette.ColorRole.Base: "#202633" if dark else "#ffffff",
        QPalette.ColorRole.AlternateBase: "#252d3b" if dark else "#f0f3f9",
        QPalette.ColorRole.Text: "#e8edf6" if dark else "#202838",
        QPalette.ColorRole.Button: "#293346" if dark else "#e8edf6",
        QPalette.ColorRole.ButtonText: "#e8edf6" if dark else "#202838",
        QPalette.ColorRole.Highlight: "#456ee8",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: "#85a7ff" if dark else "#315bd6",
        QPalette.ColorRole.ToolTipBase: "#293346" if dark else "#ffffff",
        QPalette.ColorRole.ToolTipText: "#e8edf6" if dark else "#202838",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8290a6"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#8290a6"))
    app.setPalette(palette)
    app.setStyleSheet("""
        QMainWindow { font-size: 10pt; }
        QPushButton { padding: 7px 12px; border-radius: 5px; }
        QLineEdit, QSpinBox, QComboBox { padding: 6px; border-radius: 4px; }
        QTextEdit, QTextBrowser, QTableWidget { border-radius: 5px; }
        QHeaderView::section { padding: 7px; }
        QTabBar::tab { padding: 9px 15px; }
        QSplitter::handle { margin: 3px; }
        QLabel#appTitle { font-size: 19pt; font-weight: 600; }
        QLabel#paperTitle { font-size: 14pt; font-weight: 600; }
        QPushButton#runReviewButton { background: #456ee8; color: white; }
    """)
