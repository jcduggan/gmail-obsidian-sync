"""Sync engine: initial and incremental Gmail-to-vault sync."""

import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

from gmail_sync.classify import is_newsletter
from gmail_sync.client import (
    AuthExpiredError,
    build_service,
    get_attachment,
    get_message,
    get_profile,
    list_messages,
)
from gmail_sync.convert import parse_message
from gmail_sync.state import SyncState, load_state, save_state
from gmail_sync.tidy import tidy_inbox
from gmail_sync.writer import seed_config_files, write_email

log = logging.getLogger(__name__)

MAX_RETRIES_PER_MESSAGE = 3


class HistoryExpired(Exception):
    """Raised when history.list returns 404 (startHistoryId too old)."""


def initial_sync(
    service: Any, state: SyncState, user_email: str, count: int = 100
) -> SyncState:
    """Perform initial sync: fetch recent messages and establish checkpoint.

    Args:
        service: Gmail API service resource.
        state: Current sync state (history_id should be None).
        user_email: Authenticated user's email address.
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
        _process_message(service, msg_id, state, user_email)

    profile = get_profile(service)
    state.history_id = profile["historyId"]
    state.last_sync_at = datetime.now(UTC).isoformat()
    save_state(state)
    log.info("Initial sync complete. historyId=%s", state.history_id)
    return state


def incremental_sync(service: Any, state: SyncState, user_email: str) -> SyncState:
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
        _process_message(service, msg_id, state, user_email)

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
    profile = get_profile(service)
    user_email = profile["emailAddress"]
    seed_config_files()

    log.info("Starting sync loop (interval=%ds, user=%s)", interval, user_email)

    while True:
        try:
            if state.history_id is None:
                state = initial_sync(service, state, user_email)
            else:
                state = incremental_sync(service, state, user_email)
        except HistoryExpired:
            log.warning("History expired, performing full resync")
            state.history_id = None
            continue
        except AuthExpiredError as e:
            log.error("Auth failed: %s", e)
            sys.exit(1)
        except Exception:
            log.exception("Unexpected error during sync cycle, will retry next cycle")

        try:
            tidy_inbox()
        except Exception:
            log.exception("Error during inbox tidy")

        time.sleep(interval)


def run_once() -> None:
    """Run a single sync cycle with error handling."""
    service = build_service()
    state = load_state()
    profile = get_profile(service)
    user_email = profile["emailAddress"]
    seed_config_files()

    try:
        if state.history_id is None:
            initial_sync(service, state, user_email)
        else:
            incremental_sync(service, state, user_email)
    except HistoryExpired:
        log.warning("History expired, performing full resync")
        state.history_id = None
        save_state(state)
        initial_sync(service, state, user_email)
    except AuthExpiredError as e:
        log.error("Auth failed: %s", e)
        sys.exit(1)

    tidy_inbox()


def _fetch_history(service: Any, start_history_id: str) -> list[str]:
    """Fetch all new message IDs since start_history_id.

    Paginates through all history records. Uses retry logic for
    transient errors.

    Returns:
        List of message IDs that were added.

    Raises:
        HistoryExpired: If the history ID is too old.
    """
    from googleapiclient.errors import HttpError

    from gmail_sync.client import _execute_with_retry

    message_ids: list[str] = []
    page_token = None

    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
            "labelId": "INBOX",
        }
        if page_token:
            kwargs["pageToken"] = page_token

        try:
            result = _execute_with_retry(service.users().history().list(**kwargs))
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


def _process_message(
    service: Any, msg_id: str, state: SyncState, user_email: str
) -> None:
    """Fetch, classify, convert, and write a single message.

    Skips non-newsletter emails silently. Updates state.processed_ids
    on success. Tracks errors in state.error_counts.
    """
    # Skip messages that were already tidied (archived/trashed by user)
    if msg_id in state.tidied_ids:
        state.processed_ids.append(msg_id)
        return

    error_count = state.error_counts.get(msg_id, 0)
    if error_count >= MAX_RETRIES_PER_MESSAGE:
        log.warning("Skipping message %s after %d failures", msg_id, error_count)
        state.processed_ids.append(msg_id)
        state.error_counts.pop(msg_id, None)
        return

    try:
        msg = get_message(service, msg_id)

        if not is_newsletter(msg, user_email):
            subject = _get_subject(msg)
            log.info("Skipping non-newsletter: %s", subject)
            state.processed_ids.append(msg_id)
            return

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


def _get_subject(msg: dict) -> str:
    """Extract subject from a raw Gmail API message."""
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"] == "Subject":
            return h["value"]
    return "(no subject)"
