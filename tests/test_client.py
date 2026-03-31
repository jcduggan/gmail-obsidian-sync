"""Tests for Gmail API client retry logic."""

from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from gmail_sync.client import AuthExpiredError, _execute_with_retry


def _make_http_error(status: int, content: bytes = b"error") -> HttpError:
    """Create a mock HttpError with given status."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, content)


class TestExecuteWithRetry:
    def test_success_on_first_try(self):
        request = MagicMock()
        request.execute.return_value = {"result": "ok"}

        result = _execute_with_retry(request)
        assert result == {"result": "ok"}
        assert request.execute.call_count == 1

    def test_retries_on_429(self, monkeypatch):
        monkeypatch.setattr("gmail_sync.client.INITIAL_BACKOFF_SECS", 0.01)
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(429),
            {"result": "ok"},
        ]

        result = _execute_with_retry(request)
        assert result == {"result": "ok"}
        assert request.execute.call_count == 2

    def test_retries_on_500(self, monkeypatch):
        monkeypatch.setattr("gmail_sync.client.INITIAL_BACKOFF_SECS", 0.01)
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(500),
            _make_http_error(503),
            {"result": "ok"},
        ]

        result = _execute_with_retry(request)
        assert result == {"result": "ok"}
        assert request.execute.call_count == 3

    def test_raises_after_max_retries(self, monkeypatch):
        monkeypatch.setattr("gmail_sync.client.INITIAL_BACKOFF_SECS", 0.01)
        request = MagicMock()
        request.execute.side_effect = _make_http_error(429)

        with pytest.raises(HttpError):
            _execute_with_retry(request)

        assert request.execute.call_count == 4  # 1 initial + 3 retries

    def test_raises_auth_error_on_401(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(401)

        with pytest.raises(AuthExpiredError, match="401"):
            _execute_with_retry(request)

        assert request.execute.call_count == 1  # No retries

    def test_raises_auth_error_on_403_insufficient_permissions(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(
            403, b"insufficientPermissions"
        )

        with pytest.raises(AuthExpiredError, match="permissions"):
            _execute_with_retry(request)

    def test_does_not_retry_404(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(404)

        with pytest.raises(HttpError):
            _execute_with_retry(request)

        assert request.execute.call_count == 1

    def test_retries_on_connection_error(self, monkeypatch):
        monkeypatch.setattr("gmail_sync.client.INITIAL_BACKOFF_SECS", 0.01)
        request = MagicMock()
        request.execute.side_effect = [
            ConnectionError("Connection refused"),
            {"result": "ok"},
        ]

        result = _execute_with_retry(request)
        assert result == {"result": "ok"}

    def test_retries_on_timeout(self, monkeypatch):
        monkeypatch.setattr("gmail_sync.client.INITIAL_BACKOFF_SECS", 0.01)
        request = MagicMock()
        request.execute.side_effect = [
            TimeoutError("timed out"),
            {"result": "ok"},
        ]

        result = _execute_with_retry(request)
        assert result == {"result": "ok"}

    def test_raises_network_error_after_max_retries(self, monkeypatch):
        monkeypatch.setattr("gmail_sync.client.INITIAL_BACKOFF_SECS", 0.01)
        request = MagicMock()
        request.execute.side_effect = ConnectionError("Connection refused")

        with pytest.raises(ConnectionError):
            _execute_with_retry(request)

    def test_raises_auth_error_on_refresh_failure(self):
        from google.auth.exceptions import RefreshError

        request = MagicMock()
        request.execute.side_effect = RefreshError("Token expired")

        with pytest.raises(AuthExpiredError, match="refresh"):
            _execute_with_retry(request)
