"""Tests for transcript format detection and parsing functions.

Covers detect_format, _extract_text, _normalize_role, _parse_cli_jsonl,
and parse_transcript from export_to_obsidian.
"""
import json

import pytest

from export_to_obsidian import (
    _extract_text,
    _normalize_role,
    _parse_cli_jsonl,
    detect_format,
    parse_transcript,
)


# ── detect_format ─────────────────────────────────────────────────────────────

class TestDetectFormat:
    def test_jsonl_dict_first_line(self):
        raw = '{"type": "user", "message": {"role": "user", "content": "hi"}}\n'
        assert detect_format(raw) == "jsonl"

    def test_jsonl_skips_leading_blank_lines(self):
        raw = '\n\n{"type": "user", "message": {}}\n'
        assert detect_format(raw) == "jsonl"

    def test_json_array(self):
        raw = '[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]'
        assert detect_format(raw) == "json_array"

    def test_json_object(self):
        raw = '{\n  "messages": [\n    {"role": "user", "content": "hi"}\n  ]\n}'
        assert detect_format(raw) == "json_object"

    def test_unknown_plain_text(self):
        assert detect_format("hello world, no json here") == "unknown"

    def test_empty_string(self):
        assert detect_format("") == "unknown"

    def test_binary_ish_content(self):
        assert detect_format("\x00\x01\x02\x03") == "unknown"

    def test_jsonl_scalar_first_line_whole_not_array_or_object(self):
        # First line is a JSON number (not a dict); full body is also not array/obj
        raw = "42\n43\n"
        assert detect_format(raw) == "unknown"

    def test_json_array_empty(self):
        assert detect_format("[]") == "json_array"

    def test_json_object_single_line(self):
        assert detect_format('{"key": "value"}') == "jsonl"  # first line is dict → jsonl


# ── _extract_text ─────────────────────────────────────────────────────────────

class TestExtractText:
    def test_plain_string_returned_as_is(self):
        assert _extract_text("hello world") == "hello world"

    def test_block_list_type_text_joined(self):
        blocks = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        assert _extract_text(blocks) == "hello\nworld"

    def test_block_list_non_text_type_skipped(self):
        blocks = [{"type": "tool_use", "input": {"cmd": "ls"}}]
        assert _extract_text(blocks) == ""

    def test_block_with_text_key_but_no_type_key(self):
        # Falls back to reading "text" key when block has no type=="text"
        blocks = [{"text": "fallback content"}]
        assert _extract_text(blocks) == "fallback content"

    def test_string_list_joined(self):
        assert _extract_text(["line one", "line two"]) == "line one\nline two"

    def test_none_returns_empty_string(self):
        assert _extract_text(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert _extract_text("") == ""

    def test_mixed_list_str_and_block(self):
        blocks = [{"type": "text", "text": "from block"}, "plain string"]
        assert _extract_text(blocks) == "from block\nplain string"

    def test_empty_list_returns_empty_string(self):
        assert _extract_text([]) == ""

    def test_blocks_with_empty_text_excluded(self):
        blocks = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "real content"},
        ]
        # Empty text strings get filtered by the `if p` join
        assert "real content" in _extract_text(blocks)


# ── _normalize_role ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("role,expected", [
    ("user",          "user"),
    ("human",         "user"),
    ("HUMAN",         "user"),
    ("User",          "user"),
    ("assistant",     "assistant"),
    ("ai",            "assistant"),
    ("claude",        "assistant"),
    ("CLAUDE",        "assistant"),
    ("system",        "system"),
    ("tool",          "system"),
    ("",              "system"),
    ("unknown_role",  "system"),
])
def test_normalize_role(role, expected):
    assert _normalize_role(role) == expected


# ── _parse_cli_jsonl ──────────────────────────────────────────────────────────

def _cli_line(role: str, content: str) -> str:
    return json.dumps({
        "type": role,
        "message": {"role": role, "content": content},
    })


