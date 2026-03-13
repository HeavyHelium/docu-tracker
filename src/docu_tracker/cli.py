import os
import sys
import click
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from docu_tracker.db import Database
from docu_tracker.config import load_config
from docu_tracker.scanner import scan_directory, compute_file_hash
from docu_tracker.extractor import extract_text
from docu_tracker.analyzer import analyze_document

MAX_WORKERS = 4

def _get_console():
    return Console(file=sys.stdout, highlight=False)


def _truncate_authors(authors_str):
    """Show first 3 authors + et al. if more."""
    if not authors_str:
        return ""
    parts = [a.strip() for a in authors_str.split(",")]
    if len(parts) <= 3:
        return ", ".join(parts)
    return ", ".join(parts[:3]) + " et al."



def parse_since(value):
    """Parse a duration string like '7d', '2w', '24h' into a UTC datetime cutoff."""
    import re
    match = re.fullmatch(r"(\d+)([hdwm])", value.strip().lower())
    if not match:
        raise click.BadParameter(f"Invalid duration '{value}'. Use e.g. 7d, 2w, 24h, 1m (months).")
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    elif unit == "w":
        delta = timedelta(weeks=amount)
    elif unit == "m":
        delta = timedelta(days=amount * 30)
    return datetime.now(timezone.utc) - delta


def get_db():
    config_dir = os.environ.get("DOCU_TRACKER_DIR", os.path.expanduser("~/.docu-tracker"))
    db_path = os.path.join(config_dir, "tracker.db")
    db = Database(db_path)
    db.initialize()
    return db


@click.group()
def cli():
    """Track and manage downloaded documents."""
    pass


@cli.group(invoke_without_command=True)
@click.pass_context
def topics(ctx):
    """List or manage topics."""
    if ctx.invoked_subcommand is None:
        db = get_db()
        con = _get_console()
        for name, desc in db.list_topics_with_descriptions():
            if desc:
                con.print(f"  [cyan]{name}[/cyan] — [dim]{desc}[/dim]")
            else:
                con.print(f"  [cyan]{name}[/cyan]")
        db.close()


@topics.command("add")
@click.argument("name")
@click.option("--description", "-d", default="", help="Description to help the LLM classify documents")
def topics_add(name, description):
    """Add a new topic."""
    db = get_db()
    db.add_topic(name, description)
    click.echo(f"Added topic: {name}")
    db.close()


@topics.command("describe")
@click.argument("name")
@click.argument("description")
def topics_describe(name, description):
    """Set or update a topic's description."""
    db = get_db()
    if name not in db.list_topics():
        click.echo(f"Topic '{name}' not found.", err=True)
        db.close()
        sys.exit(1)
    db.update_topic_description(name, description)
    click.echo(f"Updated description for '{name}'.")
    db.close()


@topics.command("remove")
@click.argument("name")
def topics_remove(name):
    """Remove a topic (reassigns documents to Other)."""
    db = get_db()
    try:
        db.remove_topic(name)
        click.echo(f"Removed topic: {name}")
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    finally:
        db.close()


