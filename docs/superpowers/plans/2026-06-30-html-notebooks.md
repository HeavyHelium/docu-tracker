# HTML Notebooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Notebooks" tab to the docu-tracker web UI that catalogs standalone HTML notebooks (imported as managed copies), opens them for full rendering, and edits their HTML source in-app with a live preview.

**Architecture:** A new `html_notebooks` SQLite table stores metadata; the actual HTML lives as a managed copy under `~/.docu-tracker/notebooks/`. The WSGI app in `web.py` gains id-based `/api/html-notebooks` routes for CRUD + content read/write + rendered serving. The vanilla SPA (`app.js`/`index.html`/`app.css`) gets a new tab with a list, an add form, and an enhanced-textarea editor whose preview iframe renders the saved copy.

**Tech Stack:** Python stdlib (`sqlite3`, `wsgiref`, `shutil`, `uuid`, `pathlib`), pytest, vanilla JS/CSS (no build, no new dependencies).

**Spec:** `docs/superpowers/specs/2026-06-30-html-notebooks-design.md`

---

## File Structure

- **Modify** `src/docu_tracker/db.py` — add `html_notebooks` table to `_create_tables`; add CRUD helpers.
- **Modify** `src/docu_tracker/web.py` — add serializer, storage helpers, `/api/html-notebooks` routing + handlers, and the `build_state` field.
- **Modify** `src/docu_tracker/webui/index.html` — add the Notebooks tab button + panel markup.
- **Modify** `src/docu_tracker/webui/app.js` — add tab wiring, list rendering, add form, editor (textarea + gutter + find box), autosave, preview.
- **Modify** `src/docu_tracker/webui/app.css` — styles for the panel, list, editor split, gutter, find box.
- **Modify** `tests/test_db.py` — DB-layer tests.
- **Modify** `tests/test_web.py` — web-route tests.
- **Modify** `README.md` — "Specialized HTML notebooks" subsection.

Conventions to follow (already in the codebase):
- Timestamps: `datetime.now(timezone.utc).isoformat()`.
- DB helpers return plain dicts; `list_*` orders by `updated_at DESC, id DESC`.
- Web handlers raise `HTTPError(status, message)`; responses via `_json_response` / `_text_response` / `_bytes_response`.
- Tests use `tmp_path`, a `config_dir`, `DocuTrackerWebApp(config_dir=..., cwd=...)`, and `build_test_environ(...)`. Parse JSON bodies with `json.loads(b"".join(app(environ, start_response)))`.

---

## Task 1: DB table + CRUD helpers

