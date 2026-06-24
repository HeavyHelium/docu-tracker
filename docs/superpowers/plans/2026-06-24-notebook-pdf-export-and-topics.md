# Notebook PDF Export & Note Topics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users tag notebook notes with existing topics and export note previews to PDF via the browser print dialog.

**Architecture:** Note topics mirror the existing `document_topics` many-to-many: a new `notebook_note_topics` join table, DB helpers parallel to the document-reference helpers, topics threaded through the notebook API, and topic chips in the editor. PDF export is pure client-side: reuse the existing `renderMarkdown` to build a standalone print document, open it in a same-origin window, and call `print()`.

**Tech Stack:** Python 3.10+ (sqlite3, stdlib WSGI app), vanilla JS frontend, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-24-notebook-pdf-export-design.md`

---

## File Structure

- `src/docu_tracker/db.py` — add `notebook_note_topics` table + topic read/write helpers; thread `topics` through `get_notebook_note`, `add_notebook_note`, `update_notebook_note`.
- `src/docu_tracker/web.py` — `_serialize_notebook_note` includes `topics`; add `_clean_notebook_topics`; parse `topics` in `create_notebook_note` / `update_notebook_note`.
- `src/docu_tracker/webui/app.js` — topic chips in the note editor + autosave wiring; topics on note-list cards; PDF export functions + buttons + inlined print stylesheet.
- `src/docu_tracker/webui/index.html` — no change expected (notebook UI is rendered from JS).
- `src/docu_tracker/webui/app.css` — styling for topic chips and the two export buttons.
- `tests/test_db.py` — note-topic DB behavior.
- `tests/test_web.py` — topics round-trip through the API.

Build order is bottom-up: DB → web → frontend. Python tasks are TDD; frontend tasks are manual verification (the browser logic is not covered by the Python suite).

---

## Task 1: Note-topic schema and DB read/write helpers

**Files:**
- Modify: `src/docu_tracker/db.py` (schema block at lines 66-80; helpers near `get_notebook_note` at lines 347-361 and `set_notebook_note_documents` at 413-425)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
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
```

