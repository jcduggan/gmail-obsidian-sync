"""Tests for launchd service management."""

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from gmail_sync.launchd import LABEL, generate_plist, get_service_status


class TestGeneratePlist:
    def test_contains_required_keys(self, monkeypatch):
        monkeypatch.setattr(
            "gmail_sync.launchd.get_executable_path",
            lambda: Path("/usr/local/bin/gmail-sync"),
        )
        plist = generate_plist(interval=30)

        assert plist["Label"] == LABEL
        assert plist["RunAtLoad"] is True
        assert plist["ThrottleInterval"] == 10

    def test_program_arguments(self, monkeypatch):
        monkeypatch.setattr(
            "gmail_sync.launchd.get_executable_path",
            lambda: Path("/usr/local/bin/gmail-sync"),
        )
        plist = generate_plist(interval=45)

        args = plist["ProgramArguments"]
        assert args[0] == "/usr/local/bin/gmail-sync"
        assert "run" in args
        assert "--interval" in args
        assert "45" in args

    def test_uses_obsidian_vault_path_env(self, monkeypatch):
        monkeypatch.setattr(
            "gmail_sync.launchd.get_executable_path",
            lambda: Path("/usr/local/bin/gmail-sync"),
        )
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "/custom/vault")
        plist = generate_plist()

        assert plist["EnvironmentVariables"]["OBSIDIAN_VAULT_PATH"] == "/custom/vault"

    def test_keepalive_restarts_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            "gmail_sync.launchd.get_executable_path",
            lambda: Path("/usr/local/bin/gmail-sync"),
        )
        plist = generate_plist()

        assert plist["KeepAlive"]["SuccessfulExit"] is False

    def test_plist_is_valid_plist(self, monkeypatch):
        monkeypatch.setattr(
            "gmail_sync.launchd.get_executable_path",
            lambda: Path("/usr/local/bin/gmail-sync"),
        )
        plist = generate_plist()

        # Verify it can be serialized as a valid plist
        data = plistlib.dumps(plist)
        roundtrip = plistlib.loads(data)
        assert roundtrip["Label"] == LABEL


class TestGetServiceStatus:
    def test_not_installed(self, monkeypatch):
        monkeypatch.setattr(
            "gmail_sync.launchd.PLIST_PATH",
            Path("/nonexistent/path.plist"),
        )
        assert get_service_status() == "not installed"

    def test_installed_but_not_loaded(self, tmp_path, monkeypatch):
        plist_path = tmp_path / "test.plist"
        plist_path.touch()
        monkeypatch.setattr("gmail_sync.launchd.PLIST_PATH", plist_path)

        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            assert get_service_status() == "installed but not loaded"

    def test_running(self, tmp_path, monkeypatch):
        plist_path = tmp_path / "test.plist"
        plist_path.touch()
        monkeypatch.setattr("gmail_sync.launchd.PLIST_PATH", plist_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"12345\t0\t{LABEL}\n"
        with patch("subprocess.run", return_value=mock_result):
            status = get_service_status()
            assert "running" in status
            assert "12345" in status

    def test_stopped_with_error(self, tmp_path, monkeypatch):
        plist_path = tmp_path / "test.plist"
        plist_path.touch()
        monkeypatch.setattr("gmail_sync.launchd.PLIST_PATH", plist_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"-\t1\t{LABEL}\n"
        with patch("subprocess.run", return_value=mock_result):
            status = get_service_status()
            assert "stopped" in status
            assert "exit code: 1" in status
