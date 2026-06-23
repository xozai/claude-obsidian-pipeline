"""Tests for Claude API conversation classification.

Covers classify_conversation from export_to_obsidian. All tests mock the
Anthropic SDK via sys.modules injection — no real API key required.
"""
import json
import sys
from unittest.mock import MagicMock

import pytest

from export_to_obsidian import MODEL, classify_conversation

# ── Helpers ───────────────────────────────────────────────────────────────────

def _api_response(json_text: str):
    """Build a minimal mock Anthropic API response."""
    resp = MagicMock()
    resp.content = [MagicMock(text=json_text)]
    return resp


VALID_PAYLOAD = {
    "topic_slug":          "launch-agent-setup",
    "topic_display":       "LaunchAgent Setup",
    "domain":              "technical",
    "summary":             "Discussed LaunchAgent configuration on macOS.",
    "key_decisions":       ["Use 644 permissions for the plist"],
    "open_questions":      [],
    "action_items":        [],
    "high_signal_excerpts":[],
    "related_topics":      [],
    "tags":                ["macos"],
}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestClassifyConversation:
    @pytest.fixture(autouse=True)
    def api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    def test_happy_path_returns_parsed_dict(self, mock_anthropic):
        mock_anthropic.messages.create.return_value = _api_response(
            json.dumps(VALID_PAYLOAD)
        )
        result = classify_conversation("sample transcript text", "claude-cli")
        assert isinstance(result, dict)
        assert result["topic_slug"] == "launch-agent-setup"
        assert result["domain"] == "technical"

    def test_source_field_set_from_argument_when_absent(self, mock_anthropic):
        # API response has no "source" key → defaults to source_name argument
        payload_no_source = {k: v for k, v in VALID_PAYLOAD.items() if k != "source"}
        mock_anthropic.messages.create.return_value = _api_response(
            json.dumps(payload_no_source)
        )
        result = classify_conversation("text", "claude-cli")
        assert result["source"] == "claude-cli"

    def test_source_field_from_api_response_preserved(self, mock_anthropic):
        payload_with_source = {**VALID_PAYLOAD, "source": "claude-desktop"}
        mock_anthropic.messages.create.return_value = _api_response(
            json.dumps(payload_with_source)
        )
        result = classify_conversation("text", "claude-cli")
        assert result["source"] == "claude-desktop"  # API value wins over setdefault

    def test_strips_backtick_json_fence(self, mock_anthropic):
        fenced = "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```"
        mock_anthropic.messages.create.return_value = _api_response(fenced)
        result = classify_conversation("text", "claude-cli")
        assert result is not None
        assert result["topic_slug"] == "launch-agent-setup"

    def test_strips_bare_backtick_fence(self, mock_anthropic):
        fenced = "```\n" + json.dumps(VALID_PAYLOAD) + "\n```"
        mock_anthropic.messages.create.return_value = _api_response(fenced)
        result = classify_conversation("text", "claude-cli")
        assert result is not None

    def test_returns_none_on_api_exception(self, mock_anthropic):
        mock_anthropic.messages.create.side_effect = Exception("rate limit exceeded")
        result = classify_conversation("text", "claude-cli")
        assert result is None  # no re-raise

    def test_returns_none_on_malformed_json_response(self, mock_anthropic):
        mock_anthropic.messages.create.return_value = _api_response("not valid json {{{")
        result = classify_conversation("text", "claude-cli")
        assert result is None

    def test_topic_slug_skip_returned_as_is(self, mock_anthropic):
        skip_payload = {**VALID_PAYLOAD, "topic_slug": "skip"}
        mock_anthropic.messages.create.return_value = _api_response(
            json.dumps(skip_payload)
        )
        result = classify_conversation("trivial text", "claude-cli")
        assert result is not None
        assert result["topic_slug"] == "skip"

    def test_exits_on_missing_api_key(self, mock_anthropic, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(SystemExit) as exc:
            classify_conversation("text", "claude-cli")
        assert exc.value.code == 1

    def test_exits_on_missing_sdk(self, monkeypatch):
        # Setting a module entry to None causes ImportError on `import anthropic`
        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(SystemExit) as exc:
            classify_conversation("text", "claude-cli")
        assert exc.value.code == 1

    def test_uses_correct_model_constant(self, mock_anthropic):
        mock_anthropic.messages.create.return_value = _api_response(
            json.dumps(VALID_PAYLOAD)
        )
        classify_conversation("text", "claude-cli")
        call_kwargs = mock_anthropic.messages.create.call_args
        # model may be positional or keyword — check both
        assert MODEL in str(call_kwargs)

    def test_anthropic_client_receives_api_key(self, mock_anthropic, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "my-specific-key")
        mock_anthropic.messages.create.return_value = _api_response(
            json.dumps(VALID_PAYLOAD)
        )
        classify_conversation("text", "claude-cli")
        # The fake_module.Anthropic should have been called with api_key=
        import export_to_obsidian
        # Retrieve the module-level fake_module from sys.modules
        fake_module = sys.modules["anthropic"]
        assert fake_module.Anthropic.called
        call_kwargs = fake_module.Anthropic.call_args
        assert "my-specific-key" in str(call_kwargs)
