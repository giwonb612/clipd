"""
clipd menu bar app — runs as a background NSApplication (no Dock icon).

Launch via:
    clipd-menubar &
or via launchd:
    clipd menubar start
"""

import subprocess
import time
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSAlertStyleCritical,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
)
from Foundation import NSMakeRect

from clipd.clipboard import write_clipboard_image, write_clipboard_text
from clipd.db import Database

# ── constants ─────────────────────────────────────────────────────────────────

DAEMON_PLIST_NAME = "com.clipd.daemon"
DAEMON_PLIST_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{DAEMON_PLIST_NAME}.plist"
)

MAX_RECENT = 12      # recent items shown in main menu
MAX_PINNED = 5       # pinned items shown
PREVIEW_LEN = 52     # chars per menu item title

# NSAlert button return codes
_BTN1 = 1000
_BTN2 = 1001
_BTN3 = 1002


# ── helpers ───────────────────────────────────────────────────────────────────

def _preview(row) -> str:
    """Single-line display text for a clip row."""
    if row["type"] == "text":
        t = (row["text_content"] or "").replace("\n", " ").replace("\t", " ").strip()
        return (t[:PREVIEW_LEN] + "…") if len(t) > PREVIEW_LEN else t
    ocr = (row["ocr_text"] or "").replace("\n", " ").strip()
    if ocr:
        suffix = "…" if len(ocr) > PREVIEW_LEN else ""
        return f"[img] {ocr[:PREVIEW_LEN]}{suffix}"
    return "[image]"


def _is_daemon_running() -> bool:
    r = subprocess.run(
        ["launchctl", "list", DAEMON_PLIST_NAME], capture_output=True
    )
    return r.returncode == 0


# ── menu bar delegate ─────────────────────────────────────────────────────────

