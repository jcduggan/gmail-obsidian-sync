"""Write email content to Obsidian vault as markdown files."""

import json
import os
import re
from pathlib import Path

from gmail_sync.convert import Attachment, EmailContent

_FORWARDED_BLOCK_RE = re.compile(
    r"^(> \*\*Forwarded message\*\*\n(?:> \*\*\w+:\*\*.*\n)+)\n*",
    re.MULTILINE,
)

CONFIG_DIR = Path.home() / ".gmail-obsidian"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load persistent config from ~/.gmail-obsidian/config.json."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict) -> None:
    """Save persistent config to ~/.gmail-obsidian/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2))
    tmp.rename(CONFIG_FILE)


def get_vault_path() -> Path:
    """Get the Obsidian vault path.

    Checks (in order): OBSIDIAN_VAULT_PATH env var, then config.json.

    Returns:
        Path to the vault root.

    Raises:
        SystemExit: If vault path is not configured or doesn't exist.
    """
    vault = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not vault:
        config = load_config()
        vault = config.get("vault_path")
    if not vault:
        raise SystemExit(
            "Obsidian vault path is not configured.\n"
            "Run 'gmail-sync setup' to configure, or set OBSIDIAN_VAULT_PATH."
        )

    path = Path(vault)
    if not path.is_dir():
        raise SystemExit(f"Vault path does not exist: {path}")

    return path


MAIL_DIR = "Mail"


def get_mail_dir() -> Path:
    """Get or create the Mail parent directory in the vault."""
    mail = get_vault_path() / MAIL_DIR
    mail.mkdir(parents=True, exist_ok=True)
    return mail


def get_inbox_dir() -> Path:
    """Get or create the Mail/Inbox directory in the vault."""
    inbox = get_mail_dir() / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def get_archive_dir() -> Path:
    """Get or create the Mail/Archive directory in the vault."""
    archive = get_mail_dir() / "Archive"
    archive.mkdir(parents=True, exist_ok=True)
    return archive


def get_config_dir() -> Path:
    """Get or create the Mail/Configuration directory in the vault."""
    config = get_mail_dir() / "Configuration"
    config.mkdir(parents=True, exist_ok=True)
    return config


_ALLOWLIST_SEED = """\
# Allowlist

Add senders or domains below to always keep their emails,
even if they don't match newsletter detection patterns.
One per line. Use a full email address or just a domain.

## Examples

# Keep all emails from this domain:
# stratechery.com

# Keep emails from this specific sender only:
# newsletter@platformer.news

## Your allowlist

"""

_OWN_ADDRESSES_SEED = """\
# Own Addresses

Add your email addresses below. Emails forwarded FROM these
addresses are always kept (treated as self-forwarded articles).
One per line.

"""


_ANNOTATION_GUIDE_SEED = """\
# How to Annotate

While reading a newsletter in your Inbox, you can annotate it.
When you check **read**, your annotations are automatically formatted.

## Highlighting

**Bold** any text you find interesting. Select it and tap **B**.

When you mark the article as read, bolded passages are converted
to ==highlighted text== so they stand out in your archive.

## Notes

Type your own thoughts anywhere in the article. Just tap between
paragraphs and start writing.

When you mark as read, your additions are wrapped in a callout:

> [!note] My note
> Your text appears like this in the archive

## Tags

Add tags in the Tags section near the bottom of each article.
Use Obsidian's `#tag` format, like `#AI` or `#security`.

## Summary

After archiving, a count of your highlights and notes appears
at the top of the article for quick reference.
"""


_TAGS_CONFIG_SEED = """\
# Tags

Rules for auto-tagging articles. Edit freely — changes take
effect on the next sync cycle.

Author and publication tags are always generated automatically.
Keyword and theme tags below are applied when matching terms
appear in the article body (case-insensitive).

## Keyword Tags

Map specific terms to a tag. Format: `term → #tag`

RSAC → #RSAC
Kubernetes → #kubernetes
CRISPR → #biotech

## Theme Tags

Group related keywords under one tag. If ANY keyword in the
group appears in the article, the tag is applied.

### #AI-infrastructure
GPU
TPU
tensor core
data center
NVIDIA
CUDA
inference
training cluster
machine learning infrastructure

### #cybersecurity
CISO
threat detection
zero-day
vulnerability
SOC
incident response
penetration testing
security operations

### #economics
monetary policy
inflation
interest rate
labor market
GDP
fiscal policy
central bank

### #writing
LLM writing
prose style
creative writing
essay
literary criticism
"""


_SETTINGS_SEED = """\
# Settings

Toggle features on or off. Changes take effect on the next sync cycle.

## Daily Notes

When you archive an article with highlights or notes, they are
automatically appended to your daily note (YYYY-MM-DD.md at the
vault root) with a link back to the archived article.

daily notes: on
"""


def seed_config_files() -> None:
    """Create default config files in Mail/Configuration/ if they don't exist."""
    config = get_config_dir()

    allowlist = config / "Allowlist.md"
    if not allowlist.exists():
        allowlist.write_text(_ALLOWLIST_SEED)

    own_addr = config / "Own Addresses.md"
    if not own_addr.exists():
        own_addr.write_text(_OWN_ADDRESSES_SEED)

    guide = config / "How to Annotate.md"
    if not guide.exists():
        guide.write_text(_ANNOTATION_GUIDE_SEED)

    tags_config = config / "Tags.md"
    if not tags_config.exists():
        tags_config.write_text(_TAGS_CONFIG_SEED)

    settings = config / "Settings.md"
    if not settings.exists():
        settings.write_text(_SETTINGS_SEED)


