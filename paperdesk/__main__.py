"""Application entry point, including PyInstaller's windowed mode."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import sys


def main() -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from paperdesk.database import Database
    from paperdesk.paths import data_dir, database_path, papers_dir
    from paperdesk.ui import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("PaperDesk")
    app.setOrganizationName("PaperDesk")
    app.setStyle("Fusion")
    try:
        root = data_dir()
        root.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(root / "paperdesk.log", maxBytes=1_000_000, backupCount=2)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger("paperdesk").addHandler(handler)
        logging.getLogger("paperdesk").setLevel(logging.INFO)
        # One writer UI per library protects stale note buffers across app instances.
        lock = QLockFile(str(root / "paperdesk.lock"))
        lock.setStaleLockTime(30_000)
        if not lock.tryLock(100):
            QMessageBox.information(None, "PaperDesk is already open", "Another PaperDesk window is using this library. Close it before starting another instance.")
            return 1
        window = MainWindow(Database(database_path()), papers_dir())
    except Exception as exc:
        logging.getLogger("paperdesk").exception("Startup failed")
        QMessageBox.critical(None, "PaperDesk could not start", str(exc))
        return 1
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
