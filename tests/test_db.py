import os
import time

import pytest
from datetime import datetime, timezone
from docu_tracker.db import Database


def test_initialize_creates_tables(db):
    """All tables should exist after init."""
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [t[0] for t in tables]
    assert "documents" in table_names
    assert "document_paths" in table_names
    assert "topics" in table_names
    assert "document_topics" in table_names
    assert "app_metadata" in table_names


def test_initialize_seeds_default_topics(db):
    """Default topics should be seeded on first init."""
    topics = db.execute("SELECT name FROM topics ORDER BY name").fetchall()
    topic_names = [t[0] for t in topics]
    assert topic_names == [
        "Academic", "Finance", "Other", "Personal", "Work"
    ]


def test_initialize_is_idempotent(db):
    """Running initialize twice should not duplicate topics."""
    db.initialize()  # second call
    topics = db.execute("SELECT name FROM topics").fetchall()
    assert len(topics) == 5


def test_database_creates_parent_directory(tmp_path):
    """Database should create parent directories if they don't exist."""
    db_path = tmp_path / "subdir" / "test.db"
    database = Database(str(db_path))
    database.initialize()
    assert os.path.exists(str(db_path))


def test_scan_path_last_scanned_metadata(db):
    db.set_scan_path_last_scanned_at("/tmp/downloads", "2026-01-01T00:00:00+00:00")

    assert db.get_scan_path_last_scanned_at("/tmp/downloads") == "2026-01-01T00:00:00+00:00"

    db.set_scan_path_last_scanned_at("/tmp/downloads", "2026-01-02T00:00:00+00:00")

    assert db.get_scan_path_last_scanned_at("/tmp/downloads") == "2026-01-02T00:00:00+00:00"


def test_add_document(db):
    """Should insert a document and its path."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="abc123",
        file_path="/home/user/Downloads/paper.pdf",
        title="Test Paper",
        authors="Alice, Bob",
        summary="A test summary.",
        topics=["Work", "Academic"],
        file_modified_at=now,
    )
    assert doc_id == 1

    doc = db.get_document(doc_id)
    assert doc["title"] == "Test Paper"
    assert doc["authors"] == "Alice, Bob"
    assert doc["status"] == "unread"
    assert len(doc["paths"]) == 1
    assert doc["paths"][0] == "/home/user/Downloads/paper.pdf"
    assert set(doc["topics"]) == {"Work", "Academic"}


def test_add_duplicate_path(db):
    """Adding same hash with different path should add path, not new doc."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id1 = db.add_document(
        file_hash="abc123",
        file_path="/home/user/Downloads/paper.pdf",
        title="Test Paper",
        authors="Alice",
        summary="Summary.",
        topics=["Other"],
        file_modified_at=now,
    )
    doc_id2 = db.add_duplicate_path("abc123", "/home/user/Documents/paper.pdf")
    assert doc_id2 == doc_id1

    doc = db.get_document(doc_id1)
    assert len(doc["paths"]) == 2


def test_remove_document_path_keeps_at_least_one_path(db):
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="abc123",
        file_path="/home/user/Downloads/paper.pdf",
        title="Test Paper",
        authors="Alice",
        summary="Summary.",
        topics=["Other"],
        file_modified_at=now,
    )
    db.add_duplicate_path("abc123", "/home/user/Documents/paper.pdf")

    assert db.remove_document_path(doc_id, "/home/user/Documents/paper.pdf") == 1
    assert db.get_document(doc_id)["paths"] == ["/home/user/Downloads/paper.pdf"]
    with pytest.raises(ValueError, match="only tracked path"):
        db.remove_document_path(doc_id, "/home/user/Downloads/paper.pdf")


def test_clear_document_duplicate_paths(db):
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="abc123",
        file_path="/home/user/Downloads/paper.pdf",
        title="Test Paper",
        authors="Alice",
        summary="Summary.",
        topics=["Other"],
        file_modified_at=now,
    )
    db.add_duplicate_path("abc123", "/home/user/Documents/paper.pdf")
    db.add_duplicate_path("abc123", "/home/user/Archive/paper.pdf")

    assert db.clear_document_duplicate_paths(doc_id) == 2
    assert db.get_document(doc_id)["paths"] == ["/home/user/Downloads/paper.pdf"]
    assert db.clear_document_duplicate_paths(doc_id) == 0


