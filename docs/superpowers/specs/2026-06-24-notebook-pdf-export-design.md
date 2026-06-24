# Notebook PDF Export & Note Topics — Design

**Date:** 2026-06-24
**Status:** Approved

## Goal

Two related notebook enhancements, built together:

1. **PDF export** — export notebook note previews to PDF from the web UI. Each
   note's rendered markdown (title, topics, body, embedded images, and linked
   files) should be exportable, either one note at a time or all notes at once.
2. **Note topics** — tag notes with topics drawn from the same topic set used
   for document annotation (multiple topics per note, like documents). Topics
   appear in the editor, on note-list cards, and beneath the title in exports.

## Approach

Browser print-to-PDF. The existing client-side `renderMarkdown(value)` already
produces the preview HTML shown in the editor. We reuse it to build a clean,
standalone print document, open it in a new same-origin window, and call
`window.print()`. The user saves to PDF via the OS print dialog.

This adds **no Python changes and no new dependencies**. All work is in
`src/docu_tracker/webui/app.js` (logic + an inlined print stylesheet) plus two
buttons and a small CSS rule for button styling in `app.css`.

### Why browser print over server-side generation

- Zero new dependencies; pixel-matches the on-screen preview semantics because
  it reuses the same `renderMarkdown`.
- No need to reimplement markdown rendering in Python.
- Trade-off accepted: the user picks "Save as PDF" in the OS dialog rather than
  getting a direct file download.

### Why inline the print CSS instead of reusing app.css

The print window is a separate document. The on-screen preview styling in
`app.css` is tied to the app's dark theme, which is wrong for paper. A small
dedicated light print stylesheet keeps the export self-contained and readable.

## Components (all in `app.js`)

- `buildNoteExportSection(note)` — pure function. Returns an
  `<article class="pdf-note">…</article>` HTML string containing:
  - `<h1>` with the note title (escaped).
  - The note's topics rendered as small chip-style text directly beneath the
    title (from `note.topics`; omitted if the note has none).
  - The rendered body from `renderMarkdown(note.body)`.
  - A "Linked files" section from `noteReferencedDocuments(note)`, listing each
    document's title and authors/source as static text (no buttons).
  - No timestamps.
- `openNotePrintWindow(docTitle, sectionsHtml)` — opens a blank same-origin
  window via `window.open()`, writes a complete standalone HTML document with an
  inlined print stylesheet (readable typography, sane margins,
  `img { max-width: 100% }`, and `.pdf-note { break-after: page }` between
  notes), waits for images to finish loading, then calls `print()`. If the popup
  is blocked, it surfaces a `showFlash` error.
- `exportNoteToPdf()` — builds one section from the selected note and opens the
  print window. Guards against no selected note (flash + skip).
- `exportAllNotesToPdf()` — maps every note in `state.notebookNotes` into
  sections joined with page breaks and opens one print window. Guards against
  zero notes (flash + skip).

## UI changes

- Per-note **"Export PDF"** button in the editor header actions
  (`renderNotebookEditor`, alongside Save/Delete).
- **"Export all"** button in the notebook list panel heading
  (`renderNotebook`, alongside "New").
- Both wired through the existing `els.notebookContainer` click handler.

## Data flow

1. User clicks Export PDF / Export all.
2. Handler calls `exportNoteToPdf()` / `exportAllNotesToPdf()`.
3. Sections are built via `buildNoteExportSection`.
4. `openNotePrintWindow` writes the standalone doc, awaits images, prints.
5. User saves as PDF via the OS dialog.

## Edge cases / error handling

- **Popup blocked:** `window.open` returns null → flash an error explaining the
  popup was blocked.
- **No note selected / no notes:** flash a message and skip.
- **Images not yet loaded:** await image load before printing so they aren't
  blank in the output. Data-URL and same-origin attachment images both print.

## Note topics

Tag notes with topics from the same topic set used for documents. A note can
have multiple topics, mirroring the existing `document_topics` many-to-many.

### Data model (`db.py`)

- New `notebook_note_topics(note_id, topic_id)` join table:
  `PRIMARY KEY (note_id, topic_id)`, `FOREIGN KEY (note_id) REFERENCES
  notebook_notes(id) ON DELETE CASCADE`, `FOREIGN KEY (topic_id) REFERENCES
  topics(id)`. Created in the same schema-init block as `notebook_note_documents`.
- Links are stored by `topic_id` (not name), so renaming a topic keeps note
  links intact — identical to `document_topics`.
- `_topics_for_note(note_id)` — returns topic **names**, joined to `topics` and
  ordered by name (parallels how `get_document` resolves topics).
- `get_notebook_note` includes `"topics": self._topics_for_note(...)`.
- `set_notebook_note_topics(note_id, topic_names, commit=True)` — deletes
  existing rows for the note, then inserts a row per topic **that already
  exists** (resolve name→id; silently skip unknown names, matching
  `tag_document`). Mirrors `set_notebook_note_documents`.
- `add_notebook_note` and `update_notebook_note` gain an optional `topics`
  param, threaded through exactly like `document_ids` (including the
  `updated_at` bump when topics change but no scalar fields do).

### Web layer (`web.py`)

- `_serialize_notebook_note` gains `"topics": note["topics"]`.
- `create_notebook_note` / `update_notebook_note` parse an optional `topics`
  list from the payload via a `_clean_notebook_topics` helper that validates it
  is a list of strings (parallel to `_clean_notebook_document_ids`) and pass it
  to the DB. Topics already ship to the frontend in the initial state payload,
  so no new endpoint is needed.

### Frontend (`app.js`)

- A topic selector in `renderNotebookEditor`, rendered as checkbox chips from
  `state.topics`, each checked when present in `note.topics` — consistent with
  the existing document topic-filter checkboxes.
- `currentNotebookPayload()` includes the selected `topics`. Source of truth
  follows the existing reference-checkbox flow: toggling a topic checkbox mutates
  `note.topics` on the in-memory note object (like `setNotebookReference` does
  for `document_ids`), and the payload reads from there.
- Autosave is **not** automatic for the new checkboxes: the existing `change`
  handler early-returns unless the target matches `input[data-note-ref-id]`, and
  the `input` handler early-returns unless it matches `#notebook-title,
  #notebook-body`. Add a branch to the `change` handler for the topic checkboxes
  that updates `note.topics` and triggers the save path.
- Note-list cards (`renderNotebook`) surface the note's topics.

## Testing

Python (added to the existing suite):

- `test_db`: setting and reading a note's topics round-trips; unknown topic
  names are skipped; renaming a topic preserves the note's link.
- `test_web`: `topics` round-trips through note create and update serialization.

Frontend logic runs in the browser and isn't covered by the Python suite.
`buildNoteExportSection` is a pure note→string function and is the only export
piece with real logic, so it's easy to reason about. Verification is manual:
run `docu-tracker web`, create a note with markdown + an image + a linked file +
a topic, and confirm topic editing autosaves and that both Export PDF and
Export all produce a correct print preview with the topic shown.
