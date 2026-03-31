"""Tests for MIME parsing and markdown conversion."""

import base64
from datetime import datetime

from gmail_sync.convert import (
    EmailContent,
    _clean_text,
    _html_to_markdown,
    _is_substantive,
    _parse_date,
    parse_message,
)


def _b64(text: str) -> str:
    """Base64url-encode a string for Gmail API payloads."""
    return base64.urlsafe_b64encode(text.encode()).decode()


def _make_message(
    *,
    subject: str = "Test Subject",
    from_addr: str = "sender@example.com",
    to_addr: str = "me@example.com",
    date: str = "Mon, 31 Mar 2026 14:00:00 +0000",
    body_plain: str | None = None,
    body_html: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """Build a minimal Gmail API message dict for testing."""
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to_addr},
        {"name": "Date", "value": date},
        {"name": "Message-ID", "value": "<test-123@example.com>"},
    ]

    if body_plain and body_html:
        payload = {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64(body_plain)},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64(body_html)},
                },
            ],
        }
    elif body_plain:
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": _b64(body_plain)},
        }
    elif body_html:
        payload = {
            "mimeType": "text/html",
            "headers": headers,
            "body": {"data": _b64(body_html)},
        }
    else:
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {},
        }

    return {
        "id": "msg_test_123",
        "threadId": "thread_test_123",
        "labelIds": labels or ["INBOX", "UNREAD"],
        "payload": payload,
    }


class TestParseMessage:
    def test_plain_text_message(self):
        msg = _make_message(body_plain="Hello, this is a plain text email with enough content.")
        result = parse_message(msg)

        assert isinstance(result, EmailContent)
        assert result.subject == "Test Subject"
        assert result.from_addr == "sender@example.com"
        assert result.to_addr == "me@example.com"
        assert result.gmail_id == "msg_test_123"
        assert result.labels == ["INBOX", "UNREAD"]
        assert "Hello, this is a plain text email" in result.body_markdown

    def test_html_only_message(self):
        html = "<h1>Newsletter</h1><p>This is <strong>bold</strong> content in HTML.</p>"
        msg = _make_message(body_html=html)
        result = parse_message(msg)

        assert "Newsletter" in result.body_markdown
        assert "**bold**" in result.body_markdown

    def test_multipart_prefers_plain_when_substantive(self):
        plain = "This is the plain text version with enough content to be substantive for our test."
        html = "<p>This is the HTML version with <b>formatting</b></p>"
        msg = _make_message(body_plain=plain, body_html=html)
        result = parse_message(msg)

        assert "plain text version" in result.body_markdown
        assert "<p>" not in result.body_markdown

    def test_multipart_falls_back_to_html_when_plain_is_stub(self):
        plain = "View this email in your browser"
        html = "<h1>Real Content</h1><p>This is the actual newsletter with real content.</p>"
        msg = _make_message(body_plain=plain, body_html=html)
        result = parse_message(msg)

        assert "Real Content" in result.body_markdown

    def test_empty_body(self):
        msg = _make_message()
        result = parse_message(msg)
        assert result.body_markdown == "(no body content)"

    def test_message_with_attachments(self):
        msg = _make_message(body_plain="See attached." + " " * 50)
        msg["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": msg["payload"]["headers"],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("See attached." + " " * 50)},
                },
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_123", "size": 1024},
                },
            ],
        }
        result = parse_message(msg)

        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "report.pdf"
        assert result.attachments[0].attachment_id == "att_123"
        assert result.attachments[0].size == 1024


class TestParseDate:
    def test_standard_rfc2822(self):
        dt = _parse_date("Mon, 31 Mar 2026 14:00:00 +0000")
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 31

    def test_with_timezone_name(self):
        dt = _parse_date("Mon, 31 Mar 2026 14:00:00 -0700 (PDT)")
        assert dt.year == 2026
        assert dt.hour == 14

    def test_without_day_name(self):
        dt = _parse_date("31 Mar 2026 14:00:00 +0000")
        assert dt.year == 2026
        assert dt.day == 31

    def test_empty_string_returns_now(self):
        dt = _parse_date("")
        assert dt.year >= 2026

    def test_garbage_returns_now(self):
        dt = _parse_date("not a date at all")
        assert isinstance(dt, datetime)


class TestIsSubstantive:
    def test_short_text_is_not_substantive(self):
        assert not _is_substantive("Hi")

    def test_long_text_is_substantive(self):
        assert _is_substantive("x" * 100)

    def test_view_in_browser_stub(self):
        assert not _is_substantive("View this email in your browser. Click here.")

    def test_long_view_in_browser_with_content(self):
        text = "View this email in your browser. " + "Real content here. " * 20
        assert _is_substantive(text)


class TestHtmlToMarkdown:
    def test_basic_html(self):
        result = _html_to_markdown("<p>Hello <strong>world</strong></p>")
        assert "**world**" in result

    def test_strips_style_tags(self):
        html = "<style>.foo { color: red; }</style><p>Content</p>"
        result = _html_to_markdown(html)
        assert "color" not in result
        assert "Content" in result

    def test_strips_script_tags(self):
        html = "<script>alert('xss')</script><p>Safe</p>"
        result = _html_to_markdown(html)
        assert "alert" not in result
        assert "Safe" in result

    def test_links_preserved(self):
        html = '<p>Click <a href="https://example.com">here</a></p>'
        result = _html_to_markdown(html)
        assert "https://example.com" in result


class TestCleanText:
    def test_collapses_blank_lines(self):
        text = "line1\n\n\n\n\nline2"
        result = _clean_text(text)
        assert result == "line1\n\nline2"

    def test_strips_trailing_whitespace(self):
        text = "line1   \nline2  "
        result = _clean_text(text)
        assert result == "line1\nline2"

    def test_strips_leading_trailing_blanks(self):
        text = "\n\n  content  \n\n"
        result = _clean_text(text)
        assert result == "content"