def test_clear_all_duplicate_paths(db):
    now = datetime.now(timezone.utc).isoformat()
    first_id = db.add_document(
        file_hash="abc123",
        file_path="/home/user/Downloads/paper.pdf",
        title="Test Paper",
        authors="Alice",
        summary="Summary.",
        topics=["Other"],
        file_modified_at=now,
    )
    second_id = db.add_document(
        file_hash="def456",
        file_path="/home/user/Downloads/notes.pdf",
        title="Notes",
        authors="Bob",
        summary="Summary.",
        topics=["Other"],
        file_modified_at=now,
    )
    db.add_duplicate_path("abc123", "/home/user/Documents/paper.pdf")
    db.add_duplicate_path("def456", "/home/user/Documents/notes.pdf")

    result = db.clear_all_duplicate_paths()

    assert result == {"document_count": 2, "removed_count": 2}
    assert len(db.get_document(first_id)["paths"]) == 1
    assert len(db.get_document(second_id)["paths"]) == 1


def test_prune_missing_file_records_removes_paths_and_empty_documents(db, tmp_path):
    existing_path = tmp_path / "existing.pdf"
    missing_path = tmp_path / "missing.pdf"
    empty_missing_path = tmp_path / "empty-missing.pdf"
    existing_path.write_bytes(b"existing")
    empty_missing_path.write_bytes(b"gone")
    empty_missing_path.unlink()
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="abc123",
        file_path=str(existing_path),
        title="Has Existing Path",
        authors="Alice",
        summary="Summary.",
        topics=["Other"],
        file_modified_at=now,
    )
    db.add_duplicate_path("abc123", str(missing_path))
    empty_doc_id = db.add_document(
        file_hash="def456",
        file_path=str(empty_missing_path),
        title="All Missing",
        authors="Bob",
        summary="Summary.",
        topics=["Work"],
        file_modified_at=now,
    )

    result = db.prune_missing_file_records()

    assert result == {"removed_path_count": 2, "removed_document_count": 1}
    assert db.get_document(doc_id)["paths"] == [str(existing_path)]
    assert db.get_document(empty_doc_id) is None


def test_get_document_by_hash(db):
    """Should find a document by its file hash."""
    now = datetime.now(timezone.utc).isoformat()
    db.add_document(
        file_hash="xyz789",
        file_path="/path/to/file.pdf",
        title="Paper",
        authors="Eve",
        summary="Summary.",
        topics=["Finance"],
        file_modified_at=now,
    )
    doc = db.get_document_by_hash("xyz789")
    assert doc is not None
    assert doc["title"] == "Paper"

    assert db.get_document_by_hash("nonexistent") is None


def test_update_status(db):
    """Should update document status."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="abc",
        file_path="/path.pdf",
        title="Paper",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at=now,
    )
    db.update_status(doc_id, "read")
    doc = db.get_document(doc_id)
    assert doc["status"] == "read"


def test_list_documents(db):
    """Should list documents sorted by file_modified_at descending."""
    db.add_document(
        file_hash="a",
        file_path="/a.pdf",
        title="Older",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at="2026-01-01T00:00:00",
    )
    db.add_document(
        file_hash="b",
        file_path="/b.pdf",
        title="Newer",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at="2026-03-01T00:00:00",
    )
    docs = db.list_documents()
    assert docs[0]["title"] == "Newer"
    assert docs[1]["title"] == "Older"


def test_list_documents_filter_by_topic(db):
    """Should filter documents by topic."""
    now = datetime.now(timezone.utc).isoformat()
    db.add_document(
        file_hash="a",
        file_path="/a.pdf",
        title="Work Paper",
        authors="",
        summary="",
        topics=["Work"],
        file_modified_at=now,
    )
    db.add_document(
        file_hash="b",
        file_path="/b.pdf",
        title="Finance Paper",
        authors="",
        summary="",
        topics=["Finance"],
        file_modified_at=now,
    )
    docs = db.list_documents(topic="Work")
    assert len(docs) == 1
    assert docs[0]["title"] == "Work Paper"


def test_list_documents_filter_by_status(db):
    """Should filter documents by status."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="a",
        file_path="/a.pdf",
        title="Read Paper",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at=now,
    )
    db.update_status(doc_id, "read")
    db.add_document(
        file_hash="b",
        file_path="/b.pdf",
        title="Unread Paper",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at=now,
    )
    docs = db.list_documents(status="unread")
    assert len(docs) == 1
    assert docs[0]["title"] == "Unread Paper"


def test_list_topics(db):
    """Should list all topic names."""
    topics = db.list_topics()
    assert len(topics) == 5
    assert "Work" in topics


def test_add_topic(db):
    """Should add a new topic."""
    db.add_topic("Research")
    topics = db.list_topics()
    assert "Research" in topics


def test_add_duplicate_topic(db):
    """Adding an existing topic should not raise."""
    db.add_topic("Work")  # already exists
    topics = db.list_topics()
    assert topics.count("Work") == 1


