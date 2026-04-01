import html
import json
import csv
import io
import os
import shutil
import shlex
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich import box
from rich.console import Console
from rich.table import Table

from clipd.utils import fmt_time, fmt_size, _to_png

console = Console()

PLIST_NAME = "com.clipd.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
LOG_PATH = Path.home() / ".clipd" / "daemon.log"

MENUBAR_PLIST_NAME = "com.clipd.menubar"
MENUBAR_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{MENUBAR_PLIST_NAME}.plist"


# ── helpers ──────────────────────────────────────────────────────────────────

def get_db():
    from clipd.db import Database
    return Database()


def _get_db_path():
    from clipd.db import DB_PATH
    return DB_PATH


def clip_preview(row, max_len: int = 60) -> str:
    if row["type"] == "text":
        text = row["text_content"] or ""
        text = text.replace("\n", " ").replace("\t", " ")
        return (text[:max_len] + "…") if len(text) > max_len else text
    ocr = row["ocr_text"] or ""
    if ocr:
        ocr = ocr.replace("\n", " ")
        preview = (ocr[:max_len] + "…") if len(ocr) > max_len else ocr
        return f"[dim]OCR: {preview}[/dim]"
    return "[dim][image][/dim]"


def highlight_snippet(raw: Optional[str]) -> str:
    """Convert <b>…</b> FTS5 markers to rich markup."""
    if not raw:
        return ""
    return raw.replace("<b>", "[bold yellow]").replace("</b>", "[/bold yellow]")


def _detect_terminal() -> str:
    """Return terminal type: 'iterm2' | 'kitty' | 'wezterm' | 'ghostty' | 'cmux' | 'unknown'."""
    prog = os.environ.get("TERM_PROGRAM", "")
    term = os.environ.get("TERM", "")
    # cmux sets TERM_PROGRAM=ghostty but doesn't support inline image protocols
    if os.environ.get("CMUX_SURFACE_ID") or os.environ.get("CMUX_WORKSPACE_ID"):
        return "cmux"
    if prog == "iTerm.app":
        return "iterm2"
    if prog == "WezTerm":
        return "wezterm"
    if prog == "ghostty" or term == "xterm-ghostty":
        return "ghostty"
    if term == "xterm-kitty" or os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    return "unknown"


# NSBitmapImageFileType enum: TIFF=0, BMP=1, GIF=2, JPEG=3, PNG=4, JPEG2000=5
_NS_FORMATS = {
    "png":  4,
    "jpg":  3,
    "jpeg": 3,
    "tiff": 0,
    "tif":  0,
    "bmp":  1,
    "gif":  2,
    "jp2":  5,
}
# Formats handled via CGImageDestination (UTI string)
# Try macOS 14+ "public.webp" first, fall back to legacy UTI
_CG_FORMATS = {
    "webp": "public.webp",
    "heic": "public.heic",
    "heif": "public.heif",
    "avif": "public.avif",
}
SUPPORTED_FORMATS = sorted(set(_NS_FORMATS) | set(_CG_FORMATS) - {"jpeg", "tif", "heif"})


