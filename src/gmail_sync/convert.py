"""Convert Gmail API message payloads to markdown."""

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter


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


# --- Body source selection ---

def _extract_body(payload: dict) -> str:
    """Extract the message body as markdown.

    Prefers text/plain for normal emails. For newsletters (detected by
    tracking chars, bare URLs, etc.), prefers HTML for proper link and
    heading conversion.
    """
    plain = _find_part_data(payload, "text/plain")
    html = _find_part_data(payload, "text/html")

    if plain and _is_substantive(plain):
        if html and _plain_is_degraded_newsletter(plain):
            md = _html_to_markdown(html)
            return _clean_text(md)
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


# Signals that plain text is a degraded newsletter rendering
_NEWSLETTER_PLAIN_SIGNALS = [
    # Lines of pure invisible characters (Substack tracking padding)
    re.compile(r"^[\u034f\u00ad\u200b-\u200d\u2060\ufeff\s]+$", re.MULTILINE),
    # Bare URLs on their own lines (disconnected from link text)
    re.compile(r"^\s*<https?://[^>]+>\s*$", re.MULTILINE),
    # Substack redirect URLs
    re.compile(r"substack\.com/redirect/"),
    # Newsletter app chrome
    re.compile(r"(?i)^read in app$", re.MULTILINE),
    # Mailchimp/Beehiiv redirect patterns
    re.compile(r"list-manage\.com/track/click"),
    re.compile(r"(?i)^listen to post", re.MULTILINE),
    # WordPress tracking URLs
    re.compile(r"public-api\.wordpress\.com/bar/\?stat=groovemails"),
    # "Read on blog" / "or Reader" digest chrome
    re.compile(r"(?i)^Read on blog$", re.MULTILINE),
]

_NEWSLETTER_SIGNAL_THRESHOLD = 2


def _plain_is_degraded_newsletter(plain: str) -> bool:
    """Detect if plain text is a degraded newsletter where HTML would be better."""
    return (
        sum(1 for p in _NEWSLETTER_PLAIN_SIGNALS if p.search(plain))
        >= _NEWSLETTER_SIGNAL_THRESHOLD
    )


# --- HTML to Markdown conversion ---

class EmailMarkdownConverter(MarkdownConverter):
    """Markdown converter optimized for email HTML."""

    class Options(MarkdownConverter.DefaultOptions):
        heading_style = "atx"
        strip = ["img"]  # script/style/meta/link decomposed in BeautifulSoup
        wrap = True
        wrap_width = 80
        escape_asterisks = False
        escape_underscores = False


def _fix_link_punctuation(text: str) -> str:
    """Fix quotes and punctuation that markdownify misplaces at link boundaries.

    Handles the case where an opening quote is inside the link text but
    the closing quote lands outside: [..."word](url)" → [..."word"](url)
    """

    def _fix_trailing_quote(m: re.Match) -> str:
        link_text = m.group(1)
        url = m.group(2)
        quote = m.group(3)
        # Count unescaped quotes in the link text
        quote_count = link_text.count(quote)
        if quote_count % 2 == 1:
            # Odd number = unmatched opening quote — pull closing quote in
            return f"[{link_text}{quote}]({url})"
        # Even = quotes are balanced, leave the trailing quote outside
        return m.group(0)

    # Pattern 1: Unmatched quote inside link text, closing quote after url
    # [..."word](url)" → [..."word"](url)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)(["\u201c\u201d])',
        _fix_trailing_quote,
        text,
    )

    # Pattern 2: Quotes wrapping entire link: "[ text](url)" → "[text"](url)
    # The opening quote is before [, closing quote after )
    text = re.sub(
        r'(["\u201c])(\[[^\]]+\]\([^)]+\))(["\u201d])',
        _fix_wrapping_quotes,
        text,
    )
    return text


def _fix_wrapping_quotes(m: re.Match) -> str:
    """Move quotes that wrap a markdown link into the link text."""
    open_q = m.group(1)
    link = m.group(2)  # [text](url)
    close_q = m.group(3)
    # Insert quotes inside the link text: "[text"](url)
    bracket_end = link.index("](")
    return link[:1] + open_q + link[1:bracket_end] + close_q + link[bracket_end:]


_BOILERPLATE_TEXT_PATTERNS = [
    re.compile(r"(?i)unsubscribe"),
    re.compile(r"(?i)read\s+in\s+app"),
    re.compile(r"(?i)forwarded\s+this\s+email\?\s*subscribe"),
    re.compile(r"(?i)upgrade\s+to\s+paid"),
    re.compile(r"(?i)©\s*\d{4}"),
    re.compile(r"(?i)start\s+writing"),
    re.compile(r"(?i)manage\s+(?:your\s+)?(?:email\s+)?preferences"),
    re.compile(r"(?i)view\s+(?:this\s+)?(?:email\s+)?in\s+(?:your\s+)?browser"),
]


