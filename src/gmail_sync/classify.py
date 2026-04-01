"""Newsletter classification: keep newsletters/articles, skip everything else."""

import base64
import logging
import re
from pathlib import Path

from gmail_sync.writer import get_config_dir

log = logging.getLogger(__name__)

_NEWSLETTER_DOMAINS = {
    "substack.com",
    "wordpress.com",
    "beehiiv.com",
    "mailchimp.com",
    "mail.mailchimp.com",
    "convertkit.com",
    "ck.page",
    "ghost.io",
    "buttondown.email",
    "revue.email",
    "getrevue.co",
    "sendfox.com",
    "mailerlite.com",
    "campaignmonitor.com",
    "hubspot.com",
    "hubspotmail.com",
    "keap-mail.com",
    "aweber.com",
    "activecampaign.com",
    "drip.com",
    "moosend.com",
    "flodesk.com",
    "emailoctopus.com",
    "kit.com",
}

_NEWSLETTER_MAILER_KEYWORDS = [
    "substack",
    "mailchimp",
    "convertkit",
    "beehiiv",
    "ghost",
    "buttondown",
    "sendgrid",
    "mailerlite",
    "campaignmonitor",
    "activecampaign",
]

_CAMPAIGN_HEADERS = {
    "X-MC-User",
    "X-Campaign-Id",
    "X-Campaignid",
    "X-Mailchimp-Campaign",
}

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w.-]+)")

MIN_BODY_LENGTH = 500


def is_newsletter(msg: dict, user_email: str) -> bool:
    """Classify a Gmail API message as newsletter/article.

    Returns True if the email should be kept (written to vault).
    Returns False if it should be skipped silently.
    """
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    from_addr = headers.get("From", "")

    if _is_allowlisted(from_addr):
        return True

    if _is_self_forwarded(from_addr, user_email):
        return True

    if _is_known_platform(from_addr, headers):
        return True

    return _has_list_headers(headers) and _has_substantial_body(msg)


def _is_allowlisted(from_addr: str) -> bool:
    """Check if sender matches the user's allowlist."""
    entries = _load_allowlist(get_config_dir() / "Allowlist.md")
    if not entries:
        return False

    from_lower = from_addr.lower()
    match = _EMAIL_RE.search(from_lower)
    if not match:
        return False

    sender_domain = match.group(1)
    sender_email = match.group(0)

    for entry in entries:
        if "@" in entry:
            if entry == sender_email:
                return True
        elif sender_domain == entry or sender_domain.endswith("." + entry):
            return True

    return False


def _load_allowlist(path: Path) -> list[str]:
    """Load and parse the allowlist file. Returns lowercase entries."""
    if not path.exists():
        return []

    try:
        lines = path.read_text().splitlines()
        return [
            line.strip().lower()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError:
        return []


def _is_self_forwarded(from_addr: str, user_email: str) -> bool:
    """Check if the email is from the user's own address or a linked account.

    Matches against the Gmail address and any addresses listed in
    ~/.gmail-obsidian/own-addresses.txt (one per line).
    """
    match = _EMAIL_RE.search(from_addr.lower())
    if not match:
        return False
    sender = match.group(0)

    if sender == user_email.lower():
        return True

    own = _load_own_addresses(get_config_dir() / "Own Addresses.md")
    return sender in own


def _load_own_addresses(path: Path) -> set[str]:
    """Load additional own email addresses from config file."""
    if not path.exists():
        return set()
    try:
        lines = path.read_text().splitlines()
        return {
            line.strip().lower()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        }
    except OSError:
        return set()


def _is_known_platform(from_addr: str, headers: dict[str, str]) -> bool:
    """Check if the email is from a known newsletter platform."""
    # Check sender domain
    match = _EMAIL_RE.search(from_addr.lower())
    if match:
        domain = match.group(1)
        for platform_domain in _NEWSLETTER_DOMAINS:
            if domain == platform_domain or domain.endswith("." + platform_domain):
                return True

    # Check X-Mailer header
    x_mailer = headers.get("X-Mailer", "").lower()
    if any(kw in x_mailer for kw in _NEWSLETTER_MAILER_KEYWORDS):
        return True

    # Check campaign-specific headers
    if any(h in headers for h in _CAMPAIGN_HEADERS):
        return True

    # SendGrid campaign (not transactional) — has both X-SG-EID and X-Entity-Ref-ID
    return "X-SG-EID" in headers and "X-Entity-Ref-ID" in headers


def _has_list_headers(headers: dict[str, str]) -> bool:
    """Check for List-Unsubscribe or List-Id headers (RFC 2369)."""
    return "List-Unsubscribe" in headers or "List-Id" in headers


def _has_substantial_body(msg: dict) -> bool:
    """Check if the email body is long enough to be a newsletter article."""
    # Try to get text/plain body length
    plain_data = _find_body_data(msg["payload"], "text/plain")
    if plain_data:
        try:
            text = base64.urlsafe_b64decode(plain_data).decode("utf-8", errors="replace")
            return len(text.strip()) > MIN_BODY_LENGTH
        except (ValueError, UnicodeDecodeError):
            pass

    # Fall back to sizeEstimate from the message
    size = msg.get("sizeEstimate", 0)
    return size > MIN_BODY_LENGTH * 2


def _find_body_data(payload: dict, mime_type: str) -> str | None:
    """Find body data for a MIME type in the payload (non-recursive for speed)."""
    if payload.get("mimeType") == mime_type:
        return payload.get("body", {}).get("data")

    for part in payload.get("parts", []):
        if part.get("mimeType") == mime_type:
            return part.get("body", {}).get("data")
        for subpart in part.get("parts", []):
            if subpart.get("mimeType") == mime_type:
                return subpart.get("body", {}).get("data")

    return None
