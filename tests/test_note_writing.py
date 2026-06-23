"""Tests for Obsidian note path resolution, rendering, and merging.

Covers note_path, _render_new_note, merge_note, _atomic_write,
_parse_sections, and _dedup_list_lines from export_to_obsidian.
"""
import pytest

from export_to_obsidian import (
    _atomic_write,
    _dedup_list_lines,
    _parse_sections,
    _render_new_note,
    merge_note,
    note_path,
)

DATE = "2026-04-25"
SOURCE_CLI     = "claude-cli"
SOURCE_DESKTOP = "claude-desktop"
FILEPATH       = "/path/to/transcript.jsonl"

# A realistic existing note to merge into
_EXISTING_NOTE = """\
# LaunchAgent Setup
#claude-memory #technical #macos #cron

*Last updated: 2026-04-20*

## Summary
Original summary text from first session.

## Key Decisions
- Use 644 permissions on the plist

## Open Questions
- (none)

## Action Items
- [ ] Test on clean machine — Jose

## High-Signal Context
> chmod 644 the plist

## Related Topics
(none)

## Conversation References
- 2026-04-20 `/path/old.jsonl` [cli]
"""


# ── note_path ─────────────────────────────────────────────────────────────────

class TestNotePath:
    @pytest.mark.parametrize("domain", ["projects", "personal", "technical", "general"])
    def test_valid_domain_included_in_path(self, domain, patch_export_paths):
        p = note_path("my-slug", domain)
        assert domain in str(p)

    def test_slug_is_filename_stem(self, patch_export_paths):
        p = note_path("casa-leos", "personal")
        assert p.stem == "casa-leos"
        assert p.suffix == ".md"

    def test_invalid_domain_defaults_to_general(self, patch_export_paths):
        p = note_path("my-slug", "finance")
        assert "general" in str(p)
        assert "finance" not in str(p)

    def test_empty_domain_defaults_to_general(self, patch_export_paths):
        p = note_path("my-slug", "")
        assert "general" in str(p)

    def test_private_flag_routes_to_private_subdir(self, patch_export_paths):
        p = note_path("my-slug", "technical", private=True)
        assert "private" in str(p)
        assert "technical" in str(p)

    def test_non_private_path_has_no_private_component(self, patch_export_paths):
        p = note_path("my-slug", "technical", private=False)
        # Check relative path parts, not full absolute path (macOS tmp starts with /private/)
        rel = p.relative_to(patch_export_paths)
        assert "private" not in rel.parts

    def test_creates_parent_directories(self, patch_export_paths):
        p = note_path("brand-new-slug", "technical")
        assert p.parent.exists()

    def test_private_parent_directories_created(self, patch_export_paths):
        p = note_path("secret", "personal", private=True)
        assert p.parent.exists()


# ── _render_new_note ──────────────────────────────────────────────────────────

