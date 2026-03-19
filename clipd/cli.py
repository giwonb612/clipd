import json
import csv
import io
import os
import shutil
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

console = Console()

PLIST_NAME = "com.clipd.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
LOG_PATH = Path.home() / ".clipd" / "daemon.log"


# ── helpers ──────────────────────────────────────────────────────────────────

def get_db():
    from clipd.db import Database
    return Database()


def fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    diff = datetime.now() - dt
    if diff.days == 0:
        s = diff.seconds
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        return f"{s // 3600}h ago"
    if diff.days == 1:
        return "yesterday"
    if diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 ** 2:.1f}MB"


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
    return "[dim][이미지][/dim]"


def highlight_snippet(raw: Optional[str]) -> str:
    """Convert <b>…</b> FTS5 markers to rich markup."""
    if not raw:
        return ""
    return raw.replace("<b>", "[bold yellow]").replace("</b>", "[/bold yellow]")


def _detect_terminal() -> str:
    """Return terminal type: 'iterm2' | 'kitty' | 'wezterm' | 'ghostty' | 'unknown'."""
    prog = os.environ.get("TERM_PROGRAM", "")
    term = os.environ.get("TERM", "")
    if prog == "iTerm.app":
        return "iterm2"
    if prog == "WezTerm":
        return "wezterm"
    if prog == "ghostty" or term == "xterm-ghostty":
        return "ghostty"
    if term == "xterm-kitty" or os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    return "unknown"


def display_image_inline(image_bytes: bytes) -> bool:
    """Display image inline in terminal. Returns True if displayed."""
    import base64, tempfile

    terminal = _detect_terminal()

    # iTerm2 / WezTerm / Ghostty — ESC]1337 inline image protocol
    if terminal in ("iterm2", "wezterm", "ghostty"):
        b64 = base64.b64encode(image_bytes).decode()
        size = len(image_bytes)
        sys.stdout.write(
            f"\033]1337;File=inline=1;size={size};width=auto;preserveAspectRatio=1:{b64}\a\n"
        )
        sys.stdout.flush()
        return True

    # Kitty — icat kitten
    if terminal == "kitty":
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            tmp = f.name
        try:
            subprocess.run(["kitty", "+kitten", "icat", tmp], check=False)
        finally:
            os.unlink(tmp)
        return True

    return False


def _daemon_exe() -> str:
    exe = shutil.which("clipd-daemon")
    if not exe:
        clipd = shutil.which("clipd")
        if clipd:
            candidate = Path(clipd).parent / "clipd-daemon"
            if candidate.exists():
                return str(candidate)
    return exe or "clipd-daemon"


def _require_row(db, id_: int):
    row = db.get(id_)
    if not row:
        console.print(f"[red]ID {id_} 없음[/red]")
        sys.exit(1)
    return row


# ── CLI root ──────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """clipd — macOS 클립보드 히스토리 관리 CLI"""


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.option("--limit", "-n", default=20, show_default=True, help="표시 개수")
@click.option("--type", "-t", "clip_type", type=click.Choice(["text", "image"]), help="타입 필터")
@click.option("--tag", help="태그 필터")
@click.option("--pinned", is_flag=True, help="고정 항목만")
@click.option("--full", "-f", is_flag=True, help="전체 내용 표시 (미리보기 대신)")
def list_cmd(limit, clip_type, tag, pinned, full):
    """최근 클립보드 히스토리 조회"""
    rows = get_db().list(limit=limit, clip_type=clip_type, tag=tag, pinned_only=pinned)
    if not rows:
        console.print("[dim]히스토리 없음[/dim]")
        return

    if full:
        for row in rows:
            pin = "📌 " if row["pinned"] else ""
            type_label = "[cyan]텍스트[/cyan]" if row["type"] == "text" else "[magenta]이미지[/magenta]"
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
                    console.print(f"[dim][이미지 — clipd open {row['id']} 으로 열기][/dim]")
                ocr = row["ocr_text"]
                if ocr:
                    console.print(f"[dim]OCR: {ocr}[/dim]")
            console.print()
        return

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("ID", style="dim", width=6)
    t.add_column("타입", width=7)
    t.add_column("내용 미리보기", min_width=42)
    t.add_column("태그", width=14)
    t.add_column("시간", width=12)

    for row in rows:
        pin = "📌 " if row["pinned"] else ""
        type_label = "[cyan]텍스트[/cyan]" if row["type"] == "text" else "[magenta]이미지[/magenta]"
        tags_str = " ".join(f"[yellow]#{g}[/yellow]" for g in (row["tags"] or "").split(",") if g)
        t.add_row(f"{pin}{row['id']}", type_label, clip_preview(row), tags_str, fmt_time(row["created_at"]))

    console.print(t)


