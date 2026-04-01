"""Auto-tagging: author/publication, keyword, and theme tags."""

import logging
import re
from pathlib import Path

from gmail_sync.convert import EmailContent
from gmail_sync.writer import get_config_dir

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w.-]+)")

# Parse the original sender from the forwarded message block in the body
_FWD_FROM_RE = re.compile(
    r"> \*\*From:\*\*\s+(?:\*\*)?(.+?)(?:\*\*)?\s*<"
    r"(?:\[)?([^]\s>]+)"
)

_SUBSTACK_DOMAIN_RE = re.compile(r"^(\w[\w-]*)\.substack\.com$", re.IGNORECASE)


_TAG_IN_TEXT_RE = re.compile(r"#([\w/-]+)")


def generate_tags(email: EmailContent) -> list[str]:
    """Generate auto-tags for an email from all sources."""
    tags: list[str] = []
    tags.extend(_author_publication_tags(email))
    config = _load_tag_config()
    tags.extend(_keyword_tags(email.body_markdown, config))
    tags.extend(_theme_tags(email.body_markdown, config))
    return sorted(set(tags))


def extract_tags_from_section(file_text: str) -> set[str]:
    """Extract all #tags from the Tags section of a file."""
    in_tags = False
    tags: set[str] = set()
    for line in file_text.split("\n"):
        if line.strip() == "###### Tags":
            in_tags = True
            continue
        if in_tags:
            stripped = line.strip()
            # Stop at the next heading (# followed by space) or horizontal rule
            # Tags like #RSAC start with # but aren't headings
            is_heading = re.match(r"^#{1,6}\s", stripped)
            if is_heading or stripped == "---":
                break
            for m in _TAG_IN_TEXT_RE.finditer(line):
                tags.add(m.group(1))
    return tags


def learn_user_tags(file_text: str, auto_tags: list[str]) -> None:
    """Learn new user-added tags back into Tags.md config.

    Compares tags in the file's Tags section against the auto-generated
    set. Any tags the user added manually get written into the keyword
    section of Tags.md so they auto-apply to future articles.
    """
    file_tags = extract_tags_from_section(file_text)
    auto_set = set(auto_tags)

    # User tags = tags in the file that weren't auto-generated
    # Skip author/pub tags (those are always auto-generated)
    user_tags = {
        t for t in file_tags - auto_set
        if not t.startswith("author/") and not t.startswith("pub/")
    }

    if not user_tags:
        return

    config_path = get_config_dir() / "Tags.md"
    if not config_path.exists():
        return

    # Check which user tags are already in the config
    config = _load_tag_config()
    existing_tags = set(config.keyword_tags.values())
    for tag, _ in config.theme_tags:
        existing_tags.add(tag)

    new_tags = {t for t in user_tags if t not in existing_tags}
    if not new_tags:
        return

    # Append new keyword entries to Tags.md
    text = config_path.read_text(encoding="utf-8")

    additions = []
    for tag in sorted(new_tags):
        # Use the tag name (without hyphens) as the keyword
        keyword = tag.replace("-", " ")
        additions.append(f"{keyword} → #{tag}")

    # Find the Keyword Tags section and append there
    marker = "## Keyword Tags"
    if marker in text:
        # Insert after the last non-empty line in the keyword section
        lines = text.split("\n")
        insert_idx = None
        in_section = False
        for i, line in enumerate(lines):
            if marker in line:
                in_section = True
                continue
            if in_section:
                if line.startswith("## "):
                    insert_idx = i
                    break
                if line.strip():
                    insert_idx = i + 1

        if insert_idx is None:
            insert_idx = len(lines)

        for j, addition in enumerate(additions):
            lines.insert(insert_idx + j, addition)

        text = "\n".join(lines)
    else:
        # No keyword section found — append at end
        text = text.rstrip() + "\n\n## Keyword Tags\n\n"
        text += "\n".join(additions) + "\n"

    config_path.write_text(text, encoding="utf-8")

    # Invalidate cache so next load picks up the changes
    _TAG_CONFIG_CACHE.clear()

    log.info("Learned %d new tag(s) from user: %s", len(new_tags), ", ".join(sorted(new_tags)))


def _author_publication_tags(email: EmailContent) -> list[str]:
    """Extract author and publication tags from sender info."""
    display_name, sender_email = _extract_original_sender(email)
    if not display_name and not sender_email:
        return []

    tags: list[str] = []
    author, publication = _split_author_publication(display_name, sender_email)

    if publication:
        tags.append(f"pub/{_sanitize_tag(publication)}")
    if author:
        tags.append(f"author/{_sanitize_tag(author)}")

    return tags


def _extract_original_sender(email: EmailContent) -> tuple[str, str]:
    """Extract display name and email of the original sender.

    For forwarded messages, parses the forwarded header block.
    For direct messages, uses from_addr.
    """
    # Try forwarded message block first
    match = _FWD_FROM_RE.search(email.body_markdown)
    if match:
        display = match.group(1).strip().strip("*")
        addr = match.group(2).strip()
        return display, addr

    # Fall back to from_addr
    from_addr = email.from_addr
    # Parse "Display Name <email@domain>" format
    angle = from_addr.find("<")
    if angle > 0:
        display = from_addr[:angle].strip().strip('"')
        email_match = _EMAIL_RE.search(from_addr)
        addr = email_match.group(0) if email_match else ""
        return display, addr

    email_match = _EMAIL_RE.search(from_addr)
    if email_match:
        return "", email_match.group(0)

    return "", ""


