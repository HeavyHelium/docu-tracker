import os
import pytest
from datetime import datetime, timezone
from docu_tracker.db import Database


def test_initialize_creates_tables(db):
    """All four tables should exist after init."""
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [t[0] for t in tables]
    assert "documents" in table_names
    assert "document_paths" in table_names
    assert "topics" in table_names
    assert "document_topics" in table_names


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
