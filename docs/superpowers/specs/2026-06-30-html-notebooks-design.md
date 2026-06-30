# HTML Notebooks — Design

**Date:** 2026-06-30
**Status:** Approved

## Goal

Let docu-tracker catalog and edit standalone, self-contained HTML notebooks —
documents like a literature map (`epistemic_failure_deception_literature_map_v36.html`)
that the user authors elsewhere. From a new **Notebooks** tab in the web UI the
user can:

- **Add** a notebook by pointing at an existing `.html`/`.htm` file on disk.
- **Open** it for a full interactive render in a new browser tab.
- **Edit** its HTML source in-app with a live preview, with changes persisted.
- **Remove** it from the catalog.

This is distinct from the existing **Research notebook** tab (markdown synthesis
notes stored in `notebook_notes`). The two are kept separate.

## Approach

### Storage: import a managed copy

On add, docu-tracker reads the source file once and **copies** it into its own
storage at `~/.docu-tracker/notebooks/` (the same convention as
`notebook_attachments/`). From then on the app reads and writes only the managed
copy. **The user's original source file is never written.**

This was chosen over reference-in-place because the feature includes editing:
saving edits back to the original authored file (with no undo) is unsafe. A
managed copy makes the original immutable and the edited document fully
app-owned. `source_path` is retained only for display ("imported from …").

### Editing surface: enhanced textarea, no new dependencies

The app is a vanilla, no-build, offline SPA. The editor stays a plain
`<textarea>` — no code-editor library — augmented with cheap ergonomics:

- monospace font and a **line-number gutter**;
- a **find box** (custom, with next/prev) that scrolls the textarea to matches,
  since native browser find does not search textarea contents;
- **lazy load** — source loads into the editor only when the user clicks Edit;
- **size-aware debounced autosave** — autosave debounces; for large files the
  debounce lengthens and the file size plus a one-line "large file" hint show.

A clean seam is left so a real code editor (e.g. CodeMirror) could be swapped in
later if the textarea proves too limiting.

### Preview: render the saved copy, not live source

The preview is an `<iframe>` whose `src` is the notebook's `/open` endpoint, so
it renders the **saved** managed copy. It reloads on save and via a manual
**Refresh preview** button. It deliberately does **not** use `<iframe srcdoc>`
of the in-progress text, because re-parsing and re-running a multi-megabyte
document's scripts on every keystroke is the dominant performance cost. Rendering
from the saved file removes that cost and guarantees the preview matches disk.

## Data layer (`db.py`)

New table `html_notebooks`:

| column            | notes                                             |
|-------------------|---------------------------------------------------|
| `id`              | integer primary key                               |
| `title`           | display title                                     |
| `source_path`     | original path the file was imported from (display)|
| `stored_filename` | basename of the managed copy in the notebooks dir |
| `created_at`      | ISO timestamp                                     |
| `updated_at`      | ISO timestamp, bumped on content/title change     |

Methods (mirroring the `notebook_notes` helpers):

- `add_html_notebook(title, source_path, stored_filename)` → id
- `list_html_notebooks()` → rows ordered by `updated_at DESC, id DESC`
- `get_html_notebook(id)` → row or `None`
- `update_html_notebook(id, title=None)` → bumps `updated_at`
  (file content is written to disk by the web layer, not stored in the DB)
- `delete_html_notebook(id)`

## Storage layer

- Managed copies live in `~/.docu-tracker/notebooks/` (created on demand).
- On add: validate the source path — expand to absolute, must exist, be a file,
  and end in `.html`/`.htm`. Copy to `<UTCtimestamp>-<uuid>.html`.
- On delete: remove the DB row **and** the managed copy file (no orphans).
- The original `source_path` is read exactly once, at import.

## Web layer (`web.py`) — endpoints under `/api/html-notebooks`

All lookups are by **integer id**; no file path ever appears in a URL, so there
is no path-traversal surface (consistent with the existing attachment streaming).

- `POST /api/html-notebooks` — add. Body `{path, title?}`. Title defaults to the
  source filename when blank. Validates path as above. → `201` with the
  serialized notebook.
- `GET /api/html-notebooks/{id}/content` — raw managed-copy HTML as
  `text/plain; charset=utf-8`, for the editor to load.
- `PATCH /api/html-notebooks/{id}` — body `{title?, content?}`. Writes `content`
  to the managed copy and/or updates the title; bumps `updated_at`. → serialized
  notebook.
- `GET /api/html-notebooks/{id}/open` — serve the managed copy as
  `text/html; charset=utf-8` (inline) for full rendering.
- `DELETE /api/html-notebooks/{id}` — un-register and delete the managed copy.

`build_state()` includes an `html_notebooks` list (serialized: `id`, `title`,
`source_path`, `updated_at`, `created_at`) so the SPA loads them with initial
state, exactly as `notebook_notes` are.

Serializer `_serialize_html_notebook(row)` returns the fields above. Errors use
the existing `HTTPError` pattern: `400` for missing/blank path, non-existent
path, non-file, or non-`.html`/`.htm`; `404` for an unknown id or a managed copy
that has gone missing on disk.

## Frontend (`index.html`, `app.css`, `app.js`)

A new **Notebooks** tab button + panel, following the existing tab/panel pattern.

**List view:** each registered notebook shows its title, "imported from
`<source_path>`", and the last-updated time, with three actions:

- **Open** → `window.open('/api/html-notebooks/{id}/open', '_blank')`.
- **Edit** → reveals the editor for that notebook (lazy-loads content).
- **Remove** → confirm, then `DELETE`, then drop it from `state.htmlNotebooks`.

**Add form:** title (optional) + path inputs and an **Add** button → `POST`.

**Editor (split view, mirroring the research-notebook layout):**

- Left: the enhanced `<textarea>` (monospace, line-number gutter, find box),
  loaded via the `/content` endpoint on first open.
- Right: the `<iframe src=…/open>` preview, reloaded on save and on **Refresh
  preview**.
- A save-status indicator and manual **Save**, reusing the existing
  autosave/flush conventions (debounced autosave + flush before navigating away
  from the editor). Autosave/Save sends `{content}` (and `{title}` when changed)
  via `PATCH`.

## Tests (`tests/test_web.py`)

Using the existing `build_test_environ` harness and a temp `config_dir`:

- **Add → list → delete** round-trip; the managed copy exists after add and is
  gone after delete.
- **Content GET/PATCH** round-trip: PATCH new content, GET returns it.
- **`/open`** serves the managed copy as `text/html`.
- **Validation:** missing path → 400; non-existent path → 400; a non-file path →
  400; a non-`.html` file → 400; unknown id on GET/PATCH/open/delete → 404.
- **Isolation:** after editing, the managed copy reflects the edit but the
  **original source file on disk is unchanged**.

## Documentation

A short "Specialized HTML notebooks" subsection in the README Web UI section
covering: adding by path, that a managed copy is imported (original untouched),
opening for full render, editing with live preview, and that Remove deletes the
managed copy.

## Out of scope (YAGNI)

- Descriptions, topic links, or filtering on HTML notebooks (title + path only).
- A real code-editor library / syntax highlighting (seam left for later).
- Visual/WYSIWYG editing of rendered HTML.
- Reference-in-place editing or writing back to the original source file.
