# docu-tracker

A CLI tool that automatically tracks PDF and DOCX files across your folders, extracts metadata using an LLM API call, and organizes them by topic with a reading queue.

Stop losing track of papers and documents. Scan once, find anything instantly.

## Features

- **Auto-extraction** — titles, authors, and summaries pulled from PDFs and DOCX files via LLM API (~$0.004/document with default model)
- **Topic classification** — documents auto-classified into customizable categories with descriptions that guide the LLM
- **Multi-folder scanning** — scan multiple directories, with a Source column showing where each document came from
- **Duplicate detection** — SHA-256 hashing tracks files even if copied to multiple locations
- **Reading queue** — mark documents as unread, reading, or read
- **Flexible filtering** — by topic, status, source folder, date range, or week
- **Reclassify on demand** — update topics after adding new categories, without rescanning files
- **Configurable model** — defaults to a fast, cheap model, but swap in any Anthropic model
- **Parallel processing** — LLM calls run concurrently (4 workers) for fast bulk scanning
- **Rich terminal UI** — colored tables, status indicators, and detailed panels
- **Local web UI** — edit document details, status, and topics in a browser, open files directly, and run scan/rescan workflows

## Installation

```bash
git clone https://github.com/HeavyHelium/download-docu-tracker.git
cd download-docu-tracker
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Setup

### 1. API Key

docu-tracker uses the Anthropic API for document analysis. Get an API key from [console.anthropic.com](https://console.anthropic.com/).

Set it in one of three ways (in priority order):

```bash
# Option A: Environment variable
export ANTHROPIC_API_KEY=sk-ant-...

# Option B: .env file in your project directory
cp .env.example .env
# Edit .env and add your key

# Option C: Config file
mkdir -p ~/.docu-tracker
cat > ~/.docu-tracker/config.yaml << EOF
anthropic_api_key: sk-ant-...
EOF
```

Using `~/.docu-tracker/config.yaml` is the most reliable option if you want `docu-tracker web` to work from any directory.
Make sure the file is valid YAML, for example:

```yaml
anthropic_api_key: "sk-ant-..."
scan_paths:
  - ~/Downloads
```

### 2. First Scan

```bash
# Scan your Downloads folder (default)
docu-tracker scan

# Or scan a specific folder
docu-tracker scan --path ~/Papers

# Only scan files from the last week
docu-tracker scan --since 7d
```

## Usage

### Scanning Documents

```bash
docu-tracker scan                          # scan all configured paths
docu-tracker scan --path ~/Papers          # scan a specific folder
docu-tracker scan --since 7d               # only files modified in last 7 days
docu-tracker scan --since 2w --path ~/docs # combine both
```

Duration formats: `24h` (hours), `7d` (days), `2w` (weeks), `1m` (months).

To scan multiple folders by default, configure `scan_paths` in your config:

```yaml
# ~/.docu-tracker/config.yaml
scan_paths:
  - ~/Downloads
  - ~/Papers
  - ~/Desktop/reports
```

### Listing Documents

```bash
docu-tracker list                          # all documents
docu-tracker list --topic "Work"            # filter by topic
docu-tracker list --status unread          # filter by status
docu-tracker list --since 7d               # recent documents only
docu-tracker list --path ~/Downloads       # filter by source folder
docu-tracker list --week                   # group by week
```

The table includes a **Source** column showing which folder each document came from.

Statuses: `unread` (yellow), `reading` (blue), `read` (green), `needs_review` (red).

### Web UI

```bash
docu-tracker web
```

This now opens your browser automatically at `http://127.0.0.1:8421`.
When the last UI tab is closed, the local server shuts itself down shortly after, so you can usually rerun `docu-tracker web` without manually killing an old process.

If you want to skip that:

```bash
docu-tracker web --no-browser
```

The web UI provides:

- editable document table with quick status changes
- side-panel editing for title, authors, summary, and topics
- topic/category management with add, rename, describe, and delete
- direct document opening from the browser UI
- scan controls for configured paths or a selected path
- metadata rescan for all documents or a single document

If you want the web UI to work no matter which directory you launch it from, prefer storing your key and default scan paths in `~/.docu-tracker/config.yaml` instead of relying on a repo-local `.env`.

The `Since` field applies to both bulk actions:

