"""Tests for state file management and content hashing.

Covers load_state, save_state, and content_hash from export_to_obsidian.
"""
import json
import re

import pytest

from export_to_obsidian import content_hash, load_state, save_state


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """Redirect STATE_FILE to a tmp path and return that path."""
    import export_to_obsidian as exp
    p = tmp_path / "state.json"
    monkeypatch.setattr(exp, "STATE_FILE", str(p))
    return p


# ── load_state ────────────────────────────────────────────────────────────────

class TestLoadState:
    def test_missing_file_returns_default(self, state_path):
        assert not state_path.exists()
        result = load_state()
        assert result == {"processed": {}}

    def test_valid_file_returned(self, state_path):
        data = {
            "processed": {
                "abc123": {"source": "claude-cli", "date": "2026-04-25", "slug": "test"}
            }
        }
        state_path.write_text(json.dumps(data), encoding="utf-8")
        assert load_state() == data

    def test_valid_file_without_processed_key_gets_key_injected(self, state_path):
        state_path.write_text(json.dumps({"other_key": "value"}), encoding="utf-8")
        result = load_state()
        assert "processed" in result
        assert result["processed"] == {}

    def test_corrupt_json_returns_default(self, state_path):
        state_path.write_text("{ not valid json {{{{", encoding="utf-8")
        result = load_state()
        assert result == {"processed": {}}

    def test_empty_file_returns_default(self, state_path):
        state_path.write_text("", encoding="utf-8")
        result = load_state()
        assert result == {"processed": {}}

    def test_does_not_raise_on_any_failure(self, state_path):
        state_path.write_text("\x00\x01\x02binary", encoding="latin-1")
        result = load_state()  # must not raise
        assert "processed" in result


# ── save_state ────────────────────────────────────────────────────────────────

class TestSaveState:
    def test_creates_file_with_valid_json(self, state_path):
        state = {"processed": {"h1": {"source": "claude-cli", "date": "2026-04-25", "slug": "foo"}}}
        save_state(state)
        assert state_path.exists()
        assert json.loads(state_path.read_text(encoding="utf-8")) == state

    def test_overwrites_existing_file(self, state_path):
        save_state({"processed": {"old": {}}})
        save_state({"processed": {"new": {}}})
        result = json.loads(state_path.read_text(encoding="utf-8"))
        assert "new" in result["processed"]
        assert "old" not in result["processed"]

    def test_no_tmp_file_leftover_after_save(self, state_path):
        save_state({"processed": {}})
        tmp = state_path.with_suffix(".tmp")
        assert not tmp.exists()

    def test_round_trip_preserves_data(self, state_path):
        original = {
            "processed": {
                "abc": {"source": "claude-cli", "date": "2026-01-01", "slug": "home-setup"}
            }
        }
        save_state(original)
        assert load_state() == original


# ── content_hash ──────────────────────────────────────────────────────────────

class TestContentHash:
    def test_stable_across_calls(self, sample_turns):
        assert content_hash(sample_turns) == content_hash(sample_turns)

    def test_different_content_produces_different_hash(self, sample_turns):
        modified = [
            {**t, "content": t["content"] + " EXTRA"} if i == 0 else t
            for i, t in enumerate(sample_turns)
        ]
        assert content_hash(sample_turns) != content_hash(modified)

    def test_different_role_produces_different_hash(self, sample_turns):
        swapped = [
            {"role": "assistant" if t["role"] == "user" else "user", "content": t["content"]}
            for t in sample_turns
        ]
        assert content_hash(sample_turns) != content_hash(swapped)

    def test_result_is_32_char_hex_string(self, sample_turns):
        h = content_hash(sample_turns)
        assert re.match(r"^[0-9a-f]{32}$", h)

    def test_order_of_turns_matters(self, sample_turns):
        assert content_hash(sample_turns) != content_hash(list(reversed(sample_turns)))

    def test_extra_keys_in_turn_dicts_ignored(self, sample_turns):
        # Only role+content are canonicalized; extra keys (e.g. "timestamp") don't affect hash
        turns_with_extra = [
            {"role": t["role"], "content": t["content"], "timestamp": "2026-04-25T00:00:00Z"}
            for t in sample_turns
        ]
        assert content_hash(sample_turns) == content_hash(turns_with_extra)
