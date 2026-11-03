import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from paperdesk.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "library.sqlite3")


def add(db, **changes):
    values = dict(title="A paper", file_path="/papers/file.pdf", sha256="a" * 64,
                  pages=["First page", "Second page"], authors="Jane Researcher", year=2024)
    values.update(changes)
    return db.add_paper(**values)


def test_persistence_notes_metadata_and_history(db):
    paper_id = add(db)
    db.save_notes(paper_id, "<p><b>Important</b> note</p>")
    db.update_metadata(paper_id, "New title", "Author", 2025, "10.1234/example", "In Progress", True)
    first = db.add_review(paper_id, "# First review", "test-model")
    second = db.add_review(paper_id, "# Second review", "test-model")
    reopened = Database(db.path)
    paper = reopened.get_paper(paper_id)
    assert paper["notes_html"] == "<p><b>Important</b> note</p>"
    assert paper["pages"] == ["First page", "Second page"]
    assert paper["title"] == "New title"
    assert paper["checked"] is True
    assert paper["status"] == "Reviewed"
    assert paper["year"] == 2025
    assert [review["id"] for review in reopened.list_reviews(paper_id)] == [second, first]
    assert reopened.list_reviews(paper_id)[0]["model"] == "test-model"


def test_check_does_not_override_status_and_search_is_literal(db):
    paper_id = add(db, title="50% fewer errors_under_test")
    add(db, sha256="b" * 64, title="Other paper")
    db.set_checked(paper_id, True)
    assert db.get_paper(paper_id)["status"] == "Unread"
    assert [paper["id"] for paper in db.list_papers("% fewer")] == [paper_id]
    assert len(db.list_papers("_under")) == 1
    assert db.list_papers("' OR 1=1 --") == []
    summary = db.list_papers()[0]
    assert "pages" not in summary and "notes_html" not in summary


def test_foreign_keys_cascade_and_unique_fingerprints(db):
    paper_id = add(db)
    with pytest.raises(sqlite3.IntegrityError):
        add(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.add_review(999, "Missing paper", "model")
    db.add_review(paper_id, "Review", "model")
    db.remove_paper(paper_id)
    assert db.get_paper(paper_id) is None
    assert db.list_reviews(paper_id) == []


@pytest.mark.parametrize("changes", [{"title": " "}, {"status": "invented"}, {"year": 1}, {"pages": []}])
def test_rejects_invalid_data(db, changes):
    with pytest.raises(ValueError):
        add(db, **changes)


def test_worker_connections_and_missing_rows(db):
    paper_id = add(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: db.save_notes(paper_id, f"<p>{n}</p>"), range(12)))
    assert db.get_paper(paper_id)["notes_html"].startswith("<p>")
    with pytest.raises(KeyError):
        db.save_notes(999, "note")


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "new.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)
