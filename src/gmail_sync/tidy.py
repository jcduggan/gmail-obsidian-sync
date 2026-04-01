"""Inbox management: detect checked read/delete boxes and move files."""

import logging
import re
import shutil
from pathlib import Path

from gmail_sync.writer import get_archive_dir, get_attachments_dir, get_inbox_dir, get_trash_dir

log = logging.getLogger(__name__)

_CHECKED_RE = re.compile(r"^-\s*\[x\]\s*(read|delete)\s*$", re.IGNORECASE | re.MULTILINE)

# How many lines from the end to scan for checkboxes
_FOOTER_LINES = 20


def tidy_inbox() -> None:
    """Scan Mail/Inbox for checked read/delete boxes and move files."""
    inbox = get_inbox_dir()
    for md_file in sorted(inbox.glob("*.md")):
        action = _detect_action(md_file)
        if action == "delete":
            _move_email(md_file, get_trash_dir())
        elif action == "read":
            _move_email(md_file, get_archive_dir())


def _detect_action(filepath: Path) -> str | None:
    """Read the file footer and detect which checkbox is checked.

    Returns "delete", "read", or None. Delete takes priority.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError:
        log.warning("Could not read %s", filepath.name)
        return None

    # Only scan the tail of the file for performance
    lines = text.split("\n")
    footer = "\n".join(lines[-_FOOTER_LINES:])

    actions = set()
    for match in _CHECKED_RE.finditer(footer):
        actions.add(match.group(1).lower())

    if "delete" in actions:
        return "delete"
    if "read" in actions:
        return "read"
    return None


def _move_email(md_file: Path, dest_dir: Path) -> None:
    """Move a markdown file and its attachments to the destination folder."""
    dest_name = _unique_name(md_file.name, dest_dir)
    dest_path = dest_dir / dest_name

    # Move the markdown file
    shutil.move(str(md_file), str(dest_path))
    log.info("Moved %s → %s/%s", md_file.name, dest_dir.name, dest_name)

    # Move associated attachments
    _move_attachments(md_file, dest_path, dest_dir)


def _move_attachments(old_md: Path, new_md: Path, dest_dir: Path) -> None:
    """Move attachments referenced by the email to the destination's _attachments."""
    src_att_dir = old_md.parent / "_attachments"
    if not src_att_dir.is_dir():
        return

    # Read the moved file to find attachment references
    try:
        content = new_md.read_text(encoding="utf-8")
    except OSError:
        return

    # Find all _attachments/filename references
    refs = re.findall(r"_attachments/([^\]\)\s]+)", content)
    if not refs:
        return

    dest_att_dir = get_attachments_dir(dest_dir)
    for ref_name in refs:
        src_file = src_att_dir / ref_name
        if src_file.exists():
            shutil.move(str(src_file), str(dest_att_dir / ref_name))
            log.debug("Moved attachment %s", ref_name)


def _unique_name(name: str, dest_dir: Path) -> str:
    """Generate a unique filename in dest_dir, appending -2, -3 if needed."""
    if not (dest_dir / name).exists():
        return name

    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 2
    while (dest_dir / f"{stem}-{counter}{suffix}").exists():
        counter += 1
    return f"{stem}-{counter}{suffix}"


def migrate_inbox() -> None:
    """Migrate old Inbox/ folder to Mail/Inbox/ if needed."""
    from gmail_sync.writer import get_vault_path

    vault = get_vault_path()
    old_inbox = vault / "Inbox"
    new_inbox = get_inbox_dir()  # creates Mail/Inbox/

    if not old_inbox.is_dir():
        log.info("No old Inbox/ folder to migrate")
        return

    if old_inbox.resolve() == new_inbox.resolve():
        return

    # Move all files from old inbox to new inbox
    count = 0
    for item in old_inbox.iterdir():
        dest = new_inbox / item.name
        if item.name == "_attachments" and item.is_dir():
            # Merge attachment directories
            dest_att = get_attachments_dir(new_inbox)
            for att_file in item.iterdir():
                shutil.move(str(att_file), str(dest_att / att_file.name))
            item.rmdir()
        else:
            shutil.move(str(item), str(dest))
        count += 1

    import contextlib

    with contextlib.suppress(OSError):
        old_inbox.rmdir()

    log.info("Migrated %d items from Inbox/ to Mail/Inbox/", count)
