# clipd

A macOS clipboard history manager for the terminal — captures text and images automatically, extracts text from images via OCR, stores everything locally, and provides a fast CLI to search, browse, and reuse your clipboard history.

## Features

- **Automatic capture** — background daemon monitors clipboard every second
- **Image OCR** — extracts text from screenshots and images using Apple Vision (runs fully locally)
- **Full-text search** — SQLite FTS5 searches across text content and OCR results simultaneously
- **Inline image display** — renders images directly in the terminal (Ghostty, iTerm2, WezTerm, Kitty)
- **Pin & tag** — organize important clips, protect them from bulk deletion
- **Export** — dump history to JSON or CSV
- **Web interface** — browser-based UI for browsing, searching, and managing history
- **Backup & restore** — migrate full history (including images) to a new machine with a single command
- **No cloud** — all data stays in `~/.clipd/history.db`

## Requirements

- macOS 12+
- Python 3.11+
- [pipx](https://pipx.pypa.io/) (auto-installed by the install script)

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/giwonb/clipd/main/install.sh | sh
```

Or directly with pipx:

```bash
pipx install git+https://github.com/giwonb612/clipd.git
```

## Quick Start

```bash
# Start the background daemon (auto-starts on login)
clipd daemon start

# Browse recent history
clipd list

# Search (includes OCR text from images)
clipd search "meeting notes"

# Copy a clip back to clipboard
clipd copy 42
```

## Commands

### History

| Command | Description |
|---------|-------------|
| `clipd list` | List recent history |
| `clipd list -n 50` | Show last 50 items |
| `clipd list --type image` | Filter by type (`text` or `image`) |
| `clipd list --tag work` | Filter by tag |
| `clipd list --pinned` | Show pinned items only |
| `clipd list --full` | Show full content instead of preview (images rendered inline) |
| `clipd search <query>` | Full-text search across text and OCR |
| `clipd show <id>` | Show full details (auto-pager for long content) |
| `clipd show <id> --raw` | Output raw text only — pipe-friendly |

### Actions

| Command | Description |
|---------|-------------|
| `clipd copy <id>` | Copy clip back to clipboard |
| `clipd copy <id> --ocr` | Copy OCR text from an image clip |
| `clipd open <id>` | Open image in Quick Look |
| `clipd delete <id>` | Delete a clip |
| `clipd pin <id>` | Pin a clip (protected from `clear`) |
| `clipd unpin <id>` | Unpin a clip |
| `clipd tag <id> <name>` | Add a tag |
| `clipd untag <id> <name>` | Remove a tag |
| `clipd clear` | Delete all unpinned history |
| `clipd clear --days 7` | Delete items older than 7 days |
| `clipd export` | Export history to JSON (stdout) |
| `clipd export -f csv -o out.csv` | Export to CSV file |
| `clipd backup` | Create a full backup (text + images) |
| `clipd backup -o ~/my.db` | Backup to a specific file |
| `clipd restore <file>` | Merge backup into current history |
| `clipd restore <file> --replace` | Replace current history with backup |
| `clipd stats` | Show database statistics |
| `clipd watch` | Live-monitor clipboard changes |

### Web Interface

| Command | Description |
|---------|-------------|
| `clipd web` | Start web UI at `http://localhost:8432` (opens browser automatically) |
| `clipd web --port 9000` | Use a custom port |
| `clipd web --no-open` | Start without opening the browser |

The web interface supports browsing, searching, infinite scroll, copy, pin/unpin, tag, delete, and export — all without the terminal.

### Daemon

| Command | Description |
|---------|-------------|
| `clipd daemon start` | Register with launchd and start |
| `clipd daemon stop` | Stop and unregister |
| `clipd daemon restart` | Restart the daemon |
| `clipd daemon status` | Show running status |
| `clipd daemon log` | View recent log output |
| `clipd daemon log -f` | Stream log in real-time |

## Inline Image Display

When running in a supported terminal, `clipd show <id>` and `clipd list --full` render images directly:

| Terminal | Protocol |
|----------|----------|
| Ghostty | ESC]1337 |
| iTerm2 | ESC]1337 |
| WezTerm | ESC]1337 |
| Kitty | `kitty +kitten icat` |
| Others | Fallback to `clipd open <id>` |

## Data Location

| Path | Purpose |
|------|---------|
| `~/.clipd/history.db` | SQLite database (unlimited history) |
| `~/.clipd/daemon.log` | Daemon log |
| `~/Library/LaunchAgents/com.clipd.daemon.plist` | launchd service |

## Migrating to a New Machine

```bash
# On the old machine
clipd backup -o ~/Desktop/clipd-backup.db

# Transfer via AirDrop, USB, or iCloud, then on the new machine
clipd restore ~/Desktop/clipd-backup.db
```

`restore` merges by default — existing clips are kept and only new ones are added (deduplication by content hash). Use `--replace` to overwrite entirely.

## Upgrade

```bash
pipx upgrade clipd
```

## License

MIT
