"""Gmail API client wrapper (read-only operations)."""

import base64
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from gmail_sync.auth import get_credentials


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
    return service.users().getProfile(userId="me").execute()


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

    return service.users().messages().list(**kwargs).execute()


def get_message(service: Any, message_id: str) -> dict[str, Any]:
    """Fetch a full message by ID.

    Args:
        service: Gmail API service resource.
        message_id: The message ID to fetch.

    Returns:
        Full message resource with payload, headers, etc.
    """
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
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
    result = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = result["data"]
    return base64.urlsafe_b64decode(data)
