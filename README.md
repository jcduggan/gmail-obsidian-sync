# gmail-obsidian-sync

Sync Gmail newsletters to your Obsidian vault as readable markdown. Emails are automatically converted from HTML to clean markdown with proper links, headings, and formatting. Non-newsletter emails (billing, personal, spam) are filtered out. Includes annotation support (highlights, notes, tags) and automatic inbox management (read/delete checkboxes move files to archive or trash).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- macOS (for launchd background service; the sync itself works anywhere)
- A Google Cloud project with Gmail API enabled

## Quick Start

```bash
git clone https://github.com/jcduggan/gmail-obsidian-sync.git
cd gmail-obsidian-sync
uv venv && uv pip install -e .
gmail-sync setup
```

The setup wizard walks you through:

1. Setting your Obsidian vault path
2. Configuring Google Cloud OAuth credentials
3. Authenticating with Gmail (read-only access)
4. Running an initial sync
5. Optionally installing as a background service

## How It Works

Every 30 seconds, the sync service:

1. Checks Gmail for new messages (via `history.list` API)
2. Classifies each email — keeps newsletters, skips everything else
3. Converts HTML to markdown with clean formatting
4. Writes to `Mail/Inbox/` in your vault with auto-generated tags
5. Scans for checked read/delete boxes and moves files accordingly

## Vault Structure

```
YourVault/
  Mail/
    Inbox/              New newsletters land here
    Archive/            Checked "read" moves here
    Trash/              Checked "delete" moves here
    Configuration/
      Allowlist.md      Senders/domains to always keep
      Own Addresses.md  Your forwarding email addresses
      Tags.md           Keyword and theme tag rules
      How to Annotate.md  Guide for highlights and notes
```

## Reading and Annotating

Each email has checkboxes at the bottom:

```
---
- [ ] read
- [ ] delete
```

While reading, you can:

- **Bold** text you find interesting (becomes `==highlighted==` on archive)
- **Type notes** between paragraphs (becomes `> [!note]` callouts)
- **Add tags** in the Tags section (`#topic-name`)

When you check `read`, the file moves to Archive with your annotations formatted. User-added tags are learned into `Tags.md` so they auto-apply to future articles.

## Newsletter Classification

Emails are kept if they match any of these (in priority order):

1. Sender/domain is in `Allowlist.md`
2. From one of your own addresses in `Own Addresses.md` (self-forwarded)
3. From a known newsletter platform (Substack, WordPress, Beehiiv, Mailchimp, ConvertKit, Ghost, Buttondown, etc.)
4. Has a `List-Unsubscribe` header and body longer than 500 characters

Everything else is silently skipped.

## Auto-Tagging

Tags are generated from three sources:

- **Author/Publication**: extracted from the sender (`#pub/SemiAnalysis`, `#author/Adam-Mastroianni`)
- **Keywords**: exact term matches defined in `Tags.md` (`RSAC → #RSAC`)
- **Themes**: keyword groups under one tag (GPU, NVIDIA, CUDA → `#AI-infrastructure`)

Edit `Tags.md` in Obsidian to customize. Tags you add manually to articles are learned back into the config automatically.

## CLI Reference

| Command | Description |
|---------|-------------|
| `gmail-sync setup` | Guided first-time setup |
| `gmail-sync auth` | Re-run OAuth browser flow |
| `gmail-sync run` | Start polling loop (default 30s) |
| `gmail-sync once` | Run a single sync cycle |
| `gmail-sync tidy` | Process read/delete checkboxes |
| `gmail-sync status` | Show sync and service status |
| `gmail-sync install` | Install as macOS launchd service |
| `gmail-sync uninstall` | Remove launchd service |

## Manual Setup

If you prefer not to use the setup wizard:

1. Set your vault path:
   ```bash
   export OBSIDIAN_VAULT_PATH=/path/to/your/vault
   ```

2. Create a GCP project at https://console.cloud.google.com/, enable Gmail API, create Desktop OAuth credentials, download the JSON

3. Save credentials:
   ```bash
   mkdir -p ~/.gmail-obsidian
   cp ~/Downloads/client_secret_*.json ~/.gmail-obsidian/credentials.json
   ```

4. Authenticate:
   ```bash
   gmail-sync auth
   ```

5. Sync:
   ```bash
   gmail-sync once
   ```

6. Install service (optional):
   ```bash
   gmail-sync install
   ```

## Security

- Gmail access is **read-only** (`gmail.readonly` scope)
- OAuth credentials and tokens are stored in `~/.gmail-obsidian/` (not in the vault)
- No email content is sent to any third-party service
- Redirect URLs in newsletters are resolved via HTTP HEAD requests to show real destinations