def test_remove_topic_reassigns_to_other(db):
    """Removing a topic should reassign its documents to Other."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="a",
        file_path="/a.pdf",
        title="Paper",
        authors="",
        summary="",
        topics=["Finance"],
        file_modified_at=now,
    )
    db.remove_topic("Finance")
    doc = db.get_document(doc_id)
    assert "Finance" not in doc["topics"]
    assert "Other" in doc["topics"]
    assert "Finance" not in db.list_topics()


def test_cannot_remove_other_topic(db):
    """Should not allow removing the 'Other' topic."""
    with pytest.raises(ValueError, match="Cannot remove"):
        db.remove_topic("Other")


def test_tag_document(db):
    """Should add a topic to a document."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="a",
        file_path="/a.pdf",
        title="Paper",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at=now,
    )
    db.tag_document(doc_id, "Work")
    doc = db.get_document(doc_id)
    assert "Work" in doc["topics"]


def test_untag_document(db):
    """Should remove a topic from a document."""
    now = datetime.now(timezone.utc).isoformat()
    doc_id = db.add_document(
        file_hash="a",
        file_path="/a.pdf",
        title="Paper",
        authors="",
        summary="",
        topics=["Work", "Academic"],
        file_modified_at=now,
    )
    db.untag_document(doc_id, "Academic")
    doc = db.get_document(doc_id)
    assert "Academic" not in doc["topics"]
    assert "Work" in doc["topics"]


def test_notebook_note_topics_round_trip(db):
    """Should set and read topics on a note, ignoring unknown topic names."""
    note_id = db.add_notebook_note("Synthesis", body="body")
    db.set_notebook_note_topics(note_id, ["Work", "Nonexistent", "Academic"])
    note = db.get_notebook_note(note_id)
    assert note["topics"] == ["Academic", "Work"]  # known only, name-ordered


def test_notebook_note_topics_survive_rename(db):
    """Renaming a topic should keep the note's link (stored by id)."""
    note_id = db.add_notebook_note("Synthesis")
    db.set_notebook_note_topics(note_id, ["Work"])
    db.rename_topic("Work", "Career")
    assert db.get_notebook_note(note_id)["topics"] == ["Career"]


def test_remove_topic_deletes_note_topic_links(db):
    """Removing a topic should drop it from notes (no re-home to Other)."""
    note_id = db.add_notebook_note("Note")
    db.set_notebook_note_topics(note_id, ["Work"])
    db.remove_topic("Work")
    assert db.get_notebook_note(note_id)["topics"] == []


def test_add_notebook_note_with_topics(db):
    note_id = db.add_notebook_note("Note", body="b", topics=["Work"])
    assert db.get_notebook_note(note_id)["topics"] == ["Work"]


def test_update_notebook_note_replaces_topics(db):
    note_id = db.add_notebook_note("Note", topics=["Work"])
    created_at = db.get_notebook_note(note_id)["created_at"]
    time.sleep(0.001)  # ensure the clock advances past created_at
    db.update_notebook_note(note_id, topics=["Academic"])
    note = db.get_notebook_note(note_id)
    assert note["topics"] == ["Academic"]
    # updated_at bumps even when only topics change
    assert note["updated_at"] > created_at


def test_html_notebook_crud(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.initialize()

    nb_id = db.add_html_notebook("Lit Map", "/src/map.html", "stored-1.html")
    assert isinstance(nb_id, int)

    nb = db.get_html_notebook(nb_id)
    assert nb["title"] == "Lit Map"
    assert nb["source_path"] == "/src/map.html"
    assert nb["stored_filename"] == "stored-1.html"
    assert nb["created_at"] and nb["updated_at"]

    before = nb["updated_at"]
    db.update_html_notebook(nb_id, title="Renamed")
    assert db.get_html_notebook(nb_id)["title"] == "Renamed"
    assert db.get_html_notebook(nb_id)["updated_at"] >= before

    db.add_html_notebook("Second", "/src/b.html", "stored-2.html")
    # list_html_notebooks orders most-recently-touched first (matching notebook_notes):
    # "Second" was just inserted, so it sorts ahead of the earlier-updated "Renamed".
    assert [n["title"] for n in db.list_html_notebooks()] == ["Second", "Renamed"]

    db.delete_html_notebook(nb_id)
    assert db.get_html_notebook(nb_id) is None
    db.close()


def test_html_notebook_read_only_flag(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.initialize()

    editable_id = db.add_html_notebook("Editable", "/src/e.html", "e.html")
    ro_id = db.add_html_notebook("Reader", "/src/r.html", "r.html", read_only=True)

    assert db.get_html_notebook(editable_id)["read_only"] is False
    assert db.get_html_notebook(ro_id)["read_only"] is True
    db.close()
