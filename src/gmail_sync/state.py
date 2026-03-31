"""Persistent sync state management."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_DIR = Path.home() / ".gmail-obsidian"
STATE_FILE = STATE_DIR / "state.json"

MAX_PROCESSED_IDS = 1000


@dataclass
class SyncState:
    """Persistent state for incremental Gmail sync."""

    history_id: str | None = None
    last_sync_at: str | None = None
    processed_ids: list[str] = field(default_factory=list)
    error_counts: dict[str, int] = field(default_factory=dict)


def load_state() -> SyncState:
    """Load sync state from disk.

    Returns:
        Stored state, or a fresh SyncState if no state file exists.
    """
    if not STATE_FILE.exists():
        return SyncState()

    data = json.loads(STATE_FILE.read_text())
    return SyncState(
        history_id=data.get("history_id"),
        last_sync_at=data.get("last_sync_at"),
        processed_ids=data.get("processed_ids", []),
        error_counts=data.get("error_counts", {}),
    )


def save_state(state: SyncState) -> None:
    """Atomically save sync state to disk.

    Uses write-to-temp-then-rename for atomicity.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Prune processed_ids to prevent unbounded growth
    if len(state.processed_ids) > MAX_PROCESSED_IDS:
        state.processed_ids = state.processed_ids[-MAX_PROCESSED_IDS:]

    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2))
    tmp.rename(STATE_FILE)
