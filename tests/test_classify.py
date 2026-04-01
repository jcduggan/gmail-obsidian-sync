"""Tests for newsletter classification."""

import base64

from gmail_sync.classify import is_newsletter


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _make_msg(
    *,
    from_addr: str = "someone@example.com",
    subject: str = "Test",
    extra_headers: dict[str, str] | None = None,
    body: str = "Short body",
) -> dict:
    """Build a minimal Gmail API message dict for classification tests."""
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "Subject", "value": subject},
        {"name": "To", "value": "me@gmail.com"},
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            headers.append({"name": k, "value": v})

    return {
        "id": "msg_test",
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


class TestAllowlist:
    def test_allowlisted_domain(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.classify.get_config_dir", lambda: tmp_path)
        (tmp_path / "Allowlist.md").write_text("example.com\n")

        msg = _make_msg(from_addr="newsletter@example.com")
        assert is_newsletter(msg, "me@gmail.com")

    def test_allowlisted_exact_email(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.classify.get_config_dir", lambda: tmp_path)
        (tmp_path / "Allowlist.md").write_text("specific@example.com\n")

        msg = _make_msg(from_addr="specific@example.com")
        assert is_newsletter(msg, "me@gmail.com")

        msg2 = _make_msg(from_addr="other@example.com")
        assert not is_newsletter(msg2, "me@gmail.com")

    def test_allowlist_comments_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.classify.get_config_dir", lambda: tmp_path)
        (tmp_path / "Allowlist.md").write_text("# a comment\nexample.com\n")

        msg = _make_msg(from_addr="news@example.com")
        assert is_newsletter(msg, "me@gmail.com")

    def test_no_allowlist_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.classify.get_config_dir", lambda: tmp_path / "empty")

        msg = _make_msg(from_addr="news@example.com")
        assert not is_newsletter(msg, "me@gmail.com")


class TestSelfForwarded:
    def test_from_own_address_is_newsletter(self):
        msg = _make_msg(from_addr="Joe Duggan <me@gmail.com>")
        assert is_newsletter(msg, "me@gmail.com")

    def test_from_other_address_is_not(self):
        msg = _make_msg(from_addr="stranger@example.com")
        assert not is_newsletter(msg, "me@gmail.com")


class TestKnownPlatforms:
    def test_substack_sender(self):
        msg = _make_msg(from_addr="Author Name <author@substack.com>")
        assert is_newsletter(msg, "me@gmail.com")

    def test_wordpress_sender(self):
        msg = _make_msg(from_addr="Blog <donotreply@wordpress.com>")
        assert is_newsletter(msg, "me@gmail.com")

    def test_beehiiv_sender(self):
        msg = _make_msg(from_addr="news@beehiiv.com")
        assert is_newsletter(msg, "me@gmail.com")

    def test_ghost_sender(self):
        msg = _make_msg(from_addr="blog@ghost.io")
        assert is_newsletter(msg, "me@gmail.com")

    def test_mailchimp_campaign_header(self):
        msg = _make_msg(
            from_addr="info@somecompany.com",
            extra_headers={"X-MC-User": "abc123"},
        )
        assert is_newsletter(msg, "me@gmail.com")

    def test_sendgrid_campaign(self):
        msg = _make_msg(
            from_addr="info@somecompany.com",
            extra_headers={"X-SG-EID": "abc", "X-Entity-Ref-ID": "def"},
        )
        assert is_newsletter(msg, "me@gmail.com")

    def test_x_mailer_substack(self):
        msg = _make_msg(
            from_addr="custom@owndomain.com",
            extra_headers={"X-Mailer": "Substack Mailer"},
        )
        assert is_newsletter(msg, "me@gmail.com")


class TestListHeaders:
    def test_list_unsubscribe_with_long_body(self):
        msg = _make_msg(
            from_addr="unknown@customdomain.com",
            extra_headers={"List-Unsubscribe": "<https://example.com/unsub>"},
            body="A" * 600,
        )
        assert is_newsletter(msg, "me@gmail.com")

    def test_list_id_with_long_body(self):
        msg = _make_msg(
            from_addr="unknown@customdomain.com",
            extra_headers={"List-Id": "<newsletter.example.com>"},
            body="A" * 600,
        )
        assert is_newsletter(msg, "me@gmail.com")

    def test_list_unsubscribe_with_short_body_is_not(self):
        msg = _make_msg(
            from_addr="promo@store.com",
            extra_headers={"List-Unsubscribe": "<https://store.com/unsub>"},
            body="50% OFF SALE!",
        )
        assert not is_newsletter(msg, "me@gmail.com")


class TestNonNewsletters:
    def test_personal_email(self):
        msg = _make_msg(
            from_addr="friend@example.com",
            subject="Hey, how are you?",
            body="Just wanted to check in!",
        )
        assert not is_newsletter(msg, "me@gmail.com")

    def test_billing_email(self):
        msg = _make_msg(
            from_addr="billing@service.com",
            subject="Your monthly invoice",
            body="Your invoice for March 2026 is $9.99.",
        )
        assert not is_newsletter(msg, "me@gmail.com")

    def test_security_alert(self):
        msg = _make_msg(
            from_addr="Google <no-reply@accounts.google.com>",
            subject="Security alert",
            body="You allowed access to your account.",
        )
        assert not is_newsletter(msg, "me@gmail.com")
