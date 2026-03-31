"""Tests for sync state persistence."""

import json

from gmail_sync.state import MAX_PROCESSED_IDS, SyncState, load_state, save_state


class TestSyncState:
    def test_fresh_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.state.STATE_FILE", tmp_path / "state.json")
        state = load_state()
        assert state.history_id is None
        assert state.processed_ids == []
        assert state.error_counts == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("gmail_sync.state.STATE_DIR", tmp_path)
        monkeypatch.setattr("gmail_sync.state.STATE_FILE", state_file)

        state = SyncState(
            history_id="12345",
            last_sync_at="2026-03-31T12:00:00Z",
            processed_ids=["msg_a", "msg_b"],
            error_counts={"msg_c": 2},
        )
        save_state(state)

        loaded = load_state()
        assert loaded.history_id == "12345"
        assert loaded.last_sync_at == "2026-03-31T12:00:00Z"
        assert loaded.processed_ids == ["msg_a", "msg_b"]
        assert loaded.error_counts == {"msg_c": 2}

    def test_atomic_write(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("gmail_sync.state.STATE_DIR", tmp_path)
        monkeypatch.setattr("gmail_sync.state.STATE_FILE", state_file)

        save_state(SyncState(history_id="abc"))

        # Temp file should be cleaned up
        assert not (tmp_path / "state.tmp").exists()
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert data["history_id"] == "abc"

    def test_prunes_processed_ids(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.state.STATE_DIR", tmp_path)
        monkeypatch.setattr("gmail_sync.state.STATE_FILE", tmp_path / "state.json")

        ids = [f"msg_{i}" for i in range(MAX_PROCESSED_IDS + 500)]
        state = SyncState(processed_ids=ids)
        save_state(state)

        loaded = load_state()
        assert len(loaded.processed_ids) == MAX_PROCESSED_IDS
        # Should keep the most recent (last) IDs
        assert loaded.processed_ids[-1] == f"msg_{MAX_PROCESSED_IDS + 499}"

    def test_creates_state_dir(self, tmp_path, monkeypatch):
        nested = tmp_path / "subdir" / "state"
        monkeypatch.setattr("gmail_sync.state.STATE_DIR", nested)
        monkeypatch.setattr("gmail_sync.state.STATE_FILE", nested / "state.json")

        save_state(SyncState(history_id="test"))
        assert (nested / "state.json").exists()