def get_trash_dir() -> Path:
    """Get or create the Mail/Trash directory in the vault."""
    trash = get_mail_dir() / "Trash"
    trash.mkdir(parents=True, exist_ok=True)
    return trash


def get_originals_dir() -> Path:
    """Get or create the Mail/.originals directory (hidden from Obsidian)."""
    originals = get_mail_dir() / ".originals"
    originals.mkdir(parents=True, exist_ok=True)
    return originals


def get_attachments_dir(parent: Path | None = None) -> Path:
    """Get or create the _attachments directory under the given parent.

    Args:
        parent: Directory to put _attachments in. Defaults to Mail/Inbox.
    """
    if parent is None:
        parent = get_inbox_dir()
    att_dir = parent / "_attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    return att_dir


def write_email(
    email: EmailContent,
    attachment_data: dict[str, bytes] | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Write an email to the vault as a markdown file.

    Args:
        email: Parsed email content.
        attachment_data: Map of attachment_id -> raw bytes. If None,
            attachments are listed but not saved.
        tags: Auto-generated tags to include in the Tags section.

    Returns:
        Path to the written markdown file.
    """
    inbox = get_inbox_dir()
    filename = _make_filename(email, inbox)
    filepath = inbox / filename

    attachment_refs = _save_attachments(email, attachment_data)
    content = _format_markdown(email, attachment_refs, tags or [])
    _atomic_write(filepath, content)

    # Save an original copy for diffing when user annotates and archives
    originals = get_originals_dir()
    _atomic_write(originals / filename, content)

    return filepath


def _make_filename(email: EmailContent, inbox: Path) -> str:
    """Generate a unique filename for the email.

    Format: {YYYY-MM-DD} {sanitized subject}.md
    Handles collisions by appending -2, -3, etc.
    """
    date_prefix = email.date.strftime("%Y-%m-%d")
    subject = email.subject or "(no subject)"
    sanitized = _sanitize_filename(subject)
    truncated = sanitized[:100]

    base = f"{date_prefix} {truncated}"
    candidate = f"{base}.md"

    if not (inbox / candidate).exists():
        return candidate

    counter = 2
    while (inbox / f"{base}-{counter}.md").exists():
        counter += 1
    return f"{base}-{counter}.md"


def _sanitize_filename(name: str) -> str:
    """Remove characters invalid in filenames."""
    # Replace invalid chars with space (so adjacent words don't merge)
    cleaned = re.sub(r'[/\\:*?"<>|]', " ", name)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _save_attachments(
    email: EmailContent,
    attachment_data: dict[str, bytes] | None,
) -> list[tuple[Attachment, str]]:
    """Save attachments to disk and return (attachment, relative_path) pairs."""
    if not email.attachments or not attachment_data:
        return []

    att_dir = get_attachments_dir()
    refs: list[tuple[Attachment, str]] = []

    for att in email.attachments:
        data = attachment_data.get(att.attachment_id)
        if data is None:
            continue

        safe_name = _sanitize_filename(att.filename) or "attachment"
        filename = f"{email.gmail_id}_{safe_name}"
        filepath = att_dir / filename
        _atomic_write_bytes(filepath, data)

        rel_path = f"_attachments/{filename}"
        refs.append((att, rel_path))

    return refs


def _format_markdown(
    email: EmailContent,
    attachment_refs: list[tuple[Attachment, str]],
    tags: list[str] | None = None,
) -> str:
    """Format email as markdown with properties at the bottom."""
    date_str = email.date.strftime("%Y-%m-%dT%H:%M:%SZ") if email.date else ""
    created_str = email.date.strftime("%Y-%m-%d") if email.date else ""

    body = email.body_markdown
    fwd_block = ""

    # Extract forwarded message header from body
    fwd_match = _FORWARDED_BLOCK_RE.search(body)
    if fwd_match:
        fwd_block = fwd_match.group(1).strip()
        body = body[:fwd_match.start()] + body[fwd_match.end():]
        body = body.strip()

    # Strip "Fwd:" / "Fwd: " prefix from subject for the title
    title = re.sub(r"^(?:Fwd?:\s*)+", "", email.subject, flags=re.IGNORECASE).strip()
    title = title or email.subject

    lines = [
        f"# {title}",
        "",
        body,
    ]

    if attachment_refs:
        lines.extend(["", "## Attachments", ""])
        for att, rel_path in attachment_refs:
            if att.mime_type.startswith("image/"):
                lines.append(f"![[{rel_path}]]")
            else:
                lines.append(f"[[{rel_path}|{att.filename}]]")

    if email.attachments and not attachment_refs:
        lines.extend(["", "## Attachments", ""])
        for att in email.attachments:
            lines.append(f"- {att.filename} ({att.mime_type}, {att.size} bytes)")

    # Checkboxes and tags
    tag_line = " ".join(f"#{t}" for t in (tags or []))
    lines.extend([
        "",
        "---",
        "- [ ] read",
        "- [ ] delete",
        "",
        "###### Tags",
        "",
        tag_line,
        "",
    ])

    # Forwarded message header (foldable heading)
    if fwd_block:
        lines.extend([
            "",
            "###### Forwarded message",
            "",
            fwd_block,
        ])

    # Properties (foldable heading)
    lines.extend([
        "",
        "###### Properties",
        "",
        "| | |",
        "|---|---|",
        f"| created | {created_str} |",
        "| source | gmail-sync |",
        f"| from | {email.from_addr} |",
        f"| to | {email.to_addr} |",
        f"| date | {date_str} |",
        f"| subject | {email.subject} |",
        f"| message_id | {email.message_id} |",
        f"| gmail_id | {email.gmail_id} |",
        f"| labels | {', '.join(email.labels)} |",
        "",
    ])

    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    """Write text content atomically via temp file + rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write binary content atomically via temp file + rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)
