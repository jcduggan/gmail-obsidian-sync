"""Tests for inbox management (checkbox detection and file moves)."""

from pathlib import Path

from gmail_sync.tidy import _detect_action, _move_email, _unique_name, tidy_inbox


def _write_email(inbox: Path, name: str, read_checked: bool = False, delete_checked: bool = False):
    """Write a minimal email file with checkboxes to the inbox."""
    read_box = "- [x] read" if read_checked else "- [ ] read"
    delete_box = "- [x] delete" if delete_checked else "- [ ] delete"
    content = (
        f"---\ncreated: 2026-04-01\nsource: gmail-sync\n"
        f"gmail_id: gmail_{name.lower().replace(' ', '_')}\n---\n\n"
        f"# {name}\n\nBody.\n\n---\n{read_box}\n{delete_box}\n"
    )
    filepath = inbox / f"2026-04-01 {name}.md"
    filepath.write_text(content)
    return filepath


class TestDetectAction:
    def test_unchecked_returns_none(self, tmp_path):
        f = _write_email(tmp_path, "Test")
        assert _detect_action(f) is None

    def test_read_checked(self, tmp_path):
        f = _write_email(tmp_path, "Test", read_checked=True)
        assert _detect_action(f) == "read"

    def test_delete_checked(self, tmp_path):
        f = _write_email(tmp_path, "Test", delete_checked=True)
        assert _detect_action(f) == "delete"

    def test_both_checked_delete_wins(self, tmp_path):
        f = _write_email(tmp_path, "Test", read_checked=True, delete_checked=True)
        assert _detect_action(f) == "delete"

    def test_case_insensitive(self, tmp_path):
        filepath = tmp_path / "test.md"
        filepath.write_text("Body\n\n---\n- [X] Read\n- [ ] Delete\n")
        assert _detect_action(filepath) == "read"

    def test_missing_file_returns_none(self, tmp_path):
        assert _detect_action(tmp_path / "nonexistent.md") is None


class TestUniqueName:
    def test_no_collision(self, tmp_path):
        assert _unique_name("test.md", tmp_path) == "test.md"

    def test_collision_appends_counter(self, tmp_path):
        (tmp_path / "test.md").touch()
        assert _unique_name("test.md", tmp_path) == "test-2.md"

    def test_multiple_collisions(self, tmp_path):
        (tmp_path / "test.md").touch()
        (tmp_path / "test-2.md").touch()
        assert _unique_name("test.md", tmp_path) == "test-3.md"


class TestMoveEmail:
    def test_moves_file_to_destination(self, tmp_path):
        inbox = tmp_path / "Mail" / "Inbox"
        archive = tmp_path / "Mail" / "Archive"
        inbox.mkdir(parents=True)
        archive.mkdir(parents=True)

        f = _write_email(inbox, "Newsletter")
        _move_email(f, archive)

        assert not f.exists()
        assert (archive / "2026-04-01 Newsletter.md").exists()

    def test_moves_attachments(self, tmp_path):
        inbox = tmp_path / "Mail" / "Inbox"
        archive = tmp_path / "Mail" / "Archive"
        inbox.mkdir(parents=True)
        archive.mkdir(parents=True)

        att_dir = inbox / "_attachments"
        att_dir.mkdir()
        (att_dir / "msg123_doc.pdf").write_bytes(b"pdf data")

        filepath = inbox / "2026-04-01 Test.md"
        filepath.write_text(
            "---\n---\n\nBody with ![[_attachments/msg123_doc.pdf]]\n\n"
            "---\n- [x] read\n- [ ] delete\n"
        )

        _move_email(filepath, archive)

        assert not filepath.exists()
        assert (archive / "2026-04-01 Test.md").exists()
        dest_att = archive / "_attachments" / "msg123_doc.pdf"
        assert dest_att.exists()
        assert dest_att.read_bytes() == b"pdf data"

    def test_handles_filename_collision(self, tmp_path):
        inbox = tmp_path / "Mail" / "Inbox"
        archive = tmp_path / "Mail" / "Archive"
        inbox.mkdir(parents=True)
        archive.mkdir(parents=True)

        # Create existing file in archive with same name
        (archive / "2026-04-01 Test.md").write_text("old")

        f = _write_email(inbox, "Test")
        _move_email(f, archive)

        assert not f.exists()
        assert (archive / "2026-04-01 Test-2.md").exists()


class TestTidyInbox:
    def test_moves_read_to_archive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        inbox = tmp_path / "Mail" / "Inbox"
        inbox.mkdir(parents=True)

        _write_email(inbox, "Read This", read_checked=True)
        _write_email(inbox, "Unread")

        tidy_inbox()

        assert not (inbox / "2026-04-01 Read This.md").exists()
        assert (tmp_path / "Mail" / "Archive" / "2026-04-01 Read This.md").exists()
        assert (inbox / "2026-04-01 Unread.md").exists()

    def test_moves_delete_to_trash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        inbox = tmp_path / "Mail" / "Inbox"
        inbox.mkdir(parents=True)

        _write_email(inbox, "Junk", delete_checked=True)

        tidy_inbox()

        assert not (inbox / "2026-04-01 Junk.md").exists()
        assert (tmp_path / "Mail" / "Trash" / "2026-04-01 Junk.md").exists()

    def test_records_tidied_gmail_id_in_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setattr("gmail_sync.state.STATE_DIR", tmp_path)
        monkeypatch.setattr("gmail_sync.state.STATE_FILE", tmp_path / "state.json")
        inbox = tmp_path / "Mail" / "Inbox"
        inbox.mkdir(parents=True)

        _write_email(inbox, "Newsletter", read_checked=True)
        tidy_inbox()

        from gmail_sync.state import load_state

        state = load_state()
        assert "gmail_newsletter" in state.tidied_ids

    def test_empty_inbox_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        inbox = tmp_path / "Mail" / "Inbox"
        inbox.mkdir(parents=True)
        tidy_inbox()  # should not raise
