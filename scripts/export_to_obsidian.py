#!/usr/bin/env python3
"""
export_to_obsidian.py — Nightly batch exporter for Claude conversations to Obsidian.

Scans Claude CLI and Claude Desktop transcript sources, classifies each conversation
via the Claude API, and writes/merges topic-based notes into your Obsidian vault.

Usage:
    python3 export_to_obsidian.py                        # export all new conversations
    python3 export_to_obsidian.py --since 2026-04-01     # limit to conversations after date
    python3 export_to_obsidian.py --dry-run              # preview without writing
    python3 export_to_obsidian.py --force                # reprocess already-seen conversations
    python3 export_to_obsidian.py --source cli           # only Claude CLI transcripts
    python3 export_to_obsidian.py --source desktop       # only Claude Desktop transcripts

Opt-out keywords (type in first or last message of any conversation):
    /no-export   — skip conversation entirely
    /private     — route to claude-memory/private/ subfolder
"""

# ── Configurable constants ────────────────────────────────────────────────────

TRANSCRIPT_SOURCES = [
    {
        "name": "claude-cli",
        "path": "~/.claude/projects",
        "formats": [".jsonl", ".json"],
        "enabled": True,
    },
    {
        "name": "claude-desktop",
        "path": "~/Library/Application Support/Claude",
        "formats": [".jsonl", ".json"],
        "enabled": True,
    },
]

OBSIDIAN_VAULT = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/ObsidianVault"
MEMORY_BASE    = f"{OBSIDIAN_VAULT}/claude-memory"
STATE_FILE     = "~/.claude-obsidian-state.json"
MODEL          = "claude-sonnet-4-20250514"
OPT_OUT_KEYWORD = "/no-export"
PRIVATE_KEYWORD = "/private"

KNOWN_SLUGS = [
    "lead-pass", "incremental-renewal-rate", "reverse-repull", "openclaw",
    "casa-leos", "running-metrics", "partner-search", "ten-fifty-five",
]

# Files that live alongside transcripts but are NOT conversations
_SKIP_FILENAMES = {
    "claude_desktop_config.json", "buddy-tokens.json", "bridge-state.json",
    "config.json", "extensions-installations.json", "git-worktrees.json",
    "extensions-blocklist.json", "cowork-enabled-cli-ops.json",
    "window-state.json", "extensions-blocklist.json", "Local State",
}

# ── Imports ───────────────────────────────────────────────────────────────────

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ── Format detection ──────────────────────────────────────────────────────────

def detect_format(raw: str) -> str:
    """Return 'jsonl', 'json_array', 'json_object', or 'unknown'."""
    stripped = raw.strip()
    if not stripped:
        return "unknown"
    first_line = next((l for l in stripped.splitlines() if l.strip()), "")
    try:
        obj = json.loads(first_line)
        if isinstance(obj, dict):
            return "jsonl"
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return "json_array"
        if isinstance(parsed, dict):
            return "json_object"
    except (json.JSONDecodeError, ValueError):
        pass
    return "unknown"


# ── Content extraction helpers ────────────────────────────────────────────────

