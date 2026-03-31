"""Tests for MIME parsing and markdown conversion."""

import base64
from datetime import datetime

from gmail_sync.convert import (
    EmailContent,
    _clean_text,
    _html_to_markdown,
    _is_substantive,
    _parse_date,
    _plain_is_degraded_newsletter,
    _strip_email_footer,
    _strip_invisible_chars,
    _strip_tracking_urls,
    _style_forwarded_headers,
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

    def test_multipart_prefers_html_for_newsletter(self):
        plain = (
            "Some newsletter content\n"
            "\u034f     \u00ad\u034f     \u00ad\n"
            "<https://substack.com/redirect/abc123>\n"
            "READ IN APP\n"
            "<https://open.substack.com/pub/test/p/article>\n"
            "More content here that is long enough to be substantive for our test."
        )
        html = (
            '<h2>Newsletter Title</h2>'
            '<p>Click <a href="https://example.com">this link</a> for more.</p>'
        )
        msg = _make_message(body_plain=plain, body_html=html)
        result = parse_message(msg)

        # Should use HTML path: proper heading and link
        assert "## Newsletter Title" in result.body_markdown
        assert "[this link](https://example.com)" in result.body_markdown
        # Should NOT have bare URL lines from plain text
        assert "<https://substack.com" not in result.body_markdown


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


class TestPlainIsDegradedNewsletter:
    def test_detects_substack_newsletter(self):
        plain = (
            "Content here\n"
            "\u034f     \u00ad\u034f     \u00ad\n"
            "<https://substack.com/redirect/abc123>\n"
            "More content"
        )
        assert _plain_is_degraded_newsletter(plain)

    def test_detects_read_in_app_with_bare_urls(self):
        plain = (
            "Newsletter content long enough to be real.\n"
            "READ IN APP\n"
            "<https://example.com/something>\n"
        )
        assert _plain_is_degraded_newsletter(plain)

    def test_ignores_normal_email(self):
        plain = "Hey, just a normal email. Nothing fancy here, just text."
        assert not _plain_is_degraded_newsletter(plain)

    def test_single_signal_not_enough(self):
        plain = "Some text\n<https://example.com/page>\nMore text"
        assert not _plain_is_degraded_newsletter(plain)


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

    def test_links_preserved_as_markdown(self):
        html = '<p>Click <a href="https://example.com">here</a></p>'
        result = _html_to_markdown(html)
        assert "[here](https://example.com)" in result

    def test_headings_are_atx(self):
        html = "<h2>Section Title</h2><p>Content</p>"
        result = _html_to_markdown(html)
        assert "## Section Title" in result

    def test_strips_tracking_pixels(self):
        html = (
            '<p>Content</p>'
            '<img src="https://track.example.com/pixel.gif" width="1" height="1">'
        )
        result = _html_to_markdown(html)
        assert "track.example.com" not in result
        assert "Content" in result

    def test_strips_hidden_elements(self):
        html = (
            '<p>Visible</p>'
            '<div style="display:none">Hidden tracking content</div>'
        )
        result = _html_to_markdown(html)
        assert "Visible" in result
        assert "Hidden" not in result

    def test_strips_boilerplate_with_multiple_signals(self):
        html = (
            '<p>Real article content goes here.</p>'
            '<div>'
            '<a href="https://example.com/unsubscribe">Unsubscribe</a> | '
            '<a href="https://example.com/prefs">Manage preferences</a> | '
            '© 2026 Author'
            '</div>'
        )
        result = _html_to_markdown(html)
        assert "Real article content" in result
        assert "Unsubscribe" not in result

    def test_cleans_utm_params_from_links(self):
        html = (
            '<p>Click <a href="https://example.com/article'
            '?utm_source=email&utm_medium=newsletter&id=42">here</a></p>'
        )
        result = _html_to_markdown(html)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=42" in result
        assert "[here]" in result

    def test_strips_substack_redirect_uuid_tracking(self):
        html = (
            '<p><a href="https://substack.com/redirect/abc-123'
            '?j=eyJ1IjoiNjJmc3kifQ.abc123">click</a></p>'
        )
        result = _html_to_markdown(html)
        assert "?j=" not in result
        assert "substack.com/redirect/abc-123" in result

    def test_decodes_substack_redirect_2(self):
        import base64
        import json

        payload = base64.urlsafe_b64encode(
            json.dumps({"e": "https://example.com/real-article"}).encode()
        ).decode().rstrip("=")
        html = (
            f'<p><a href="https://substack.com/redirect/2/{payload}'
            f'?utm_source=email">click</a></p>'
        )
        result = _html_to_markdown(html)
        assert "example.com/real-article" in result
        assert "substack.com/redirect" not in result

    def test_fixes_trailing_quote_after_link(self):
        # Simulates: <a href="url">"strawberry</a>" — quote misplaced
        html = (
            '<p>the word '
            '<a href="https://example.com">"strawberry</a>"</p>'
        )
        result = _html_to_markdown(html)
        # The closing " should be inside the link text
        assert '[\"strawberry\"](https://example.com)' in result

    def test_fixes_wrapping_quotes_around_link(self):
        html = '<p>a "<a href="https://example.com">country of geniuses</a>".</p>'
        result = _html_to_markdown(html)
        assert '["country of geniuses"](https://example.com)' in result

    def test_preserves_balanced_quotes_after_link(self):
        html = (
            '<p>the <a href="https://example.com">"scare quotes"</a> word</p>'
        )
        result = _html_to_markdown(html)
        # Quotes are balanced inside — trailing " stays outside is fine
        # but actually both quotes are inside the link, so no trailing quote
        assert '"scare quotes"' in result

    def test_preserves_content_with_single_boilerplate_word(self):
        html = (
            '<p>This paragraph discusses how to unsubscribe from '
            'negative thinking patterns. It is long enough content.</p>'
        )
        result = _html_to_markdown(html)
        assert "unsubscribe" in result


class TestStripInvisibleChars:
    def test_removes_zero_width_spaces(self):
        text = "Hello\u200bWorld\u200cFoo\u200dBar"
        assert _strip_invisible_chars(text) == "HelloWorldFooBar"

    def test_removes_soft_hyphens(self):
        text = "test\u00adword"
        assert _strip_invisible_chars(text) == "testword"

    def test_removes_combining_grapheme_joiner(self):
        text = "\u034f     \u00ad\u034f     \u00ad"
        result = _strip_invisible_chars(text)
        assert "\u034f" not in result
        assert "\u00ad" not in result

    def test_preserves_normal_unicode(self):
        text = "Héllo Wörld 你好 café"
        assert _strip_invisible_chars(text) == text

    def test_preserves_ascii(self):
        text = "Hello, World! 123 @#$"
        assert _strip_invisible_chars(text) == text


class TestStripTrackingUrls:
    def test_removes_substack_redirect(self):
        text = "Some text\n<https://substack.com/redirect/abc123>\nMore text"
        result = _strip_tracking_urls(text)
        assert "substack.com/redirect" not in result
        assert "Some text" in result
        assert "More text" in result

    def test_removes_substack_app_link(self):
        text = "Title\n<https://substack.com/app-link/post?id=123>\nContent"
        result = _strip_tracking_urls(text)
        assert "substack.com/app-link" not in result

    def test_removes_mailchimp_tracking(self):
        text = "Article\n<https://list-manage.com/track/click?u=abc>\nMore"
        result = _strip_tracking_urls(text)
        assert "list-manage.com" not in result

    def test_preserves_normal_urls(self):
        text = "Check out https://example.com for more info."
        assert _strip_tracking_urls(text) == text

    def test_preserves_standalone_normal_urls(self):
        text = "Link:\nhttps://example.com\nMore text"
        assert _strip_tracking_urls(text) == text


class TestStyleForwardedHeaders:
    def test_styles_gmail_forward(self):
        text = (
            "Some intro.\n"
            "---------- Forwarded message ---------\n"
            "From: Alice <alice@example.com>\n"
            "Date: Mon, 31 Mar 2026\n"
            "Subject: Hello\n"
            "To: Bob <bob@example.com>\n"
            "\n"
            "The actual forwarded content."
        )
        result = _style_forwarded_headers(text)
        assert "> **Forwarded message**" in result
        assert "> **From:** Alice <alice@example.com>" in result
        assert "> **Subject:** Hello" in result
        assert "The actual forwarded content." in result

    def test_no_match_for_normal_text(self):
        text = "Just a normal email with dashes --- in it."
        assert _style_forwarded_headers(text) == text


class TestStripEmailFooter:
    def test_strips_copyright_at_end(self):
        body = "A" * 100 + "\n\n© 2026 Author\nNew York, NY\nUnsubscribe"
        result = _strip_email_footer(body)
        assert "© 2026" not in result
        assert "A" * 100 in result

    def test_preserves_early_copyright(self):
        body = "© 2026 is discussed here.\n" + "More content. " * 50
        result = _strip_email_footer(body)
        assert "© 2026" in result

    def test_strips_unsubscribe_at_end(self):
        content = "Real content here. " * 20
        body = content + "\nUnsubscribe\nSome address info"
        result = _strip_email_footer(body)
        assert "Unsubscribe" not in result

    def test_no_footer_unchanged(self):
        body = "Just a normal email body with nothing special at the end."
        assert _strip_email_footer(body) == body


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

    def test_removes_image_artifacts(self):
        text = "Click [image: Google] here for info"
        result = _clean_text(text)
        assert "[image:" not in result
        assert "here for info" in result

    def test_removes_invisible_chars(self):
        text = "Hello\u034f\u200b\u00adWorld"
        result = _clean_text(text)
        assert result == "HelloWorld"

    def test_full_pipeline_newsletter_junk(self):
        text = (
            "Real content here.\n"
            "\u034f     \u00ad\u034f     \u00ad\n"
            "\u034f     \u00ad\u034f     \u00ad\n"
            "<https://substack.com/redirect/abc123>\n"
            "[image: Start writing]\n"
            "<https://substack.com/redirect/def456>\n"
            "\n"
            "More real content."
        )
        result = _clean_text(text)
        assert "Real content here." in result
        assert "More real content." in result
        assert "\u034f" not in result
        assert "[image:" not in result
        assert "substack.com/redirect" not in result