def convert_image(image_bytes: bytes, fmt: str) -> bytes:
    """Convert image bytes to the requested format. Raises ValueError on failure."""
    fmt = fmt.lower().lstrip(".")

    if fmt in _NS_FORMATS:
        try:
            from AppKit import NSBitmapImageRep, NSImage
            from Foundation import NSData
            ns_data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
            img = NSImage.alloc().initWithData_(ns_data)
            if not img:
                raise ValueError("Failed to load image")
            rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
            if not rep:
                raise ValueError("Failed to create bitmap representation")
            props = {5: 0.92} if fmt in ("jpg", "jpeg") else {}  # NSImageCompressionFactor
            data = rep.representationUsingType_properties_(_NS_FORMATS[fmt], props)
            if not data:
                raise ValueError(f"Conversion to {fmt} failed")
            return bytes(data)
        except ImportError:
            raise ValueError("pyobjc-framework-Cocoa required")

    if fmt in _CG_FORMATS:
        try:
            import Quartz
            from Foundation import NSData, NSMutableData
            ns_data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
            source = Quartz.CGImageSourceCreateWithData(ns_data, None)
            if not source:
                raise ValueError("Failed to load image source")
            cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
            if not cg_image:
                raise ValueError("Failed to decode image")

            # Try primary UTI, then legacy fallback for webp
            utis = [_CG_FORMATS[fmt]]
            if fmt == "webp":
                utis.append("org.webmproject.webp")

            for uti in utis:
                output = NSMutableData.data()
                dest = Quartz.CGImageDestinationCreateWithData(output, uti, 1, None)
                if dest:
                    Quartz.CGImageDestinationAddImage(dest, cg_image, None)
                    if Quartz.CGImageDestinationFinalize(dest) and len(output) > 0:
                        return bytes(output)

            raise ValueError(f"Format '{fmt}' not supported on this macOS version")
        except ImportError:
            raise ValueError("pyobjc-framework-Quartz required")

    raise ValueError(f"Unsupported format: {fmt}. Supported: {', '.join(SUPPORTED_FORMATS)}")


def display_image_inline(image_bytes: bytes) -> bool:
    """Display image inline in terminal. Returns True if displayed."""
    import base64
    import tempfile

    terminal = _detect_terminal()

    # iTerm2 / WezTerm / Ghostty — ESC]1337 inline image protocol
    if terminal in ("iterm2", "wezterm", "ghostty"):
        png = _to_png(image_bytes)
        b64 = base64.b64encode(png).decode()
        size = len(png)
        sys.stdout.write(
            f"\033]1337;File=inline=1;size={size};width=auto;preserveAspectRatio=1:{b64}\a\n"
        )
        sys.stdout.flush()
        return True

    # Kitty — icat kitten (always needs a real file)
    if terminal == "kitty":
        png = _to_png(image_bytes)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png)
            tmp = f.name
        try:
            subprocess.run(["kitty", "+kitten", "icat", tmp], check=False)
        finally:
            os.unlink(tmp)
        return True

    return False


