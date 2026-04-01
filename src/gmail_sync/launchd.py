"""macOS launchd service management for gmail-sync."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from gmail_sync.writer import load_config

LABEL = "com.gmail-obsidian-sync"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _get_vault_path_for_plist() -> str:
    """Get vault path for the launchd plist from env or config."""
    vault = os.environ.get("OBSIDIAN_VAULT_PATH")
    if vault:
        return vault
    config = load_config()
    vault = config.get("vault_path")
    if vault:
        return vault
    raise FileNotFoundError(
        "Obsidian vault path not configured. Run 'gmail-sync setup' first."
    )


def get_executable_path() -> Path:
    """Find the gmail-sync executable path.

    Returns the path to the gmail-sync script in the current venv,
    or falls back to whichever is on PATH.
    """
    # Prefer the executable adjacent to the running Python
    venv_bin = Path(sys.executable).parent / "gmail-sync"
    if venv_bin.exists():
        return venv_bin

    found = shutil.which("gmail-sync")
    if found:
        return Path(found)

    raise FileNotFoundError(
        "Cannot find gmail-sync executable. "
        "Ensure the package is installed: uv pip install -e ."
    )


def generate_plist(interval: int = 30) -> dict:
    """Generate the launchd plist configuration.

    Args:
        interval: Polling interval in seconds.

    Returns:
        Plist dict suitable for plistlib.dumps().
    """
    executable = get_executable_path()
    log_dir = Path.home() / ".gmail-obsidian"

    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "run", "--interval", str(interval)],
        "RunAtLoad": True,
        "KeepAlive": {
            "SuccessfulExit": False,
        },
        "EnvironmentVariables": {
            "OBSIDIAN_VAULT_PATH": _get_vault_path_for_plist(),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "StandardOutPath": str(log_dir / "launchd-stdout.log"),
        "StandardErrorPath": str(log_dir / "launchd-stderr.log"),
        "ThrottleInterval": 10,
    }


def install(interval: int = 30) -> Path:
    """Install the launchd plist and load the service.

    Args:
        interval: Polling interval in seconds.

    Returns:
        Path to the installed plist.
    """
    # Unload first if already installed
    if PLIST_PATH.exists():
        uninstall(quiet=True)

    plist = generate_plist(interval)

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        check=True,
    )

    return PLIST_PATH


def uninstall(*, quiet: bool = False) -> None:
    """Unload and remove the launchd plist."""
    if not PLIST_PATH.exists():
        if not quiet:
            print(f"No plist found at {PLIST_PATH}")
        return

    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        check=False,  # May fail if not loaded
    )
    PLIST_PATH.unlink()

    if not quiet:
        print(f"Removed {PLIST_PATH}")


def is_running() -> bool:
    """Check if the launchd service is currently loaded."""
    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_service_status() -> str:
    """Get a human-readable service status string."""
    if not PLIST_PATH.exists():
        return "not installed"

    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return "installed but not loaded"

    # Parse PID and exit status from launchctl list output
    for line in result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2] == LABEL:
            pid = parts[0]
            last_exit = parts[1]
            if pid != "-":
                return f"running (PID {pid})"
            if last_exit != "0":
                return f"stopped (last exit code: {last_exit})"
            return "loaded, waiting to start"

    return "loaded"