class TestRenderNewNote:
    def test_contains_topic_display_title(self, sample_classification):
        output = _render_new_note(sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert "LaunchAgent Setup" in output

    def test_tags_use_hash_prefix(self, sample_classification):
        output = _render_new_note(sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert "#macos" in output
        assert "#cron" in output

    def test_cli_source_name_produces_cli_ref_label(self, sample_classification):
        output = _render_new_note(sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert "[cli]" in output

    def test_non_cli_source_name_produces_desktop_ref_label(self, sample_classification):
        output = _render_new_note(sample_classification, DATE, SOURCE_DESKTOP, FILEPATH)
        assert "[desktop]" in output

    def test_empty_list_fields_render_none_placeholder(self, sample_classification):
        c = {
            **sample_classification,
            "key_decisions":       [],
            "open_questions":      [],
            "action_items":        [],
            "high_signal_excerpts":[],
            "related_topics":      [],
        }
        output = _render_new_note(c, DATE, SOURCE_CLI, FILEPATH)
        assert "(none)" in output

    def test_action_item_with_owner_and_due(self):
        c = {
            "topic_display":       "Deployment",
            "domain":              "technical",
            "summary":             "Deploy to prod.",
            "key_decisions":       [],
            "open_questions":      [],
            "action_items":        [{"task": "Run migration", "owner": "Jose", "due": "2026-05-01"}],
            "high_signal_excerpts":[],
            "related_topics":      [],
            "tags":                [],
            "source":              SOURCE_CLI,
        }
        output = _render_new_note(c, DATE, SOURCE_CLI, FILEPATH)
        assert "- [ ] Run migration" in output
        assert "Jose" in output
        assert "2026-05-01" in output

    def test_action_item_without_due_omits_due_field(self):
        c = {
            "topic_display":       "Research",
            "domain":              "general",
            "summary":             "Look into options.",
            "key_decisions":       [],
            "open_questions":      [],
            "action_items":        [{"task": "Investigate", "owner": "Jose", "due": ""}],
            "high_signal_excerpts":[],
            "related_topics":      [],
            "tags":                [],
            "source":              SOURCE_CLI,
        }
        output = _render_new_note(c, DATE, SOURCE_CLI, FILEPATH)
        assert "due:" not in output

    def test_related_topics_rendered_as_wikilinks(self):
        c = {
            "topic_display":       "Main Topic",
            "domain":              "projects",
            "summary":             "Summary.",
            "key_decisions":       [],
            "open_questions":      [],
            "action_items":        [],
            "high_signal_excerpts":[],
            "related_topics":      ["other-topic", "another-slug"],
            "tags":                [],
            "source":              SOURCE_CLI,
        }
        output = _render_new_note(c, DATE, SOURCE_CLI, FILEPATH)
        assert "[[other-topic]]" in output
        assert "[[another-slug]]" in output

    def test_summary_included_in_output(self, sample_classification):
        output = _render_new_note(sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert sample_classification["summary"] in output

    def test_date_string_appears_in_last_updated(self, sample_classification):
        output = _render_new_note(sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert f"*Last updated: {DATE}*" in output


# ── merge_note — new file ─────────────────────────────────────────────────────

class TestMergeNoteNewFile:
    def test_creates_new_file(self, patch_export_paths, sample_classification):
        p = patch_export_paths / "technical" / "launch-agent-setup.md"
        merge_note(p, sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert p.exists()

    def test_new_file_contains_all_section_headers(self, patch_export_paths, sample_classification):
        p = patch_export_paths / "technical" / "launch-agent-setup.md"
        merge_note(p, sample_classification, DATE, SOURCE_CLI, FILEPATH)
        content = p.read_text(encoding="utf-8")
        for section in (
            "Summary", "Key Decisions", "Open Questions", "Action Items",
            "High-Signal Context", "Related Topics", "Conversation References",
        ):
            assert f"## {section}" in content

    def test_new_file_no_tmp_leftover(self, patch_export_paths, sample_classification):
        p = patch_export_paths / "technical" / "launch-agent-setup.md"
        merge_note(p, sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert not p.with_suffix(".tmp").exists()


# ── merge_note — existing file ────────────────────────────────────────────────

class TestMergeNoteExistingFile:
    @pytest.fixture
    def existing_note(self, patch_export_paths):
        p = patch_export_paths / "technical" / "launch-agent-setup.md"
        p.write_text(_EXISTING_NOTE, encoding="utf-8")
        return p

    def test_updates_summary(self, existing_note, sample_classification):
        updated = {**sample_classification, "summary": "New summary after second session."}
        merge_note(existing_note, updated, DATE, SOURCE_CLI, FILEPATH)
        content = existing_note.read_text(encoding="utf-8")
        assert "New summary after second session." in content
        assert "Original summary text from first session." not in content

    def test_updates_last_updated_date(self, existing_note, sample_classification):
        merge_note(existing_note, sample_classification, DATE, SOURCE_CLI, FILEPATH)
        content = existing_note.read_text(encoding="utf-8")
        assert f"*Last updated: {DATE}*" in content
        assert "*Last updated: 2026-04-20*" not in content

    def test_deduplicates_existing_decision(self, existing_note, sample_classification):
        # "Use 644 permissions on the plist" already exists — must not double up
        merge_note(existing_note, sample_classification, DATE, SOURCE_CLI, FILEPATH)
        content = existing_note.read_text(encoding="utf-8")
        assert content.count("Use 644 permissions on the plist") == 1

    def test_appends_new_decision(self, existing_note, sample_classification):
        updated = {**sample_classification, "key_decisions": ["Newly discovered decision"]}
        merge_note(existing_note, updated, DATE, SOURCE_CLI, FILEPATH)
        content = existing_note.read_text(encoding="utf-8")
        assert "Newly discovered decision" in content
        assert "Use 644 permissions on the plist" in content  # original preserved

    def test_appends_new_conversation_ref(self, existing_note, sample_classification):
        merge_note(existing_note, sample_classification, DATE, SOURCE_CLI, "/new.jsonl")
        content = existing_note.read_text(encoding="utf-8")
        assert "2026-04-20" in content          # old ref preserved
        assert f"{DATE}" in content             # new ref added
        assert "/new.jsonl" in content

    def test_does_not_duplicate_existing_ref(self, existing_note, sample_classification):
        # Re-merge with the same filepath and date that already appears in the note
        merge_note(existing_note, sample_classification, "2026-04-20", SOURCE_CLI, "/path/old.jsonl")
        content = existing_note.read_text(encoding="utf-8")
        assert content.count("2026-04-20 `/path/old.jsonl` [cli]") == 1

    def test_appends_new_action_items(self, existing_note, sample_classification):
        updated = {**sample_classification,
                   "action_items": [{"task": "Brand new task", "owner": "Jose", "due": ""}]}
        merge_note(existing_note, updated, DATE, SOURCE_CLI, FILEPATH)
        content = existing_note.read_text(encoding="utf-8")
        assert "Brand new task" in content
        assert "Test on clean machine" in content  # original still present

    def test_atomic_write_no_tmp_leftover(self, existing_note, sample_classification):
        merge_note(existing_note, sample_classification, DATE, SOURCE_CLI, FILEPATH)
        assert not existing_note.with_suffix(".tmp").exists()


# ── _parse_sections ───────────────────────────────────────────────────────────

class TestParseSections:
    def test_basic_split(self):
        text = "## Summary\nSome summary text.\n\n## Key Decisions\n- Decision 1\n"
        sections = _parse_sections(text)
        assert "Summary" in sections
        assert "Key Decisions" in sections
        assert "Some summary text." in sections["Summary"]
        assert "Decision 1" in sections["Key Decisions"]

    def test_empty_input_returns_empty_dict(self):
        assert _parse_sections("") == {}

    def test_no_headers_returns_empty_dict(self):
        assert _parse_sections("Just plain text with no markdown headers.") == {}

    def test_section_content_is_stripped(self):
        text = "## Summary\n\n   trimmed content   \n\n"
        sections = _parse_sections(text)
        assert sections["Summary"] == "trimmed content"

    def test_multiple_sections_all_captured(self):
        text = "\n".join([
            "## Alpha",  "alpha content",
            "## Beta",   "beta content",
            "## Gamma",  "gamma content",
        ])
        sections = _parse_sections(text)
        assert set(sections) == {"Alpha", "Beta", "Gamma"}


# ── _dedup_list_lines ─────────────────────────────────────────────────────────

class TestDedupListLines:
    def test_strips_dash_prefix_before_dedup_check(self):
        existing = "- existing item"
        result = _dedup_list_lines(existing, ["existing item"])
        assert result.count("existing item") == 1

    def test_new_items_added_when_not_already_present(self):
        existing = "- first item"
        result = _dedup_list_lines(existing, ["second item"])
        assert "first item" in result
        assert "second item" in result

    def test_none_placeholder_in_new_items_not_added(self):
        # "(none)" appearing in new_items is explicitly excluded
        result = _dedup_list_lines("", ["(none)", "real item"])
        assert "(none)" not in result
        assert "real item" in result

    def test_duplicate_new_items_not_added_twice(self):
        existing = "- original"
        result = _dedup_list_lines(existing, ["original", "fresh"])
        assert result.count("original") == 1
        assert "fresh" in result

    def test_empty_existing_and_non_empty_new_items(self):
        result = _dedup_list_lines("", ["alpha", "beta"])
        assert "alpha" in result
        assert "beta" in result

    def test_result_uses_dash_prefix_format(self):
        result = _dedup_list_lines("", ["item one"])
        assert result.startswith("- ")


# ── _atomic_write ─────────────────────────────────────────────────────────────

class TestAtomicWrite:
    def test_writes_content_to_target_path(self, tmp_path):
        p = tmp_path / "output.md"
        _atomic_write(p, "hello world")
        assert p.read_text(encoding="utf-8") == "hello world"

    def test_no_tmp_file_left_after_write(self, tmp_path):
        p = tmp_path / "output.md"
        _atomic_write(p, "content")
        assert not p.with_suffix(".tmp").exists()

    def test_overwrites_existing_file(self, tmp_path):
        p = tmp_path / "output.md"
        p.write_text("old content", encoding="utf-8")
        _atomic_write(p, "new content")
        assert p.read_text(encoding="utf-8") == "new content"