def _find_exe(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        clipd = shutil.which("clipd")
        if clipd:
            candidate = Path(clipd).parent / name
            if candidate.exists():
                return str(candidate)
    return exe or name


def _daemon_exe() -> str:
    return _find_exe("clipd-daemon")


def _menubar_exe() -> str:
    return _find_exe("clipd-menubar")


def _require_row(db, id_: int):
    row = db.get(id_)
    if not row:
        console.print(f"[red]ID {id_} not found[/red]")
        sys.exit(1)
    return row


# ── CLI root ──────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="clipd")
def cli():
    """clipd — macOS clipboard history manager"""


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.option("--limit", "-n", default=20, show_default=True, help="Number of items to show")
@click.option("--type", "-t", "clip_type", type=click.Choice(["text", "image"]), help="Filter by type")
@click.option("--tag", help="Filter by tag")
@click.option("--pinned", is_flag=True, help="Show pinned items only")
@click.option("--full", "-f", is_flag=True, help="Show full content instead of preview")
def list_cmd(limit, clip_type, tag, pinned, full):
    """List recent clipboard history."""
    rows = get_db().list(limit=limit, clip_type=clip_type, tag=tag, pinned_only=pinned)
    if not rows:
        console.print("[dim]No history[/dim]")
        return

    if full:
        for row in rows:
            pin = "📌 " if row["pinned"] else ""
            type_label = "[cyan]text[/cyan]" if row["type"] == "text" else "[magenta]image[/magenta]"
            tags_str = " ".join(f"[yellow]#{g}[/yellow]" for g in (row["tags"] or "").split(",") if g)
            header = f"[dim]─── {pin}ID {row['id']} · {type_label}"
            if tags_str:
                header += f" · {tags_str}"
            header += f" · {fmt_time(row['created_at'])} ───[/dim]"
            console.print(header)
            if row["type"] == "text":
                console.print(row["text_content"] or "")
            else:
                shown = display_image_inline(bytes(row["content"]))
                if not shown:
                    console.print(f"[dim][image — use clipd open {row['id']}][/dim]")
                ocr = row["ocr_text"]
                if ocr:
                    console.print(f"[dim]OCR: {ocr}[/dim]")
            console.print()
        return

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("ID", style="dim", width=6)
    t.add_column("Type", width=7)
    t.add_column("Preview", min_width=42)
    t.add_column("Tags", width=14)
    t.add_column("Time", width=12)

    for row in rows:
        pin = "📌 " if row["pinned"] else ""
        type_label = "[cyan]text[/cyan]" if row["type"] == "text" else "[magenta]image[/magenta]"
        tags_str = " ".join(f"[yellow]#{g}[/yellow]" for g in (row["tags"] or "").split(",") if g)
        t.add_row(f"{pin}{row['id']}", type_label, clip_preview(row), tags_str, fmt_time(row["created_at"]))

    console.print(t)


# ── search ────────────────────────────────────────────────────────────────────

@cli.command("search")
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", "-n", default=20, show_default=True, help="Maximum results")
def search_cmd(query, limit):
    """Full-text search across text and OCR content. Multiple words are AND-ed."""
    query_str = " ".join(query)
    rows = get_db().search(query_str, limit=limit)
    if not rows:
        console.print(f'[dim]No results for "{query_str}"[/dim]')
        return

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("ID", style="dim", width=6)
    t.add_column("Type", width=7)
    t.add_column("Match", min_width=50)
    t.add_column("Time", width=12)

    for row in rows:
        type_label = "[cyan]text[/cyan]" if row["type"] == "text" else "[magenta]image[/magenta]"
        if row["snippet_text"]:
            snippet = highlight_snippet(row["snippet_text"])
        elif row["snippet_ocr"]:
            snippet = "OCR: " + highlight_snippet(row["snippet_ocr"])
        else:
            snippet = clip_preview(row)
        t.add_row(str(row["id"]), type_label, snippet, fmt_time(row["created_at"]))

    console.print(t)


# ── show ──────────────────────────────────────────────────────────────────────

@cli.command("show")
@click.argument("id", type=int)
@click.option("--raw", "-r", is_flag=True, help="Output raw content only (pipe-friendly)")
@click.option("--output", "-o", type=click.Path(), help="Save image to file (format auto-detected from extension)")
@click.option("--format", "-f", "fmt", help=f"Image output format: {', '.join(SUPPORTED_FORMATS)}")
def show_cmd(id, raw, output, fmt):
    """Show full details of a clip. Long text opens in a pager automatically.

    \b
    Save image examples:
      clipd show 42 -o screenshot.png
      clipd show 42 -o photo.jpg
      clipd show 42 -o image.webp
      clipd show 42 -o out.heic
    """
    row = _require_row(get_db(), id)

    # ── save image to file ────────────────────────────────────────────────────
    if output:
        if row["type"] != "image":
            console.print("[red]--output is only supported for image clips[/red]")
            sys.exit(1)
        out_path = Path(output)
        # Determine format: explicit --format > file extension > default png
        target_fmt = (fmt or out_path.suffix or "png").lower().lstrip(".")
        if not target_fmt:
            target_fmt = "png"
        try:
            data = convert_image(bytes(row["content"]), target_fmt)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)
        out_path.write_bytes(data)
        console.print(f"[green]Saved[/green] {out_path} ({fmt_size(len(data))})")
        return

    # ── raw text output ───────────────────────────────────────────────────────
    if raw:
        if row["type"] == "text":
            click.echo(row["text_content"] or "")
        else:
            click.echo(row["ocr_text"] or "")
        return

    meta = (
        f"[bold]ID:[/bold]      {row['id']}\n"
        f"[bold]Type:[/bold]    {row['type']}\n"
        f"[bold]Pinned:[/bold]  {'yes' if row['pinned'] else 'no'}\n"
        f"[bold]Tags:[/bold]    {row['tags'] or 'none'}\n"
        f"[bold]Time:[/bold]    {datetime.fromtimestamp(row['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"[bold]Size:[/bold]    {fmt_size(len(row['content']))}\n"
    )

    if row["type"] == "image":
        console.print(meta)
        shown = display_image_inline(bytes(row["content"]))
        if not shown:
            console.print(
                "[dim]This terminal does not support inline images.[/dim]\n"
                "[dim]Use iTerm2 / Ghostty / WezTerm / Kitty, or run "
                f"[bold]clipd open {row['id']}[/bold] for Quick Look.[/dim]"
            )
        ocr = row["ocr_text"]
        if ocr:
            console.print("\n[bold]OCR Text:[/bold]")
            console.print(ocr)
        return

    content = row["text_content"] or ""
    # Auto-pager for content longer than 30 lines or 2000 chars
    long = content.count("\n") > 29 or len(content) > 2000
    if long:
        with console.pager(styles=True):
            console.print(meta)
            console.print("[bold]Content:[/bold]")
            console.print(content)
    else:
        console.print(meta)
        console.print("[bold]Content:[/bold]")
        console.print(content)


