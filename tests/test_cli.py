"""End-to-end CLI tests for export_to_obsidian.main() and load_memory.main().

Tests both main() functions by patching sys.argv and all filesystem paths.
No real API key or Anthropic SDK calls are made.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import export_to_obsidian
import load_memory


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_jsonl(*pairs: tuple[str, str]) -> str:
    """Build valid Claude CLI JSONL from (role, content) pairs."""
    lines = [
        json.dumps({"type": role, "message": {"role": role, "content": content}})
        for role, content in pairs
    ]
    return "\n".join(lines)


SAMPLE_TRANSCRIPT = _make_jsonl(
    ("user",      "How do I configure a macOS LaunchAgent?"),
    ("assistant", "Here are the steps to configure a LaunchAgent on macOS."),
    ("user",      "What permissions should the plist file have?"),
    ("assistant", "Use chmod 644 for the plist file in ~/Library/LaunchAgents/"),
)

SAMPLE_CLASSIFICATION = {
    "topic_slug":          "launch-agent-setup",
    "topic_display":       "LaunchAgent Setup",
    "domain":              "technical",
    "summary":             "Configured a macOS LaunchAgent for the nightly pipeline.",
    "key_decisions":       ["Use 644 plist permissions"],
    "open_questions":      [],
    "action_items":        [],
    "high_signal_excerpts":[],
    "related_topics":      [],
    "tags":                ["macos"],
    "source":              "claude-cli",
}


# ── export_to_obsidian.main() ─────────────────────────────────────────────────

class TestExportMain:
    @pytest.fixture(autouse=True)
    def setup_sources(self, vault, monkeypatch):
        """Redirect all path constants and build two tmp source dirs."""
        self.vault      = vault
        self.state_path = vault.parent / "state.json"
        self.cli_dir    = vault.parent / "cli_source"
        self.desktop_dir= vault.parent / "desktop_source"
        self.cli_dir.mkdir()
        self.desktop_dir.mkdir()

        monkeypatch.setattr(export_to_obsidian, "MEMORY_BASE",  str(vault))
        monkeypatch.setattr(export_to_obsidian, "STATE_FILE",   str(self.state_path))
        monkeypatch.setattr(export_to_obsidian, "TRANSCRIPT_SOURCES", [
            {
                "name":    "claude-cli",
                "path":    str(self.cli_dir),
                "formats": [".jsonl"],
                "enabled": True,
            },
            {
                "name":    "claude-desktop",
                "path":    str(self.desktop_dir),
                "formats": [".jsonl"],
                "enabled": True,
            },
        ])
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    # ── dry-run ───────────────────────────────────────────────────────────────

    def test_dry_run_prints_dry_run_label(self, capsys):
        (self.cli_dir / "session.jsonl").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
        with patch("sys.argv", ["exp", "--dry-run", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()
        out, _ = capsys.readouterr()
        assert "[DRY RUN]" in out

    def test_dry_run_writes_no_md_files(self, capsys):
        (self.cli_dir / "session.jsonl").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
        with patch("sys.argv", ["exp", "--dry-run", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()
        assert list(self.vault.rglob("*.md")) == []

    def test_dry_run_writes_no_state_file(self, capsys):
        (self.cli_dir / "session.jsonl").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
        with patch("sys.argv", ["exp", "--dry-run", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()
        assert not self.state_path.exists()

    # ── --since date validation ───────────────────────────────────────────────

    def test_since_invalid_date_format_returns_1(self):
        with patch("sys.argv", ["exp", "--since", "April 25th 2026"]):
            result = export_to_obsidian.main()
        assert result == 1

    def test_since_wrong_format_returns_1(self):
        with patch("sys.argv", ["exp", "--since", "25-04-2026"]):
            result = export_to_obsidian.main()
        assert result == 1

    def test_since_valid_date_accepted(self):
        with patch("sys.argv", ["exp", "--since", "2026-01-01", "--dry-run"]):
            result = export_to_obsidian.main()
        assert result == 0  # no transcripts, no errors

    # ── --source filtering ────────────────────────────────────────────────────

    def test_source_cli_only_scans_cli_dir(self, mock_anthropic):
        cli_file     = self.cli_dir     / "cli_session.jsonl"
        desktop_file = self.desktop_dir / "desktop_session.jsonl"
        cli_file.write_text(SAMPLE_TRANSCRIPT,     encoding="utf-8")
        desktop_file.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        mock_anthropic.messages.create.return_value = MagicMock(
            content=[MagicMock(text=json.dumps(SAMPLE_CLASSIFICATION))]
        )

        with patch("sys.argv", ["exp", "--source", "cli", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()

        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            # Only one transcript processed (CLI only, not desktop)
            assert len(state["processed"]) == 1

    def test_source_desktop_skips_cli(self, mock_anthropic):
        cli_file     = self.cli_dir     / "cli_session.jsonl"
        desktop_file = self.desktop_dir / "desktop_session.jsonl"
        cli_file.write_text(SAMPLE_TRANSCRIPT,     encoding="utf-8")
        desktop_file.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        desktop_classification = {**SAMPLE_CLASSIFICATION, "source": "claude-desktop"}
        mock_anthropic.messages.create.return_value = MagicMock(
            content=[MagicMock(text=json.dumps(desktop_classification))]
        )

        with patch("sys.argv", ["exp", "--source", "desktop", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()

        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            assert len(state["processed"]) == 1

    # ── missing API key ───────────────────────────────────────────────────────

    def test_missing_api_key_exits_when_transcript_present(self, monkeypatch):
        (self.cli_dir / "session.jsonl").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("sys.argv", ["exp", "--force", "--since", "2000-01-01"]):
            with pytest.raises(SystemExit) as exc:
                export_to_obsidian.main()
        assert exc.value.code == 1

    # ── no transcripts ────────────────────────────────────────────────────────

    def test_empty_source_dirs_return_0(self):
        with patch("sys.argv", ["exp", "--since", "2000-01-01"]):
            result = export_to_obsidian.main()
        assert result == 0

    # ── main-loop branches ────────────────────────────────────────────────────

    def test_opt_out_transcript_skipped(self, mock_anthropic, capsys):
        """Transcript with /no-export → skipped, no note written, classify not called."""
        opt_out_transcript = _make_jsonl(
            ("user",      "/no-export — please skip this session"),
            ("assistant", "Understood, skipping."),
        )
        (self.cli_dir / "optout.jsonl").write_text(opt_out_transcript, encoding="utf-8")
        with patch("sys.argv", ["exp", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()
        mock_anthropic.messages.create.assert_not_called()
        assert list(self.vault.rglob("*.md")) == []

    def test_already_processed_transcript_skipped(self, mock_anthropic):
        """Transcript whose hash is already in state → skipped when --force absent."""
        from export_to_obsidian import content_hash, parse_transcript, save_state

        transcript_file = self.cli_dir / "session.jsonl"
        transcript_file.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        turns = parse_transcript(SAMPLE_TRANSCRIPT, "claude-cli")
        h = content_hash(turns)
        state = {"processed": {h: {"source": "claude-cli", "date": "2026-04-20", "slug": "old"}}}
        save_state(state)

        with patch("sys.argv", ["exp", "--since", "2000-01-01"]):  # no --force
            export_to_obsidian.main()

        mock_anthropic.messages.create.assert_not_called()

    def test_classify_returns_none_increments_errors(self, capsys):
        """When classify_conversation returns None, pipeline counts it as an error."""
        (self.cli_dir / "session.jsonl").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        with patch("export_to_obsidian.classify_conversation", return_value=None):
            with patch("sys.argv", ["exp", "--force", "--since", "2000-01-01"]):
                result = export_to_obsidian.main()

        assert result == 1  # total_errors > 0 → exit code 1

    def test_topic_slug_skip_recorded_in_state(self, mock_anthropic):
        """When the classifier returns topic_slug='skip', state records it but no note written."""
        (self.cli_dir / "session.jsonl").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
        skip_classification = {**SAMPLE_CLASSIFICATION, "topic_slug": "skip"}
        mock_anthropic.messages.create.return_value = MagicMock(
            content=[MagicMock(text=json.dumps(skip_classification))]
        )

        with patch("sys.argv", ["exp", "--force", "--since", "2000-01-01"]):
            export_to_obsidian.main()

        assert list(self.vault.rglob("*.md")) == []
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        assert any(v["slug"] == "skip" for v in state["processed"].values())


# ── load_memory.main() ────────────────────────────────────────────────────────

class TestLoadMemoryMain:
    @pytest.fixture(autouse=True)
    def setup(self, vault, monkeypatch):
        self.vault = vault
        monkeypatch.setattr(load_memory, "MEMORY_BASE", str(vault))

    # ── --all ─────────────────────────────────────────────────────────────────

    def test_all_with_empty_vault_prints_no_notes_found(self, capsys):
        with patch("sys.argv", ["lm", "--all"]):
            load_memory.main()
        out, _ = capsys.readouterr()
        assert "No notes found." in out

    def test_all_with_notes_prints_index_wrapper(self, make_note, capsys):
        make_note("technical", "test-slug", "# Test Title\nContent here.")
        with patch("sys.argv", ["lm", "--all"]):
            load_memory.main()
        out, _ = capsys.readouterr()
        assert "<!-- OBSIDIAN MEMORY CONTEXT -->" in out
        assert "test-slug" in out

    # ── topic slug ────────────────────────────────────────────────────────────

    def test_slug_not_found_returns_1(self, capsys):
        with patch("sys.argv", ["lm", "nonexistent-slug"]):
            result = load_memory.main()
        assert result == 1

    def test_slug_not_found_prints_helpful_message(self, capsys):
        with patch("sys.argv", ["lm", "nonexistent-slug"]):
            load_memory.main()
        out, _ = capsys.readouterr()
        assert "No notes found matching slug" in out

    def test_slug_found_returns_0(self, make_note, capsys):
        make_note("technical", "casa-leos", "# Casa Leos\nContent here.")
        with patch("sys.argv", ["lm", "casa-leos"]):
            result = load_memory.main()
        assert result == 0

    def test_slug_found_prints_note_content(self, make_note, capsys):
        make_note("technical", "casa-leos", "# Casa Leos\nSpecific content here.")
        with patch("sys.argv", ["lm", "casa-leos"]):
            load_memory.main()
        out, _ = capsys.readouterr()
        assert "<!-- OBSIDIAN MEMORY CONTEXT -->" in out

    # ── --search ──────────────────────────────────────────────────────────────

    def test_search_not_found_returns_1(self, capsys):
        with patch("sys.argv", ["lm", "--search", "xyzzy-never-found"]):
            result = load_memory.main()
        assert result == 1

    def test_search_found_returns_0(self, make_note, capsys):
        make_note("technical", "my-note", "contains the keyword renewal in it")
        with patch("sys.argv", ["lm", "--search", "renewal"]):
            result = load_memory.main()
        assert result == 0
