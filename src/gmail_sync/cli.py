"""CLI entry point for gmail-obsidian-sync."""

import argparse
import sys

from gmail_sync.auth import run_auth_flow
from gmail_sync.client import build_service, get_message, get_profile, list_messages
from gmail_sync.convert import parse_message


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


def cmd_status(_args: argparse.Namespace) -> None:
    """Show auth and sync status."""
    service = build_service()
    profile = get_profile(service)
    print(f"Account: {profile['emailAddress']}")
    print(f"Messages: {profile['messagesTotal']}")
    print(f"History ID: {profile['historyId']}")


def _format_markdown(email) -> str:
    """Format an EmailContent as markdown with frontmatter."""
    date_str = email.date.strftime("%Y-%m-%dT%H:%M:%SZ") if email.date else ""
    created_str = email.date.strftime("%Y-%m-%d") if email.date else ""

    lines = [
        "---",
        f"created: {created_str}",
        "source: gmail-sync",
        f'from: "{email.from_addr}"',
        f'to: "{email.to_addr}"',
        f"date: {date_str}",
        f'subject: "{email.subject}"',
        f'message_id: "{email.message_id}"',
        f"labels: [{', '.join(email.labels)}]",
        "---",
        "",
        f"# {email.subject}",
        "",
        email.body_markdown,
    ]

    if email.attachments:
        lines.extend(["", "## Attachments", ""])
        for att in email.attachments:
            lines.append(f"- {att.filename} ({att.mime_type}, {att.size} bytes)")

    lines.extend(["", "---", "- [ ] read", "- [ ] delete", ""])

    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="gmail-sync",
        description="Sync Gmail emails to Obsidian vault as markdown",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("auth", help="Run OAuth browser flow")
    sub.add_parser("fetch-one", help="Fetch most recent email as markdown")
    sub.add_parser("status", help="Show auth and sync status")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "auth": cmd_auth,
        "fetch-one": cmd_fetch_one,
        "status": cmd_status,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