# ── copy ──────────────────────────────────────────────────────────────────────

@cli.command("copy")
@click.argument("ids", type=int, nargs=-1, required=True)
@click.option("--ocr", is_flag=True, help="Copy OCR text instead of the image")
@click.option("--sep", default="\n", show_default=True, help="Separator when joining multiple clips")
def copy_cmd(ids, ocr, sep):
    """Copy one or more clips to the clipboard.

    Multiple IDs are joined with --sep (default: newline).

    \b
    Examples:
      clipd copy 3
      clipd copy 3 7 12          # join with newline
      clipd copy 3 7 --sep ", "  # join with comma-space
    """
    from clipd.clipboard import write_clipboard_image, write_clipboard_text

    db = get_db()

    if len(ids) == 1:
        row = _require_row(db, ids[0])
        if ocr or row["type"] == "text":
            text = row["ocr_text"] if ocr else row["text_content"]
            if not text:
                console.print("[red]No text to copy[/red]")
                sys.exit(1)
            write_clipboard_text(text)
            console.print(f"[green]Copied text[/green] ({len(text)} chars)")
        else:
            content = db.get_content(ids[0])
            write_clipboard_image(content)
            console.print(f"[green]Copied image[/green] ({fmt_size(len(content))})")
        return

    # Multiple IDs — join as text
    parts = []
    for id_ in ids:
        row = _require_row(db, id_)
        if row["type"] == "image":
            text = row["ocr_text"]
            if not text:
                console.print(f"[yellow]ID {id_} is an image with no OCR text — skipped[/yellow]")
                continue
        else:
            text = row["text_content"] or ""
        parts.append(text)

    if not parts:
        console.print("[red]Nothing to copy[/red]")
        sys.exit(1)

    joined = sep.join(parts)
    write_clipboard_text(joined)
    console.print(f"[green]Copied {len(parts)} clips[/green] ({len(joined)} chars)")


# ── delete ────────────────────────────────────────────────────────────────────

