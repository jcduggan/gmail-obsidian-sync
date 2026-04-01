"""Inbox management: detect checked read/delete boxes and move files."""

import logging
import re
import shutil
from pathlib import Path

from gmail_sync.state import load_state, save_state
from gmail_sync.writer import (
    get_archive_dir,
    get_attachments_dir,
    get_inbox_dir,
    get_originals_dir,
    get_trash_dir,
)

log = logging.getLogger(__name__)

_CHECKED_RE = re.compile(r"^-\s*\[x\]\s*(read|delete)\s*$", re.IGNORECASE | re.MULTILINE)
_GMAIL_ID_RE = re.compile(r"^gmail_id:\s*(\S+)", re.MULTILINE)

# How many lines from the end to scan for checkboxes
# Must be large enough to cover: checkboxes + tags + fwd block + properties table
_FOOTER_LINES = 50


def tidy_inbox() -> None:
    """Scan Mail/Inbox for checked read/delete boxes and move files."""
    inbox = get_inbox_dir()
    state = load_state()
    moved = False

    originals_dir = get_originals_dir()

    for md_file in sorted(inbox.glob("*.md")):
        action = _detect_action(md_file)
        if action is None:
            continue
        gmail_id = _extract_gmail_id(md_file)
        original_file = originals_dir / md_file.name

        if action == "delete":
            _move_email(md_file, get_trash_dir())
        else:
            _apply_annotations(md_file, original_file)
            _move_email(md_file, get_archive_dir())

        # Clean up the original copy
        if original_file.exists():
            original_file.unlink()

        if gmail_id and gmail_id not in state.tidied_ids:
            state.tidied_ids.append(gmail_id)
        moved = True

    if moved:
        save_state(state)


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


def _extract_gmail_id(md_file: Path) -> str | None:
    """Extract the Gmail API ID from the file's properties block."""
    try:
        text = md_file.read_text(encoding="utf-8")
        # Properties are at the bottom of the file
        tail = "\n".join(text.split("\n")[-20:])
        match = _GMAIL_ID_RE.search(tail)
        if match:
            return match.group(1)
    except OSError:
        pass
    return None


def _apply_annotations(modified_file: Path, original_file: Path) -> None:
    """Diff modified file against original to detect and format user annotations.

    Detects:
    - Bold text that wasn't bold in the original → converted to ==highlight==
    - Entirely new lines not in the original → wrapped in > [!note] callout
    Adds a summary count after the H1 title.
    """
    if not original_file.exists():
        return

    try:
        modified = modified_file.read_text(encoding="utf-8")
        original = original_file.read_text(encoding="utf-8")
    except OSError:
        return

    if modified == original:
        return

    import difflib

    modified_lines = modified.split("\n")
    original_lines = original.split("\n")
    original_bold_spans = set(re.findall(r"\*\*(.+?)\*\*", original))

    # Use SequenceMatcher to find truly new (inserted) lines
    sm = difflib.SequenceMatcher(None, original_lines, modified_lines)
    inserted_indices: set[int] = set()
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            inserted_indices.update(range(j1, j2))

    highlight_count = 0
    note_count = 0
    result_lines = []

    for idx, line in enumerate(modified_lines):
        new_line = line

        # Convert new bold spans to highlights
        for bold_match in re.finditer(r"\*\*(.+?)\*\*", line):
            bold_text = bold_match.group(1)
            if bold_text not in original_bold_spans:
                new_line = new_line.replace(
                    f"**{bold_text}**", f"=={bold_text}==", 1
                )
                highlight_count += 1

        # Wrap truly new (inserted) non-empty lines as user notes
        stripped = line.strip()
        if (
            idx in inserted_indices
            and stripped
            and not stripped.startswith("#")
            and not stripped.startswith(">")
            and not stripped.startswith("-")
            and not stripped.startswith("|")
            and not re.match(r"^-\s*\[", stripped)
        ):
            new_line = f"> [!note] My note\n> {stripped}"
            note_count += 1

        result_lines.append(new_line)

    if highlight_count == 0 and note_count == 0:
        return

    # Add annotation summary after the H1 title
    summary_parts = []
    if highlight_count:
        s = "s" if highlight_count != 1 else ""
        summary_parts.append(f"{highlight_count} highlight{s}")
    if note_count:
        s = "s" if note_count != 1 else ""
        summary_parts.append(f"{note_count} note{s}")
    summary = f"*{', '.join(summary_parts)}*"

    final_lines = []
    inserted_summary = False
    for line in result_lines:
        final_lines.append(line)
        if not inserted_summary and line.startswith("# "):
            final_lines.append("")
            final_lines.append(summary)
            inserted_summary = True

    modified_file.write_text("\n".join(final_lines), encoding="utf-8")
    log.info(
        "Formatted annotations in %s: %d highlights, %d notes",
        modified_file.name,
        highlight_count,
        note_count,
    )


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
