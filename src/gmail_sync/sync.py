"""Sync engine: initial and incremental Gmail-to-vault sync."""

import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

from gmail_sync.client import (
    build_service,
    get_attachment,
    get_message,
    get_profile,
    list_messages,
)
from gmail_sync.convert import parse_message
from gmail_sync.state import SyncState, load_state, save_state
from gmail_sync.writer import write_email

log = logging.getLogger(__name__)

MAX_RETRIES_PER_MESSAGE = 3


class HistoryExpired(Exception):
    """Raised when history.list returns 404 (startHistoryId too old)."""


class AuthError(Exception):
    """Raised when authentication fails and cannot be auto-refreshed."""


def initial_sync(service: Any, state: SyncState, count: int = 100) -> SyncState:
    """Perform initial sync: fetch recent messages and establish checkpoint.

    Args:
        service: Gmail API service resource.
        state: Current sync state (history_id should be None).
        count: Number of recent messages to fetch.

    Returns:
        Updated state with history_id set.
    """
    log.info("Starting initial sync (fetching %d recent messages)", count)

    result = list_messages(service, max_results=count, label_ids=["INBOX"])
    messages = result.get("messages", [])
    log.info("Found %d messages to process", len(messages))

    for msg_stub in reversed(messages):
        msg_id = msg_stub["id"]
        if msg_id in state.processed_ids:
            continue
        _process_message(service, msg_id, state)

    profile = get_profile(service)
    state.history_id = profile["historyId"]
    state.last_sync_at = datetime.now(UTC).isoformat()
    save_state(state)
    log.info("Initial sync complete. historyId=%s", state.history_id)
    return state


def incremental_sync(service: Any, state: SyncState) -> SyncState:
    """Perform incremental sync using history.list.

    Args:
        service: Gmail API service resource.
        state: Current sync state with valid history_id.

    Returns:
        Updated state.

    Raises:
        HistoryExpired: If the stored history_id is too old (404).
    """
    new_message_ids = _fetch_history(service, state.history_id)

    new_ids = [mid for mid in new_message_ids if mid not in state.processed_ids]
    if new_ids:
        log.info("Processing %d new messages", len(new_ids))

    for msg_id in new_ids:
        _process_message(service, msg_id, state)

    state.last_sync_at = datetime.now(UTC).isoformat()
    save_state(state)
    return state


def run_loop(interval: int = 30) -> None:
    """Run the polling sync loop.

    Args:
        interval: Seconds between sync cycles.
    """
    service = build_service()
    state = load_state()

    log.info("Starting sync loop (interval=%ds)", interval)

    while True:
        try:
            if state.history_id is None:
                state = initial_sync(service, state)
            else:
                state = incremental_sync(service, state)
        except HistoryExpired:
            log.warning("History expired, performing full resync")
            state.history_id = None
            continue
        except AuthError as e:
            log.error("Auth failed: %s. Run 'gmail-sync auth' to re-authenticate.", e)
            sys.exit(1)
        except Exception:
            log.exception("Unexpected error during sync cycle, will retry next cycle")

        time.sleep(interval)


def run_once() -> None:
    """Run a single sync cycle."""
    service = build_service()
    state = load_state()

    if state.history_id is None:
        state = initial_sync(service, state)
    else:
        state = incremental_sync(service, state)


def _fetch_history(service: Any, start_history_id: str) -> list[str]:
    """Fetch all new message IDs since start_history_id.

    Paginates through all history records.

    Returns:
        List of message IDs that were added.

    Raises:
        HistoryExpired: If the history ID is too old.
    """
    from googleapiclient.errors import HttpError

    message_ids: list[str] = []
    page_token = None

    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if page_token:
            kwargs["pageToken"] = page_token

        try:
            result = service.users().history().list(**kwargs).execute()
        except HttpError as e:
            if e.resp.status == 404:
                raise HistoryExpired(
                    f"History ID {start_history_id} is too old. Full resync required."
                ) from e
            raise

        for record in result.get("history", []):
            for added in record.get("messagesAdded", []):
                message_ids.append(added["message"]["id"])

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return message_ids


def _process_message(service: Any, msg_id: str, state: SyncState) -> None:
    """Fetch, convert, and write a single message.

    Updates state.processed_ids on success. Tracks errors in
    state.error_counts and writes an error stub after MAX_RETRIES.
    """
    error_count = state.error_counts.get(msg_id, 0)
    if error_count >= MAX_RETRIES_PER_MESSAGE:
        log.warning("Skipping message %s after %d failures", msg_id, error_count)
        state.processed_ids.append(msg_id)
        state.error_counts.pop(msg_id, None)
        return

    try:
        msg = get_message(service, msg_id)
        email = parse_message(msg)

        attachment_data: dict[str, bytes] = {}
        for att in email.attachments:
            try:
                data = get_attachment(service, msg_id, att.attachment_id)
                attachment_data[att.attachment_id] = data
            except Exception:
                log.warning(
                    "Failed to fetch attachment %s for message %s",
                    att.filename,
                    msg_id,
                    exc_info=True,
                )

        path = write_email(email, attachment_data if attachment_data else None)
        log.info("Wrote %s", path.name)

        state.processed_ids.append(msg_id)
        state.error_counts.pop(msg_id, None)

    except Exception:
        state.error_counts[msg_id] = error_count + 1
        log.error(
            "Failed to process message %s (attempt %d/%d)",
            msg_id,
            error_count + 1,
            MAX_RETRIES_PER_MESSAGE,
            exc_info=True,
        )