# ── search ────────────────────────────────────────────────────────────────────

@cli.command("search")
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True)
def search_cmd(query, limit):
    """텍스트 및 OCR 풀텍스트 검색"""
    rows = get_db().search(query, limit=limit)
    if not rows:
        console.print(f'[dim]"{query}" 검색 결과 없음[/dim]')
        return

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("ID", style="dim", width=6)
    t.add_column("타입", width=7)
    t.add_column("매칭 내용", min_width=50)
    t.add_column("시간", width=12)

    for row in rows:
        type_label = "[cyan]텍스트[/cyan]" if row["type"] == "text" else "[magenta]이미지[/magenta]"
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
@click.option("--raw", "-r", is_flag=True, help="내용만 출력 (파이프 친화적)")
def show_cmd(id, raw):
    """특정 클립 상세 보기 (긴 내용은 pager로 자동 열림)"""
    row = _require_row(get_db(), id)

    if raw:
        # 순수 텍스트만 stdout으로 — grep, pbcopy 등 파이프 용도
        if row["type"] == "text":
            click.echo(row["text_content"] or "")
        else:
            click.echo(row["ocr_text"] or "")
        return

    meta = (
        f"[bold]ID:[/bold]    {row['id']}\n"
        f"[bold]타입:[/bold]   {row['type']}\n"
        f"[bold]고정:[/bold]   {'예' if row['pinned'] else '아니오'}\n"
        f"[bold]태그:[/bold]   {row['tags'] or '없음'}\n"
        f"[bold]시간:[/bold]   {datetime.fromtimestamp(row['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"[bold]크기:[/bold]   {fmt_size(len(row['content']))}\n"
    )

    if row["type"] == "image":
        console.print(meta)
        shown = display_image_inline(bytes(row["content"]))
        if not shown:
            console.print(
                "[dim]이 터미널은 인라인 이미지를 지원하지 않습니다.[/dim]\n"
                "[dim]iTerm2 / WezTerm / Kitty 에서 실행하거나 "
                f"[bold]clipd open {row['id']}[/bold] 으로 Quick Look을 사용하세요.[/dim]"
            )
        ocr = row["ocr_text"]
        if ocr:
            console.print(f"\n[bold]OCR 텍스트:[/bold]")
            console.print(ocr)
        return

    content = row["text_content"] or ""
    label = "내용"
    # 30줄 또는 2000자 초과 시 pager로 열기
    long = content.count("\n") > 29 or len(content) > 2000
    if long:
        with console.pager(styles=True):
            console.print(meta)
            console.print(f"[bold]{label}:[/bold]")
            console.print(content)
    else:
        console.print(meta)
        console.print(f"[bold]{label}:[/bold]")
        console.print(content)


# ── copy ──────────────────────────────────────────────────────────────────────