@cli.command()
@click.option("--path", default=None, help="Directory to scan (default: configured downloads path)")
@click.option("--since", default=None, help="Only scan files modified within duration (e.g. 7d, 2w, 24h)")
def scan(path, since):
    """Scan downloads directory for new PDF/DOCX files."""
    db = get_db()
    config_dir = os.environ.get("DOCU_TRACKER_DIR", os.path.expanduser("~/.docu-tracker"))
    config = load_config(
        config_dir=config_dir,
        dotenv_path=os.path.join(os.getcwd(), ".env"),
    )
    api_key = config["anthropic_api_key"]
    model = config["model"]

    if not api_key:
        click.echo("Error: No API key found. Set ANTHROPIC_API_KEY or add it to .env", err=True)
        db.close()
        sys.exit(1)

    if path:
        scan_paths = [path]
    else:
        scan_paths = config.get("scan_paths", [config["downloads_path"]])

    files = []
    for sp in scan_paths:
        files.extend(scan_directory(sp))

    if since:
        cutoff = parse_since(since)
        cutoff_ts = cutoff.timestamp()
        files = [f for f in files if os.path.getmtime(f) >= cutoff_ts]

    if not files:
        click.echo("No PDF/DOCX files found.")
        db.close()
        return

    con = _get_console()
    new_count = 0
    dup_count = 0
    fail_count = 0

    # Phase 1: filter duplicates and extract text (local, fast)
    to_analyze = []
    for file_path in files:
        file_hash = compute_file_hash(file_path)
        existing = db.get_document_by_hash(file_hash)

        if existing:
            if file_path not in existing["paths"]:
                db.add_duplicate_path(file_hash, file_path)
                con.print(f"  [dim]Already tracked:[/dim] {existing['title']} [dim](new location added)[/dim]")
            else:
                con.print(f"  [dim]Already tracked:[/dim] {existing['title']}")
            dup_count += 1
            continue

        text = extract_text(file_path)
        if not text.strip():
            file_mtime = os.path.getmtime(file_path)
            mtime_iso = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()
            db.add_document(
                file_hash=file_hash,
                file_path=file_path,
                title=f"Unknown — {os.path.basename(file_path)}",
                authors="",
                summary="",
                topics=["Other"],
                file_modified_at=mtime_iso,
            )
            db.update_status(
                db.get_document_by_hash(file_hash)["id"], "needs_review"
            )
            con.print(f"  [bold red]Warning:[/bold red] No text extracted from {os.path.basename(file_path)} — marked for review")
            fail_count += 1
            continue

        to_analyze.append((file_path, file_hash, text))

    # Phase 2: LLM calls in parallel
    if to_analyze:
        topic_names = db.list_topics()
        topics_with_desc = db.list_topics_with_descriptions()

        def _analyze_one(item):
            fp, fh, txt = item
            return fp, fh, analyze_document(txt, topic_names, api_key,
                                            topics_with_descriptions=topics_with_desc,
                                            model=model)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_analyze_one, item): item for item in to_analyze}
            for future in as_completed(futures):
                file_path, file_hash, result = future.result()

                if result is None:
                    con.print(f"  [bold red]Warning:[/bold red] LLM analysis failed for {os.path.basename(file_path)} — skipping")
                    fail_count += 1
                    continue

                file_mtime = os.path.getmtime(file_path)
                mtime_iso = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()

                authors_str = ", ".join(result["authors"]) if result["authors"] else ""
                db.add_document(
                    file_hash=file_hash,
                    file_path=file_path,
                    title=result["title"],
                    authors=authors_str,
                    summary=result["summary"],
                    topics=result["topics"],
                    file_modified_at=mtime_iso,
                )
                con.print(f"  [green]+[/green] {result['title']}")
                new_count += 1

    con.print(f"\n[bold]Scan complete:[/bold] [green]{new_count} new[/green], [dim]{dup_count} duplicates[/dim], [red]{fail_count} failed[/red]")
    db.close()