class TestParseCliJsonl:
    def test_basic_two_turns(self):
        raw = "\n".join([
            _cli_line("user", "Hello"),
            _cli_line("assistant", "Hi there"),
        ])
        turns = _parse_cli_jsonl(raw)
        assert len(turns) == 2
        assert turns[0] == {"role": "user",      "content": "Hello"}
        assert turns[1] == {"role": "assistant",  "content": "Hi there"}

    def test_ignores_blank_lines(self):
        raw = "\n" + _cli_line("user", "A") + "\n\n" + _cli_line("assistant", "B") + "\n"
        turns = _parse_cli_jsonl(raw)
        assert len(turns) == 2

    def test_skips_line_without_message_key(self):
        raw = json.dumps({"type": "user", "other": "data"})
        turns = _parse_cli_jsonl(raw)
        assert turns == []

    def test_skips_malformed_json_line_continues_parsing(self):
        raw = "THIS IS NOT JSON\n" + _cli_line("assistant", "valid turn")
        turns = _parse_cli_jsonl(raw)
        assert len(turns) == 1
        assert turns[0]["role"] == "assistant"

    def test_content_as_block_list(self):
        blocks = [{"type": "text", "text": "block content here"}]
        raw = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": blocks},
        })
        turns = _parse_cli_jsonl(raw)
        assert len(turns) == 1
        assert turns[0]["content"] == "block content here"

    def test_empty_content_skipped(self):
        raw = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "   "},
        })
        turns = _parse_cli_jsonl(raw)
        assert turns == []

    def test_empty_string_returns_empty_list(self):
        assert _parse_cli_jsonl("") == []

    def test_queue_operation_type_skipped(self):
        raw = json.dumps({"type": "queue-operation", "content": "some content"})
        turns = _parse_cli_jsonl(raw)
        assert turns == []

    def test_role_normalized_from_message(self):
        # top_type=assistant, message.role=human → role should normalize from message
        raw = json.dumps({
            "type": "user",
            "message": {"role": "human", "content": "hello"},
        })
        turns = _parse_cli_jsonl(raw)
        assert turns[0]["role"] == "user"


# ── parse_transcript ──────────────────────────────────────────────────────────

def _make_jsonl(*pairs) -> str:
    """Build JSONL from (role, content) pairs."""
    return "\n".join(_cli_line(r, c) for r, c in pairs)


def _make_json_array(*pairs) -> str:
    items = [{"role": r, "content": c} for r, c in pairs]
    return json.dumps(items)


class TestParseTranscript:
    def test_dispatch_to_cli_jsonl_for_claude_cli_source(self):
        raw = _make_jsonl(("user", "A"), ("assistant", "B"))
        turns = parse_transcript(raw, "claude-cli")
        assert len(turns) == 2

    def test_dispatch_to_cli_jsonl_when_format_is_jsonl(self):
        # source_name is not claude-cli but format detects as jsonl → jsonl parser used
        raw = _make_jsonl(("user", "A"), ("assistant", "B"))
        turns = parse_transcript(raw, "claude-desktop")
        assert len(turns) == 2

    def test_dispatch_to_json_array_parser(self):
        raw = _make_json_array(("user", "A"), ("assistant", "B"))
        turns = parse_transcript(raw, "claude-desktop")
        assert len(turns) == 2
        assert turns[0]["role"] == "user"

    def test_dispatch_to_json_object_with_messages_key(self):
        # Must be multi-line so detect_format sees "{" on line 1 (not a full dict)
        # and falls through to try json.loads on the whole body → "json_object"
        raw = json.dumps({
            "messages": [
                {"role": "user",      "content": "A"},
                {"role": "assistant", "content": "B"},
            ]
        }, indent=2)
        turns = parse_transcript(raw, "claude-desktop")
        assert len(turns) == 2

    def test_dispatch_to_json_object_with_turns_key(self):
        raw = json.dumps({
            "turns": [
                {"role": "user",      "content": "A"},
                {"role": "assistant", "content": "B"},
            ]
        }, indent=2)
        turns = parse_transcript(raw, "claude-desktop")
        assert len(turns) == 2

    def test_returns_empty_list_for_one_turn(self):
        raw = _make_jsonl(("user", "just me"))
        turns = parse_transcript(raw, "claude-cli")
        assert turns == []

    def test_returns_empty_list_for_zero_turns(self):
        turns = parse_transcript("", "claude-cli")
        assert turns == []

    def test_never_raises_on_garbage_input(self):
        # Should silently return [] regardless of input
        assert parse_transcript("\x00\x01binary\x02garbage", "claude-cli") == []

    def test_never_raises_on_deeply_malformed_json(self):
        assert parse_transcript("{{{not json at all}}}", "claude-desktop") == []

    def test_only_system_messages_returns_empty(self):
        # system role is filtered by _parse_json_array (not in user/assistant)
        raw = _make_json_array(("system", "You are helpful"), ("system", "Be concise"))
        turns = parse_transcript(raw, "claude-desktop")
        assert turns == []

    def test_source_name_cli_forces_jsonl_parser_even_for_array_content(self):
        # Even if content looks like a JSON array, cli source always uses jsonl path
        raw = _make_json_array(("user", "A"), ("assistant", "B"))
        # jsonl parser won't parse a bare array as JSONL lines → returns []
        turns = parse_transcript(raw, "claude-cli")
        # Result depends on parser: the jsonl parser tries each line as a dict.
        # A JSON array line won't have type in ("user","assistant") with a message key,
        # so it returns 0 turns → parse_transcript returns [].
        assert turns == []