@cli.command("copy")
@click.argument("id", type=int)
@click.option("--ocr", is_flag=True, help="이미지의 OCR 텍스트를 클립보드로 복사")
def copy_cmd(id, ocr):
    """클립보드로 복사"""
    from clipd.clipboard import write_clipboard_image, write_clipboard_text

    row = _require_row(get_db(), id)

    if ocr or row["type"] == "text":
        text = row["ocr_text"] if ocr else row["text_content"]
        if not text:
            console.print("[red]복사할 텍스트 없음[/red]")
            sys.exit(1)
        write_clipboard_text(text)
        console.print(f"[green]텍스트 복사됨[/green] ({len(text)}자)")
    else:
        write_clipboard_image(bytes(row["content"]))
        console.print(f"[green]이미지 복사됨[/green] ({fmt_size(len(row['content']))})")


# ── delete ────────────────────────────────────────────────────────────────────

@cli.command("delete")
@click.argument("id", type=int)
@click.option("--yes", "-y", is_flag=True, help="확인 생략")
def delete_cmd(id, yes):
    """특정 클립 삭제"""
    db = get_db()
    row = _require_row(db, id)
    if row["pinned"] and not yes:
        if not click.confirm(f"ID {id}는 고정 항목입니다. 삭제하시겠습니까?"):
            return
    elif not yes:
        if not click.confirm(f"ID {id}를 삭제하시겠습니까?"):
            return
    db.delete(id)
    console.print("[green]삭제됨[/green]")


# ── pin / unpin ───────────────────────────────────────────────────────────────

@cli.command("pin")
@click.argument("id", type=int)
def pin_cmd(id):
    """클립 고정 (clear 시 보호됨)"""
    db = get_db()
    if db.pin(id, True):
        console.print(f"[green]ID {id} 고정됨[/green]")
    else:
        console.print(f"[red]ID {id} 없음[/red]")


@cli.command("unpin")
@click.argument("id", type=int)
def unpin_cmd(id):
    """클립 고정 해제"""
    db = get_db()
    if db.pin(id, False):
        console.print(f"[green]ID {id} 고정 해제됨[/green]")
    else:
        console.print(f"[red]ID {id} 없음[/red]")


# ── tag / untag ───────────────────────────────────────────────────────────────

@cli.command("tag")
@click.argument("id", type=int)
@click.argument("tag_name")
def tag_cmd(id, tag_name):
    """클립에 태그 추가 (예: clipd tag 42 work)"""
    db = get_db()
    if db.tag(id, tag_name):
        console.print(f"[green]태그 #{tag_name} 추가됨[/green]")
    else:
        console.print(f"[red]ID {id} 없음[/red]")


@cli.command("untag")
@click.argument("id", type=int)
@click.argument("tag_name")
def untag_cmd(id, tag_name):
    """클립에서 태그 제거"""
    db = get_db()
    if db.untag(id, tag_name):
        console.print(f"[green]태그 #{tag_name} 제거됨[/green]")
    else:
        console.print(f"[red]ID {id} 없음[/red]")


# ── clear ─────────────────────────────────────────────────────────────────────

@cli.command("clear")
@click.option("--days", type=int, help="N일 이전 항목만 삭제")
@click.option("--yes", "-y", is_flag=True, help="확인 생략")
def clear_cmd(days, yes):
    """히스토리 일괄 삭제 (고정 항목 제외)"""
    if days:
        before_ts = time.time() - days * 86400
        msg = f"{days}일 이전 항목을 모두 삭제합니다 (고정 항목 제외). 계속하시겠습니까?"
    else:
        before_ts = None
        msg = "전체 히스토리를 삭제합니다 (고정 항목 제외). 계속하시겠습니까?"

    if not yes and not click.confirm(msg):
        return

    count = get_db().clear(before_ts=before_ts)
    console.print(f"[green]{count}개 항목 삭제됨[/green]")


# ── export ────────────────────────────────────────────────────────────────────