def _html_to_markdown(html: str) -> str:
    """Convert HTML email body to markdown with boilerplate stripping."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove tags whose content should be discarded entirely
    for tag_name in ("script", "style", "meta", "link"):
        for el in soup.find_all(tag_name):
            el.decompose()

    _strip_tracking_pixels(soup)
    _strip_hidden_elements(soup)
    _clean_link_urls(soup)
    _unwrap_layout_tables(soup)
    _strip_boilerplate_elements(soup)

    converter = EmailMarkdownConverter()
    md = converter.convert_soup(soup)
    md = _fix_link_punctuation(md)
    return md


_SUBSTACK_REDIRECT_2_RE = re.compile(
    r"https://substack\.com/redirect/2/([A-Za-z0-9_-]+)"
)

# Query params that are pure tracking — safe to strip from any URL
_TRACKING_PARAMS = {"j", "utm_source", "utm_medium", "utm_campaign", "utm_content", "r", "token"}


def _clean_link_urls(soup: BeautifulSoup) -> None:
    """Clean tracking junk from link URLs in the HTML.

    - Substack /redirect/2/{base64}: decode to get the real destination
    - Substack /redirect/{uuid}?j=...: strip the ?j= tracking param
    - All URLs: strip utm_* and other tracking query params
    """
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Decode Substack /redirect/2/ URLs (base64 JWT with real URL)
        m = _SUBSTACK_REDIRECT_2_RE.match(href)
        if m:
            real_url = _decode_substack_redirect(m.group(1))
            if real_url:
                href = real_url

        # Resolve WordPress tracking redirects
        if "public-api.wordpress.com/bar/" in href:
            parsed_wp = urlparse(href)
            wp_params = parse_qs(parsed_wp.query)
            redirect_to = wp_params.get("redirect_to", [None])[0]
            if redirect_to:
                href = redirect_to

        # Resolve WordPress user_content_redirect (base64 encoded_url param)
        if "action=user_content_redirect" in href:
            parsed_wp = urlparse(href)
            wp_params = parse_qs(parsed_wp.query)
            encoded_url = wp_params.get("encoded_url", [None])[0]
            if encoded_url:
                real = _decode_base64_url(encoded_url)
                if real:
                    href = real

        # Strip tracking query params from all URLs
        parsed = urlparse(href)
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            cleaned = {
                k: v for k, v in params.items() if k not in _TRACKING_PARAMS
            }
            new_query = urlencode(cleaned, doseq=True) if cleaned else ""
            href = urlunparse(parsed._replace(query=new_query))

        # Strip trailing ? if all params were removed
        href = href.rstrip("?")

        a["href"] = href


def _decode_substack_redirect(encoded: str) -> str | None:
    """Decode a Substack /redirect/2/ base64 payload to get the real URL."""
    import json

    # Add padding
    padded = encoded + "=" * (4 - len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        data = json.loads(decoded)
        return data.get("e")
    except (ValueError, json.JSONDecodeError, KeyError):
        return None


def _decode_base64_url(encoded: str) -> str | None:
    """Decode a plain base64-encoded URL (used by WordPress redirects)."""
    padded = encoded + "=" * (4 - len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _strip_tracking_pixels(soup: BeautifulSoup) -> None:
    """Remove 1x1 and 0x0 tracking pixel images."""
    for img in soup.find_all("img"):
        width = str(img.get("width", "")).strip()
        height = str(img.get("height", "")).strip()
        if width in ("1", "0") and height in ("1", "0"):
            img.decompose()
            continue
        # Also remove images with no src or empty src (spacer gifs)
        src = img.get("src", "")
        if not src or src.endswith("/spacer.gif"):
            img.decompose()


def _strip_hidden_elements(soup: BeautifulSoup) -> None:
    """Remove elements with display:none or visibility:hidden."""
    pattern = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
    for el in soup.find_all(style=pattern):
        el.decompose()


def _unwrap_layout_tables(soup: BeautifulSoup) -> None:
    """Replace HTML layout tables with their content.

    Email HTML uses tables for layout, not data. These produce ugly
    markdown table syntax. Unwrap them so the content flows naturally.
    A table is considered "layout" if it has no <th> headers.
    """
    for table in soup.find_all("table"):
        # Data tables have <th> elements; keep those as markdown tables
        if table.find("th"):
            continue
        # Unwrap the table structure: replace table/tbody/tr/td with divs
        table.unwrap()

    # Clean up remaining tbody, tr, td tags from unwrapped tables
    for tag_name in ("tbody", "tr", "td"):
        for el in soup.find_all(tag_name):
            el.unwrap()


_BLOCK_TAGS = {"div", "table", "section", "footer", "td", "tr", "p"}


def _strip_boilerplate_elements(soup: BeautifulSoup) -> None:
    """Remove newsletter boilerplate elements (subscribe, unsubscribe, etc.)."""
    for el in soup.find_all(list(_BLOCK_TAGS)):
        el_text = el.get_text(strip=True)
        if not el_text or len(el_text) > 500:
            continue
        hits = sum(1 for p in _BOILERPLATE_TEXT_PATTERNS if p.search(el_text))
        if hits >= 2:
            el.decompose()


# --- Text cleanup ---

_INVISIBLE_CHARS = re.compile(
    "[\u00ad\u034f\u061c\u180e\u200b-\u200f\u2028-\u202f"
    "\u2060-\u2069\u2800\ufeff\ufff9-\ufffb]+"
)

_TRACKING_DOMAINS = re.compile(
    r"substack\.com/redirect|substack\.com/app-link|"
    r"list-manage\.com|mailchi\.mp|"
    r"beehiiv\.com/.*(?:redirect|click)|"
    r"public-api\.wordpress\.com/bar/|"
    r"email\.mg\.|trk\.|track\.|click\."
)

_FORWARDED_HEADER_RE = re.compile(
    r"^-{5,}\s*Forwarded message\s*-{5,}\s*\n"
    r"((?:(?:From|To|Date|Subject|Cc|Bcc):.*\n)+)",
    flags=re.MULTILINE | re.IGNORECASE,
)

_FOOTER_START_PATTERNS = [
    re.compile(r"^©\s*\d{4}", re.MULTILINE),
    re.compile(r"^Unsubscribe$", re.MULTILINE),
    re.compile(r"^You're receiving this", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^This email was sent to", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^To stop receiving", re.MULTILINE | re.IGNORECASE),
]


def _strip_invisible_chars(text: str) -> str:
    """Remove invisible Unicode characters used for tracking/formatting."""
    return _INVISIBLE_CHARS.sub("", text)


def _strip_tracking_urls(text: str) -> str:
    """Remove standalone tracking/redirect URLs on their own lines."""
    lines = text.split("\n")
    return "\n".join(
        line
        for line in lines
        if not (
            re.match(r"^\s*<?https?://\S+>?\s*$", line.strip())
            and _TRACKING_DOMAINS.search(line)
        )
    )


def _style_forwarded_headers(text: str) -> str:
    """Convert forwarded message headers into a blockquote."""

    def _replace(m: re.Match) -> str:
        header_lines = m.group(1).strip().split("\n")
        quoted_lines = []
        for line in header_lines:
            if ":" in line:
                key, value = line.split(":", 1)
                quoted_lines.append(f"> **{key.strip()}:** {value.strip()}")
        return "> **Forwarded message**\n" + "\n".join(quoted_lines) + "\n"

    return _FORWARDED_HEADER_RE.sub(_replace, text)


def _strip_email_footer(text: str) -> str:
    """Remove email footer (copyright, unsubscribe, mailing info)."""
    earliest_pos = len(text)
    for pattern in _FOOTER_START_PATTERNS:
        match = pattern.search(text)
        if match and match.start() > len(text) * 0.7:
            earliest_pos = min(earliest_pos, match.start())

    if earliest_pos < len(text):
        text = text[:earliest_pos].rstrip()
    return text


_BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"^\s*\[READ IN APP\]"),
    re.compile(r"^\s*\[Listen to post"),
    re.compile(r"^\s*\[Like\]"),
    re.compile(r"^\s*\[Comment\]"),
    re.compile(r"^\s*\[Restack\]"),
    re.compile(r"^\s*\[Upgrade to paid\]"),
    re.compile(r"^\s*\[Share\]"),
    re.compile(r"^\s*\[Subscribe\]"),
    re.compile(r"^\s*Forwarded this email\?\s*\[Subscribe"),
    re.compile(r"^\s*\[Like\]\(https?://"),
    re.compile(r"^\s*\[Comment\]\(https?://"),
    re.compile(r"^\s*\[Restack\]\(https?://"),
    re.compile(r"^\s*\[Upgrade to paid\]\(https?://"),
    re.compile(r"^\s*\[READ IN APP\]\(https?://"),
    re.compile(r"^\s*\[Listen to post[^\]]*\]\(https?://"),
    re.compile(r"^\s*\[Share\]\(https?://"),
    re.compile(r"^\s*photo cred:", re.IGNORECASE),
    # WordPress digest chrome
    re.compile(r"^\s*Read on blog\s*$", re.IGNORECASE),
    re.compile(r"^\s*or Reader\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[Read on blog\]"),
    re.compile(r"^\s*\[or Reader\]"),
    re.compile(r"^\s*\[Reader\]\("),
]


def _strip_boilerplate_lines(text: str) -> str:
    """Remove known newsletter boilerplate lines from markdown output."""
    lines = text.split("\n")
    return "\n".join(
        line
        for line in lines
        if not any(p.search(line) for p in _BOILERPLATE_LINE_PATTERNS)
    )


def _clean_text(text: str) -> str:
    """Clean up converted text: strip junk, normalize whitespace."""
    text = _strip_invisible_chars(text)
    text = re.sub(r"\[image:[^\]]*\]", "", text)
    text = _strip_tracking_urls(text)
    text = _strip_boilerplate_lines(text)
    text = _style_forwarded_headers(text)
    text = _strip_email_footer(text)
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing whitespace on each line
    lines = [line.rstrip() for line in text.split("\n")]
    # Strip leading/trailing blank lines
    return "\n".join(lines).strip()


# --- Attachment extraction ---

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
