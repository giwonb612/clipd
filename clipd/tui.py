"""
clipd TUI — interactive clipboard history browser.

Keybindings
-----------
  ↑ / k       Move up
  ↓ / j       Move down
  Enter        Copy selected clip to clipboard
  d            Delete selected clip (confirm)
  p            Pin / unpin selected clip
  y            Yank (alias for Enter)
  /            Open search
  Escape       Clear search / dismiss dialog
  r            Refresh list
  q / Ctrl+C   Quit
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical  # Horizontal used in ConfirmScreen
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static


# ── formatting helpers ────────────────────────────────────────────────────────

def _fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    diff = datetime.now() - dt
    if diff.days == 0:
        s = diff.seconds
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        return f"{s // 3600}h"
    if diff.days == 1:
        return "1d"
    if diff.days < 30:
        return f"{diff.days}d"
    return dt.strftime("%m/%d")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f}KB"
    return f"{n / 1024 ** 2:.1f}MB"


def _preview_text(row: dict, max_len: int = 64) -> str:
    if row["type"] == "text":
        t = (row.get("text_content") or "").replace("\n", " ").replace("\t", " ").strip()
        return (t[:max_len] + "…") if len(t) > max_len else t
    ocr = (row.get("ocr_text") or "").replace("\n", " ").strip()
    if ocr:
        return ("[img] " + ocr[:max_len - 6] + "…") if len(ocr) > max_len - 6 else f"[img] {ocr}"
    return "[img]"


# ── widgets ───────────────────────────────────────────────────────────────────

class ClipListItem(ListItem):
    """Single row in the clip list."""

    def __init__(self, row: dict) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        row = self.row
        pin_mark = "+" if row["pinned"] else " "
        kind = "T" if row["type"] == "text" else "I"
        age = _fmt_time(row["created_at"])
        size = _fmt_size(len(row["content"]) if row.get("content") else 0)
        preview = _preview_text(row)
        yield Label(f" {pin_mark} {kind} {age:>3} {size:>6}  {preview}")


class PreviewPanel(Static):
    """Right-hand detail panel for the selected clip."""

    DEFAULT_CSS = """
    PreviewPanel {
        padding: 1 2;
        overflow-y: auto;
    }
    """

    def show_clip(self, row: Optional[dict]) -> None:
        if row is None:
            self.update("[dim]No clip selected[/dim]")
            return

        age = _fmt_time(row["created_at"])
        size = _fmt_size(len(row["content"]) if row.get("content") else 0)
        tags = (row.get("tags") or "").strip(", ")
        pinned = "yes" if row["pinned"] else "no"

        meta = (
            f"[bold]#{row['id']}[/bold]  {row['type']}  {age} ago  {size}"
            + (f"  [yellow]pinned[/yellow]" if row["pinned"] else "")
            + (f"\n[dim]Tags:[/dim] {tags}" if tags else "")
        )

        sep = "─" * 48

        if row["type"] == "text":
            body = (row.get("text_content") or "").strip()
            if len(body) > 2000:
                body = body[:2000] + "\n[dim]… (truncated)[/dim]"
        else:
            ocr = (row.get("ocr_text") or "").strip()
            body = (f"[dim]OCR:[/dim]\n{ocr}" if ocr else "[dim](no OCR text)[/dim]")

        self.update(f"{meta}\n{sep}\n{body}")


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes / no modal."""

    BINDINGS = [
        Binding("y", "confirm", show=False),
        Binding("n", "cancel", show=False),
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm-box {
        background: $surface;
        border: solid $warning;
        padding: 2 4;
        width: 52;
        height: auto;
    }
    #confirm-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._message)
            yield Label("[bold]y[/bold] Yes   [bold]n[/bold] / Esc  No", id="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── main app ──────────────────────────────────────────────────────────────────

class ClipApp(App[None]):

    TITLE = "clipd"

    CSS = """
    Screen {
        layout: vertical;
    }

    /* ── top: list ── */
    #list-col {
        height: 40%;
        border: solid $primary-darken-1;
    }

    #list-header {
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        height: 1;
    }

    ListView {
        height: 1fr;
    }

    ListItem {
        padding: 0 0;
    }

    ListItem.--highlight {
        background: $accent 30%;
    }

    /* ── bottom: preview ── */
    #preview-col {
        height: 1fr;
        border: solid $primary-darken-2;
    }

    #preview-header {
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }

    /* ── search bar ── */
    #search-row {
        height: 3;
        display: none;
        border-top: solid $accent;
    }

    #search-row.active {
        display: block;
    }

    #search-input {
        border: none;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("q",      "quit",    "Quit"),
        Binding("slash",  "search",  "Search"),
        Binding("enter",  "copy",    "Copy"),
        Binding("y",      "copy",    "Copy", show=False),
        Binding("d",      "delete",  "Delete"),
        Binding("p",      "pin",     "Pin"),
        Binding("r",      "refresh", "Refresh", show=False),
        Binding("escape", "escape",  "Clear",   show=False),
        Binding("j",      "down",    "Down",    show=False),
        Binding("k",      "up",      "Up",      show=False),
    ]

    def __init__(self, initial_query: str = "") -> None:
        super().__init__()
        self._initial_query = initial_query
        self._rows: list[dict] = []
        self._search_timer = None

    # ── layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="list-col"):
            yield Label(" + T  age   size  preview", id="list-header")
            yield ListView(id="clip-list")
        with Vertical(id="preview-col"):
            yield Label(" Detail", id="preview-header")
            yield PreviewPanel(id="preview")
        with Vertical(id="search-row"):
            yield Input(placeholder="Search… (Enter to focus list, Esc to clear)", id="search-input")
        yield Footer()

    def on_mount(self) -> None:
        if self._initial_query:
            self._open_search(self._initial_query)
        else:
            self._load(query=None)

    # ── data loading ──────────────────────────────────────────────────────────

    def _load(self, query: Optional[str]) -> None:
        from clipd.db import Database
        db = Database()
        if query:
            rows = db.search(query, limit=200)
        else:
            rows = db.list(limit=200)
        self._rows = [dict(r) for r in rows]
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        lv = self.query_one("#clip-list", ListView)
        lv.clear()
        for row in self._rows:
            lv.append(ClipListItem(row))
        if self._rows:
            self._show_preview(self._rows[0])
        else:
            self.query_one("#preview", PreviewPanel).show_clip(None)

    # ── preview ───────────────────────────────────────────────────────────────

    def _show_preview(self, row: Optional[dict]) -> None:
        self.query_one("#preview", PreviewPanel).show_clip(row)

    def _current(self) -> Optional[dict]:
        lv = self.query_one("#clip-list", ListView)
        item = lv.highlighted_child
        if isinstance(item, ClipListItem):
            return item.row
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, ClipListItem):
            self._show_preview(event.item.row)

    # ── actions ───────────────────────────────────────────────────────────────

    def action_copy(self) -> None:
        row = self._current()
        if not row:
            return
        try:
            from clipd.db import Database
            from clipd.clipboard import write_clipboard_text, write_clipboard_image
            db = Database()
            if row["type"] == "text":
                write_clipboard_text(row.get("text_content") or "")
            else:
                content = db.get_content(row["id"])
                if content:
                    write_clipboard_image(content)
            self.notify(f"Copied #{row['id']}", title="Copied", timeout=2)
        except Exception as exc:
            self.notify(str(exc), title="Error", severity="error")

    def action_delete(self) -> None:
        row = self._current()
        if not row:
            return

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            from clipd.db import Database
            Database().delete(row["id"])
            # Remove from local list without full reload
            self._rows = [r for r in self._rows if r["id"] != row["id"]]
            self._rebuild_list()
            self.notify(f"Deleted #{row['id']}", title="Deleted", timeout=2)

        self.push_screen(
            ConfirmScreen(f"Delete clip #{row['id']}?"),
            _on_confirm,
        )

    def action_pin(self) -> None:
        row = self._current()
        if not row:
            return
        from clipd.db import Database
        new_state = not row["pinned"]
        Database().pin(row["id"], new_state)
        row["pinned"] = new_state
        # Refresh item label in-place
        lv = self.query_one("#clip-list", ListView)
        if isinstance(lv.highlighted_child, ClipListItem):
            lv.highlighted_child.row = row
            lv.highlighted_child.query_one(Label).update(self._item_label(row))
        self._show_preview(row)
        label = "Pinned" if new_state else "Unpinned"
        self.notify(f"{label} #{row['id']}", timeout=2)

    def action_refresh(self) -> None:
        inp = self.query_one("#search-input", Input)
        query = inp.value.strip() or None
        self._load(query)
        self.notify("Refreshed", timeout=1)

    def action_search(self) -> None:
        self._open_search()

    def action_escape(self) -> None:
        search_row = self.query_one("#search-row")
        if search_row.has_class("active"):
            self.query_one("#search-input", Input).value = ""
            search_row.remove_class("active")
            self._load(query=None)
            self.query_one("#clip-list", ListView).focus()

    def action_down(self) -> None:
        self.query_one("#clip-list", ListView).action_cursor_down()

    def action_up(self) -> None:
        self.query_one("#clip-list", ListView).action_cursor_up()

    # ── search helpers ────────────────────────────────────────────────────────

    def _open_search(self, prefill: str = "") -> None:
        search_row = self.query_one("#search-row")
        search_row.add_class("active")
        inp = self.query_one("#search-input", Input)
        if prefill:
            inp.value = prefill
            self._load(query=prefill)
        inp.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Debounce: cancel previous timer and schedule a new one
        if hasattr(self, "_search_timer") and self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(
            0.25, lambda: self._load(event.value.strip() or None)
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter moves focus back to the list
        self.query_one("#clip-list", ListView).focus()

    # ── item label helper ─────────────────────────────────────────────────────

    @staticmethod
    def _item_label(row: dict) -> str:
        pin_mark = "+" if row["pinned"] else " "
        kind = "T" if row["type"] == "text" else "I"
        age = _fmt_time(row["created_at"])
        size = _fmt_size(len(row["content"]) if row.get("content") else 0)
        preview = _preview_text(row)
        return f" {pin_mark} {kind} {age:>3} {size:>6}  {preview}"


# ── entry point ───────────────────────────────────────────────────────────────

def run_tui(initial_query: str = "") -> None:
    ClipApp(initial_query=initial_query).run()