@cli.command("export")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv"]), default="json", show_default=True)
@click.option("--output", "-o", type=click.Path(), help="출력 파일 경로 (기본: stdout)")
@click.option("--limit", "-n", default=10000, show_default=True)
def export_cmd(fmt, output, limit):
    """히스토리를 JSON 또는 CSV로 내보내기"""
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
        console.print(f"[green]{len(data)}개 항목 → {output}[/green]")
    else:
        click.echo(out)


# ── stats ─────────────────────────────────────────────────────────────────────

@cli.command("stats")
def stats_cmd():
    """DB 통계"""
    s = get_db().stats()
    console.print(f"[bold]전체:[/bold]  {s['total']}개")
    console.print(f"  텍스트: {s['text_count']}개")
    console.print(f"  이미지: {s['image_count']}개")
    console.print(f"  고정:   {s['pinned_count']}개")
    if s["total_size"]:
        console.print(f"[bold]크기:[/bold]  {fmt_size(s['total_size'])}")
    if s["oldest"]:
        console.print(f"[bold]시작:[/bold]  {datetime.fromtimestamp(s['oldest']).strftime('%Y-%m-%d %H:%M')}")
    if s["newest"]:
        console.print(f"[bold]최근:[/bold]  {datetime.fromtimestamp(s['newest']).strftime('%Y-%m-%d %H:%M')}")


# ── watch ─────────────────────────────────────────────────────────────────────

@cli.command("watch")
def watch_cmd():
    """실시간 클립보드 변경 모니터링 (Ctrl+C 종료)"""
    console.print("[dim]클립보드 감시 중… (Ctrl+C 종료)[/dim]\n")
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
        console.print("\n[dim]종료[/dim]")


# ── open ──────────────────────────────────────────────────────────────────────

@cli.command("open")
@click.argument("id", type=int)
def open_cmd(id):
    """이미지 클립을 Quick Look으로 열기"""
    import tempfile

    row = _require_row(get_db(), id)
    if row["type"] != "image":
        console.print("[red]이미지 클립만 열 수 있습니다[/red]")
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(bytes(row["content"]))
        tmp = f.name

    subprocess.run(["qlmanage", "-p", tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.unlink(tmp)


# ── daemon group ──────────────────────────────────────────────────────────────

@cli.group("daemon")
def daemon_group():
    """데몬 관리"""


@daemon_group.command("start")
def daemon_start():
    """데몬 시작 (launchd 등록, 재부팅 후 자동 실행)"""
    exe = _daemon_exe()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_PATH}</string>
</dict>
</plist>"""

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)

    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]데몬 시작됨[/green]")
        console.print(f"[dim]로그: {LOG_PATH}[/dim]")
    else:
        console.print(f"[red]시작 실패:[/red] {result.stderr.strip()}")


@daemon_group.command("stop")
def daemon_stop():
    """데몬 중지 (launchd 해제)"""
    result = subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]데몬 중지됨[/green]")
    else:
        console.print(f"[yellow]이미 중지되었거나 미등록:[/yellow] {result.stderr.strip()}")


@daemon_group.command("restart")
def daemon_restart():
    """데몬 재시작"""
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    time.sleep(0.5)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]데몬 재시작됨[/green]")
    else:
        console.print(f"[red]재시작 실패:[/red] {result.stderr.strip()}")


@daemon_group.command("status")
def daemon_status():
    """데몬 상태 확인"""
    result = subprocess.run(["launchctl", "list", PLIST_NAME], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]실행 중[/green]")
        console.print(result.stdout)
    else:
        console.print("[yellow]중지됨 또는 미등록[/yellow]")


@daemon_group.command("log")
@click.option("--lines", "-n", default=50, show_default=True)
@click.option("--follow", "-f", is_flag=True, help="실시간 로그 스트림")
def daemon_log(lines, follow):
    """데몬 로그 확인"""
    if not LOG_PATH.exists():
        console.print("[dim]로그 없음[/dim]")
        return
    args = ["tail", "-n", str(lines)]
    if follow:
        args.append("-f")
    args.append(str(LOG_PATH))
    subprocess.run(args)