def _extract_text(content: Any) -> str:
    """Normalize content field (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _normalize_role(role: str) -> str:
    role = (role or "").lower()
    if role in ("user", "human"):
        return "user"
    if role in ("assistant", "ai", "claude"):
        return "assistant"
    return "system"


# ── Transcript parsing ────────────────────────────────────────────────────────

def _parse_cli_jsonl(raw: str) -> list[dict]:
    turns = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        # CLI schema: top-level type in ("user","assistant") with nested message
        top_type = obj.get("type", "")
        if top_type in ("user", "assistant") and "message" in obj:
            msg = obj["message"]
            if isinstance(msg, dict):
                role = _normalize_role(msg.get("role", top_type))
                text = _extract_text(msg.get("content", ""))
                if text.strip():
                    turns.append({"role": role, "content": text})
        # Also handle flat {role, content} objects (queue-operation etc.)
        elif top_type == "queue-operation":
            content = obj.get("content", "")
            if isinstance(content, str) and content.strip():
                # These don't have a reliable role — skip to avoid noise
                pass
    return turns


def _parse_json_array(raw: str) -> list[dict]:
    items = json.loads(raw)
    turns = []
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = _normalize_role(item.get("role", ""))
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(item.get("content", ""))
        if text.strip():
            turns.append({"role": role, "content": text})
    return turns


def parse_transcript(raw: str, source_name: str) -> list[dict]:
    """Parse raw transcript text into normalized [{role, content}] turns.

    Never raises — returns [] on any failure.
    """
    try:
        if not raw or not raw.strip():
            return []
        fmt = detect_format(raw)
        if fmt == "jsonl" or source_name == "claude-cli":
            turns = _parse_cli_jsonl(raw)
        elif fmt == "json_array":
            turns = _parse_json_array(raw)
        elif fmt == "json_object":
            obj = json.loads(raw)
            messages = obj.get("messages", obj.get("turns", []))
            turns = _parse_json_array(json.dumps(messages))
        else:
            return []
        return turns if len(turns) >= 2 else []
    except Exception as exc:
        log.warning("parse_transcript failed (%s): %s", source_name, exc)
        return []


# ── Transcript scanning ───────────────────────────────────────────────────────

def scan_transcripts(source: dict, since: datetime) -> list[dict]:
    """Scan source path for transcript files modified after `since`."""
    base = Path(source["path"]).expanduser()
    if not base.exists():
        log.warning("⚠️  [%s] path not found: %s", source["name"], base)
        return []

    results = []
    for ext in source["formats"]:
        for filepath in base.rglob(f"*{ext}"):
            # Skip known non-conversation config files
            if filepath.name in _SKIP_FILENAMES:
                continue
            try:
                stat = filepath.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if modified <= since.replace(tzinfo=timezone.utc):
                    continue
                raw = filepath.read_text(encoding="utf-8", errors="replace")
                results.append({
                    "path": filepath,
                    "modified": modified,
                    "raw": raw,
                    "source_name": source["name"],
                })
            except Exception as exc:
                log.warning("Could not read %s: %s", filepath, exc)
    return results


# ── Text preparation ──────────────────────────────────────────────────────────

def turns_to_text(turns: list[dict], max_chars: int = 12000) -> str:
    """Convert turns to a capped text block for API classification."""
    parts = []
    total = 0
    for turn in turns:
        role_label = "[USER]" if turn["role"] == "user" else "[ASSISTANT]"
        content = turn["content"][:2000]
        chunk = f"\n\n{role_label}: {content}"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts).strip()


def content_hash(turns: list[dict]) -> str:
    """MD5 hash of normalized turn content for deduplication."""
    canonical = json.dumps(
        [{"role": t["role"], "content": t["content"]} for t in turns],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


# ── Opt-out detection ─────────────────────────────────────────────────────────

def _check_window(turns: list[dict], keyword: str) -> bool:
    window = turns[:3] + turns[-3:]
    return any(keyword in t.get("content", "") for t in window)


def is_opt_out(turns: list[dict]) -> bool:
    return _check_window(turns, OPT_OUT_KEYWORD)


def is_private(turns: list[dict]) -> bool:
    return _check_window(turns, PRIVATE_KEYWORD)


# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    path = Path(STATE_FILE).expanduser()
    if not path.exists():
        return {"processed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "processed" not in data:
            data["processed"] = {}
        return data
    except Exception as exc:
        log.warning("State file corrupt, starting fresh: %s", exc)
        return {"processed": {}}


def save_state(state: dict) -> None:
    path = Path(STATE_FILE).expanduser()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.rename(path)


# ── Claude API classification ─────────────────────────────────────────────────

_CLASSIFICATION_SYSTEM = """You classify Claude conversation transcripts into structured topic notes for an Obsidian knowledge base.

