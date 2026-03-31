# gmail-obsidian-sync

Syncs Gmail emails to an Obsidian vault as readable markdown files.

## Toolchain

Python 3.13 with uv, ruff, ty, pytest. See `~/.claude/lang-python.md`.

## Architecture

- `src/gmail_sync/auth.py` — OAuth credential management (gmail.readonly scope)
- `src/gmail_sync/client.py` — Gmail API wrapper (history, messages, attachments)
- `src/gmail_sync/sync.py` — Sync engine (initial + incremental + checkpoint)
- `src/gmail_sync/convert.py` — MIME parsing, HTML-to-markdown conversion
- `src/gmail_sync/writer.py` — Vault file writer (naming, frontmatter, atomic writes)
- `src/gmail_sync/state.py` — Persistent state (historyId, processed message IDs)
- `src/gmail_sync/cli.py` — CLI entry point and subcommands

## Key invariants

- READ-ONLY Gmail access — `gmail.readonly` scope only, never request write scopes
- history_id checkpoint advances only AFTER all messages in a batch are written to disk
- All file writes (state + vault) use atomic write-to-temp-then-rename
- Single message failures must not block the pipeline

## Config paths

- `~/.gmail-obsidian/credentials.json` — GCP OAuth client ID (user provides)
- `~/.gmail-obsidian/token.json` — OAuth refresh/access tokens
- `~/.gmail-obsidian/state.json` — Sync state (history_id, processed_ids)
- `$OBSIDIAN_VAULT_PATH/Inbox/` — Output directory for email markdown files