- `Scan Files` only discovers files modified within that time window
- `Rescan Metadata` only refreshes tracked documents whose `file_modified_at` falls within that time window

The per-document `Rescan` button ignores `Since` and only refreshes that one record.

### Viewing Details

```bash
docu-tracker show 1                        # full details in a panel
```

Shows title, authors, summary, topics, paths, and timestamps.

### Reading Queue

```bash
docu-tracker mark-reading 3               # currently reading
docu-tracker mark-read 3                  # finished
docu-tracker mark-unread 3                # back to queue
docu-tracker list --status reading         # what am I reading?
```

### Tagging

```bash
docu-tracker tag 1 "Finance"               # add a topic
docu-tracker untag 1 "Other"              # remove a topic
```

## Customizing Topics

docu-tracker ships with default topics, but the real power is defining your own with descriptions that guide the AI classification.

### Viewing Topics

```bash
docu-tracker topics
```

Shows all topics with their descriptions:

```
  Academic — University and education — coursework, syllabi, transcripts...
  Finance — Financial documents — invoices, receipts, tax forms...
  Other — Documents that don't fit any other category
  Personal — Personal documents — IDs, medical records, travel...
  Work — Work-related documents — reports, memos, presentations...
```

### Adding Topics

```bash
# Add with a description (recommended — helps the LLM classify accurately)
docu-tracker topics add "Research" -d "Academic research papers, journal articles, and conference proceedings"

# Add without description
docu-tracker topics add "Personal"
```

### Updating Descriptions

```bash
docu-tracker topics describe "Academic" "University coursework, syllabi, admin forms, enrollment documents. NOT research papers or journal articles"
```

Good descriptions tell the LLM what **is** and **isn't** in scope. The "NOT research papers" part above prevents AI papers from being tagged as Academic just because they come from universities.

### Removing Topics

```bash
docu-tracker topics remove "Personal"      # documents reassigned to Other
```

The "Other" topic cannot be removed.

### Reclassifying After Changes

After adding or updating topics, reclassify existing documents:

```bash
docu-tracker reclassify                    # all documents
docu-tracker reclassify --id 5             # single document
docu-tracker reclassify --topic "Other"    # only docs currently in Other
```

Reclassify also updates titles, authors, and summaries from the LLM.

## Configuration

docu-tracker looks for configuration in this priority order:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Environment variables | `ANTHROPIC_API_KEY`, `DOCU_TRACKER_MODEL` |
| 2 | `.env` file in current directory | `ANTHROPIC_API_KEY=sk-ant-...` |
| 3 | `~/.docu-tracker/config.yaml` | `anthropic_api_key: sk-ant-...` |
| 4 | Defaults | `~/Downloads`, `claude-haiku-4-5-20251001` |

### Config file example

```yaml
# ~/.docu-tracker/config.yaml
anthropic_api_key: "sk-ant-..."
model: claude-haiku-4-5-20251001  # or any Anthropic model
scan_paths:
  - ~/Downloads
  - ~/Papers
```

### Changing the Model

The default model is `claude-haiku-4-5-20251001` (cheapest). To use a more capable model:

```bash
# Via environment variable
export DOCU_TRACKER_MODEL=claude-sonnet-4-5-20241022

# Via config file
# model: claude-sonnet-4-5-20241022

# Via .env file
# DOCU_TRACKER_MODEL=claude-sonnet-4-5-20241022
```

## Cost

docu-tracker defaults to `claude-haiku-4-5-20251001`, one of the cheapest models available:

| Operation | Cost |
|-----------|------|
| Per document (scan) | ~$0.004 |
| Per document (reclassify) | ~$0.004 |
| 100 documents | ~$0.40 |
| 1000 documents | ~$4.00 |

## Supported Formats

- **PDF** — text extracted from first 4 pages using PyMuPDF
- **DOCX** — text extracted up to 5000 characters using python-docx

Files with no extractable text (scanned images, encrypted PDFs) are tracked with status `needs_review`.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_cli.py -v
```

## Architecture

```
src/docu_tracker/
  cli.py        # Click commands, rich UI, parallel processing
  db.py         # SQLite database (documents, topics, paths)
  scanner.py    # File discovery and SHA-256 hashing
  extractor.py  # PDF/DOCX text extraction
  analyzer.py   # LLM API integration with tool_use
  config.py     # Configuration loading (env > .env > yaml)
```

## License

MIT
