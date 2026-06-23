#!/usr/bin/env python3
"""
load_memory.py — Retrieve Obsidian memory notes for injection into Claude sessions.

Reads notes from claude-memory/ in your Obsidian vault and prints a formatted
context block suitable for pasting at the start of a new Claude session.

Usage:
    python3 load_memory.py casa-leos              # exact or partial topic slug
    python3 load_memory.py --recent 3             # 3 most recently modified notes
    python3 load_memory.py --search "renewal"     # keyword search across notes
    python3 load_memory.py --all                  # list all notes (title + date only)
    python3 load_memory.py --recent 5 | pbcopy    # copy to clipboard

Output is wrapped in HTML comments for easy identification:
    <!-- OBSIDIAN MEMORY CONTEXT -->
    ...
    <!-- END MEMORY CONTEXT -->
"""

# ── Configurable constants ────────────────────────────────────────────────────

OBSIDIAN_VAULT = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/ObsidianVault"
MEMORY_BASE    = f"{OBSIDIAN_VAULT}/claude-memory"
NOTE_MAX_CHARS = 3000
DEFAULT_MAX    = 5

# ── Imports ───────────────────────────────────────────────────────────────────

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


# ── Note discovery ────────────────────────────────────────────────────────────

def _all_note_paths(include_private: bool = False) -> list[Path]:
    base = Path(MEMORY_BASE).expanduser()
    if not base.exists():
        return []
    paths = []
    for p in base.rglob("*.md"):
        if "private" in p.parts and not include_private:
            continue
        paths.append(p)
    return paths


def _slug_from_path(path: Path) -> str:
    return path.stem


def _title_from_note(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _last_modified(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


# ── Loading a note ────────────────────────────────────────────────────────────

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[truncated]"


def _load_note(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


# ── Retrieval modes ───────────────────────────────────────────────────────────

def find_by_slug(slug: str, include_private: bool, max_results: int) -> list[Path]:
    """Exact match first, then partial match."""
    paths = _all_note_paths(include_private)
    # Exact
    exact = [p for p in paths if _slug_from_path(p) == slug]
    if exact:
        return exact[:max_results]
    # Partial
    partial = [p for p in paths if slug.lower() in _slug_from_path(p).lower()]
    return partial[:max_results]


def find_recent(n: int, include_private: bool) -> list[Path]:
    paths = _all_note_paths(include_private)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[:n]


def find_by_search(query: str, include_private: bool, max_results: int) -> list[Path]:
    """Keyword frequency scoring — returns top matches."""
    keywords = re.findall(r"\w+", query.lower())
    if not keywords:
        return []
    paths = _all_note_paths(include_private)
    scored: list[tuple[int, Path]] = []
    for p in paths:
        text = _load_note(p).lower()
        score = sum(text.count(kw) for kw in keywords)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_results]]


# ── Output formatting ─────────────────────────────────────────────────────────

def _format_context(notes: list[tuple[Path, str]]) -> str:
    lines = ["<!-- OBSIDIAN MEMORY CONTEXT -->", ""]
    for path, content in notes:
        slug = _slug_from_path(path)
        modified = _last_modified(path).strftime("%Y-%m-%d")
        lines.append(f"### [{slug}] (updated: {modified})")
        lines.append(_truncate(content, NOTE_MAX_CHARS))
        lines.append("")
    lines.append("<!-- END MEMORY CONTEXT -->")
    return "\n".join(lines)


def _format_index(paths: list[Path]) -> str:
    lines = ["<!-- OBSIDIAN MEMORY CONTEXT -->", ""]
    lines.append(f"{'Slug':<40} {'Last Modified':<14} Title")
    lines.append("-" * 80)
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        slug = _slug_from_path(path)
        modified = _last_modified(path).strftime("%Y-%m-%d")
        title = _title_from_note(_load_note(path), slug)
        lines.append(f"{slug:<40} {modified:<14} {title}")
    lines.append("")
    lines.append("<!-- END MEMORY CONTEXT -->")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load Obsidian memory notes for injection into Claude sessions."
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help="Topic slug (exact or partial match)",
    )
    parser.add_argument(
        "--recent",
        metavar="N",
        type=int,
        help="Load N most recently modified notes",
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="Search notes by keyword frequency",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print index of all notes",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Include private/ subfolder notes",
    )
    parser.add_argument(
        "--max",
        metavar="N",
        type=int,
        default=DEFAULT_MAX,
        help=f"Maximum notes to return (default: {DEFAULT_MAX})",
    )
    args = parser.parse_args()

    base = Path(MEMORY_BASE).expanduser()
    if not base.exists():
        print(f"<!-- OBSIDIAN MEMORY CONTEXT -->")
        print(f"# Error: claude-memory directory not found at {base}")
        print(f"# Run export_to_obsidian.py first to create notes.")
        print(f"<!-- END MEMORY CONTEXT -->")
        return 1

    # --all mode: just print index
    if args.all:
        paths = _all_note_paths(args.private)
        if not paths:
            print("No notes found.")
            return 0
        print(_format_index(paths))
        return 0

    # Determine which notes to load
    matched_paths: list[Path] = []

    if args.topic:
        matched_paths = find_by_slug(args.topic, args.private, args.max)
        if not matched_paths:
            print(f"<!-- OBSIDIAN MEMORY CONTEXT -->")
            print(f"# No notes found matching slug: {args.topic}")
            print(f"<!-- END MEMORY CONTEXT -->")
            return 1

    elif args.recent:
        matched_paths = find_recent(args.recent, args.private)[:args.max]

    elif args.search:
        matched_paths = find_by_search(args.search, args.private, args.max)
        if not matched_paths:
            print(f"<!-- OBSIDIAN MEMORY CONTEXT -->")
            print(f"# No notes found matching query: {args.search}")
            print(f"<!-- END MEMORY CONTEXT -->")
            return 1

    else:
        parser.print_help()
        return 1

    # Load and format
    notes = [(p, _load_note(p)) for p in matched_paths if _load_note(p)]
    if not notes:
        print("No readable notes found.")
        return 1

    print(_format_context(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
