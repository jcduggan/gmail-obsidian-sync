"""Tests for Obsidian vault writer."""

from datetime import UTC, datetime

from gmail_sync.convert import Attachment, EmailContent
from gmail_sync.writer import _format_markdown, _make_filename, _sanitize_filename, write_email


def _make_email(**kwargs) -> EmailContent:
    """Create an EmailContent with sensible defaults."""
    defaults = {
        "subject": "Test Subject",
        "from_addr": "sender@example.com",
        "to_addr": "me@example.com",
        "date": datetime(2026, 3, 31, 14, 0, 0, tzinfo=UTC),
        "message_id": "<test-123@example.com>",
        "gmail_id": "msg_test_123",
        "labels": ["INBOX", "UNREAD"],
        "body_markdown": "Hello, this is a test email.",
        "attachments": [],
    }
    defaults.update(kwargs)
    return EmailContent(**defaults)


class TestSanitizeFilename:
    def test_strips_invalid_chars(self):
        assert _sanitize_filename('Re: Hello/World "test"') == "Re Hello World test"

    def test_collapses_spaces(self):
        assert _sanitize_filename("too   many   spaces") == "too many spaces"

    def test_strips_edges(self):
        assert _sanitize_filename("  padded  ") == "padded"

    def test_empty_string(self):
        assert _sanitize_filename("") == ""

    def test_all_invalid_chars(self):
        assert _sanitize_filename('/:*?"<>|\\') == ""
        # All replaced with spaces, then collapsed and stripped


class TestMakeFilename:
    def test_basic_filename(self, tmp_path):
        email = _make_email(subject="Weekly Newsletter")
        filename = _make_filename(email, tmp_path)
        assert filename == "2026-03-31 Weekly Newsletter.md"

    def test_empty_subject(self, tmp_path):
        email = _make_email(subject="")
        filename = _make_filename(email, tmp_path)
        assert filename == "2026-03-31 (no subject).md"

    def test_collision_handling(self, tmp_path):
        email = _make_email(subject="Same Title")

        # Create the first file
        (tmp_path / "2026-03-31 Same Title.md").touch()

        filename = _make_filename(email, tmp_path)
        assert filename == "2026-03-31 Same Title-2.md"

    def test_multiple_collisions(self, tmp_path):
        email = _make_email(subject="Same Title")

        (tmp_path / "2026-03-31 Same Title.md").touch()
        (tmp_path / "2026-03-31 Same Title-2.md").touch()
        (tmp_path / "2026-03-31 Same Title-3.md").touch()

        filename = _make_filename(email, tmp_path)
        assert filename == "2026-03-31 Same Title-4.md"

    def test_truncates_long_subjects(self, tmp_path):
        email = _make_email(subject="x" * 200)
        filename = _make_filename(email, tmp_path)
        # date prefix (10) + space (1) + truncated (100) + .md (3) = 114
        assert len(filename) <= 114


class TestFormatMarkdown:
    def test_basic_format(self):
        email = _make_email()
        result = _format_markdown(email, [])

        assert "---" in result
        assert "created: 2026-03-31" in result
        assert "source: gmail-sync" in result
        assert 'from: "sender@example.com"' in result
        assert "# Test Subject" in result
        assert "Hello, this is a test email." in result
        assert "- [ ] read" in result
        assert "- [ ] delete" in result

    def test_with_attachment_refs(self):
        att = Attachment(
            filename="doc.pdf",
            mime_type="application/pdf",
            attachment_id="att_1",
            size=1024,
        )
        email = _make_email(attachments=[att])
        refs = [(att, "_attachments/msg_test_123_doc.pdf")]
        result = _format_markdown(email, refs)

        assert "[[_attachments/msg_test_123_doc.pdf|doc.pdf]]" in result

    def test_with_image_attachment(self):
        att = Attachment(
            filename="photo.png",
            mime_type="image/png",
            attachment_id="att_1",
            size=2048,
        )
        email = _make_email(attachments=[att])
        refs = [(att, "_attachments/msg_test_123_photo.png")]
        result = _format_markdown(email, refs)

        assert "![[_attachments/msg_test_123_photo.png]]" in result

    def test_attachments_listed_when_no_data(self):
        att = Attachment(
            filename="report.pdf",
            mime_type="application/pdf",
            attachment_id="att_1",
            size=1024,
        )
        email = _make_email(attachments=[att])
        result = _format_markdown(email, [])

        assert "report.pdf (application/pdf, 1024 bytes)" in result


class TestWriteEmail:
    def test_writes_file_to_vault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        email = _make_email(subject="My Newsletter")
        path = write_email(email)

        assert path.exists()
        assert path.name == "2026-03-31 My Newsletter.md"
        content = path.read_text()
        assert "# My Newsletter" in content
        assert "- [ ] read" in content

    def test_creates_inbox_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        email = _make_email()
        path = write_email(email)

        assert (tmp_path / "Mail" / "Inbox").is_dir()
        assert path.parent == tmp_path / "Mail" / "Inbox"

    def test_saves_attachments(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        att = Attachment(
            filename="doc.pdf",
            mime_type="application/pdf",
            attachment_id="att_1",
            size=4,
        )
        email = _make_email(attachments=[att])
        attachment_data = {"att_1": b"test"}
        path = write_email(email, attachment_data)

        att_path = tmp_path / "Mail" / "Inbox" / "_attachments" / "msg_test_123_doc.pdf"
        assert att_path.exists()
        assert att_path.read_bytes() == b"test"

        content = path.read_text()
        assert "[[_attachments/msg_test_123_doc.pdf|doc.pdf]]" in content

    def test_no_vault_path_exits(self, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        import pytest

        with pytest.raises(SystemExit, match="OBSIDIAN_VAULT_PATH"):
            write_email(_make_email())

    def test_nonexistent_vault_exits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "nonexistent"))
        import pytest

        with pytest.raises(SystemExit, match="does not exist"):
            write_email(_make_email())