Return ONLY valid JSON — no markdown fences, no explanation. The JSON must match this exact schema:
{
  "topic_slug": "kebab-case-slug",
  "topic_display": "Human Readable Title",
  "domain": "projects|personal|technical|general",
  "summary": "2-3 sentence summary of what was discussed and decided.",
  "key_decisions": ["decision 1", "decision 2"],
  "open_questions": ["question 1"],
  "action_items": [{"task": "do something", "owner": "Jose", "due": ""}],
  "high_signal_excerpts": ["notable quote or insight from the conversation"],
  "related_topics": ["other-slug"],
  "tags": ["tag1", "tag2"],
  "source": "claude-cli"
}

Rules:
- topic_slug must be kebab-case, max 40 chars
- Prefer existing slugs when the conversation clearly continues a known topic: {known_slugs}
- If the conversation is trivial (fewer than 2 substantive exchanges, just testing, single-word replies), return topic_slug = "skip"
- domain must be one of: projects, personal, technical, general
- All list fields may be empty arrays []
- action_items.due may be empty string if not mentioned
- source will be provided in the user message — copy it verbatim into the source field
"""


def classify_conversation(text: str, source_name: str) -> dict | None:
    """Call Claude API to classify a conversation. Returns None on failure."""
    try:
        import anthropic
    except ImportError:
        log.error("anthropic SDK not installed. Run: pip3 install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    system = _CLASSIFICATION_SYSTEM.replace("{known_slugs}", json.dumps(KNOWN_SLUGS))

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Source: {source_name}\n\n"
                        f"Classify this conversation:\n\n{text}"
                    ),
                }
            ],
        )
        raw_json = response.content[0].text.strip()
        # Strip accidental markdown fences
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)
        result = json.loads(raw_json)
        result.setdefault("source", source_name)
        return result
    except Exception as exc:
        log.warning("Classification failed: %s", exc)
        return None


# ── Note path resolution ──────────────────────────────────────────────────────

def note_path(slug: str, domain: str, private: bool = False) -> Path:
    base = Path(MEMORY_BASE).expanduser()
    domain = domain if domain in ("projects", "personal", "technical", "general") else "general"
    if private:
        p = base / "private" / domain / f"{slug}.md"
    else:
        p = base / domain / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── Note rendering & merging ──────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)


def _render_list(items: list, prefix: str = "- ") -> str:
    if not items:
        return f"{prefix}(none)"
    return "\n".join(f"{prefix}{item}" for item in items)


def _render_action_items(items: list) -> str:
    if not items:
        return "- [ ] (none)"
    lines = []
    for item in items:
        if isinstance(item, dict):
            task = item.get("task", "")
            owner = item.get("owner", "")
            due = item.get("due", "")
            parts = [task]
            if owner:
                parts.append(f"— {owner}")
            if due:
                parts.append(f"(due: {due})")
            lines.append("- [ ] " + " ".join(parts))
        else:
            lines.append(f"- [ ] {item}")
    return "\n".join(lines)


def _render_excerpts(excerpts: list) -> str:
    if not excerpts:
        return "> (none)"
    return "\n\n".join(f"> {e}" for e in excerpts)


def _render_related(slugs: list) -> str:
    if not slugs:
        return "(none)"
    return " ".join(f"[[{s}]]" for s in slugs)


def _render_new_note(c: dict, date_str: str, source_name: str, filepath: str) -> str:
    template_path = Path(__file__).parent.parent / "templates" / "claude-memory-note.md"
    tags = " ".join(f"#{t}" for t in c.get("tags", []))
    tags = (" " + tags) if tags else ""

    ref_label = "[cli]" if "cli" in source_name else "[desktop]"
    ref = f"- {date_str} `{filepath}` {ref_label}"

    return (
        f"# {c['topic_display']}\n"
        f"#claude-memory #{c['domain']}{tags}\n\n"
        f"*Last updated: {date_str}*\n\n"
        f"## Summary\n{c['summary']}\n\n"
        f"## Key Decisions\n{_render_list(c.get('key_decisions', []))}\n\n"
        f"## Open Questions\n{_render_list(c.get('open_questions', []))}\n\n"
        f"## Action Items\n{_render_action_items(c.get('action_items', []))}\n\n"
        f"## High-Signal Context\n{_render_excerpts(c.get('high_signal_excerpts', []))}\n\n"
        f"## Related Topics\n{_render_related(c.get('related_topics', []))}\n\n"
        f"## Conversation References\n{ref}\n"
    )


def _parse_sections(text: str) -> dict[str, str]:
    """Split note into {section_title: content} dict."""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections


def _dedup_list_lines(existing: str, new_items: list[str]) -> str:
    lines = [l.lstrip("- ").strip() for l in existing.splitlines() if l.strip()]
    existing_set = set(lines)
    for item in new_items:
        clean = item.strip()
        if clean and clean not in existing_set and clean != "(none)":
            lines.append(clean)
            existing_set.add(clean)
    return "\n".join(f"- {l}" for l in lines if l)


def merge_note(path: Path, c: dict, date_str: str, source_name: str, filepath: str) -> None:
    ref_label = "[cli]" if "cli" in source_name else "[desktop]"
    new_ref = f"- {date_str} `{filepath}` {ref_label}"

    if not path.exists():
        content = _render_new_note(c, date_str, source_name, filepath)
        _atomic_write(path, content)
        return

    existing = path.read_text(encoding="utf-8")
    sections = _parse_sections(existing)

    # Update header (tags line stays, update last-updated date)
    header_end = existing.find("\n## ")
    header = existing[:header_end] if header_end != -1 else existing
    header = re.sub(r"\*Last updated: .*?\*", f"*Last updated: {date_str}*", header)

    # Latest summary wins
    sections["Summary"] = c["summary"]

    # Deduplicate lists
    sections["Key Decisions"] = _dedup_list_lines(
        sections.get("Key Decisions", ""), c.get("key_decisions", [])
    )
    sections["Open Questions"] = _dedup_list_lines(
        sections.get("Open Questions", ""), c.get("open_questions", [])
    )

    # Action items: append new ones
    new_action_lines = []
    for item in c.get("action_items", []):
        if isinstance(item, dict):
            task = item.get("task", "")
            owner = item.get("owner", "")
            due = item.get("due", "")
            parts = [task]
            if owner:
                parts.append(f"— {owner}")
            if due:
                parts.append(f"(due: {due})")
            new_action_lines.append("- [ ] " + " ".join(parts))
    existing_actions = sections.get("Action Items", "")
    if new_action_lines:
        merged_actions = (existing_actions + "\n" + "\n".join(new_action_lines)).strip()
        sections["Action Items"] = merged_actions

    # Deduplicate excerpts
    existing_excerpts = sections.get("High-Signal Context", "")
    new_excerpts = c.get("high_signal_excerpts", [])
    existing_exc_set = set(existing_excerpts.splitlines())
    for exc in new_excerpts:
        line = f"> {exc}"
        if line not in existing_exc_set:
            existing_excerpts = (existing_excerpts + f"\n\n{line}").strip()
            existing_exc_set.add(line)
    sections["High-Signal Context"] = existing_excerpts

    # Append new conversation reference
    existing_refs = sections.get("Conversation References", "")
    if new_ref not in existing_refs:
        sections["Conversation References"] = (existing_refs + "\n" + new_ref).strip()

    # Rebuild note
    rebuilt = (
        f"{header}\n\n"
        f"## Summary\n{sections.get('Summary', '')}\n\n"
        f"## Key Decisions\n{sections.get('Key Decisions', '')}\n\n"
        f"## Open Questions\n{sections.get('Open Questions', '')}\n\n"
        f"## Action Items\n{sections.get('Action Items', '')}\n\n"
        f"## High-Signal Context\n{sections.get('High-Signal Context', '')}\n\n"
        f"## Related Topics\n{sections.get('Related Topics', _render_related(c.get('related_topics', [])))}\n\n"
        f"## Conversation References\n{sections.get('Conversation References', new_ref)}\n"
    )
    _atomic_write(path, rebuilt)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Claude conversations to Obsidian topic notes."
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only process conversations modified after this date (default: yesterday)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess conversations already in the state file",
    )
    parser.add_argument(
        "--source",
        choices=["cli", "desktop", "all"],
        default="all",
        help="Which transcript source to process (default: all)",
    )
    args = parser.parse_args()

    # Parse --since date
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            log.error("Invalid date format for --since: %s (expected YYYY-MM-DD)", args.since)
            return 1
    else:
        since = datetime.now(tz=timezone.utc) - timedelta(days=1)

    # Filter sources
    sources = TRANSCRIPT_SOURCES
    if args.source == "cli":
        sources = [s for s in TRANSCRIPT_SOURCES if s["name"] == "claude-cli"]
    elif args.source == "desktop":
        sources = [s for s in TRANSCRIPT_SOURCES if s["name"] == "claude-desktop"]
    sources = [s for s in sources if s.get("enabled", True)]

    state = load_state()
    date_str = datetime.now().strftime("%Y-%m-%d")

    total_processed = 0
    total_skipped = 0
    total_errors = 0

    for source in sources:
        log.info("🔍 Scanning source: %s", source["name"])
        transcripts = scan_transcripts(source, since)
        log.info("   Found %d candidate file(s)", len(transcripts))

        source_processed = 0
        source_skipped = 0

        for item in transcripts:
            filepath = str(item["path"])
            source_name = item["source_name"]

            turns = parse_transcript(item["raw"], source_name)
            if not turns:
                log.info("   ⏭️  Skipped (no usable turns): %s", filepath)
                source_skipped += 1
                continue

            # Opt-out check
            if is_opt_out(turns):
                log.info("   ⏭️  Skipped (/no-export): %s", filepath)
                source_skipped += 1
                continue

            private = is_private(turns)
            h = content_hash(turns)

            if not args.force and h in state["processed"]:
                log.info("   ⏭️  Already processed: %s", filepath)
                source_skipped += 1
                continue

            text = turns_to_text(turns)

            if args.dry_run:
                preview = text[:400].replace("\n", " ")
                note_dir = "claude-memory/private/..." if private else "claude-memory/..."
                print(
                    f"\n{'='*60}\n"
                    f"[DRY RUN] Source: {source_name}\n"
                    f"File: {filepath}\n"
                    f"Destination: {note_dir}\n"
                    f"Preview: {preview}...\n"
                )
                source_skipped += 1
                continue

            result = classify_conversation(text, source_name)
            if result is None:
                log.warning("   ❌ Classification failed: %s", filepath)
                total_errors += 1
                continue

            slug = result.get("topic_slug", "")
            domain = result.get("domain", "general")

            if slug == "skip":
                log.info("   ⏭️  Trivial conversation (skip): %s", filepath)
                state["processed"][h] = {
                    "source": source_name,
                    "date": date_str,
                    "slug": "skip",
                }
                source_skipped += 1
                save_state(state)
                continue

            dest = note_path(slug, domain, private=private)
            merge_note(dest, result, date_str, source_name, filepath)

            state["processed"][h] = {
                "source": source_name,
                "date": date_str,
                "slug": slug,
            }
            save_state(state)

            log.info("   ✅ [%s] → %s", source_name, dest)
            source_processed += 1

        log.info(
            "   Source %s: %d written, %d skipped",
            source["name"], source_processed, source_skipped,
        )
        total_processed += source_processed
        total_skipped += source_skipped

    log.info(
        "\n📊 Done — %d note(s) written, %d skipped, %d error(s)",
        total_processed, total_skipped, total_errors,
    )
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