**Files:**
- Modify: `src/docu_tracker/db.py` (table in `_create_tables` ~line 88; helpers near the `notebook_notes` helpers ~line 469)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
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
    assert [n["title"] for n in db.list_html_notebooks()][:1] == ["Renamed"]

    db.delete_html_notebook(nb_id)
    assert db.get_html_notebook(nb_id) is None
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_html_notebook_crud -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'add_html_notebook'`.

- [ ] **Step 3: Add the table**

In `db.py` `_create_tables`, inside the `executescript("""..."""`) block (after the `notebook_note_topics` table, before the closing `"""`):

```sql
            CREATE TABLE IF NOT EXISTS html_notebooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                stored_filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
```

- [ ] **Step 4: Add the helpers**

In `db.py`, after `delete_notebook_note` (~line 469):

```python
    def get_html_notebook(self, notebook_id):
        row = self.conn.execute(
            "SELECT id, title, source_path, stored_filename, created_at, updated_at "
            "FROM html_notebooks WHERE id = ?",
            (notebook_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "source_path": row[2],
            "stored_filename": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    def list_html_notebooks(self):
        rows = self.conn.execute(
            "SELECT id FROM html_notebooks ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [self.get_html_notebook(row[0]) for row in rows]

    def add_html_notebook(self, title, source_path, stored_filename):
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "INSERT INTO html_notebooks (title, source_path, stored_filename, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, source_path, stored_filename, now, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_html_notebook(self, notebook_id, title=None):
        fields = []
        params = []
        if title is not None:
            fields.append("title = ?")
            params.append(title)
        fields.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(notebook_id)
        self.conn.execute(
            f"UPDATE html_notebooks SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def delete_html_notebook(self, notebook_id):
        self.conn.execute("DELETE FROM html_notebooks WHERE id = ?", (notebook_id,))
        self.conn.commit()
```

Note: `update_html_notebook` always bumps `updated_at` (it is called whenever the
title and/or the file content changes; content writes happen in the web layer).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py::test_html_notebook_crud -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/docu_tracker/db.py tests/test_db.py
git commit -m "Add html_notebooks table and DB CRUD helpers"
```

---

## Task 2: Web storage helpers + serializer + add/delete routes

**Files:**
- Modify: `src/docu_tracker/web.py` (constants ~line 36; serializers ~line 155; routing in `__call__` ~line 319; new methods near the notebook methods)
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web.py`:

```python
def test_html_notebook_add_list_delete(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    source = tmp_path / "map.html"
    source.write_text("<html><body>Hello map</body></html>")

    captured = {}
    def start_response(status, headers):
        captured["status"] = status

    # add
    body = app(
        build_test_environ("POST", "/api/html-notebooks",
                           payload={"path": str(source), "title": "Map"}),
        start_response,
    )
    created = json.loads(b"".join(body))["notebook"]
    assert captured["status"].startswith("201")
    assert created["title"] == "Map"
    assert created["source_path"] == str(source)

    stored_dir = config_dir / "notebooks"
    assert len(list(stored_dir.iterdir())) == 1

    # appears in state
    state = json.loads(
        b"".join(app(build_test_environ("GET", "/api/state"), start_response))
    )
    assert [n["title"] for n in state["html_notebooks"]] == ["Map"]

    # delete removes the managed copy
    app(build_test_environ("DELETE", f"/api/html-notebooks/{created['id']}"), start_response)
    assert captured["status"].startswith("200")
    assert list(stored_dir.iterdir()) == []
```

(Ensure `import json` is present at the top of `tests/test_web.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_web.py::test_html_notebook_add_list_delete -v`
Expected: FAIL — 404 (route not found) so `created` KeyError / status not 201.

- [ ] **Step 3: Add module constants + serializer**

In `web.py`, near `MAX_NOTEBOOK_ATTACHMENT_BYTES` (~line 36):

```python
HTML_NOTEBOOK_EXTENSIONS = {".html", ".htm"}
```

Add a serializer after `_serialize_notebook_note` (~line 155):

```python
def _serialize_html_notebook(notebook):
    return {
        "id": notebook["id"],
        "title": notebook["title"] or "",
        "source_path": notebook["source_path"] or "",
        "created_at": notebook["created_at"],
        "updated_at": notebook["updated_at"],
    }
```

- [ ] **Step 4: Add storage + handler methods**

In `web.py`, in `DocuTrackerWebApp`, near the notebook-attachment methods:

```python
    def _html_notebook_dir(self):
        return Path(self.config_dir) / "notebooks"

    def _html_notebook_path(self, notebook):
        return self._html_notebook_dir() / notebook["stored_filename"]

    def create_html_notebook(self, payload):
        raw_path = (payload.get("path") or "").strip()
        if not raw_path:
            raise HTTPError(400, "Notebook path is required")
        source = Path(os.path.abspath(os.path.expanduser(raw_path)))
        if not source.exists() or not source.is_file():
            raise HTTPError(400, f"No file found at {raw_path}")
        if source.suffix.lower() not in HTML_NOTEBOOK_EXTENSIONS:
            raise HTTPError(400, "Notebook must be an .html or .htm file")

        title = (payload.get("title") or "").strip() or source.name
        notebook_dir = self._html_notebook_dir()
        notebook_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}.html"
        )
        shutil.copyfile(source, notebook_dir / stored_filename)

        with database_for_path(self.db_path) as db:
            notebook_id = db.add_html_notebook(title, str(source), stored_filename)
            return {"notebook": _serialize_html_notebook(db.get_html_notebook(notebook_id))}

    def update_html_notebook(self, notebook_id, payload):
        with database_for_path(self.db_path) as db:
            notebook = db.get_html_notebook(notebook_id)
            if not notebook:
                raise HTTPError(404, f"HTML notebook {notebook_id} not found")

            title = payload.get("title") if "title" in payload else None
            if title is not None:
                if not isinstance(title, str):
                    raise HTTPError(400, "Notebook title must be a string")
                title = title.strip()
                if not title:
                    raise HTTPError(400, "Notebook title is required")

            content = payload.get("content") if "content" in payload else None
            if content is not None:
                if not isinstance(content, str):
                    raise HTTPError(400, "Notebook content must be a string")
                self._html_notebook_path(notebook).write_text(content, encoding="utf-8")

            if title is not None or content is not None:
                db.update_html_notebook(notebook_id, title=title)
            return {"notebook": _serialize_html_notebook(db.get_html_notebook(notebook_id))}

    def delete_html_notebook(self, notebook_id):
        with database_for_path(self.db_path) as db:
            notebook = db.get_html_notebook(notebook_id)
            if not notebook:
                raise HTTPError(404, f"HTML notebook {notebook_id} not found")
            stored_path = self._html_notebook_path(notebook)
            db.delete_html_notebook(notebook_id)
        if stored_path.exists():
            stored_path.unlink()
        return {"ok": True}

    def read_html_notebook_content(self, notebook_id):
        with database_for_path(self.db_path) as db:
            notebook = db.get_html_notebook(notebook_id)
            if not notebook:
                raise HTTPError(404, f"HTML notebook {notebook_id} not found")
            stored_path = self._html_notebook_path(notebook)
        if not stored_path.exists():
            raise HTTPError(404, "Notebook file is missing")
        return stored_path.read_text(encoding="utf-8")

    def stream_html_notebook(self, notebook_id, start_response):
        with database_for_path(self.db_path) as db:
            notebook = db.get_html_notebook(notebook_id)
            if not notebook:
                raise HTTPError(404, f"HTML notebook {notebook_id} not found")
            stored_path = self._html_notebook_path(notebook)
        if not stored_path.exists():
            raise HTTPError(404, "Notebook file is missing")
        return _text_response(
            start_response, 200, stored_path.read_bytes(), "text/html; charset=utf-8"
        )
```

Add the imports `shutil` (top of file, alphabetically near `os`). `uuid`, `Path`,
`datetime`/`timezone` are already imported.

- [ ] **Step 5: Wire routing in `__call__`**

In `web.py` `__call__`, alongside the other explicit routes (before the
`path.startswith("/api/notebook/")` block ~line 320):

```python
            if path == "/api/html-notebooks" and method == "POST":
                payload = self._parse_json(environ)
                return _json_response(start_response, 201, self.create_html_notebook(payload))
            if path.startswith("/api/html-notebooks/"):
                return self._handle_html_notebook_route(path, method, environ, start_response)
```

Add the route helper method (near `_handle_notebook_route`):

```python
    def _handle_html_notebook_route(self, path, method, environ, start_response):
        parts = [unquote(part) for part in path.split("/") if part]
        # parts == ["api", "html-notebooks", "<id>", optional "content"/"open"]
        if len(parts) < 3:
            raise HTTPError(404, "Not found")
        try:
            notebook_id = int(parts[2])
        except ValueError as exc:
            raise HTTPError(400, "Notebook id must be an integer") from exc

        if len(parts) == 3 and method == "PATCH":
            payload = self._parse_json(environ)
            return _json_response(start_response, 200, self.update_html_notebook(notebook_id, payload))
        if len(parts) == 3 and method == "DELETE":
            return _json_response(start_response, 200, self.delete_html_notebook(notebook_id))
        if len(parts) == 4 and parts[3] == "content" and method == "GET":
            content = self.read_html_notebook_content(notebook_id)
            return _text_response(start_response, 200, content, "text/plain; charset=utf-8")
        if len(parts) == 4 and parts[3] == "open" and method == "GET":
            return self.stream_html_notebook(notebook_id, start_response)

        raise HTTPError(404, "Not found")
```

- [ ] **Step 6: Add `html_notebooks` to `build_state`**

In `build_state` (~line 476), inside the `with database_for_path(...)` block add:

```python
            html_notebooks = [
                _serialize_html_notebook(nb) for nb in db.list_html_notebooks()
            ]
```

and add `"html_notebooks": html_notebooks,` to the returned dict.

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_web.py::test_html_notebook_add_list_delete -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/docu_tracker/web.py tests/test_web.py
git commit -m "Add HTML notebook storage, serializer, and add/list/delete routes"
```

---

## Task 3: Content read/write, open rendering, validation + isolation tests

**Files:**
- Modify: `tests/test_web.py`
- (Implementation already added in Task 2; this task verifies content/open/validation/isolation.)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web.py`:

```python
def _add_notebook(app, source, title="NB"):
    captured = {}
    def start_response(status, headers):
        captured["status"] = status
    body = app(
        build_test_environ("POST", "/api/html-notebooks",
                           payload={"path": str(source), "title": title}),
        start_response,
    )
    return json.loads(b"".join(body))["notebook"]


def test_html_notebook_content_open_and_isolation(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    source = tmp_path / "map.html"
    source.write_text("<html><body>original</body></html>")
    nb = _add_notebook(app, source)

    def start_response(status, headers):
        start_response.status = status
        start_response.headers = dict(headers)

    # edit content
    app(
        build_test_environ("PATCH", f"/api/html-notebooks/{nb['id']}",
                           payload={"content": "<html><body>edited</body></html>"}),
        start_response,
    )

    # GET content returns the edit
    content = b"".join(
        app(build_test_environ("GET", f"/api/html-notebooks/{nb['id']}/content"), start_response)
    ).decode("utf-8")
    assert "edited" in content

    # /open serves text/html
    b"".join(app(build_test_environ("GET", f"/api/html-notebooks/{nb['id']}/open"), start_response))
    assert start_response.headers["Content-Type"] == "text/html; charset=utf-8"

    # the ORIGINAL source file is untouched
    assert source.read_text() == "<html><body>original</body></html>"


def test_html_notebook_validation_and_404(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    def start_response(status, headers):
        start_response.status = status

    # missing path
    app(build_test_environ("POST", "/api/html-notebooks", payload={"title": "x"}), start_response)
    assert start_response.status.startswith("400")

    # non-existent path
    app(build_test_environ("POST", "/api/html-notebooks",
                           payload={"path": str(tmp_path / "nope.html")}), start_response)
    assert start_response.status.startswith("400")

    # non-html file
    bad = tmp_path / "note.txt"
    bad.write_text("hi")
    app(build_test_environ("POST", "/api/html-notebooks", payload={"path": str(bad)}), start_response)
    assert start_response.status.startswith("400")

    # unknown id
    app(build_test_environ("GET", "/api/html-notebooks/999/content"), start_response)
    assert start_response.status.startswith("404")
    app(build_test_environ("DELETE", "/api/html-notebooks/999"), start_response)
    assert start_response.status.startswith("404")
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/pytest tests/test_web.py -k html_notebook -v`
Expected: PASS (implementation exists from Task 2). If any fail, fix the Task 2 handlers, not the tests.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_web.py
git commit -m "Test HTML notebook content, open rendering, validation, and source isolation"
```

---

## Task 4: Frontend — tab, list, add form

**Files:**
- Modify: `src/docu_tracker/webui/index.html`
- Modify: `src/docu_tracker/webui/app.js`
- Modify: `src/docu_tracker/webui/app.css`

> The webui has no automated tests; verify by reading the existing tab/panel
> pattern first and mirroring it. Find how the existing tabs (`Table`, `Graph`,
> `Research notebook`) register their button, panel, and switch logic in
> `index.html` and `app.js`, and follow it exactly.

- [ ] **Step 1: Inspect existing tab wiring**

Run: `grep -n "tab\|panel\|notebookNotes\|switchTab\|data-tab\|renderNotebook" src/docu_tracker/webui/app.js | head -40`
and read the matching markup in `index.html`. Note the exact class names and the
function that shows/hides panels and the place `state` is populated from
`/api/state`.

- [ ] **Step 2: Add the tab button + panel markup**

In `index.html`, add a "Notebooks" tab button next to the existing tab buttons,
and a `<section>`/panel (hidden by default, matching siblings) containing:
- an **Add** form: a text input for title (placeholder "Title (optional)"), a
  text input for path (placeholder "/path/to/notebook.html"), and an **Add** button;
- an empty `<div>` list container (e.g. `id="html-notebook-list"`);
- an editor container (hidden until Edit is clicked) — built in Task 5.

- [ ] **Step 3: Load `html_notebooks` into state and render the list**

In `app.js`, where `state` is hydrated from `/api/state`, store
`state.htmlNotebooks = data.html_notebooks || []`. Add `renderHtmlNotebookList()`
that fills the list container: for each notebook a row showing `title`, a muted
"imported from `<source_path>`", `formatDateTime(updated_at)`, and three buttons:
**Open**, **Edit**, **Remove**. Call it on initial render and after any change.

- [ ] **Step 4: Wire Open and Add and Remove**

- **Open:** `window.open('/api/html-notebooks/' + id + '/open', '_blank')`.
- **Add:** POST `{ path, title }` to `/api/html-notebooks` (reuse the existing
  `api()` helper), push `result.notebook` into `state.htmlNotebooks`, clear the
  inputs, re-render, and `showFlash` on error.
- **Remove:** confirm, `DELETE /api/html-notebooks/{id}`, drop from
  `state.htmlNotebooks`, re-render. If the editor is open on that id, close it.

- [ ] **Step 5: Style the panel/list**

In `app.css`, add rules for the list rows and buttons consistent with existing
components (reuse existing button/card classes where possible).

- [ ] **Step 6: Manual verification**

Run: `.venv/bin/docu-tracker web --no-browser` then open `http://127.0.0.1:8421`.
Confirm: the Notebooks tab shows; adding the repo's
`epistemic_failure_deception_literature_map_v36.html` by path lists it; **Open**
renders the full literature map in a new tab; **Remove** clears it. Stop the server.

- [ ] **Step 7: Commit**

```bash
git add src/docu_tracker/webui/index.html src/docu_tracker/webui/app.js src/docu_tracker/webui/app.css
git commit -m "Add Notebooks tab with list, add form, open, and remove"
```

---

## Task 5: Frontend — editor (textarea + gutter + find box + preview + autosave)

**Files:**
- Modify: `src/docu_tracker/webui/index.html`
- Modify: `src/docu_tracker/webui/app.js`
- Modify: `src/docu_tracker/webui/app.css`

> Mirror the existing research-notebook editor conventions: how it debounces
> autosave (`scheduleNotebookAutosave`, `DETAIL_AUTOSAVE_DELAY_MS`), shows a
> save-status indicator (`setNotebookSaveStatus`), and flushes on navigate-away
> (`saveNotebookNote`). Reuse those patterns; do not invent new ones.

- [ ] **Step 1: Editor markup**

In `index.html`, inside the Notebooks panel, build the editor container (hidden
until Edit): a header with the title (editable input) + a save-status span + a
**Save** button + a **Refresh preview** button + a **Close** button; below it a
split: left = a find box (text input + prev/next buttons) above a line-number
gutter + `<textarea class="html-notebook-source">`; right =
`<iframe class="html-notebook-preview">`.

- [ ] **Step 2: Open the editor (lazy load)**

In `app.js`, `openHtmlNotebookEditor(id)`:
- set `state.editingHtmlNotebookId = id`, show the editor container;
- set the title input from the notebook;
- `GET /api/html-notebooks/{id}/content` (use `fetch`, read `.text()`), put it in
  the textarea, render the gutter, and set the preview iframe
  `src = '/api/html-notebooks/{id}/open'`;
- show the file size next to the status; if length is large (e.g. > 1_000_000
  chars) show a one-line "Large file — autosave is slower" hint.

- [ ] **Step 3: Autosave + manual save**

- `scheduleHtmlNotebookAutosave()` — debounced (reuse `DETAIL_AUTOSAVE_DELAY_MS`;
  if the source length is large, use a longer delay), setting status "Autosave
  pending". On fire, call `saveHtmlNotebook()`.
- `saveHtmlNotebook()` — `PATCH /api/html-notebooks/{id}` with
  `{ content, title }`; on success set status "Saved", reload the preview iframe
  (`iframe.src = iframe.src` or re-assign with a cache-busting query), and update
  the list row's `updated_at`. On failure set "Save failed" and `showFlash`.
- Bind `input` on the textarea → `scheduleHtmlNotebookAutosave()` + gutter update.
- Bind `input` on the title field → `scheduleHtmlNotebookAutosave()`.
- **Refresh preview** button → reload the iframe without waiting for save.
- **Close** button → flush (`await saveHtmlNotebook()`), hide the editor, clear
  `state.editingHtmlNotebookId`.

- [ ] **Step 4: Line-number gutter**

Add `renderHtmlNotebookGutter()` that sets the gutter content to line numbers for
`textarea.value.split("\n").length`, and sync gutter scroll to textarea scroll
(`textarea.onscroll = () => gutter.scrollTop = textarea.scrollTop`).

- [ ] **Step 5: Find box**

Add a small search over the textarea: on **Next/Prev**, find the query in
`textarea.value` from the current selection, `textarea.focus()`,
`textarea.setSelectionRange(start, end)`, and scroll the match into view by
setting `textarea.scrollTop` proportionally (lines-before / total-lines *
scrollHeight). Wrap around at the ends. No external library.

- [ ] **Step 6: Style the editor**

In `app.css`: the split layout (flex/grid), the gutter (right-aligned, muted,
non-selectable, same line-height/font as the textarea), the monospace textarea,
the find box, and the preview iframe (full height, border). Match the dark theme.

- [ ] **Step 7: Manual verification**

Run: `.venv/bin/docu-tracker web --no-browser`, open the UI, add a small test
`.html` file, click **Edit**:
- textarea loads the source; gutter shows line numbers aligned to lines;
- edit the body, wait for "Saved"; click an element via **Open** in a new tab and
  confirm the edit rendered; the original source file on disk is unchanged
  (managed copy only);
- find box jumps to matches; **Refresh preview** reloads; **Close** flushes.
Stop the server.

- [ ] **Step 8: Commit**

```bash
git add src/docu_tracker/webui/index.html src/docu_tracker/webui/app.js src/docu_tracker/webui/app.css
git commit -m "Add enhanced-textarea HTML notebook editor with preview and autosave"
```

---

## Task 6: Documentation + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README subsection**

Under the "Web UI" section, add a "Specialized HTML notebooks" subsection
covering: the Notebooks tab; adding by path; that docu-tracker imports a managed
copy (original file untouched); **Open** for a full interactive render; **Edit**
for in-app source editing with live preview and autosave; and that **Remove**
deletes the managed copy. Also add the feature to the top-level Features list.

- [ ] **Step 2: Full suite**

Run: `.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document HTML notebooks tab in README"
```

---

## Done criteria

- `.venv/bin/pytest -q` is green, including the new DB and web tests.
- The Notebooks tab can add (by path), list, open (full render), edit (with live
  preview + autosave), and remove HTML notebooks.
- Editing writes only to the managed copy under `~/.docu-tracker/notebooks/`; the
  original source file is never modified.
- README documents the feature.
