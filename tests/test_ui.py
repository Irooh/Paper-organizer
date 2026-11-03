"""Exercise real Qt widgets with a local database and a deterministic provider."""

import threading

import pytest
from PySide6.QtCore import QSettings, Qt

from paperdesk.database import Database
from paperdesk import ui


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "QSettings", lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    db = Database(tmp_path / "library.sqlite3")
    for number in (1, 2):
        db.add_paper(title=f"Paper {number}", file_path=str(tmp_path / f"{number}.pdf"),
                     sha256=str(number) * 64, pages=[f"Evidence from paper {number}."])
    widget = ui.MainWindow(db, tmp_path / "papers")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def select(window, paper_id):
    for row in range(window.table.rowCount()):
        if window._paper_id_at(row) == paper_id:
            window.table.selectRow(row)
            assert window._active_id == paper_id
            return
    raise AssertionError(f"No row for paper {paper_id}")


def test_switching_papers_flushes_notes_to_correct_id(window, qtbot):
    select(window, 1)
    window.notes.setHtml("<p><b>Important finding</b></p>")
    select(window, 2)  # Switch before the debounce interval expires.
    window.notes.setPlainText("A different note")
    qtbot.waitUntil(lambda: "A different note" in window.db.get_paper(2)["notes_html"], timeout=3000)
    first = window.db.get_paper(1)["notes_html"]
    assert "Important finding" in first and "font-weight" in first
    assert "A different note" not in first
    select(window, 1)
    assert window.notes.toPlainText() == "Important finding"


def test_focus_out_and_close_flush_notes(window, qtbot):
    select(window, 1)
    window.notes.setFocus()
    qtbot.waitUntil(window.notes.hasFocus)
    window.notes.setPlainText("Saved on focus loss")
    window.search.setFocus()
    qtbot.waitUntil(lambda: "Saved on focus loss" in window.db.get_paper(1)["notes_html"])
    window.notes.setPlainText("Saved on close")
    window.close()
    assert "Saved on close" in window.db.get_paper(1)["notes_html"]


def test_failed_note_save_preserves_buffer_and_selection(window, monkeypatch):
    select(window, 1)
    window.notes.setPlainText("Do not lose this note")
    errors = []
    monkeypatch.setattr(window, "_error", lambda *args: errors.append(args))
    original = window.db.save_notes
    monkeypatch.setattr(window.db, "save_notes", lambda *args: (_ for _ in ()).throw(OSError("Disk full")))
    window.table.selectRow(next(row for row in range(window.table.rowCount()) if window._paper_id_at(row) == 2))
    assert window._active_id == 1
    assert window.notes.toPlainText() == "Do not lose this note"
    assert errors
    monkeypatch.setattr(window.db, "save_notes", original)
    assert window._save_notes()


def test_check_and_search_preserve_metadata_and_notes(window, qtbot):
    select(window, 1)
    row = next(row for row in range(window.table.rowCount()) if window._paper_id_at(row) == 1)
    window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert window.db.get_paper(1)["checked"]
    assert window.db.get_paper(1)["status"] == "Unread"
    window.notes.setPlainText("Survives filtering")
    window.search.setText("Paper 2")
    qtbot.waitUntil(lambda: window.table.rowCount() == 1)
    assert "Survives filtering" in window.db.get_paper(1)["notes_html"]
    assert window._active_id == 2


def test_review_completion_uses_captured_paper_id(window, qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def review(self, *, pages, title, progress, cancelled):
            assert "paper 1" in pages[0]
            entered.set()
            assert release.wait(5)
            progress("Fake synthesis complete")
            return "## Core Contributions & Novelty\nEvidence [P1-C1]"

        def close(self):
            closed.set()

    monkeypatch.setattr(ui, "StormEngine", FakeEngine)
    select(window, 1)
    window.api_key.setText("test-session-key")
    window._run_review()
    try:
        qtbot.waitUntil(entered.is_set)
        select(window, 2)
        window.notes.setPlainText("Working while the review runs")
    finally:
        release.set()
        qtbot.waitUntil(lambda: window._thread is None, timeout=6000)
    assert len(window.db.list_reviews(1)) == 1
    assert window.db.list_reviews(2) == []
    assert window._active_id == 2
    assert window.review.toPlainText() == ""
    assert "Working while" in window.db.get_paper(2)["notes_html"]
    assert closed.is_set()
    assert "test-session-key" not in str([window.settings.value(k) for k in window.settings.allKeys()])


def test_cancelled_worker_saves_no_review(window, qtbot):
    def operation(progress, cancelled):
        from paperdesk.storm import ReviewCancelled
        while not cancelled():
            threading.Event().wait(0.01)
        raise ReviewCancelled("Cancelled")

    window._start_job(operation, window._review_completed)
    window._cancel_job()
    qtbot.waitUntil(lambda: window._thread is None)
    assert "cancelled" in window.task_status.text().lower()
    assert window.db.list_reviews(1) == []


def test_pdf_import_worker(window, qtbot, tmp_path, monkeypatch):
    from test_pdf import pdf_bytes
    source = tmp_path / "import.pdf"
    source.write_bytes(pdf_bytes())
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileNames", lambda *args: ([str(source)], ""))
    window._import_pdfs()
    qtbot.waitUntil(lambda: window._thread is None, timeout=6000)
    assert len(window.db.list_papers()) == 3
    paper = window.db.get_paper(window._active_id)
    assert paper["title"] == "Measured progress"
    assert "A research paper" in window.reader.toPlainText()
    assert (tmp_path / "papers" / f"{paper['sha256']}.pdf").is_file()


def test_empty_library_clears_notes_and_disables_review(qtbot, tmp_path):
    widget = ui.MainWindow(Database(tmp_path / "empty.sqlite3"), tmp_path / "papers")
    qtbot.addWidget(widget)
    assert widget.table.rowCount() == 0
    assert not widget.notes.isEnabled()
    assert not widget.run_button.isEnabled()


def test_theme_switch_keeps_content(window):
    window.notes.setPlainText("Theme independent note")
    window.theme_combo.setCurrentText("Light")
    assert window.notes.toPlainText() == "Theme independent note"
    assert window.settings.value("theme") == "Light"


def test_close_when_worker_finishes_during_confirmation(window, qtbot, monkeypatch):
    release = threading.Event()

    def operation(progress, cancelled):
        assert release.wait(5)
        return None

    window._start_job(operation, lambda result: None)

    def question(*args):
        # A real message box runs a nested event loop: completion can arrive here.
        release.set()
        qtbot.waitUntil(lambda: window._thread is None, timeout=6000)
        return ui.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(ui.QMessageBox, "question", question)
    window.close()
    assert not window.isVisible()
    assert not window._close_pending


def test_close_waits_for_cancellation_without_destroying_thread(window, qtbot, monkeypatch):
    def operation(progress, cancelled):
        while not cancelled():
            threading.Event().wait(0.01)
        return None

    monkeypatch.setattr(ui.QMessageBox, "question", lambda *args: ui.QMessageBox.StandardButton.Yes)
    window._start_job(operation, lambda result: None)
    window.close()
    qtbot.waitUntil(lambda: window._thread is None)
    qtbot.waitUntil(lambda: not window.isVisible())
