"""CLI entry point for gmail-obsidian-sync."""

import argparse
import logging
import sys
from pathlib import Path

from gmail_sync.auth import run_auth_flow
from gmail_sync.client import build_service, get_message, get_profile, list_messages
from gmail_sync.convert import parse_message
from gmail_sync.state import load_state
from gmail_sync.sync import run_loop, run_once


def cmd_setup(_args: argparse.Namespace) -> None:
    """Guided first-time setup."""
    import shutil

    from gmail_sync.auth import CREDENTIALS_FILE, run_auth_flow
    from gmail_sync.launchd import install
    from gmail_sync.writer import CONFIG_DIR, save_config, seed_config_files

    print("Gmail-to-Obsidian Sync Setup")
    print("=" * 35)
    print()

    # Step 1: Vault path
    print("Step 1: Obsidian vault path")
    vault = input("  Enter the path to your Obsidian vault: ").strip()
    vault = str(Path(vault).expanduser().resolve())
    if not Path(vault).is_dir():
        print(f"  Error: {vault} does not exist.")
        sys.exit(1)
    config = {"vault_path": vault}
    save_config(config)
    print(f"  Saved. Will write to {vault}/Mail/")
    print()

    # Seed config files now that vault path is set
    import os

    os.environ["OBSIDIAN_VAULT_PATH"] = vault
    seed_config_files()

    # Step 2: Credentials
    print("Step 2: Gmail API credentials")
    if CREDENTIALS_FILE.exists():
        print(f"  Found existing credentials at {CREDENTIALS_FILE}")
    else:
        print("  You need a Google Cloud OAuth credential file.")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project, enable the Gmail API")
        print("  3. Create OAuth credentials (Desktop app type)")
        print("  4. Download the JSON file")
        print()
        cred_path = input("  Enter path to downloaded credentials JSON: ").strip()
        cred_path = str(Path(cred_path).expanduser().resolve())
        if not Path(cred_path).exists():
            print(f"  Error: {cred_path} does not exist.")
            sys.exit(1)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cred_path, str(CREDENTIALS_FILE))
        print(f"  Saved to {CREDENTIALS_FILE}")
    print()

    # Step 3: Auth
    print("Step 3: Authenticate")
    print("  Opening browser for Google sign-in...")
    creds = run_auth_flow()
    service = build_service(creds)
    profile = get_profile(service)
    user_email = profile["emailAddress"]
    print(f"  Authenticated as {user_email}")
    print()

    # Step 4: Own addresses
    print("Step 4: Own addresses (optional)")
    print("  If you forward emails from another account,")
    fwd = input("  enter that email (or press Enter to skip): ").strip()
    if fwd:
        own_path = Path(vault) / "Mail" / "Configuration" / "Own Addresses.md"
        if own_path.exists():
            text = own_path.read_text()
            if fwd not in text:
                text = text.rstrip() + f"\n{fwd}\n"
                own_path.write_text(text)
        print(f"  Added {fwd}")
    print()

    # Step 5: Initial sync
    print("Step 5: Initial sync")
    print("  Fetching recent emails...")
    _setup_logging()
    run_once()
    inbox = Path(vault) / "Mail" / "Inbox"
    count = len(list(inbox.glob("*.md"))) if inbox.exists() else 0
    print(f"  Synced {count} newsletters to Mail/Inbox/")
    print()

    # Step 6: Service
    print("Step 6: Install background service (optional)")
    svc = input("  Install as a background service? [Y/n]: ").strip().lower()
    if svc in ("", "y", "yes"):
        try:
            install()
            print("  Installed. Emails will sync every 30 seconds.")
        except Exception as e:
            print(f"  Could not install service: {e}")
            print("  You can run 'gmail-sync run' manually instead.")
    print()

    print("Setup complete! Open your vault in Obsidian to see your newsletters.")


def cmd_auth(_args: argparse.Namespace) -> None:
    """Run the OAuth browser flow."""
    creds = run_auth_flow()
    service = build_service(creds)
    profile = get_profile(service)
    print(f"Authenticated as {profile['emailAddress']}")
    print(f"Total messages: {profile['messagesTotal']}")
    print(f"Current historyId: {profile['historyId']}")


def cmd_fetch_one(_args: argparse.Namespace) -> None:
    """Fetch the most recent email and print as markdown."""
    service = build_service()
    result = list_messages(service, max_results=1, label_ids=["INBOX"])

    messages = result.get("messages", [])
    if not messages:
        print("No messages found in INBOX.")
        return

    msg = get_message(service, messages[0]["id"])
    email = parse_message(msg)

    print(_format_markdown(email))


