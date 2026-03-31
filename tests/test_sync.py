"""Tests for the sync engine."""

import base64
from unittest.mock import MagicMock

import pytest

from gmail_sync.state import SyncState
from gmail_sync.sync import HistoryExpired, _fetch_history, _process_message, initial_sync


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _mock_message(msg_id: str, subject: str = "Test") -> dict:
    """Create a mock Gmail API message response."""
    return {
        "id": msg_id,
        "threadId": f"thread_{msg_id}",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Mon, 31 Mar 2026 14:00:00 +0000"},
                {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
            ],
            "body": {"data": _b64("Test email body content for testing purposes.")},
        },
    }


class TestInitialSync:
    def test_fetches_and_processes_messages(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "msg_1"}, {"id": "msg_2"}],
        }
        service.users().messages().get().execute.side_effect = [
            _mock_message("msg_1", "First Email"),
            _mock_message("msg_2", "Second Email"),
        ]
        service.users().getProfile().execute.return_value = {
            "emailAddress": "me@example.com",
            "historyId": "99999",
        }

        state = SyncState()
        monkeypatch.setattr("gmail_sync.sync.save_state", lambda s: None)

        result = initial_sync(service, state, count=10)

        assert result.history_id == "99999"
        assert "msg_1" in result.processed_ids
        assert "msg_2" in result.processed_ids

    def test_skips_already_processed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "msg_1"}, {"id": "msg_2"}],
        }
        service.users().messages().get().execute.return_value = _mock_message("msg_2")
        service.users().getProfile().execute.return_value = {
            "emailAddress": "me@example.com",
            "historyId": "99999",
        }

        state = SyncState(processed_ids=["msg_1"])
        monkeypatch.setattr("gmail_sync.sync.save_state", lambda s: None)

        result = initial_sync(service, state, count=10)

        # msg_1 was already processed, only msg_2 should be new
        assert "msg_2" in result.processed_ids
        # messages.get should only be called once (for msg_2)
        assert service.users().messages().get().execute.call_count == 1


class TestFetchHistory:
    def test_returns_message_ids(self):
        service = MagicMock()
        service.users().history().list().execute.return_value = {
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "msg_new_1"}},
                        {"message": {"id": "msg_new_2"}},
                    ]
                }
            ],
        }

        ids = _fetch_history(service, "12345")
        assert ids == ["msg_new_1", "msg_new_2"]

    def test_paginates(self):
        service = MagicMock()
        # Two pages of results
        service.users().history().list().execute.side_effect = [
            {
                "history": [
                    {"messagesAdded": [{"message": {"id": "msg_1"}}]},
                ],
                "nextPageToken": "page2",
            },
            {
                "history": [
                    {"messagesAdded": [{"message": {"id": "msg_2"}}]},
                ],
            },
        ]

        ids = _fetch_history(service, "12345")
        assert ids == ["msg_1", "msg_2"]

    def test_empty_history(self):
        service = MagicMock()
        service.users().history().list().execute.return_value = {}

        ids = _fetch_history(service, "12345")
        assert ids == []

    def test_raises_on_404(self):
        from googleapiclient.errors import HttpError

        service = MagicMock()
        resp = MagicMock()
        resp.status = 404
        service.users().history().list().execute.side_effect = HttpError(
            resp, b"Not Found"
        )

        with pytest.raises(HistoryExpired):
            _fetch_history(service, "old_id")


class TestProcessMessage:
    def test_writes_email_to_vault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))

        service = MagicMock()
        service.users().messages().get().execute.return_value = _mock_message(
            "msg_1", "Hello World"
        )

        state = SyncState()
        _process_message(service, "msg_1", state)

        assert "msg_1" in state.processed_ids
        inbox = tmp_path / "Inbox"
        assert inbox.exists()
        files = list(inbox.glob("*.md"))
        assert len(files) == 1
        assert "Hello World" in files[0].read_text()

    def test_tracks_errors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))

        service = MagicMock()
        service.users().messages().get().execute.side_effect = Exception("API error")

        state = SyncState()
        _process_message(service, "msg_fail", state)

        assert "msg_fail" not in state.processed_ids
        assert state.error_counts["msg_fail"] == 1

    def test_skips_after_max_retries(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))

        service = MagicMock()
        state = SyncState(error_counts={"msg_fail": 3})

        _process_message(service, "msg_fail", state)

        # Should be marked as processed (skipped) and error cleared
        assert "msg_fail" in state.processed_ids
        assert "msg_fail" not in state.error_counts