@cli.command()
@click.option("--topic", default=None, help="Only reclassify docs currently in this topic")
@click.option("--id", "doc_id", default=None, type=int, help="Reclassify a single document by ID")
def reclassify(topic, doc_id):
    """Re-run topic classification on existing documents using current topic list."""
    db = get_db()
    config_dir = os.environ.get("DOCU_TRACKER_DIR", os.path.expanduser("~/.docu-tracker"))
    config = load_config(
        config_dir=config_dir,
        dotenv_path=os.path.join(os.getcwd(), ".env"),
    )
    api_key = config["anthropic_api_key"]
    model = config["model"]

    if not api_key:
        click.echo("Error: No API key found. Set ANTHROPIC_API_KEY or add it to .env", err=True)
        db.close()
        sys.exit(1)

    if doc_id:
        doc = db.get_document(doc_id)
        if not doc:
            click.echo(f"Document {doc_id} not found.")
            db.close()
            sys.exit(1)
        docs = [doc]
    else:
        docs = db.list_documents(topic=topic)

    if not docs:
        click.echo("No documents to reclassify.")
        db.close()
        return

    topic_names = db.list_topics()
    ok_count = 0
    fail_count = 0
    con = _get_console()

    # Phase 1: extract text locally
    to_analyze = []
    for doc in docs:
        file_path = doc["paths"][0] if doc["paths"] else None
        if not file_path or not os.path.exists(file_path):
            con.print(f"  [bold red]Skipped:[/bold red] {doc['title']} — file not found")
            fail_count += 1
            continue

        text = extract_text(file_path)
        if not text.strip():
            if doc["title"].startswith("Unknown") and "—" not in doc["title"]:
                new_title = f"Unknown — {os.path.basename(file_path)}"
                db.update_document(doc["id"], title=new_title)
                con.print(f"  [dim]Renamed:[/dim] {doc['title']} -> {new_title}")
            else:
                con.print(f"  [bold red]Skipped:[/bold red] {doc['title']} — no text extracted")
            fail_count += 1
            continue

        to_analyze.append((doc, text))

    # Phase 2: LLM calls in parallel
    if to_analyze:
        topics_with_desc = db.list_topics_with_descriptions()

        def _analyze_one(item):
            doc, txt = item
            return doc, analyze_document(txt, topic_names, api_key,
                                         topics_with_descriptions=topics_with_desc,
                                         model=model)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_analyze_one, item): item for item in to_analyze}
            for future in as_completed(futures):
                doc, result = future.result()

                if result is None:
                    con.print(f"  [bold red]Skipped:[/bold red] {doc['title']} — LLM analysis failed")
                    fail_count += 1
                    continue

                old_topics = ", ".join(doc["topics"])
                new_topics = ", ".join(result["topics"])
                db.set_topics(doc["id"], result["topics"])
                authors_str = ", ".join(result["authors"]) if result["authors"] else ""
                db.update_document(
                    doc["id"],
                    title=result["title"],
                    authors=authors_str,
                    summary=result["summary"],
                )
                display_title = result["title"] if doc["title"].startswith("Unknown") else doc["title"]
                con.print(f"  [green]{display_title}[/green]: {old_topics} -> {new_topics}")
                ok_count += 1

    con.print(f"\n[bold]Reclassify complete:[/bold] [green]{ok_count} updated[/green], [red]{fail_count} failed[/red]")
    db.close()


@cli.command("list")
@click.option("--topic", default=None, help="Filter by topic")
@click.option("--status", default=None, help="Filter by status (unread/reading/read/needs_review)")
@click.option("--since", default=None, help="Only show docs modified within duration (e.g. 7d, 2w, 24h)")
@click.option("--path", default=None, help="Filter by source folder")
@click.option("--week", is_flag=True, help="Group by week")
def list_docs(topic, status, since, path, week):
    """List tracked documents."""
    db = get_db()
    docs = db.list_documents(topic=topic, status=status)

    if since:
        cutoff = parse_since(since)
        cutoff_iso = cutoff.isoformat()
        docs = [d for d in docs if (d.get("file_modified_at") or "") >= cutoff_iso]

    if path:
        abs_path = os.path.abspath(os.path.expanduser(path))
        docs = [d for d in docs if any(p.startswith(abs_path) for p in d.get("paths", []))]

    if not docs:
        click.echo("No documents found.")
        db.close()
        return

    if week:
        _print_docs_by_week(docs)
    else:
        _print_docs_table(docs)
    db.close()


VALID_STATUSES = {"unread", "reading", "read", "needs_review"}

STATUS_STYLES = {
    "unread": "bold yellow",
    "reading": "bold blue",
    "read": "dim green",
    "needs_review": "bold red",
}


def _get_source(doc):
    """Get the parent folder name from the first path."""
    paths = doc.get("paths", [])
    if paths:
        return os.path.basename(os.path.dirname(paths[0]))
    return ""


def _print_docs_table(docs):
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("ID", style="dim", width=5, justify="right")
    table.add_column("Title", min_width=20, max_width=45)
    table.add_column("Authors", max_width=20)
    table.add_column("Topics", max_width=25)
    table.add_column("Status", width=12)
    table.add_column("Source", style="dim", max_width=15)
    table.add_column("Date", width=12)

    for doc in docs:
        status = doc["status"]
        style = STATUS_STYLES.get(status, "")
        status_text = Text(status, style=style)
        topics = ", ".join(doc["topics"])
        date = (doc["file_modified_at"] or "")[:10]
        table.add_row(
            str(doc["id"]),
            doc["title"] or "",
            _truncate_authors(doc["authors"]),
            topics,
            status_text,
            _get_source(doc),
            date,
        )

    _get_console().print(table)