> Note: `add_notebook_note` does not yet accept being called with only a title in every path — it already defaults `body=""` and `document_ids=None`, so `db.add_notebook_note("Synthesis")` works. The default seeded topics include `Work` and `Academic` (see `DEFAULT_TOPICS`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_notebook_note_topics_round_trip tests/test_db.py::test_notebook_note_topics_survive_rename -v`
Expected: FAIL — `set_notebook_note_topics` does not exist / `note["topics"]` KeyError.

- [ ] **Step 3: Add the join table to the schema**

In `src/docu_tracker/db.py`, inside the `executescript` block, immediately after the `notebook_note_documents` table (line 80, before the closing `"""`):

```python
            CREATE TABLE IF NOT EXISTS notebook_note_topics (
                note_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                PRIMARY KEY (note_id, topic_id),
                FOREIGN KEY (note_id) REFERENCES notebook_notes(id) ON DELETE CASCADE,
                FOREIGN KEY (topic_id) REFERENCES topics(id)
            );
```

- [ ] **Step 4: Add `_topics_for_note` and include topics in `get_notebook_note`**

Add a helper next to `_document_ids_for_note` (after line 345):

```python
    def _topics_for_note(self, note_id):
        rows = self.conn.execute(
            "SELECT t.name FROM topics t "
            "JOIN notebook_note_topics nnt ON t.id = nnt.topic_id "
            "WHERE nnt.note_id = ? ORDER BY t.name",
            (note_id,),
        ).fetchall()
        return [row[0] for row in rows]
```

In `get_notebook_note`'s returned dict (after the `"document_ids"` line ~360), add:

```python
            "topics": self._topics_for_note(row[0]),
```

- [ ] **Step 5: Add `set_notebook_note_topics`**

Next to `set_notebook_note_documents` (after line 425):

```python
    def set_notebook_note_topics(self, note_id, topic_names, commit=True):
        self.conn.execute(
            "DELETE FROM notebook_note_topics WHERE note_id = ?",
            (note_id,),
        )
        for name in topic_names:
            topic_row = self.conn.execute(
                "SELECT id FROM topics WHERE name = ?", (name,)
            ).fetchone()
            if topic_row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO notebook_note_topics (note_id, topic_id) "
                    "VALUES (?, ?)",
                    (note_id, topic_row[0]),
                )
        if commit:
            self.conn.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_db.py::test_notebook_note_topics_round_trip tests/test_db.py::test_notebook_note_topics_survive_rename -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/docu_tracker/db.py tests/test_db.py
git commit -m "Add notebook_note_topics table and read/write helpers"
```

---

## Task 2: Thread topics through note create/update

**Files:**
- Modify: `src/docu_tracker/db.py` (`add_notebook_note` 369-379, `update_notebook_note` 381-411)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_add_notebook_note_with_topics(db):
    note_id = db.add_notebook_note("Note", body="b", topics=["Work"])
    assert db.get_notebook_note(note_id)["topics"] == ["Work"]


def test_update_notebook_note_replaces_topics(db):
    note_id = db.add_notebook_note("Note", topics=["Work"])
    db.update_notebook_note(note_id, topics=["Academic"])
    note = db.get_notebook_note(note_id)
    assert note["topics"] == ["Academic"]
    # updated_at bumps even when only topics change
    assert note["updated_at"] >= note["created_at"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_add_notebook_note_with_topics tests/test_db.py::test_update_notebook_note_replaces_topics -v`
Expected: FAIL — `add_notebook_note() got an unexpected keyword argument 'topics'`.

- [ ] **Step 3: Extend `add_notebook_note`**

Change the signature and body of `add_notebook_note` (lines 369-379):

```python
    def add_notebook_note(self, title, body="", document_ids=None, topics=None):
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "INSERT INTO notebook_notes (title, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (title, body, now, now),
        )
        note_id = cursor.lastrowid
        self.set_notebook_note_documents(note_id, document_ids or [], commit=False)
        self.set_notebook_note_topics(note_id, topics or [], commit=False)
        self.conn.commit()
        return note_id
```

- [ ] **Step 4: Extend `update_notebook_note`**

Add `topics=None` to the signature (after `document_ids=None`, line 386). Then change the `updated_at`-bump condition so topic-only updates still bump it. Replace the `document_ids` block (lines 404-410) with:

```python
        if document_ids is not None:
            self.set_notebook_note_documents(note_id, document_ids, commit=False)
        if topics is not None:
            self.set_notebook_note_topics(note_id, topics, commit=False)
        if not fields and (document_ids is not None or topics is not None):
            self.conn.execute(
                "UPDATE notebook_notes SET updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), note_id),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db.py -k notebook -v`
Expected: PASS (all four notebook DB tests)

- [ ] **Step 6: Commit**

```bash
git add src/docu_tracker/db.py tests/test_db.py
git commit -m "Thread topics through notebook note create/update"
```

---

## Task 3: Topics in the notebook API

**Files:**
- Modify: `src/docu_tracker/web.py` (`_serialize_notebook_note` 146-154; `_clean_notebook_document_ids` 515-529; `create_notebook_note` 531-544; `update_notebook_note` 546-574)
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web.py`:

```python
def test_notebook_topics_round_trip(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    created = call_app(
        app, "POST", "/api/notebook",
        {"title": "Topic note", "topics": ["Work"]},
    )
    assert created["status"].startswith("201")
    assert created["json"]["note"]["topics"] == ["Work"]

    note_id = created["json"]["note"]["id"]
    updated = call_app(
        app, "PATCH", f"/api/notebook/{note_id}",
        {"topics": ["Academic"]},
    )
    assert updated["json"]["note"]["topics"] == ["Academic"]

    bad = call_app(
        app, "POST", "/api/notebook",
        {"title": "Bad", "topics": "Work"},
    )
    assert bad["status"].startswith("400")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web.py::test_notebook_topics_round_trip -v`
Expected: FAIL — serialized note has no `topics` key.

- [ ] **Step 3: Serialize topics**

In `_serialize_notebook_note` (lines 146-154), add to the returned dict:

```python
        "topics": note["topics"],
```

- [ ] **Step 4: Add `_clean_notebook_topics`**

Next to `_clean_notebook_document_ids` (after line 529):

```python
    def _clean_notebook_topics(self, topics):
        if topics is None:
            return None
        if not isinstance(topics, list):
            raise HTTPError(400, "Notebook topics must be a list")
        cleaned = []
        for raw in topics:
            if not isinstance(raw, str):
                raise HTTPError(400, "Notebook topics must be strings")
            name = raw.strip()
            if name and name not in cleaned:
                cleaned.append(name)
        return cleaned
```

- [ ] **Step 5: Parse topics in create and update**

In `create_notebook_note`, after the `document_ids = ...` block (line 542), add:

```python
            topics = self._clean_notebook_topics(payload.get("topics", []))
```

and pass it: `db.add_notebook_note(title, body, document_ids, topics)`.

In `update_notebook_note`, after the `document_ids = ...` block (line 567), add:

```python
            topics = self._clean_notebook_topics(
                payload.get("topics") if "topics" in payload else None
            )
```

and add `topics=topics,` to the `db.update_notebook_note(...)` call.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_web.py -k notebook -v`
Expected: PASS (new test + existing `test_notebook_routes_persist_notes_and_references`)

- [ ] **Step 7: Commit**

```bash
git add src/docu_tracker/web.py tests/test_web.py
git commit -m "Round-trip notebook note topics through the API"
```

---

## Task 4: Topic chips in the note editor (frontend)

**Files:**
- Modify: `src/docu_tracker/webui/app.js` (`renderNotebookEditor` 1074-1111; `currentNotebookPayload` 1205-1215; the `change` handler at 2064-2072)
- Modify: `src/docu_tracker/webui/app.css` (chip styles)

No Python test — verify manually in Step 6.

- [ ] **Step 1: Render a topic selector in `renderNotebookEditor`**

Add a topics block between the markdown toolbar and the linked-files block (after the `.notebook-compose` div closes, before `.notebook-linked-files`, around line 1100). Build it from `state.topics`, checking those in `note.topics`:

```javascript
    <div class="notebook-topics">
      <p class="section-kicker">Topics</p>
      <div class="notebook-topic-chips">
        ${state.topics.length ? state.topics.map((topic) => `
          <label class="notebook-topic-chip ${(note.topics || []).includes(topic.name) ? "selected" : ""}">
            <input type="checkbox" data-note-topic="${escapeAttribute(topic.name)}" ${(note.topics || []).includes(topic.name) ? "checked" : ""}>
            <span>${escapeHtml(topic.name)}</span>
          </label>
        `).join("") : `<span class="empty-state">No topics defined yet.</span>`}
      </div>
    </div>
```

- [ ] **Step 2: Include topics in `currentNotebookPayload`**

In `currentNotebookPayload` (1205-1215), read topics from the in-memory note (kept current by the change handler in Step 3):

```javascript
  const note = selectedNotebookNote();
  const checkedRefs = note?.document_ids || [];
  return {
    title: (titleInput?.value || "").trim() || "Untitled note",
    body: bodyInput?.value || "",
    document_ids: checkedRefs,
    topics: note?.topics || [],
  };
```

- [ ] **Step 3: Wire the topic checkbox into the change handler**

The `change` handler (2064-2072) early-returns unless the target matches `input[data-note-ref-id]`. Add a branch for topic checkboxes at the top of that handler:

```javascript
  if (event.target.matches("input[data-note-topic]")) {
    const note = selectedNotebookNote();
    if (!note) return;
    const name = event.target.dataset.noteTopic;
    const topics = new Set(note.topics || []);
    if (event.target.checked) topics.add(name);
    else topics.delete(name);
    note.topics = Array.from(topics).sort();
    event.target.closest(".notebook-topic-chip")?.classList.toggle("selected", event.target.checked);
    scheduleNotebookAutosave();
    return;
  }
```

- [ ] **Step 4: Add chip styles to `app.css`**

Append (match existing notebook styling conventions — find a nearby `.notebook-*` rule for the palette):

```css
.notebook-topics { margin-top: 0.75rem; }
.notebook-topic-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.notebook-topic-chip {
  display: inline-flex; align-items: center; gap: 0.35rem;
  padding: 0.2rem 0.55rem; border-radius: 999px;
  border: 1px solid var(--border, #3a3a3a); cursor: pointer; font-size: 0.85rem;
}
.notebook-topic-chip.selected { border-color: var(--accent, #6ea8fe); }
.notebook-topic-chip input { margin: 0; }
```

- [ ] **Step 5: Verify the suite still passes**

Run: `pytest -q`
Expected: PASS (no Python behavior changed)

- [ ] **Step 6: Manual verification**

Run `docu-tracker web`, open the Notebook, select a note, toggle a topic chip. Confirm: the chip highlights, "Autosave pending" appears, and after reload the topic is still selected.

- [ ] **Step 7: Commit**

```bash
git add src/docu_tracker/webui/app.js src/docu_tracker/webui/app.css
git commit -m "Add topic chips to the note editor with autosave"
```

---

## Task 5: Show topics on note-list cards (frontend)

**Files:**
- Modify: `src/docu_tracker/webui/app.js` (`renderNotebook` card markup 690-696)
- Modify: `src/docu_tracker/webui/app.css`

- [ ] **Step 1: Add topics to the card markup**

In the note card template (lines 690-696), after the `<small>` line, add a topics row:

```javascript
                ${(item.topics || []).length ? `<div class="notebook-card-topics">${item.topics.map((t) => `<span class="notebook-card-topic">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
```

- [ ] **Step 2: Style the card topics**

Append to `app.css`:

```css
.notebook-card-topics { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.3rem; }
.notebook-card-topic {
  font-size: 0.7rem; padding: 0.05rem 0.4rem; border-radius: 999px;
  background: var(--surface-2, #2a2a2a); color: var(--text-muted, #aaa);
}
```

- [ ] **Step 3: Manual verification**

Reload the web UI; notes with topics show topic pills on their list cards.

- [ ] **Step 4: Commit**

```bash
git add src/docu_tracker/webui/app.js src/docu_tracker/webui/app.css
git commit -m "Show topics on notebook list cards"
```

---

## Task 6: PDF export (frontend)

**Files:**
- Modify: `src/docu_tracker/webui/app.js` (new export functions; buttons in `renderNotebookEditor` actions 1084-1087 and `renderNotebook` heading 682-688; click handler 1998-2047)
- Modify: `src/docu_tracker/webui/app.css` (export button, if needed)

- [ ] **Step 1: Add the export builder and print-window functions**

Add near the other notebook helpers (e.g. after `documentMarkdownLink`, ~line 1117):

```javascript
function buildNoteExportSection(note) {
  const title = escapeHtml(note.title || "Untitled note");
  const topics = (note.topics || []).length
    ? `<div class="pdf-note-topics">${note.topics.map((t) => `<span>${escapeHtml(t)}</span>`).join("")}</div>`
    : "";
  const linkedDocs = noteReferencedDocuments(note);
  const linked = linkedDocs.length
    ? `<section class="pdf-linked"><h2>Linked files</h2><ul>${linkedDocs.map((doc) => `<li><strong>${escapeHtml(doc.title || `Document #${doc.id}`)}</strong> — ${escapeHtml(doc.authors || doc.source || "No authors")}</li>`).join("")}</ul></section>`
    : "";
  return `<article class="pdf-note"><h1>${title}</h1>${topics}${renderMarkdown(note.body)}${linked}</article>`;
}

const PDF_PRINT_CSS = `
  body { font-family: Georgia, "Times New Roman", serif; color: #111; margin: 2.5rem; line-height: 1.55; }
  h1 { font-size: 1.7rem; margin: 0 0 0.4rem; }
  h2, h3 { margin-top: 1.4rem; }
  img { max-width: 100%; height: auto; }
  .pdf-note-topics { margin: 0 0 1rem; }
  .pdf-note-topics span { display: inline-block; font-size: 0.75rem; padding: 0.1rem 0.5rem; margin-right: 0.3rem; border: 1px solid #999; border-radius: 999px; }
  .pdf-linked { margin-top: 1.5rem; border-top: 1px solid #ccc; padding-top: 0.6rem; }
  .pdf-note { break-after: page; }
  .pdf-note:last-child { break-after: auto; }
  blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 1rem; color: #444; }
  pre, code { font-family: "SFMono-Regular", Consolas, monospace; }
  pre { background: #f5f5f5; padding: 0.75rem; overflow-x: auto; }
`;

function awaitImages(win) {
  const images = Array.from(win.document.images || []);
  const pending = images.filter((img) => !img.complete);
  if (!pending.length) return Promise.resolve();
  return Promise.race([
    Promise.all(pending.map((img) => new Promise((resolve) => {
      img.addEventListener("load", resolve, { once: true });
      img.addEventListener("error", resolve, { once: true });
    }))),
    new Promise((resolve) => win.setTimeout(resolve, 3000)),
  ]);
}

function openNotePrintWindow(docTitle, sectionsHtml) {
  const win = window.open("", "_blank");
  if (!win) {
    showFlash("Could not open the print window — allow popups for this site.", "error");
    return;
  }
  win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(docTitle)}</title><style>${PDF_PRINT_CSS}</style></head><body>${sectionsHtml}</body></html>`);
  win.document.close();
  awaitImages(win).then(() => {
    win.focus();
    win.print();
  });
}

function exportNoteToPdf() {
  const note = selectedNotebookNote();
  if (!note) {
    showFlash("Select a note to export.", "error");
    return;
  }
  openNotePrintWindow(note.title || "Note", buildNoteExportSection(note));
}

function exportAllNotesToPdf() {
  if (!state.notebookNotes.length) {
    showFlash("No notes to export.", "error");
    return;
  }
  const sections = state.notebookNotes.map(buildNoteExportSection).join("");
  openNotePrintWindow("Notebook", sections);
}
```

> `renderMarkdown`, `noteReferencedDocuments`, `selectedNotebookNote`, `showFlash`, `escapeHtml`, `escapeAttribute`, and `state.notebookNotes` all already exist in this file.

- [ ] **Step 2: Add the two buttons**

In `renderNotebookEditor` actions (1084-1087), add before the Save button:

```javascript
        <button id="notebook-export" class="button" type="button">Export PDF</button>
```

In `renderNotebook` list-panel heading (682-688), next to the New button:

```javascript
            <button id="notebook-export-all" class="button" type="button">Export all</button>
```

- [ ] **Step 3: Wire the buttons in the click handler**

In the `els.notebookContainer` click handler (1998-2047), add lookups alongside the existing ones and branches in the try block:

```javascript
  const exportButton = event.target.closest("#notebook-export");
  const exportAllButton = event.target.closest("#notebook-export-all");
```

```javascript
    if (exportButton) { exportNoteToPdf(); return; }
    if (exportAllButton) { exportAllNotesToPdf(); return; }
```

- [ ] **Step 4: Verify the suite still passes**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 5: Manual verification**

Run `docu-tracker web`. Create a note with a heading, a list, a pasted image, a linked file, and a topic. Click **Export PDF** → a print preview opens showing the title, the topic pill, rendered body with the image, and the linked-files list. Use the dialog's "Save as PDF". Then click **Export all** → all notes appear with page breaks between them. Confirm a blocked popup shows the flash error (optional: block popups to test).

- [ ] **Step 6: Commit**

```bash
git add src/docu_tracker/webui/app.js src/docu_tracker/webui/app.css
git commit -m "Add PDF export for notebook note previews"
```

---

## Task 7: Full verification

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: all tests PASS (including the 5 new notebook tests).

- [ ] **Step 2: Final manual smoke test**

With `docu-tracker web` running: tag a note with two topics, confirm autosave + reload persistence, confirm topics appear on the list card and in both single and "Export all" PDF previews.

- [ ] **Step 3: Confirm no stray debug output / leftover console logs in `app.js`.**
