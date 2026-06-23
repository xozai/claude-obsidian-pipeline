"""Tests for Obsidian memory note retrieval functions.

Covers _all_note_paths, find_by_slug, find_recent, find_by_search,
_format_context, and _format_index from load_memory.
"""
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import load_memory
from load_memory import (
    NOTE_MAX_CHARS,
    _all_note_paths,
    _format_context,
    _format_index,
    find_by_search,
    find_by_slug,
    find_recent,
)


# ── _all_note_paths ───────────────────────────────────────────────────────────

class TestAllNotePaths:
    def test_finds_md_files(self, patch_memory_paths, make_note):
        make_note("technical", "my-topic", "content")
        paths = _all_note_paths(include_private=False)
        assert any(p.stem == "my-topic" for p in paths)

    def test_excludes_private_by_default(self, patch_memory_paths, make_note):
        make_note("private", "secret-topic", "private content")
        paths = _all_note_paths(include_private=False)
        assert not any(p.stem == "secret-topic" for p in paths)

    def test_includes_private_when_flag_set(self, patch_memory_paths, make_note):
        make_note("private", "secret-topic", "private content")
        paths = _all_note_paths(include_private=True)
        assert any(p.stem == "secret-topic" for p in paths)

    def test_empty_vault_returns_empty_list(self, patch_memory_paths):
        assert _all_note_paths(include_private=False) == []


# ── find_by_slug ──────────────────────────────────────────────────────────────

class TestFindBySlug:
    def test_exact_match_found(self, patch_memory_paths, make_note):
        make_note("technical", "casa-leos", "content")
        results = find_by_slug("casa-leos", include_private=False, max_results=5)
        assert len(results) == 1
        assert results[0].stem == "casa-leos"

    def test_partial_match_found(self, patch_memory_paths, make_note):
        make_note("technical", "casa-leos-extended", "content")
        results = find_by_slug("casa", include_private=False, max_results=5)
        assert any(p.stem == "casa-leos-extended" for p in results)

    def test_exact_preferred_over_partial(self, patch_memory_paths, make_note):
        make_note("technical", "casa-leos",          "exact content")
        make_note("technical", "casa-leos-extended", "partial content")
        results = find_by_slug("casa-leos", include_private=False, max_results=5)
        # Exact hit returns only the exact note
        assert len(results) == 1
        assert results[0].stem == "casa-leos"

    def test_no_match_returns_empty_list(self, patch_memory_paths, make_note):
        make_note("technical", "unrelated-note", "content")
        results = find_by_slug("xyz-never-exists", include_private=False, max_results=5)
        assert results == []

    def test_respects_include_private_false(self, patch_memory_paths, make_note):
        make_note("private", "private-slug", "secret")
        results = find_by_slug("private-slug", include_private=False, max_results=5)
        assert results == []

    def test_respects_include_private_true(self, patch_memory_paths, make_note):
        make_note("private", "private-slug", "secret")
        results = find_by_slug("private-slug", include_private=True, max_results=5)
        assert len(results) == 1

    def test_max_results_limits_partial_matches(self, patch_memory_paths, make_note):
        for i in range(5):
            make_note("technical", f"casa-topic-{i}", "content")
        results = find_by_slug("casa", include_private=False, max_results=2)
        assert len(results) <= 2


# ── find_recent ───────────────────────────────────────────────────────────────

class TestFindRecent:
    def test_returns_n_most_recent(self, patch_memory_paths, make_note):
        for i in range(5):
            make_note("technical", f"topic-{i}", "content")
            time.sleep(0.02)
        results = find_recent(3, include_private=False)
        assert len(results) == 3

    def test_returns_all_when_fewer_than_n(self, patch_memory_paths, make_note):
        make_note("technical", "only-note", "content")
        results = find_recent(10, include_private=False)
        assert len(results) == 1

    def test_sorted_newest_first(self, patch_memory_paths, make_note):
        make_note("technical", "older-note", "content")
        time.sleep(0.05)
        make_note("technical", "newer-note", "content")
        results = find_recent(2, include_private=False)
        assert results[0].stem == "newer-note"
        assert results[1].stem == "older-note"

    def test_excludes_private_by_default(self, patch_memory_paths, make_note):
        make_note("technical", "public-note",  "content")
        make_note("private",   "private-note", "content")
        results = find_recent(10, include_private=False)
        stems = [p.stem for p in results]
        assert "public-note"  in stems
        assert "private-note" not in stems


# ── find_by_search ────────────────────────────────────────────────────────────

