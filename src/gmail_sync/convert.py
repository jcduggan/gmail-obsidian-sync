"""Convert Gmail API message payloads to markdown."""

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from markdownify import markdownify


@dataclass
class Attachment:
    """Metadata for a message attachment."""

    filename: str
    mime_type: str
    attachment_id: str
    size: int = 0


@dataclass
class EmailContent:
    """Parsed email content ready for vault writing."""

    subject: str
    from_addr: str
    to_addr: str
    date: datetime
    message_id: str
    gmail_id: str
    labels: list[str]
    body_markdown: str
    attachments: list[Attachment] = field(default_factory=list)


def parse_message(msg: dict) -> EmailContent:
    """Parse a Gmail API full-format message into EmailContent.

    Args:
        msg: Gmail API message resource (format="full").

    Returns:
        Parsed EmailContent with markdown body.
    """
    headers = _extract_headers(msg["payload"])
    date = _parse_date(headers.get("Date", ""))
    body = _extract_body(msg["payload"])
    attachments = _extract_attachments(msg["payload"])

    return EmailContent(
        subject=headers.get("Subject", ""),
        from_addr=headers.get("From", ""),
        to_addr=headers.get("To", ""),
        date=date,
        message_id=headers.get("Message-Id", headers.get("Message-ID", "")),
        gmail_id=msg["id"],
        labels=msg.get("labelIds", []),
        body_markdown=body,
        attachments=attachments,
    )


def _extract_headers(payload: dict) -> dict[str, str]:
    """Extract headers from a payload into a simple dict."""
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


def _parse_date(date_str: str) -> datetime:
    """Parse an email Date header into a datetime.

    Handles common email date formats. Falls back to current time
    if parsing fails.
    """
    if not date_str:
        return datetime.now(UTC)

    # Strip timezone name in parentheses, e.g. "(PST)"
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", date_str.strip())

    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

    return datetime.now(UTC)


def _extract_body(payload: dict) -> str:
    """Extract the message body as markdown.

    Prefers text/plain when substantive. Falls back to text/html
    converted to markdown.
    """
    plain = _find_part_data(payload, "text/plain")
    html = _find_part_data(payload, "text/html")

    if plain and _is_substantive(plain):
        return _clean_text(plain)

    if html:
        md = _html_to_markdown(html)
        return _clean_text(md)

    if plain:
        return _clean_text(plain)

    return "(no body content)"


def _find_part_data(payload: dict, mime_type: str) -> str | None:
    """Recursively find and decode a MIME part by type."""
    if payload.get("mimeType") == mime_type:
        body = payload.get("body", {})
        data = body.get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return None

    for part in payload.get("parts", []):
        result = _find_part_data(part, mime_type)
        if result is not None:
            return result

    return None


def _is_substantive(text: str) -> bool:
    """Check if plain text is substantive (not just a 'view in browser' stub)."""
    stripped = text.strip()
    if len(stripped) < 50:
        return False
    stub_phrases = [
        "view this email in your browser",
        "click here to view",
        "having trouble viewing",
        "view as webpage",
        "view online",
    ]
    lower = stripped.lower()
    return all(not (phrase in lower and len(stripped) < 200) for phrase in stub_phrases)


def _html_to_markdown(html: str) -> str:
    """Convert HTML email body to markdown."""
    # Remove tracking pixels (1x1 images)
    html = re.sub(
        r'<img[^>]*(?:width\s*=\s*["\']?1["\']?\s+height\s*=\s*["\']?1["\']?'
        r"|height\s*=\s*[\"']?1[\"']?\\s+width\\s*=\\s*[\"']?1[\"']?)[^>]*>",
        "",
        html,
        flags=re.IGNORECASE,
    )

    # Remove style and script tags entirely
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)

    return markdownify(html, strip=["img"], wrap=True, wrap_width=80)


def _clean_text(text: str) -> str:
    """Clean up converted text: collapse blank lines, strip trailing whitespace."""
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing whitespace on each line
    lines = [line.rstrip() for line in text.split("\n")]
    # Strip leading/trailing blank lines
    text = "\n".join(lines).strip()
    return text


def _extract_attachments(payload: dict) -> list[Attachment]:
    """Recursively extract attachment metadata from a message payload."""
    attachments: list[Attachment] = []
    _collect_attachments(payload, attachments)
    return attachments


def _collect_attachments(payload: dict, acc: list[Attachment]) -> None:
    """Recursively collect attachment metadata."""
    filename = payload.get("filename", "")
    body = payload.get("body", {})
    attachment_id = body.get("attachmentId")

    if filename and attachment_id:
        acc.append(
            Attachment(
                filename=filename,
                mime_type=payload.get("mimeType", "application/octet-stream"),
                attachment_id=attachment_id,
                size=body.get("size", 0),
            )
        )

    for part in payload.get("parts", []):
        _collect_attachments(part, acc)
