# CLAUDE.md — clipd Development Guide

## Project Overview

`clipd` is a macOS clipboard history manager — a Python CLI that monitors the clipboard in the background, stores text and images in a local SQLite database, and provides a rich CLI for browsing, searching, and managing history.

## Tech Stack

| Component | Library / Tool |
|-----------|----------------|
| Clipboard access | `pyobjc` (NSPasteboard) |
| Image OCR | Apple Vision framework (`pyobjc-framework-Vision`) |
| Storage | SQLite + FTS5 full-text search |
| CLI | `click` + `rich` |
| Background daemon | `launchd` plist |
| Package manager | `pipx` (isolated install) |
| Build backend | `hatchling` |

## Repository Structure

```
clipd/
├── clipd/
│   ├── __init__.py
│   ├── cli.py            # All CLI commands (click + rich)
│   ├── clipboard.py      # NSPasteboard read/write
│   ├── daemon.py         # 1-second polling loop
│   ├── daemon_entry.py   # Entry point for clipd-daemon binary
│   ├── db.py             # SQLite + FTS5 CRUD
│   └── ocr.py            # Apple Vision OCR (ko-KR + en-US)
├── .github/
│   └── workflows/
│       └── release.yml   # GitHub Actions release workflow (requires billing)
├── CLAUDE.md             # This file
├── README.md             # English readme
├── README.ko.md          # Korean readme
├── install.sh            # One-line curl installer
└── pyproject.toml        # Project metadata + dependencies
```

## Data & Runtime Paths

| Path | Purpose |
|------|---------|
| `~/.clipd/history.db` | SQLite database |
| `~/.clipd/daemon.log` | Daemon log file |
| `~/Library/LaunchAgents/com.clipd.daemon.plist` | launchd service definition |

## Development Setup

```bash
git clone https://github.com/giwonb612/clipd.git
cd clipd
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The `.venv/` directory is git-ignored. Always use the project's venv when testing locally.

## Making Changes

1. Edit source files under `clipd/`
2. Test immediately — no build step needed with `pip install -e .`
3. For daemon changes, restart: `clipd daemon restart`
4. Reinstall into pipx after changes:
   ```bash
   pipx install . --force
   ```

## Key Design Decisions

- **Deduplication**: SHA-256 hash stored as UNIQUE in DB. Consecutive copies of the same content bump the timestamp but don't create duplicates.
- **FTS5 content table**: `clips_fts` is a shadow of `clips` maintained via INSERT/UPDATE/DELETE triggers. Never write to `clips_fts` directly.
- **OCR**: Runs via Apple Vision (`VNRecognizeTextRequest`) on the daemon side at capture time, not on demand. Results stored in `ocr_text` column.
- **Inline images**: Terminal detected via `$TERM_PROGRAM` / `$TERM`. Ghostty/iTerm2/WezTerm use ESC]1337; Kitty uses `kitty +kitten icat`.
- **CLI language**: All `--help` text and output labels are in **English**.

## CLI Commands Reference

```
clipd list [-n N] [-t text|image] [--tag TAG] [--pinned] [--full]
clipd search <query>
clipd show <id> [--raw]
clipd copy <id> [--ocr]
clipd delete <id> [-y]
clipd pin <id> / unpin <id>
clipd tag <id> <name> / untag <id> <name>
clipd clear [--days N] [-y]
clipd export [-f json|csv] [-o FILE]
clipd backup [-o FILE]
clipd restore <FILE> [--merge|--replace] [-y]
clipd stats
clipd watch
clipd open <id>
clipd daemon start | stop | restart | status | log [-f]
```

## Release Process

GitHub Actions (`release.yml`) is configured but **requires GitHub Actions billing** on this account. Use manual release via `gh` CLI instead:

```bash
# 1. Bump version in pyproject.toml
#    version = "X.Y.Z"

# 2. Commit
git add pyproject.toml
git commit -m "chore: bump version to vX.Y.Z"
git push

# 3. Tag
git tag vX.Y.Z
git push origin vX.Y.Z

# 4. Create GitHub Release (attach install.sh)
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes "## Changes
- ..." \
  install.sh
```

## Installing / Upgrading for Testing

```bash
# New install via curl
curl -fsSL https://raw.githubusercontent.com/giwonb612/clipd/main/install.sh | sh

# Upgrade existing pipx install
pipx upgrade clipd

# Force reinstall from local source
pipx install . --force
```

## Git Workflow

- Branch: `main` (single branch, direct commits are fine for this project)
- Commit style: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`)
- Always push after committing
- Tag every release as `vX.Y.Z` before running `gh release create`

## Things to Watch Out For

- `pyobjc` only works on macOS. Do not add Linux/Windows code paths.
- The daemon runs as the user's launchd agent — it has access to the GUI session's clipboard. Do not change the launchd scope.
- FTS5 MATCH syntax is strict. The `search` command has a fallback to LIKE if the query is syntactically invalid FTS5.
- `clipd show --raw` outputs to stdout without any rich formatting — keep it that way for pipe-safety.
- GitHub Actions billing is inactive. Always use `gh release create` manually.