def _print_docs_by_week(docs):
    weeks = defaultdict(list)
    for doc in docs:
        date_str = doc.get("file_modified_at", "")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str)
                monday = dt - timedelta(days=dt.weekday())
                week_key = monday.strftime("%b %d, %Y")
            except (ValueError, TypeError):
                week_key = "Unknown"
        else:
            week_key = "Unknown"
        weeks[week_key].append(doc)

    for week_key, week_docs in weeks.items():
        _get_console().print(f"\n[bold magenta]Week of {week_key}[/bold magenta]")
        _print_docs_table(week_docs)


@cli.command()
@click.argument("doc_id", type=int)
def show(doc_id):
    """Show full details of a document."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        click.echo(f"Document {doc_id} not found.")
        db.close()
        sys.exit(1)

    status = doc["status"]
    status_style = STATUS_STYLES.get(status, "")
    topics_str = ", ".join(doc["topics"])

    details = Text()
    details.append("ID:       ", style="bold")
    details.append(f"{doc['id']}\n")
    details.append("Title:    ", style="bold")
    details.append(f"{doc['title']}\n")
    details.append("Authors:  ", style="bold")
    details.append(f"{_truncate_authors(doc['authors'])}\n")
    details.append("Status:   ", style="bold")
    details.append(f"{status}\n", style=status_style)
    details.append("Topics:   ", style="bold")
    details.append(f"{topics_str}\n", style="cyan")
    details.append("Scanned:  ", style="bold")
    details.append(f"{doc['scanned_at']}\n")
    details.append("Modified: ", style="bold")
    details.append(f"{doc['file_modified_at']}\n")
    details.append("\nSummary:\n", style="bold")
    details.append(f"  {doc['summary']}\n")
    details.append("\nPaths:\n", style="bold")
    for p in doc["paths"]:
        details.append(f"  {p}\n", style="dim")

    _get_console().print(Panel(details, title=doc["title"], border_style="cyan"))
    db.close()


@cli.command("mark-read")
@click.argument("doc_id", type=int)
def mark_read(doc_id):
    """Mark a document as read."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        click.echo(f"Document {doc_id} not found.")
        db.close()
        sys.exit(1)
    db.update_status(doc_id, "read")
    click.echo(f"Marked '{doc['title']}' as read.")
    db.close()


@cli.command("mark-unread")
@click.argument("doc_id", type=int)
def mark_unread(doc_id):
    """Mark a document as unread."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        click.echo(f"Document {doc_id} not found.")
        db.close()
        sys.exit(1)
    db.update_status(doc_id, "unread")
    click.echo(f"Marked '{doc['title']}' as unread.")
    db.close()


@cli.command("mark-reading")
@click.argument("doc_id", type=int)
def mark_reading(doc_id):
    """Mark a document as currently reading."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        click.echo(f"Document {doc_id} not found.")
        db.close()
        sys.exit(1)
    db.update_status(doc_id, "reading")
    click.echo(f"Marked '{doc['title']}' as reading.")
    db.close()


@cli.command()
@click.argument("doc_id", type=int)
@click.argument("topic_name")
def tag(doc_id, topic_name):
    """Add a topic to a document."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        click.echo(f"Document {doc_id} not found.")
        db.close()
        sys.exit(1)
    if topic_name not in db.list_topics():
        click.echo(f"Topic '{topic_name}' does not exist. Add it first with: docu-tracker topics add \"{topic_name}\"")
        db.close()
        sys.exit(1)
    db.tag_document(doc_id, topic_name)
    click.echo(f"Tagged '{doc['title']}' with '{topic_name}'.")
    db.close()


@cli.command()
@click.argument("doc_id", type=int)
@click.argument("topic_name")
def untag(doc_id, topic_name):
    """Remove a topic from a document."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        click.echo(f"Document {doc_id} not found.")
        db.close()
        sys.exit(1)
    db.untag_document(doc_id, topic_name)
    click.echo(f"Removed '{topic_name}' from '{doc['title']}'.")
    db.close()