class TestFindBySearch:
    def test_higher_frequency_scores_first(self, patch_memory_paths, make_note):
        make_note("technical", "high-match", "launchagent " * 8 + "extra words")
        make_note("technical", "low-match",  "launchagent once and done")
        results = find_by_search("launchagent", include_private=False, max_results=5)
        assert results[0].stem == "high-match"

    def test_case_insensitive_matching(self, patch_memory_paths, make_note):
        make_note("technical", "test-note", "this contains foo bar baz")
        results = find_by_search("FOO", include_private=False, max_results=5)
        assert any(p.stem == "test-note" for p in results)

    def test_empty_query_returns_empty_list(self, patch_memory_paths, make_note):
        make_note("technical", "test-note", "content")
        assert find_by_search("", include_private=False, max_results=5) == []

    def test_no_matches_returns_empty_list(self, patch_memory_paths, make_note):
        make_note("technical", "test-note", "completely unrelated text here")
        results = find_by_search("xyzzy-absolutely-never-found", include_private=False, max_results=5)
        assert results == []

    def test_whitespace_only_query_returns_empty_list(self, patch_memory_paths, make_note):
        make_note("technical", "test-note", "content")
        assert find_by_search("   ", include_private=False, max_results=5) == []


# ── _format_context ───────────────────────────────────────────────────────────

class TestFormatContext:
    def test_wraps_output_in_html_comments(self, patch_memory_paths, make_note):
        p = make_note("technical", "test-slug", "Short content.")
        output = _format_context([(p, "Short content.")])
        assert output.startswith("<!-- OBSIDIAN MEMORY CONTEXT -->")
        assert output.strip().endswith("<!-- END MEMORY CONTEXT -->")

    def test_truncates_long_note_content(self, patch_memory_paths, make_note):
        long_content = "x" * (NOTE_MAX_CHARS + 200)
        p = make_note("technical", "long-note", long_content)
        output = _format_context([(p, long_content)])
        assert "[truncated]" in output

    def test_short_note_not_truncated(self, patch_memory_paths, make_note):
        short_content = "A short note that easily fits."
        p = make_note("technical", "short-note", short_content)
        output = _format_context([(p, short_content)])
        assert "[truncated]" not in output

    def test_slug_appears_in_output(self, patch_memory_paths, make_note):
        p = make_note("technical", "my-special-slug", "content")
        output = _format_context([(p, "content")])
        assert "my-special-slug" in output


# ── _format_index ─────────────────────────────────────────────────────────────

class TestFormatIndex:
    def test_wraps_output_in_html_comments(self, patch_memory_paths, make_note):
        make_note("technical", "slug-one", "# Title One\ncontent")
        paths = _all_note_paths(include_private=False)
        output = _format_index(paths)
        assert "<!-- OBSIDIAN MEMORY CONTEXT -->" in output
        assert "<!-- END MEMORY CONTEXT -->" in output

    def test_sorted_newest_first(self, patch_memory_paths, make_note):
        make_note("technical", "older-slug", "content")
        time.sleep(0.05)
        make_note("technical", "newer-slug", "content")
        paths = _all_note_paths(include_private=False)
        output = _format_index(paths)
        assert output.index("newer-slug") < output.index("older-slug")

    def test_slug_appears_in_index(self, patch_memory_paths, make_note):
        make_note("technical", "indexed-topic", "# Indexed Title\ncontent")
        paths = _all_note_paths(include_private=False)
        output = _format_index(paths)
        assert "indexed-topic" in output


# ── Double-load bug (regression documentation) ────────────────────────────────

class TestLoadNoteDoubleCallBug:
    def test_load_note_called_twice_per_matched_path(
        self, patch_memory_paths, make_note, monkeypatch
    ):
        """Documents a known inefficiency in load_memory.main().

        The list comprehension:
            [(p, _load_note(p)) for p in matched_paths if _load_note(p)]
        calls _load_note once for the predicate check and once for the tuple value,
        resulting in 2 calls per note. With 2 notes that is 4 total calls.
        """
        make_note("technical", "note-a", "content a")
        make_note("technical", "note-b", "content b")

        original_fn = load_memory._load_note
        call_count = {"n": 0}

        def counting_load(path):
            call_count["n"] += 1
            return original_fn(path)

        with patch("load_memory._load_note", side_effect=counting_load):
            with patch("sys.argv", ["load_memory.py", "--recent", "2"]):
                try:
                    load_memory.main()
                except SystemExit:
                    pass

        # 2 notes × 2 calls each = 4 total
        assert call_count["n"] == 4