class ClipdMenuBar(NSObject):

    def init(self):
        self = objc.super(ClipdMenuBar, self).init()
        if self is None:
            return None
        self._db = Database()
        self._status_item = None
        self._menu = None
        # None  → show recent history
        # list  → show search results
        self._search_results = None
        self._search_query = ""
        return self

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def applicationDidFinishLaunching_(self, _notification):
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._setup_status_item()

    @objc.python_method
    def _setup_status_item(self):
        bar = NSStatusBar.systemStatusBar()
        self._status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)

        # SF Symbol (macOS 11+) → template so it inverts in dark mode
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "doc.on.clipboard", "Clipboard History"
        )
        if img:
            img.setTemplate_(True)
            self._status_item.button().setImage_(img)
        else:
            # Fallback: emoji text
            self._status_item.button().setTitle_("📋")

        self._menu = NSMenu.alloc().init()
        self._menu.setDelegate_(self)
        self._status_item.setMenu_(self._menu)

    # ── menu delegate ─────────────────────────────────────────────────────────

    def menuWillOpen_(self, menu):
        """Rebuild menu every time it opens so content is always fresh."""
        menu.removeAllItems()
        if self._search_results is not None:
            self._build_search_menu(menu)
        else:
            self._build_main_menu(menu)

    # ── menu builders ─────────────────────────────────────────────────────────

    @objc.python_method
    def _label(self, menu, text):
        """Add a non-clickable section header."""
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text, None, "")
        item.setEnabled_(False)
        menu.addItem_(item)

    @objc.python_method
    def _action(self, menu, title, sel, key=""):
        """Add a clickable menu item targeting self."""
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key)
        item.setTarget_(self)
        menu.addItem_(item)
        return item

    @objc.python_method
    def _clip_item(self, menu, row, prefix=""):
        pin = "📌 " if row["pinned"] else ""
        icon = "🖼  " if row["type"] == "image" else ""
        title = f"{prefix}{pin}{icon}{_preview(row)}"
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, "clipItemClicked:", ""
        )
        item.setTarget_(self)
        item.setTag_(row["id"])
        menu.addItem_(item)

    @objc.python_method
    def _build_main_menu(self, menu):
        # ── Recent ────────────────────────────────────────────────────────────
        self._label(menu, "  Clipboard History")
        menu.addItem_(NSMenuItem.separatorItem())

        try:
            rows = self._db.list(limit=MAX_RECENT)
        except Exception:
            rows = []

        if rows:
            for row in rows:
                self._clip_item(menu, row)
        else:
            self._label(menu, "  No history yet")

        # ── Pinned ────────────────────────────────────────────────────────────
        try:
            pinned = self._db.list(limit=MAX_PINNED, pinned_only=True)
        except Exception:
            pinned = []

        if pinned:
            menu.addItem_(NSMenuItem.separatorItem())
            self._label(menu, "  \U0001f4cc  Pinned")
            for row in pinned:
                self._clip_item(menu, row)

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Search ────────────────────────────────────────────────────────────
        self._action(menu, "  \U0001f50d  Search\u2026", "searchClicked:", "f")

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Daemon ────────────────────────────────────────────────────────────
        running = _is_daemon_running()
        dot, state = ("\u25cf", "Running") if running else ("\u25cb", "Stopped")
        self._label(menu, f"  {dot}  Daemon: {state}")
        if running:
            self._action(menu, "  Stop Daemon", "stopDaemon:", "")
        else:
            self._action(menu, "  Start Daemon", "startDaemon:", "")

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Stats + Clear ─────────────────────────────────────────────────────
        try:
            s = self._db.stats()
            self._label(
                menu,
                f"  \U0001f4ca  {s['total']} clips"
                f"  \xb7  \U0001f4dd {s['text_count']}"
                f"  \xb7  \U0001f5bc  {s['image_count']}"
                f"  \xb7  \U0001f4cc {s['pinned_count']}",
            )
        except Exception:
            pass

        self._action(menu, "  \U0001f5d1  Clear History\u2026", "clearClicked:", "")

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Quit ──────────────────────────────────────────────────────────────
        self._action(menu, "Quit clipd", "quitApp:", "q")

    @objc.python_method
    def _build_search_menu(self, menu):
        self._label(menu, f"  \U0001f50d  \u201c{self._search_query}\u201d")
        menu.addItem_(NSMenuItem.separatorItem())

        if self._search_results:
            for row in self._search_results:
                self._clip_item(menu, row)
        else:
            self._label(menu, "  No results found")

        menu.addItem_(NSMenuItem.separatorItem())
        self._action(menu, "\u2190 Back to History", "clearSearch:", "")
        self._action(menu, "  \U0001f50d  New Search\u2026", "searchClicked:", "f")
        menu.addItem_(NSMenuItem.separatorItem())
        self._action(menu, "Quit clipd", "quitApp:", "q")

    # ── actions ───────────────────────────────────────────────────────────────

    def clipItemClicked_(self, sender):
        clip_id = sender.tag()
        try:
            db = Database()
            row = db.get(clip_id)
            if not row:
                return
            if row["type"] == "text":
                write_clipboard_text(row["text_content"] or "")
            else:
                content = db.get_content(clip_id)
                if content:
                    write_clipboard_image(content)
        except Exception as e:
            print(f"[clipd-menubar] copy error: {e}")

    def searchClicked_(self, _sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Search Clipboard History")
        alert.addButtonWithTitle_("Search")
        alert.addButtonWithTitle_("Cancel")

        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 24))
        field.setPlaceholderString_("Type to search text or OCR…")
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)

        if alert.runModal() != _BTN1:
            return

        query = field.stringValue().strip()
        if not query:
            return

        try:
            results = self._db.search(query, limit=MAX_RECENT)
        except Exception:
            results = []

        self._search_query = query
        self._search_results = list(results)

    def clearSearch_(self, _sender):
        self._search_results = None
        self._search_query = ""

    def startDaemon_(self, _sender):
        try:
            from clipd.cli import _write_plist
            _write_plist()
            subprocess.run(
                ["launchctl", "load", str(DAEMON_PLIST_PATH)], capture_output=True
            )
        except Exception as e:
            print(f"[clipd-menubar] start daemon error: {e}")

    def stopDaemon_(self, _sender):
        subprocess.run(
            ["launchctl", "unload", str(DAEMON_PLIST_PATH)], capture_output=True
        )

    def clearClicked_(self, _sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Clear Clipboard History")
        alert.setInformativeText_(
            "Pinned items are always kept.\nChoose what to delete:"
        )
        # Buttons added first → rightmost (default). Cancel should be safe default.
        alert.addButtonWithTitle_("Cancel")           # 1000 — rightmost / Enter
        alert.addButtonWithTitle_("Older than 30d")   # 1001
        alert.addButtonWithTitle_("All Unpinned")     # 1002 — leftmost
        alert.setAlertStyle_(NSAlertStyleCritical)

        resp = alert.runModal()
        try:
            if resp == _BTN3:                          # "All Unpinned"
                count = self._db.clear()
                self._notify(f"Cleared {count} items")
            elif resp == _BTN2:                        # "Older than 30d"
                before = time.time() - 30 * 86400
                count = self._db.clear(before_ts=before)
                self._notify(f"Cleared {count} items older than 30 days")
            # _BTN1 = Cancel → do nothing
        except Exception as e:
            print(f"[clipd-menubar] clear error: {e}")

    def quitApp_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)

    # ── internal helpers ──────────────────────────────────────────────────────

    @objc.python_method
    def _notify(self, msg: str):
        a = NSAlert.alloc().init()
        a.setMessageText_(msg)
        a.addButtonWithTitle_("OK")
        a.runModal()


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = ClipdMenuBar.alloc().init()
    app.setDelegate_(delegate)
    app.run()
