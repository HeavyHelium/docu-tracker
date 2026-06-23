import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_TOPICS = [
    ("Work", "Work-related documents — reports, memos, presentations, and professional correspondence"),
    ("Academic", "University and education — coursework, syllabi, transcripts, and enrollment documents"),
    ("Finance", "Financial documents — invoices, receipts, tax forms, bank statements, and budgets"),
    ("Personal", "Personal documents — IDs, medical records, travel, and miscellaneous"),
    ("Other", "Documents that don't fit any other category"),
]


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    def initialize(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_tables()
        self._seed_topics()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT NOT NULL UNIQUE,
                title TEXT,
                authors TEXT,
                summary TEXT,
                status TEXT NOT NULL DEFAULT 'unread',
                scanned_at TEXT NOT NULL,
                file_modified_at TEXT
            );

            CREATE TABLE IF NOT EXISTS document_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                added_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            );

            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS document_topics (
                document_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                PRIMARY KEY (document_id, topic_id),
                FOREIGN KEY (document_id) REFERENCES documents(id),
                FOREIGN KEY (topic_id) REFERENCES topics(id)
            );

            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notebook_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notebook_note_documents (
                note_id INTEGER NOT NULL,
                document_id INTEGER NOT NULL,
                PRIMARY KEY (note_id, document_id),
                FOREIGN KEY (note_id) REFERENCES notebook_notes(id) ON DELETE CASCADE,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            );
        """)
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """Add columns that may be missing in older databases."""
        columns = {
            r[1] for r in self.conn.execute("PRAGMA table_info(topics)").fetchall()
        }
        if "description" not in columns:
            self.conn.execute("ALTER TABLE topics ADD COLUMN description TEXT DEFAULT ''")
            self.conn.commit()

    def _seed_topics(self):
        count = self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        if count == 0:
            for name, description in DEFAULT_TOPICS:
                self.conn.execute(
                    "INSERT INTO topics (name, description) VALUES (?, ?)",
                    (name, description),
                )
            self.conn.commit()

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def get_metadata(self, key):
        row = self.conn.execute(
            "SELECT value FROM app_metadata WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key, value):
        self.conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_scan_path_last_scanned_at(self, scan_path):
        return self.get_metadata(f"last_scan_at:{scan_path}")

    def set_scan_path_last_scanned_at(self, scan_path, scanned_at):
        self.set_metadata(f"last_scan_at:{scan_path}", scanned_at)

    def add_document(self, file_hash, file_path, title, authors, summary,
                     topics, file_modified_at):
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """INSERT INTO documents
               (file_hash, title, authors, summary, status, scanned_at, file_modified_at)
               VALUES (?, ?, ?, ?, 'unread', ?, ?)""",
            (file_hash, title, authors, summary, now, file_modified_at),
        )
        doc_id = cursor.lastrowid
        self.conn.execute(
            "INSERT INTO document_paths (document_id, file_path, added_at) VALUES (?, ?, ?)",
            (doc_id, file_path, now),
        )
        for topic_name in topics:
            row = self.conn.execute(
                "SELECT id FROM topics WHERE name = ?", (topic_name,)
            ).fetchone()
            if row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO document_topics (document_id, topic_id) VALUES (?, ?)",
                    (doc_id, row[0]),
                )
        self.conn.commit()
        return doc_id

    def add_duplicate_path(self, file_hash, file_path):
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if row:
            self.conn.execute(
                "INSERT INTO document_paths (document_id, file_path, added_at) VALUES (?, ?, ?)",
                (row[0], file_path, now),
            )
            self.conn.commit()
            return row[0]
        return None

    def remove_document_path(self, doc_id, file_path):
        path_count = self.conn.execute(
            "SELECT COUNT(*) FROM document_paths WHERE document_id = ?",
            (doc_id,),
        ).fetchone()[0]
        if path_count <= 1:
            raise ValueError("Cannot remove the only tracked path for a document")

        row = self.conn.execute(
            "SELECT id FROM document_paths WHERE document_id = ? AND file_path = ? "
            "ORDER BY added_at, id LIMIT 1",
            (doc_id, file_path),
        ).fetchone()
        if not row:
            return 0

        self.conn.execute("DELETE FROM document_paths WHERE id = ?", (row[0],))
        self.conn.commit()
        return 1

    def clear_document_duplicate_paths(self, doc_id):
        rows = self.conn.execute(
            "SELECT id FROM document_paths WHERE document_id = ? ORDER BY added_at, id",
            (doc_id,),
        ).fetchall()
        if len(rows) <= 1:
            return 0

        duplicate_ids = [row[0] for row in rows[1:]]
        placeholders = ",".join("?" for _ in duplicate_ids)
        self.conn.execute(
            f"DELETE FROM document_paths WHERE id IN ({placeholders})",
            duplicate_ids,
        )
        self.conn.commit()
        return len(duplicate_ids)

    def clear_all_duplicate_paths(self):
        doc_rows = self.conn.execute(
            "SELECT document_id FROM document_paths "
            "GROUP BY document_id HAVING COUNT(*) > 1"
        ).fetchall()
        removed_count = 0
        for row in doc_rows:
            removed_count += self.clear_document_duplicate_paths(row[0])
        return {
            "document_count": len(doc_rows),
            "removed_count": removed_count,
        }

    def prune_missing_file_records(self):
        rows = self.conn.execute(
            "SELECT id, document_id, file_path FROM document_paths ORDER BY document_id, added_at, id"
        ).fetchall()
        missing_path_ids = []
        affected_doc_ids = set()
        for path_id, doc_id, file_path in rows:
            if not os.path.exists(file_path):
                missing_path_ids.append(path_id)
                affected_doc_ids.add(doc_id)

        if missing_path_ids:
            placeholders = ",".join("?" for _ in missing_path_ids)
            self.conn.execute(
                f"DELETE FROM document_paths WHERE id IN ({placeholders})",
                missing_path_ids,
            )

        removed_doc_ids = []
        for doc_id in affected_doc_ids:
            remaining_count = self.conn.execute(
                "SELECT COUNT(*) FROM document_paths WHERE document_id = ?",
                (doc_id,),
            ).fetchone()[0]
            if remaining_count == 0:
                removed_doc_ids.append(doc_id)

        for doc_id in removed_doc_ids:
            self.conn.execute(
                "DELETE FROM document_topics WHERE document_id = ?",
                (doc_id,),
            )
            self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

        self.conn.commit()
        return {
            "removed_path_count": len(missing_path_ids),
            "removed_document_count": len(removed_doc_ids),
        }

    def get_document(self, doc_id):
        row = self.conn.execute(
            "SELECT id, file_hash, title, authors, summary, status, scanned_at, file_modified_at "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        paths = [
            r[0] for r in self.conn.execute(
                "SELECT file_path FROM document_paths WHERE document_id = ? ORDER BY added_at, id",
                (row[0],),
            ).fetchall()
        ]
        topics = [
            r[0] for r in self.conn.execute(
                "SELECT t.name FROM topics t "
                "JOIN document_topics dt ON t.id = dt.topic_id "
                "WHERE dt.document_id = ?",
                (row[0],),
            ).fetchall()
        ]
        return {
            "id": row[0], "file_hash": row[1], "title": row[2],
            "authors": row[3], "summary": row[4], "status": row[5],
            "scanned_at": row[6], "file_modified_at": row[7],
            "paths": paths, "topics": topics,
        }

    def get_document_by_hash(self, file_hash):
        row = self.conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if row:
            return self.get_document(row[0])
        return None

    def update_document(self, doc_id, title=None, authors=None, summary=None):
        fields = []
        params = []
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if authors is not None:
            fields.append("authors = ?")
            params.append(authors)
        if summary is not None:
            fields.append("summary = ?")
            params.append(summary)
        if fields:
            params.append(doc_id)
            self.conn.execute(
                f"UPDATE documents SET {', '.join(fields)} WHERE id = ?", params
            )
            self.conn.commit()

    def update_status(self, doc_id, status):
        self.conn.execute(
            "UPDATE documents SET status = ? WHERE id = ?", (status, doc_id)
        )
        self.conn.commit()

    def list_documents(self, topic=None, status=None):
        query = "SELECT DISTINCT d.id FROM documents d"
        params = []
        if topic:
            query += (
                " JOIN document_topics dt ON d.id = dt.document_id"
                " JOIN topics t ON dt.topic_id = t.id"
            )
        query += " WHERE 1=1"
        if topic:
            query += " AND t.name = ?"
            params.append(topic)
        if status:
            query += " AND d.status = ?"
            params.append(status)
        query += " ORDER BY d.file_modified_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        return [self.get_document(r[0]) for r in rows]


    def _document_ids_for_note(self, note_id):
        rows = self.conn.execute(
            "SELECT document_id FROM notebook_note_documents WHERE note_id = ? "
            "ORDER BY document_id",
            (note_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def get_notebook_note(self, note_id):
        row = self.conn.execute(
            "SELECT id, title, body, created_at, updated_at FROM notebook_notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "body": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "document_ids": self._document_ids_for_note(row[0]),
        }

    def list_notebook_notes(self):
        rows = self.conn.execute(
            "SELECT id FROM notebook_notes ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [self.get_notebook_note(row[0]) for row in rows]

    def add_notebook_note(self, title, body="", document_ids=None):
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "INSERT INTO notebook_notes (title, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (title, body, now, now),
        )
        note_id = cursor.lastrowid
        self.set_notebook_note_documents(note_id, document_ids or [], commit=False)
        self.conn.commit()
        return note_id

    def update_notebook_note(
        self,
        note_id,
        title=None,
        body=None,
        document_ids=None,
    ):
        fields = []
        params = []
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        if body is not None:
            fields.append("body = ?")
            params.append(body)
        if fields:
            fields.append("updated_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())
            params.append(note_id)
            self.conn.execute(
                f"UPDATE notebook_notes SET {', '.join(fields)} WHERE id = ?",
                params,
            )
        if document_ids is not None:
            self.set_notebook_note_documents(note_id, document_ids, commit=False)
            if not fields:
                self.conn.execute(
                    "UPDATE notebook_notes SET updated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), note_id),
                )
        self.conn.commit()

    def set_notebook_note_documents(self, note_id, document_ids, commit=True):
        self.conn.execute(
            "DELETE FROM notebook_note_documents WHERE note_id = ?",
            (note_id,),
        )
        for doc_id in document_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO notebook_note_documents (note_id, document_id) "
                "VALUES (?, ?)",
                (note_id, doc_id),
            )
        if commit:
            self.conn.commit()

    def delete_notebook_note(self, note_id):
        self.conn.execute("DELETE FROM notebook_notes WHERE id = ?", (note_id,))
        self.conn.commit()

    def list_topics(self):
        rows = self.conn.execute("SELECT name FROM topics ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def list_topics_with_descriptions(self):
        rows = self.conn.execute(
            "SELECT name, description FROM topics ORDER BY name"
        ).fetchall()
        return [(r[0], r[1] or "") for r in rows]

    def add_topic(self, name, description=""):
        self.conn.execute(
            "INSERT OR IGNORE INTO topics (name, description) VALUES (?, ?)",
            (name, description),
        )
        self.conn.commit()

    def update_topic_description(self, name, description):
        self.conn.execute(
            "UPDATE topics SET description = ? WHERE name = ?",
            (description, name),
        )
        self.conn.commit()

    def rename_topic(self, old_name, new_name):
        if old_name == "Other":
            raise ValueError("Cannot rename the 'Other' topic")
        if not new_name.strip():
            raise ValueError("Topic name cannot be empty")
        existing = self.conn.execute(
            "SELECT id FROM topics WHERE name = ?", (new_name,)
        ).fetchone()
        if existing and new_name != old_name:
            raise ValueError(f"Topic '{new_name}' already exists")
        self.conn.execute(
            "UPDATE topics SET name = ? WHERE name = ?",
            (new_name, old_name),
        )
        self.conn.commit()

    def remove_topic(self, name):
        if name == "Other":
            raise ValueError("Cannot remove the 'Other' topic")
        topic_row = self.conn.execute(
            "SELECT id FROM topics WHERE name = ?", (name,)
        ).fetchone()
        if not topic_row:
            return
        topic_id = topic_row[0]
        other_id = self.conn.execute(
            "SELECT id FROM topics WHERE name = 'Other'"
        ).fetchone()[0]
        doc_ids = [
            r[0] for r in self.conn.execute(
                "SELECT document_id FROM document_topics WHERE topic_id = ?",
                (topic_id,),
            ).fetchall()
        ]
        self.conn.execute("DELETE FROM document_topics WHERE topic_id = ?", (topic_id,))
        for doc_id in doc_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO document_topics (document_id, topic_id) VALUES (?, ?)",
                (doc_id, other_id),
            )
        self.conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        self.conn.commit()

    def tag_document(self, doc_id, topic_name):
        topic_row = self.conn.execute(
            "SELECT id FROM topics WHERE name = ?", (topic_name,)
        ).fetchone()
        if topic_row:
            self.conn.execute(
                "INSERT OR IGNORE INTO document_topics (document_id, topic_id) VALUES (?, ?)",
                (doc_id, topic_row[0]),
            )
            self.conn.commit()

    def untag_document(self, doc_id, topic_name):
        topic_row = self.conn.execute(
            "SELECT id FROM topics WHERE name = ?", (topic_name,)
        ).fetchone()
        if topic_row:
            self.conn.execute(
                "DELETE FROM document_topics WHERE document_id = ? AND topic_id = ?",
                (doc_id, topic_row[0]),
            )
            self.conn.commit()

    def set_topics(self, doc_id, topic_names):
        """Replace all topics on a document."""
        if not topic_names:
            topic_names = ["Other"]
        self.conn.execute(
            "DELETE FROM document_topics WHERE document_id = ?", (doc_id,)
        )
        for name in topic_names:
            row = self.conn.execute(
                "SELECT id FROM topics WHERE name = ?", (name,)
            ).fetchone()
            if row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO document_topics (document_id, topic_id) VALUES (?, ?)",
                    (doc_id, row[0]),
                )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
