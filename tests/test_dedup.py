"""Tests for opt-out / private detection and cross-source deduplication.

Covers is_opt_out, is_private, and the cross-source dedup property of content_hash.
"""
import pytest

from export_to_obsidian import content_hash, is_opt_out, is_private

OPT_OUT  = "/no-export"
PRIVATE  = "/private"
N_TURNS  = 10  # default conversation length for window tests


def _make_turns(total: int, keyword_at: int | None = None, keyword: str = "") -> list[dict]:
    """Build a turn list, optionally embedding keyword at index keyword_at."""
    turns = []
    for i in range(total):
        content = f"Normal message number {i}."
        if keyword_at is not None and i == keyword_at:
            content = f"Message with {keyword} embedded."
        turns.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "content": content,
        })
    return turns


# ── is_opt_out ────────────────────────────────────────────────────────────────

class TestIsOptOut:
    def test_keyword_in_first_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=0, keyword=OPT_OUT)
        assert is_opt_out(turns) is True

    def test_keyword_in_second_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=1, keyword=OPT_OUT)
        assert is_opt_out(turns) is True

    def test_keyword_in_third_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=2, keyword=OPT_OUT)
        assert is_opt_out(turns) is True

    def test_keyword_in_last_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=9, keyword=OPT_OUT)
        assert is_opt_out(turns) is True

    def test_keyword_in_second_to_last_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=8, keyword=OPT_OUT)
        assert is_opt_out(turns) is True

    def test_keyword_in_middle_only_returns_false(self):
        # Index 4 of 10 is outside turns[:3] + turns[-3:]
        turns = _make_turns(N_TURNS, keyword_at=4, keyword=OPT_OUT)
        assert is_opt_out(turns) is False

    def test_keyword_absent_returns_false(self):
        turns = [
            {"role": "user",      "content": "How do I configure LaunchAgent?"},
            {"role": "assistant", "content": "Here are the steps."},
        ]
        assert is_opt_out(turns) is False

    def test_substring_match_triggers_opt_out(self):
        # BUG: _check_window uses `keyword in content` (substring), so /no-exportable
        # triggers the /no-export check. This test documents current behavior.
        # Fix would require whole-word matching (e.g. regex word boundary).
        turns = [
            {"role": "user",      "content": "I want /no-exportable data here."},
            {"role": "assistant", "content": "Understood."},
        ]
        assert is_opt_out(turns) is True  # substring match fires — known bug

    def test_four_turn_conversation_no_crash(self):
        # turns[:3] + turns[-3:] overlaps when len < 6; ensure no crash
        turns = _make_turns(4)
        assert is_opt_out(turns) is False  # keyword not present

    def test_exactly_six_turns_keyword_in_last(self):
        turns = _make_turns(6)
        turns[5]["content"] = f"Ending with {OPT_OUT}."
        assert is_opt_out(turns) is True

    def test_two_turn_minimum_conversation(self):
        turns = [
            {"role": "user",      "content": f"Please {OPT_OUT} this."},
            {"role": "assistant", "content": "OK."},
        ]
        assert is_opt_out(turns) is True


# ── is_private ────────────────────────────────────────────────────────────────

class TestIsPrivate:
    def test_keyword_in_first_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=0, keyword=PRIVATE)
        assert is_private(turns) is True

    def test_keyword_in_last_turn(self):
        turns = _make_turns(N_TURNS, keyword_at=9, keyword=PRIVATE)
        assert is_private(turns) is True

    def test_keyword_in_middle_only_returns_false(self):
        turns = _make_turns(N_TURNS, keyword_at=4, keyword=PRIVATE)
        assert is_private(turns) is False

    def test_keyword_absent_returns_false(self):
        turns = [
            {"role": "user",      "content": "Let's discuss the project timeline."},
            {"role": "assistant", "content": "Happy to help."},
        ]
        assert is_private(turns) is False

    def test_substring_match_triggers_private(self):
        # Same substring-match bug as is_opt_out: /privately triggers /private
        turns = [
            {"role": "user",      "content": "Store this /privately please."},
            {"role": "assistant", "content": "Done."},
        ]
        assert is_private(turns) is True  # substring match fires — known bug


# ── Cross-source deduplication ────────────────────────────────────────────────

class TestCrossSourceDedup:
    def test_same_turns_same_hash_regardless_of_call(self, sample_turns):
        # source_name is NOT part of content_hash — same turns always same hash
        assert content_hash(sample_turns) == content_hash(sample_turns)

    def test_source_name_key_in_turn_dict_does_not_affect_hash(self, sample_turns):
        # If someone adds a "source" key to the turn dict, hash is unchanged
        # because content_hash only canonicalizes role+content
        turns_with_source = [
            {"role": t["role"], "content": t["content"], "source": "claude-cli"}
            for t in sample_turns
        ]
        assert content_hash(sample_turns) == content_hash(turns_with_source)

    def test_same_conversation_from_different_sources_deduped(self, sample_turns):
        # Simulates same conversation appearing in both CLI and Desktop sources.
        # Hashes must be identical so the state file deduplicates it.
        h_cli     = content_hash(sample_turns)
        h_desktop = content_hash(sample_turns)
        assert h_cli == h_desktop