def _split_author_publication(display_name: str, sender_email: str) -> tuple[str, str]:
    """Split a display name into author and publication.

    Handles patterns like:
    - "Ross Haleliuk from Venture in Security" → ("Ross Haleliuk", "Venture in Security")
    - "SemiAnalysis" → ("", "SemiAnalysis")
    - "Adam Mastroianni" → ("Adam Mastroianni", from domain)
    """
    author = ""
    publication = ""

    # Check for "Name from Publication" pattern
    if " from " in display_name.lower():
        parts = re.split(r"\s+from\s+", display_name, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            author = parts[0].strip()
            publication = parts[1].strip()
            return author, publication

    # Check for Substack → use local part as publication name
    email_parts = sender_email.split("@") if "@" in sender_email else []
    email_domain = email_parts[-1] if email_parts else ""
    if email_domain == "substack.com" or email_domain.endswith(".substack.com"):
        local_part = email_parts[0] if email_parts else ""
        if local_part:
            publication = local_part.replace("-", " ").title()
            if display_name and display_name.lower() != publication.lower():
                author = display_name
            return author, publication

    # WordPress digest → publication from subject
    if "wordpress.com" in sender_email.lower():
        publication = "WordPress"
        return author, publication

    # If display name looks like a person name (2-3 words, capitalized)
    words = display_name.split()
    if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w):
        author = display_name
        # Use domain as publication
        email_match = _EMAIL_RE.search(sender_email)
        if email_match:
            domain = email_match.group(1)
            # Strip common email provider domains
            if domain not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com"):
                publication = domain.split(".")[0].title()
    else:
        # Treat as publication name
        publication = display_name

    return author, publication


def _sanitize_tag(text: str) -> str:
    """Sanitize text for use as an Obsidian tag component."""
    # Replace spaces and special chars with hyphens
    result = re.sub(r"[^\w/-]", "-", text)
    # Collapse multiple hyphens
    result = re.sub(r"-+", "-", result)
    return result.strip("-")


# --- Config-driven tags ---

_TAG_CONFIG_CACHE: dict[str, object] = {}


class TagConfig:
    """Parsed tag configuration."""

    def __init__(self):
        self.keyword_tags: dict[str, str] = {}  # lowercase keyword → tag
        self.theme_tags: list[tuple[str, list[str]]] = []  # (tag, [keywords])


def _load_tag_config() -> TagConfig:
    """Load and parse Mail/Configuration/Tags.md."""
    config_path = get_config_dir() / "Tags.md"

    if not config_path.exists():
        return TagConfig()

    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        return TagConfig()

    cache_key = str(config_path)
    cached = _TAG_CONFIG_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]

    config = _parse_tag_config(config_path)
    _TAG_CONFIG_CACHE[cache_key] = (mtime, config)
    return config


def _parse_tag_config(path: Path) -> TagConfig:
    """Parse the Tags.md config file."""
    config = TagConfig()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return config

    section = None
    current_theme_tag = None
    current_theme_keywords: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()

        # Track which section we're in
        if stripped.startswith("## Keyword Tags"):
            _flush_theme(config, current_theme_tag, current_theme_keywords)
            current_theme_tag = None
            current_theme_keywords = []
            section = "keyword"
            continue
        if stripped.startswith("## Theme Tags"):
            _flush_theme(config, current_theme_tag, current_theme_keywords)
            current_theme_tag = None
            current_theme_keywords = []
            section = "theme"
            continue
        if stripped.startswith("## "):
            section = None
            continue

        if section == "keyword":
            # Parse "term → #tag" lines
            if "→" in stripped:
                parts = stripped.split("→", 1)
                keyword = parts[0].strip().lower()
                tag = parts[1].strip().lstrip("#")
                if keyword and tag:
                    config.keyword_tags[keyword] = tag

        elif section == "theme":
            # Parse "### #tag-name" as theme header
            if stripped.startswith("### #"):
                _flush_theme(config, current_theme_tag, current_theme_keywords)
                current_theme_tag = stripped[5:].strip()
                current_theme_keywords = []
            elif current_theme_tag and stripped and not stripped.startswith("#"):
                current_theme_keywords.append(stripped.lower())

    _flush_theme(config, current_theme_tag, current_theme_keywords)
    return config


def _flush_theme(config: TagConfig, tag: str | None, keywords: list[str]) -> None:
    """Add a completed theme to the config."""
    if tag and keywords:
        config.theme_tags.append((tag, keywords))


def _keyword_tags(body: str, config: TagConfig) -> list[str]:
    """Match exact keywords in the body against config."""
    body_lower = body.lower()
    tags: list[str] = []
    for keyword, tag in config.keyword_tags.items():
        if keyword in body_lower:
            tags.append(tag)
    return tags


def _theme_tags(body: str, config: TagConfig) -> list[str]:
    """Match theme keyword groups against the body."""
    body_lower = body.lower()
    tags: list[str] = []
    for tag, keywords in config.theme_tags:
        if any(kw in body_lower for kw in keywords):
            tags.append(tag)
    return tags
