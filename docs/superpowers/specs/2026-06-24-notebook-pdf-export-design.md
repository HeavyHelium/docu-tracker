# Notebook PDF Export — Design

**Date:** 2026-06-24
**Status:** Approved

## Goal

Let users export notebook note previews to PDF from the web UI. Each note's
rendered markdown (title, body, embedded images, and linked files) should be
exportable, either one note at a time or all notes at once.

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

## Testing

The notebook UI logic runs in the browser and isn't covered by the Python test
suite. `buildNoteExportSection` is a pure note→string function and is the only
piece with real logic, so it's easy to reason about. Verification is manual:
run `docu-tracker web`, create a note with markdown + an image + a linked file,
and confirm both Export PDF and Export all produce a correct print preview.
