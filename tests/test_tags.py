"""Tests for auto-tagging."""

from datetime import UTC, datetime

from gmail_sync.convert import EmailContent
from gmail_sync.tags import (
    _author_publication_tags,
    _keyword_tags,
    _parse_tag_config,
    _sanitize_tag,
    _split_author_publication,
    _theme_tags,
)


def _make_email(
    body: str = "Some article content.",
    from_addr: str = "sender@example.com",
    subject: str = "Test",
) -> EmailContent:
    return EmailContent(
        subject=subject,
        from_addr=from_addr,
        to_addr="me@gmail.com",
        date=datetime(2026, 4, 1, tzinfo=UTC),
        message_id="<test@example.com>",
        gmail_id="msg_test",
        labels=["INBOX"],
        body_markdown=body,
    )


class TestSanitizeTag:
    def test_spaces_to_hyphens(self):
        assert _sanitize_tag("Venture in Security") == "Venture-in-Security"

    def test_special_chars_removed(self):
        assert _sanitize_tag("O'Reilly Media!") == "O-Reilly-Media"

    def test_strips_trailing_hyphens(self):
        assert _sanitize_tag("--test--") == "test"


class TestSplitAuthorPublication:
    def test_name_from_publication(self):
        author, pub = _split_author_publication(
            "Ross Haleliuk from Venture in Security",
            "ventureinsecurity@substack.com",
        )
        assert author == "Ross Haleliuk"
        assert pub == "Venture in Security"

    def test_substack_domain_as_publication(self):
        author, pub = _split_author_publication(
            "Adam Mastroianni",
            "experimentalhistory@substack.com",
        )
        assert author == "Adam Mastroianni"
        assert pub == "Experimentalhistory"

    def test_single_name_as_publication(self):
        author, pub = _split_author_publication("SemiAnalysis", "semi@substack.com")
        assert pub == "Semi"
        assert author == "SemiAnalysis"

    def test_wordpress_publication(self):
        _, pub = _split_author_publication(
            "Marginal REVOLUTION", "donotreply@wordpress.com"
        )
        assert pub == "WordPress"


class TestAuthorPublicationTags:
    def test_forwarded_substack(self):
        body = (
            "Content here.\n\n"
            "> **Forwarded message**\n"
            "> **From:** **Adam Mastroianni** "
            "<[experimentalhistory@substack.com]"
            "(mailto:experimentalhistory@substack.com)>\n"
            "> **Subject:** Test\n"
        )
        email = _make_email(body=body)
        tags = _author_publication_tags(email)
        assert any("author/" in t for t in tags)
        assert any("pub/" in t for t in tags)

    def test_direct_sender(self):
        email = _make_email(from_addr="Jane Doe <jane@newsletter.com>")
        tags = _author_publication_tags(email)
        assert any("author/Jane-Doe" in t for t in tags)


class TestKeywordTags:
    def test_matches_keyword(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        (tmp_path / "Tags.md").write_text(
            "## Keyword Tags\n\nRSAC → #RSAC\n"
        )

        from gmail_sync.tags import _load_tag_config

        config = _load_tag_config()
        tags = _keyword_tags("I went to RSAC this year.", config)
        assert "RSAC" in tags

    def test_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        (tmp_path / "Tags.md").write_text(
            "## Keyword Tags\n\nRSAC → #RSAC\n"
        )

        from gmail_sync.tags import _load_tag_config

        config = _load_tag_config()
        tags = _keyword_tags("Nothing relevant here.", config)
        assert tags == []


class TestThemeTags:
    def test_matches_theme(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        (tmp_path / "Tags.md").write_text(
            "## Theme Tags\n\n### #AI-infrastructure\nGPU\nNVIDIA\nCUDA\n"
        )

        from gmail_sync.tags import _load_tag_config

        config = _load_tag_config()
        tags = _theme_tags("The new NVIDIA GPU is fast.", config)
        assert "AI-infrastructure" in tags

    def test_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        (tmp_path / "Tags.md").write_text(
            "## Theme Tags\n\n### #AI-infrastructure\nGPU\nNVIDIA\n"
        )

        from gmail_sync.tags import _load_tag_config

        config = _load_tag_config()
        tags = _theme_tags("The economy is doing well.", config)
        assert tags == []


class TestLearnUserTags:
    def test_learns_new_tag_into_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        monkeypatch.setattr("gmail_sync.tags._TAG_CONFIG_CACHE", {})
        (tmp_path / "Tags.md").write_text(
            "# Tags\n\n## Keyword Tags\n\nRSAC → #RSAC\n\n## Theme Tags\n"
        )

        file_text = (
            "###### Tags\n\n"
            "#RSAC #startups\n"
        )
        from gmail_sync.tags import learn_user_tags

        learn_user_tags(file_text, ["RSAC"])

        config_text = (tmp_path / "Tags.md").read_text()
        assert "startups → #startups" in config_text

    def test_does_not_duplicate_existing_tag(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        monkeypatch.setattr("gmail_sync.tags._TAG_CONFIG_CACHE", {})
        (tmp_path / "Tags.md").write_text(
            "# Tags\n\n## Keyword Tags\n\nRSAC → #RSAC\n\n## Theme Tags\n"
        )

        file_text = "###### Tags\n\n#RSAC\n"
        from gmail_sync.tags import learn_user_tags

        learn_user_tags(file_text, ["RSAC"])

        config_text = (tmp_path / "Tags.md").read_text()
        assert config_text.count("RSAC") == 2  # heading + one entry, no duplicate

    def test_skips_author_pub_tags(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gmail_sync.tags.get_config_dir", lambda: tmp_path)
        monkeypatch.setattr("gmail_sync.tags._TAG_CONFIG_CACHE", {})
        (tmp_path / "Tags.md").write_text(
            "# Tags\n\n## Keyword Tags\n\n## Theme Tags\n"
        )

        file_text = "###### Tags\n\n#author/Someone #pub/Blog #newtag\n"
        from gmail_sync.tags import learn_user_tags

        learn_user_tags(file_text, [])

        config_text = (tmp_path / "Tags.md").read_text()
        assert "newtag" in config_text
        assert "author" not in config_text.split("## Keyword Tags")[1]
        assert "pub" not in config_text.split("## Keyword Tags")[1]


class TestParseTagConfig:
    def test_parses_keywords_and_themes(self, tmp_path):
        (tmp_path / "Tags.md").write_text(
            "# Tags\n\n"
            "## Keyword Tags\n\n"
            "RSAC → #RSAC\n"
            "Kubernetes → #kubernetes\n\n"
            "## Theme Tags\n\n"
            "### #cybersecurity\n"
            "CISO\n"
            "zero-day\n"
        )
        config = _parse_tag_config(tmp_path / "Tags.md")
        assert config.keyword_tags == {"rsac": "RSAC", "kubernetes": "kubernetes"}
        assert len(config.theme_tags) == 1
        assert config.theme_tags[0][0] == "cybersecurity"
        assert "ciso" in config.theme_tags[0][1]

    def test_missing_file_returns_empty(self, tmp_path):
        config = _parse_tag_config(tmp_path / "nonexistent.md")
        assert config.keyword_tags == {}
        assert config.theme_tags == []