@cli.command("delete")
@click.argument("id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def delete_cmd(id, yes):
    """Delete a clip by ID."""
    db = get_db()
    row = _require_row(db, id)
    if row["pinned"] and not yes:
        if not click.confirm(f"ID {id} is pinned. Delete anyway?"):
            return
    elif not yes:
        if not click.confirm(f"Delete ID {id}?"):
            return
    db.delete(id)
    console.print("[green]Deleted[/green]")


# ── pin / unpin ───────────────────────────────────────────────────────────────

@cli.command("pin")
@click.argument("id", type=int)
def pin_cmd(id):
    """Pin a clip (protected from clear)."""
    db = get_db()
    if db.pin(id, True):
        console.print(f"[green]Pinned ID {id}[/green]")
    else:
        console.print(f"[red]ID {id} not found[/red]")


@cli.command("unpin")
@click.argument("id", type=int)
def unpin_cmd(id):
    """Unpin a clip."""
    db = get_db()
    if db.pin(id, False):
        console.print(f"[green]Unpinned ID {id}[/green]")
    else:
        console.print(f"[red]ID {id} not found[/red]")


# ── tag / untag ───────────────────────────────────────────────────────────────

@cli.command("tag")
@click.argument("id", type=int)
@click.argument("tag_name")
def tag_cmd(id, tag_name):
    """Add a tag to a clip.  Example: clipd tag 42 work"""
    db = get_db()
    if db.tag(id, tag_name):
        console.print(f"[green]Tagged with #{tag_name}[/green]")
    else:
        console.print(f"[red]ID {id} not found[/red]")


@cli.command("untag")
@click.argument("id", type=int)
@click.argument("tag_name")
def untag_cmd(id, tag_name):
    """Remove a tag from a clip."""
    db = get_db()
    if db.untag(id, tag_name):
        console.print(f"[green]Removed tag #{tag_name}[/green]")
    else:
        console.print(f"[red]ID {id} not found[/red]")


# ── clear ─────────────────────────────────────────────────────────────────────

@cli.command("clear")
@click.option("--days", type=int, help="Delete items older than N days")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def clear_cmd(days, yes):
    """Bulk-delete history. Pinned items are always kept."""
    if days:
        before_ts = time.time() - days * 86400
        msg = f"Delete all items older than {days} days (pinned items kept). Continue?"
    else:
        before_ts = None
        msg = "Delete entire history (pinned items kept). Continue?"

    if not yes and not click.confirm(msg):
        return

    count = get_db().clear(before_ts=before_ts)
    console.print(f"[green]Deleted {count} items[/green]")


# ── export ────────────────────────────────────────────────────────────────────

@cli.command("export")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv"]), default="json", show_default=True, help="Output format")
@click.option("--output", "-o", type=click.Path(), help="Output file path (default: stdout)")
@click.option("--limit", "-n", default=10000, show_default=True, help="Maximum items to export")
def export_cmd(fmt, output, limit):
    """Export history to JSON or CSV."""
    rows = get_db().list(limit=limit)
    data = [
        {
            "id": r["id"],
            "type": r["type"],
            "content": r["text_content"] if r["type"] == "text" else "[binary]",
            "ocr_text": r["ocr_text"],
            "pinned": bool(r["pinned"]),
            "tags": r["tags"],
            "created_at": datetime.fromtimestamp(r["created_at"]).isoformat(),
        }
        for r in rows
    ]

    if fmt == "json":
        out = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        buf = io.StringIO()
        if data:
            writer = csv.DictWriter(buf, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        out = buf.getvalue()

    if output:
        Path(output).write_text(out, encoding="utf-8")
        console.print(f"[green]Exported {len(data)} items → {output}[/green]")
    else:
        click.echo(out)


# ── backup ────────────────────────────────────────────────────────────────────

@cli.command("backup")
@click.option("--output", "-o", type=click.Path(), help="Backup file path")
def backup_cmd(output):
    """Create a backup of clipboard history (includes images)."""
    if not output:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = str(Path.home() / f"clipd-backup-{ts}.db")
    dest = Path(output)
    count = get_db().backup(dest)
    size = dest.stat().st_size
    console.print(f"[green]Backed up {count} items → {dest} ({fmt_size(size)})[/green]")


# ── restore ───────────────────────────────────────────────────────────────────

@cli.command("restore")
@click.argument("file", type=click.Path(exists=True))
@click.option("--merge/--replace", default=True, show_default=True, help="Merge into existing data, or replace it entirely")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def restore_cmd(file, merge, yes):
    """Restore clipboard history from a backup file."""
    src = Path(file)

    # 유효한 clipd DB인지 확인
    try:
        check = sqlite3.connect(str(src))
        check.execute("SELECT COUNT(*) FROM clips").fetchone()
        check.close()
    except Exception:
        console.print("[red]Error: not a valid clipd backup file[/red]")
        raise SystemExit(1)

    if merge:
        src_count, added = get_db().merge_from(src)
        skipped = src_count - added
        console.print(f"[green]Added {added} new items[/green] ({skipped} duplicates skipped)")
    else:
        if not yes and not click.confirm(f"Replace entire history with {src}? This cannot be undone."):
            return
        db_path = _get_db_path()
        auto_backup = db_path.parent / f"history-before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        shutil.copy2(db_path, auto_backup)
        console.print(f"[dim]Current DB backed up to {auto_backup}[/dim]")
        shutil.copy2(src, db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        finally:
            conn.close()
        console.print(f"[green]Restored {count} items from {src}[/green]")


# ── stats ─────────────────────────────────────────────────────────────────────

@cli.command("stats")
def stats_cmd():
    """Show database statistics."""
    s = get_db().stats()
    console.print(f"[bold]Total:[/bold]   {s['total']}")
    console.print(f"  Text:    {s['text_count']}")
    console.print(f"  Image:   {s['image_count']}")
    console.print(f"  Pinned:  {s['pinned_count']}")
    if s["total_size"]:
        console.print(f"[bold]Size:[/bold]    {fmt_size(s['total_size'])}")
    if s["oldest"]:
        console.print(f"[bold]Oldest:[/bold]  {datetime.fromtimestamp(s['oldest']).strftime('%Y-%m-%d %H:%M')}")
    if s["newest"]:
        console.print(f"[bold]Newest:[/bold]  {datetime.fromtimestamp(s['newest']).strftime('%Y-%m-%d %H:%M')}")


# ── watch ─────────────────────────────────────────────────────────────────────

@cli.command("tui")
@click.option("-s", "--search", "query", default="", help="Initial search query.")
def tui_cmd(query: str):
    """Interactive TUI browser — navigate, copy, delete, pin, search."""
    try:
        from clipd.tui import run_tui
    except ImportError:
        console.print("[red]textual is required:[/red] pip install textual")
        raise SystemExit(1)
    run_tui(initial_query=query)


@cli.command("watch")
def watch_cmd():
    """Watch clipboard changes in real-time. Press Ctrl+C to stop."""
    console.print("[dim]Watching clipboard… (Ctrl+C to stop)[/dim]\n")
    db = get_db()
    since = time.time()
    try:
        while True:
            rows = db.latest_after(since)
            for row in rows:
                since = max(since, row["created_at"])
                ts = datetime.fromtimestamp(row["created_at"]).strftime("%H:%M:%S")
                type_label = "[cyan]TEXT [/cyan]" if row["type"] == "text" else "[magenta]IMAGE[/magenta]"
                console.print(f"[dim]{ts}[/dim]  {type_label}  ", end="")
                if row["type"] == "image":
                    shown = display_image_inline(bytes(row["content"]))
                    if not shown:
                        console.print(clip_preview(row, max_len=80))
                    else:
                        ocr = row["ocr_text"]
                        if ocr:
                            console.print(f"[dim]OCR: {ocr.replace(chr(10), ' ')[:60]}[/dim]")
                        else:
                            console.print()
                else:
                    console.print(clip_preview(row, max_len=80))
            time.sleep(1.0)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped[/dim]")


# ── edit ──────────────────────────────────────────────────────────────────────

@cli.command("edit")
@click.argument("id", type=int)
@click.option("--copy", "-c", "do_copy", is_flag=True, help="Copy edited content to clipboard after saving")
def edit_cmd(id, do_copy):
    """Edit a text clip in $EDITOR and save back to history."""
    import tempfile

    db = get_db()
    row = _require_row(db, id)
    if row["type"] != "text":
        console.print("[red]Only text clips can be edited[/red]")
        sys.exit(1)

    original = row["text_content"] or ""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(original)
        tmp = f.name

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    subprocess.run(shlex.split(editor) + [tmp])

    new_text = Path(tmp).read_text(encoding="utf-8")
    os.unlink(tmp)

    if new_text == original:
        console.print("[dim]No changes[/dim]")
        return

    if db.update_text(id, new_text):
        console.print(f"[green]Saved[/green] ({len(new_text)} chars)")
        if do_copy:
            from clipd.clipboard import write_clipboard_text
            write_clipboard_text(new_text)
            console.print("[green]Copied to clipboard[/green]")
    else:
        console.print("[red]Failed to save[/red]")


# ── ocr ───────────────────────────────────────────────────────────────────────

@cli.command("ocr")
@click.argument("id", type=int)
@click.option("--copy", "-c", "do_copy", is_flag=True, help="Copy extracted text to clipboard")
def ocr_cmd(id, do_copy):
    """Re-run OCR on an image clip and update stored text."""
    from clipd.ocr import extract_text_from_image

    db = get_db()
    row = _require_row(db, id)
    if row["type"] != "image":
        console.print("[red]Only image clips support OCR[/red]")
        sys.exit(1)

    content = db.get_content(id)
    if not content:
        console.print("[red]Failed to load image content[/red]")
        sys.exit(1)

    console.print("[dim]Running OCR…[/dim]")
    ocr_text = extract_text_from_image(content)

    if not ocr_text:
        console.print("[yellow]No text found in image[/yellow]")
        return

    db.update_ocr(id, ocr_text)
    console.print(f"[green]OCR complete[/green] ({len(ocr_text)} chars)\n")
    console.print(ocr_text)

    if do_copy:
        from clipd.clipboard import write_clipboard_text
        write_clipboard_text(ocr_text)
        console.print("\n[green]Copied to clipboard[/green]")


# ── open ──────────────────────────────────────────────────────────────────────

@cli.command("open")
@click.argument("id", type=int, required=False, default=None)
def open_cmd(id):
    """Open an image clip in Preview. Defaults to the most recent image."""
    import tempfile

    db = get_db()
    if id is None:
        row = db.conn.execute(
            "SELECT * FROM clips WHERE type = 'image' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            console.print("[red]No image clips found[/red]")
            sys.exit(1)
    else:
        row = _require_row(db, id)
        if row["type"] != "image":
            console.print("[red]Only image clips can be opened[/red]")
            sys.exit(1)

    content = db.get_content(row["id"])
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(bytes(content))
        tmp = f.name

    subprocess.run(["open", "-a", "Preview", tmp])


# ── daemon group ──────────────────────────────────────────────────────────────

@cli.group("daemon")
def daemon_group():
    """Manage the background daemon."""


def _write_launchd_plist(label: str, exe: str, plist_path: Path) -> None:
    """Write (or overwrite) a launchd plist."""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{html.escape(label)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{html.escape(exe)}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>""")


def _write_plist() -> None:
    """Write (or overwrite) the daemon launchd plist."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_launchd_plist(PLIST_NAME, _daemon_exe(), PLIST_PATH)


@daemon_group.command("start")
def daemon_start():
    """Register and start the daemon via launchd (auto-starts on login)."""
    _write_plist()
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Daemon started[/green]")
        console.print(f"[dim]Log: {LOG_PATH}[/dim]")
    else:
        console.print(f"[red]Failed to start:[/red] {result.stderr.strip()}")


@daemon_group.command("stop")
def daemon_stop():
    """Unload and stop the daemon."""
    result = subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Daemon stopped[/green]")
    else:
        console.print(f"[yellow]Already stopped or not registered:[/yellow] {result.stderr.strip()}")


@daemon_group.command("restart")
def daemon_restart():
    """Restart the daemon (also updates the plist)."""
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    time.sleep(0.5)
    _write_plist()
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Daemon restarted[/green]")
    else:
        console.print(f"[red]Failed to restart:[/red] {result.stderr.strip()}")


@daemon_group.command("status")
def daemon_status():
    """Show daemon status."""
    result = subprocess.run(["launchctl", "list", PLIST_NAME], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Running[/green]")
        console.print(result.stdout)
    else:
        console.print("[yellow]Stopped or not registered[/yellow]")


@daemon_group.command("log")
@click.option("--lines", "-n", default=50, show_default=True, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Stream log in real-time")
@click.option("--all", "show_all", is_flag=True, help="Show all rotated log files (oldest → newest)")
@click.option("--ls", is_flag=True, help="List log files with sizes")
def daemon_log(lines, follow, show_all, ls):
    """View daemon logs.

    Log rotation policy: max 1 MB per file, 3 backups kept.
    Files: daemon.log (current), daemon.log.1, .2, .3 (oldest).
    """
    # Collect all log files: .3 .2 .1 (oldest→newest) then current
    rotated = sorted(LOG_PATH.parent.glob("daemon.log.*"),
                     key=lambda p: int(p.suffix.lstrip(".")), reverse=True)
    all_files = rotated + ([LOG_PATH] if LOG_PATH.exists() else [])

    if not all_files:
        console.print("[dim]No log files[/dim]")
        return

    if ls:
        for p in all_files:
            label = "[dim](oldest)[/dim]" if p == all_files[0] and len(all_files) > 1 else ""
            label = "[green](current)[/green]" if p == LOG_PATH else label
            console.print(f"  {fmt_size(p.stat().st_size):>8}  {p.name}  {label}")
        return

    if show_all:
        args = ["cat"] + [str(p) for p in all_files]
        subprocess.run(args)
        return

    args = ["tail", "-n", str(lines)]
    if follow:
        args.append("-f")
    args.append(str(LOG_PATH))
    subprocess.run(args)


# ── menubar group ─────────────────────────────────────────────────────────────

@cli.group("menubar")
def menubar_group():
    """Manage the menu bar app."""


def _write_menubar_plist() -> None:
    """Write (or overwrite) the menubar launchd plist."""
    _write_launchd_plist(MENUBAR_PLIST_NAME, _menubar_exe(), MENUBAR_PLIST_PATH)


@menubar_group.command("start")
def menubar_start():
    """Register and start the menu bar app (auto-starts on login)."""
    _write_menubar_plist()
    result = subprocess.run(
        ["launchctl", "load", str(MENUBAR_PLIST_PATH)], capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print("[green]Menu bar app started[/green]")
        console.print("[dim]A clipboard icon will appear in your menu bar.[/dim]")
    else:
        console.print(f"[red]Failed to start:[/red] {result.stderr.strip()}")


@menubar_group.command("stop")
def menubar_stop():
    """Stop and unregister the menu bar app."""
    result = subprocess.run(
        ["launchctl", "unload", str(MENUBAR_PLIST_PATH)], capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print("[green]Menu bar app stopped[/green]")
    else:
        console.print(f"[yellow]Already stopped or not registered:[/yellow] {result.stderr.strip()}")


@menubar_group.command("restart")
def menubar_restart():
    """Restart the menu bar app."""
    subprocess.run(["launchctl", "unload", str(MENUBAR_PLIST_PATH)], capture_output=True)
    time.sleep(0.5)
    _write_menubar_plist()
    result = subprocess.run(
        ["launchctl", "load", str(MENUBAR_PLIST_PATH)], capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print("[green]Menu bar app restarted[/green]")
    else:
        console.print(f"[red]Failed to restart:[/red] {result.stderr.strip()}")


@menubar_group.command("status")
def menubar_status():
    """Show menu bar app status."""
    result = subprocess.run(
        ["launchctl", "list", MENUBAR_PLIST_NAME], capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print("[green]Running[/green]")
        console.print(result.stdout)
    else:
        console.print("[yellow]Stopped or not registered[/yellow]")


@cli.command("web")
@click.option("--port", "-p", default=8432, show_default=True, help="Port to listen on.")
@click.option("--no-open", is_flag=True, help="Do not open browser automatically.")
def web_cmd(port: int, no_open: bool):
    """Start the web interface on localhost."""
    from clipd.web import run_server
    run_server(port=port, open_browser=not no_open)
