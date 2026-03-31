"""Gmail API client wrapper (read-only operations) with retry logic."""

import base64
import logging
import time
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_sync.auth import get_credentials

log = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF_SECS = 1
MAX_BACKOFF_SECS = 300

# HTTP status codes that are transient and worth retrying
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class AuthExpiredError(Exception):
    """Raised when credentials are invalid and cannot be refreshed."""


def _execute_with_retry(request: Any) -> Any:
    """Execute a Gmail API request with exponential backoff on transient errors.

    Retries on 429 (rate limit) and 5xx (server errors). Raises
    AuthExpiredError on 401/403 credential issues.

    Args:
        request: A Gmail API request object (has .execute()).

    Returns:
        The API response.

    Raises:
        AuthExpiredError: On 401 or unrecoverable 403.
        HttpError: On non-retryable HTTP errors after exhausting retries.
    """
    backoff = INITIAL_BACKOFF_SECS

    for attempt in range(MAX_RETRIES + 1):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status

            if status == 401:
                raise AuthExpiredError(
                    "Gmail API returned 401. Run 'gmail-sync auth' to re-authenticate."
                ) from e

            if status == 403 and b"insufficientPermissions" in (e.content or b""):
                raise AuthExpiredError(
                    "Insufficient permissions. Run 'gmail-sync auth' to re-authenticate "
                    "with the correct scopes."
                ) from e

            if status not in RETRYABLE_STATUS_CODES or attempt == MAX_RETRIES:
                raise

            log.warning(
                "Gmail API error %d (attempt %d/%d), retrying in %ds",
                status,
                attempt + 1,
                MAX_RETRIES + 1,
                backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECS)

        except RefreshError as e:
            raise AuthExpiredError(
                "Token refresh failed. Run 'gmail-sync auth' to re-authenticate."
            ) from e

        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt == MAX_RETRIES:
                raise

            log.warning(
                "Network error (attempt %d/%d): %s, retrying in %ds",
                attempt + 1,
                MAX_RETRIES + 1,
                e,
                backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECS)

    raise RuntimeError("Unreachable: retry loop exited without return or raise")


def build_service(creds: Credentials | None = None) -> Any:
    """Build an authenticated Gmail API service.

    Args:
        creds: Optional credentials. If None, loads from stored token.

    Returns:
        Gmail API service resource (read-only).
    """
    if creds is None:
        creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def get_profile(service: Any) -> dict[str, Any]:
    """Get the authenticated user's email profile.

    Returns:
        Dict with emailAddress, messagesTotal, threadsTotal, historyId.
    """
    return _execute_with_retry(service.users().getProfile(userId="me"))


def list_messages(
    service: Any,
    *,
    max_results: int = 100,
    label_ids: list[str] | None = None,
    query: str | None = None,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List message IDs matching the given criteria.

    Args:
        service: Gmail API service resource.
        max_results: Maximum messages to return (max 500).
        label_ids: Filter by label IDs.
        query: Gmail search query string.
        page_token: Pagination token.

    Returns:
        Dict with messages (list of {id, threadId}), nextPageToken,
        resultSizeEstimate.
    """
    kwargs: dict[str, Any] = {
        "userId": "me",
        "maxResults": min(max_results, 500),
    }
    if label_ids:
        kwargs["labelIds"] = label_ids
    if query:
        kwargs["q"] = query
    if page_token:
        kwargs["pageToken"] = page_token

    return _execute_with_retry(service.users().messages().list(**kwargs))


def get_message(service: Any, message_id: str) -> dict[str, Any]:
    """Fetch a full message by ID.

    Args:
        service: Gmail API service resource.
        message_id: The message ID to fetch.

    Returns:
        Full message resource with payload, headers, etc.
    """
    return _execute_with_retry(
        service.users().messages().get(userId="me", id=message_id, format="full")
    )


def get_attachment(service: Any, message_id: str, attachment_id: str) -> bytes:
    """Fetch attachment binary data.

    Args:
        service: Gmail API service resource.
        message_id: The parent message ID.
        attachment_id: The attachment ID from the message payload.

    Returns:
        Raw attachment bytes.
    """
    result = _execute_with_retry(
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
    )
    data = result["data"]
    return base64.urlsafe_b64decode(data)
