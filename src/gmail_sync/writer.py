"""Write email content to Obsidian vault as markdown files."""

import os
import re
from pathlib import Path

from gmail_sync.convert import Attachment, EmailContent

_FORWARDED_BLOCK_RE = re.compile(
    r"^(> \*\*Forwarded message\*\*\n(?:> \*\*\w+:\*\*.*\n)+)\n*",
    re.MULTILINE,
)


def get_vault_path() -> Path:
    """Get the Obsidian vault path from environment.

    Returns:
        Path to the vault root.

    Raises:
        SystemExit: If OBSIDIAN_VAULT_PATH is not set or doesn't exist.
    """
    vault = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not vault:
        raise SystemExit(
            "OBSIDIAN_VAULT_PATH environment variable is not set.\n"
            "Set it to your Obsidian vault root directory."
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


def get_trash_dir() -> Path:
    """Get or create the Mail/Trash directory in the vault."""
    trash = get_mail_dir() / "Trash"
    trash.mkdir(parents=True, exist_ok=True)
    return trash


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


def write_email(email: EmailContent, attachment_data: dict[str, bytes] | None = None) -> Path:
    """Write an email to the vault as a markdown file.

    Args:
        email: Parsed email content.
        attachment_data: Map of attachment_id -> raw bytes. If None,
            attachments are listed but not saved.

    Returns:
        Path to the written markdown file.
    """
    inbox = get_inbox_dir()
    filename = _make_filename(email, inbox)
    filepath = inbox / filename

    attachment_refs = _save_attachments(email, attachment_data)
    content = _format_markdown(email, attachment_refs)
    _atomic_write(filepath, content)

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

    # Checkboxes
    lines.extend(["", "---", "- [ ] read", "- [ ] delete"])

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
