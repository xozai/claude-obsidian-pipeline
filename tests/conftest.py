"""Shared pytest fixtures for claude-obsidian-pipeline tests."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault directory tree and return the claude-memory base."""
    base = tmp_path / "vault" / "claude-memory"
    for domain in ("projects", "personal", "technical", "general", "private"):
        (base / domain).mkdir(parents=True)
    return base


@pytest.fixture
def patch_export_paths(vault, monkeypatch):
    """Redirect MEMORY_BASE and STATE_FILE in export_to_obsidian to tmp dirs."""
    import export_to_obsidian as exp
    monkeypatch.setattr(exp, "MEMORY_BASE", str(vault))
    monkeypatch.setattr(exp, "STATE_FILE", str(vault.parent / "state.json"))
    return vault


@pytest.fixture
def patch_memory_paths(vault, monkeypatch):
    """Redirect MEMORY_BASE in load_memory to the same tmp vault."""
    import load_memory as lm
    monkeypatch.setattr(lm, "MEMORY_BASE", str(vault))
    return vault


@pytest.fixture
def mock_anthropic(monkeypatch):
    """Inject a fake anthropic module into sys.modules.

    classify_conversation does `import anthropic` inside the function body;
    setting sys.modules prevents it from hitting the real SDK or network.
    Returns the fake client so tests can configure .messages.create.return_value.
    """
    fake_module = MagicMock()
    fake_client = MagicMock()
    fake_module.Anthropic.return_value = fake_client
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_client


@pytest.fixture
def sample_turns():
    return [
        {"role": "user",      "content": "How do I set up a LaunchAgent?"},
        {"role": "assistant", "content": "Here are the steps to configure it."},
        {"role": "user",      "content": "What permissions should the plist have?"},
        {"role": "assistant", "content": "Use chmod 644 for the plist file."},
    ]


@pytest.fixture
def sample_jsonl(sample_turns):
    """Well-formed Claude CLI JSONL for four turns."""
    lines = [
        json.dumps({
            "type": t["role"],
            "message": {"role": t["role"], "content": t["content"]},
        })
        for t in sample_turns
    ]
    return "\n".join(lines)


@pytest.fixture
def sample_classification():
    return {
        "topic_slug":          "launch-agent-setup",
        "topic_display":       "LaunchAgent Setup",
        "domain":              "technical",
        "summary":             "Discussed how to configure a macOS LaunchAgent.",
        "key_decisions":       ["Use 644 permissions on the plist"],
        "open_questions":      [],
        "action_items":        [{"task": "Test on clean machine", "owner": "Jose", "due": ""}],
        "high_signal_excerpts":["chmod 644 the plist"],
        "related_topics":      [],
        "tags":                ["macos", "cron"],
        "source":              "claude-cli",
    }


@pytest.fixture
def make_note(vault):
    """Factory: write a .md file at vault/{domain}/{slug}.md and return its path."""
    def _make(domain: str, slug: str, content: str) -> Path:
        path = vault / domain / f"{slug}.md"
        path.write_text(content, encoding="utf-8")
        return path
    return _make