def cmd_run(args: argparse.Namespace) -> None:
    """Start the polling sync loop."""
    _setup_logging()
    run_loop(interval=args.interval)


def cmd_once(_args: argparse.Namespace) -> None:
    """Run a single sync cycle."""
    _setup_logging()
    run_once()


def cmd_tidy(_args: argparse.Namespace) -> None:
    """Scan inbox for checked read/delete boxes and move files."""
    _setup_logging()
    from gmail_sync.tidy import tidy_inbox

    tidy_inbox()


def cmd_migrate(_args: argparse.Namespace) -> None:
    """Migrate old Inbox/ folder to Mail/Inbox/."""
    _setup_logging()
    from gmail_sync.tidy import migrate_inbox

    migrate_inbox()
    print("Migration complete.")


def cmd_install(args: argparse.Namespace) -> None:
    """Install as a macOS launchd service."""
    from gmail_sync.launchd import install

    path = install(interval=args.interval)
    print(f"Installed launchd service at {path}")
    print(f"gmail-sync will run at login with {args.interval}s polling interval.")
    print("Check status with: gmail-sync status")


def cmd_uninstall(_args: argparse.Namespace) -> None:
    """Remove the macOS launchd service."""
    from gmail_sync.launchd import uninstall

    uninstall()
    print("Service uninstalled. gmail-sync will no longer run at login.")


def cmd_status(_args: argparse.Namespace) -> None:
    """Show auth and sync status."""
    from datetime import UTC, datetime

    from gmail_sync.launchd import get_service_status

    service = build_service()
    profile = get_profile(service)
    state = load_state()
    svc_status = get_service_status()

    print(f"Account: {profile['emailAddress']}")
    print(f"Messages: {profile['messagesTotal']}")
    print(f"Gmail history ID: {profile['historyId']}")
    print(f"Synced history ID: {state.history_id or '(not yet synced)'}")
    print(f"Last sync: {state.last_sync_at or '(never)'}")
    print(f"Processed messages: {len(state.processed_ids)}")
    print(f"Service: {svc_status}")

    if state.error_counts:
        print(f"Messages with errors: {len(state.error_counts)}")

    if state.last_sync_at:
        last = datetime.fromisoformat(state.last_sync_at)
        age_secs = (datetime.now(UTC) - last).total_seconds()
        if age_secs > 300:
            mins = int(age_secs // 60)
            print(f"WARNING: Last sync was {mins} minutes ago")


def _setup_logging() -> None:
    """Configure logging to stderr and rotating log file."""
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    log_dir = Path.home() / ".gmail-obsidian"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sync.log"

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Stderr handler
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(fmt)

    # Rotating file handler: 5MB max, keep 3 backups
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(stderr_handler)
    root.addHandler(file_handler)


def _format_markdown(email) -> str:
    """Format an EmailContent as markdown (delegates to writer)."""
    from gmail_sync.writer import _format_markdown as fmt

    return fmt(email, [])


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="gmail-sync",
        description="Sync Gmail emails to Obsidian vault as markdown",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="Guided first-time setup")
    sub.add_parser("auth", help="Run OAuth browser flow")
    sub.add_parser("fetch-one", help="Fetch most recent email as markdown")

    run_parser = sub.add_parser("run", help="Start polling sync loop")
    run_parser.add_argument(
        "--interval", type=int, default=30, help="Polling interval in seconds (default: 30)"
    )

    sub.add_parser("once", help="Run a single sync cycle")

    install_parser = sub.add_parser("install", help="Install as macOS launchd service")
    install_parser.add_argument(
        "--interval", type=int, default=30, help="Polling interval in seconds (default: 30)"
    )

    sub.add_parser("uninstall", help="Remove macOS launchd service")
    sub.add_parser("tidy", help="Scan inbox for checked boxes and move files")
    sub.add_parser("migrate", help="Migrate old Inbox/ to Mail/Inbox/")
    sub.add_parser("status", help="Show auth and sync status")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "setup": cmd_setup,
        "auth": cmd_auth,
        "fetch-one": cmd_fetch_one,
        "run": cmd_run,
        "once": cmd_once,
        "tidy": cmd_tidy,
        "migrate": cmd_migrate,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
