# claude-obsidian-pipeline

Nightly export of Claude CLI and Claude Desktop conversations into topic-based Obsidian notes, with context injection support for new Claude sessions.

---

## Prerequisites

- macOS 12 or later
- Python 3.9+ (`python3 --version`)
- Claude CLI installed (`~/.claude/` directory exists)
- Obsidian installed with a vault configured
- `ANTHROPIC_API_KEY` environment variable set

---

## Installation

### 1. Install the Python dependency

```bash
cd claude-obsidian-pipeline
pip3 install -r requirements.txt
```

### 2. Configure your vault path

Open `scripts/export_to_obsidian.py` and update `OBSIDIAN_VAULT` if your vault is in a different location:

```python
# Default (iCloud-synced vault):
OBSIDIAN_VAULT = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/ObsidianVault"

# Local vault example:
OBSIDIAN_VAULT = "~/Documents/ObsidianVault"
```

The same constant is at the top of `scripts/load_memory.py` — update both.

### 3. Run the installer

```bash
bash cron/setup_cron.sh
```

The installer will:
- Verify Python version and `anthropic` SDK
- Confirm `ANTHROPIC_API_KEY` is set
- Warn (not fail) if transcript directories are missing
- Install a nightly LaunchAgent at `~/Library/LaunchAgents/com.josecasaleos.claude-obsidian.plist`
- Install a symlink at `~/.local/bin/claude-obsidian-export`
- Add a crontab fallback entry

### 4. Load the LaunchAgent

```bash
launchctl load ~/Library/LaunchAgents/com.josecasaleos.claude-obsidian.plist
```

### 5. Add shell alias (optional)

```bash
echo "alias claude-export='claude-obsidian-export'" >> ~/.zshrc && source ~/.zshrc
```

---

## Verifying transcript paths

### Claude CLI

```bash
ls ~/.claude/projects/
```

You should see UUID-named subdirectories, each containing `.jsonl` files. The pipeline scans all of these.

### Claude Desktop

```bash
ls ~/Library/Application\ Support/Claude/
```

If you see a `conversations/` or `.jsonl` files here, they will be scanned. If the directory only contains config files (which is normal for many Desktop versions), the Desktop source will log a warning and continue — the CLI source is unaffected.

---

## Configuration reference

All constants are at the top of each script. Edit `scripts/export_to_obsidian.py`:

| Constant | Default | Description |
|---|---|---|
| `OBSIDIAN_VAULT` | iCloud vault path | Path to your Obsidian vault root |
| `MEMORY_BASE` | `{vault}/claude-memory` | Where topic notes are written |
| `STATE_FILE` | `~/.claude-obsidian-state.json` | Tracks processed conversations (JSON) |
| `MODEL` | `claude-sonnet-4-20250514` | Claude model used for classification |
| `OPT_OUT_KEYWORD` | `/no-export` | Conversation-level skip signal |
| `PRIVATE_KEYWORD` | `/private` | Routes note to private/ subfolder |
| `KNOWN_SLUGS` | see script | Hint existing topic slugs to the classifier |
| `TRANSCRIPT_SOURCES` | see script | Enable/disable individual sources |

To disable a source without removing it:

```python
{"name": "claude-desktop", ..., "enabled": False},
```

---

## Daily usage

### Nightly (automatic)

The LaunchAgent runs `export_to_obsidian.py --source all` at 11:00 PM every night. No action needed.

### Manual export

```bash
# Export all new conversations (since yesterday)
claude-obsidian-export

# Preview without writing anything
claude-obsidian-export --dry-run

# Export from CLI source only
claude-obsidian-export --source cli

# Export from Desktop source only
claude-obsidian-export --source desktop

# Export conversations modified after a specific date
claude-obsidian-export --since 2026-04-01

# Reprocess conversations already in the state file
claude-obsidian-export --force
```

### Load context into a new Claude session

```bash
# Load notes for a specific topic (exact or partial slug match)
python3 scripts/load_memory.py casa-leos | pbcopy

# Load 3 most recently updated notes
python3 scripts/load_memory.py --recent 3 | pbcopy

# Search notes by keyword
python3 scripts/load_memory.py --search "renewal rate" | pbcopy

# List all notes (index view)
python3 scripts/load_memory.py --all

# Include private notes
python3 scripts/load_memory.py --recent 5 --private | pbcopy

# Limit number of results
python3 scripts/load_memory.py --search "openclaw" --max 2 | pbcopy
```

The output is wrapped in `<!-- OBSIDIAN MEMORY CONTEXT -->` comments. Paste it at the start of a Claude conversation to inject context.

### Controlling export from within conversations

Type either of these anywhere in the **first or last message** of a conversation:

| Keyword | Effect |
|---|---|
| `/no-export` | Conversation is skipped entirely — never written to vault |
| `/private` | Note is written to `claude-memory/private/{domain}/` instead |

These work whether typed at the **start** (opt-out before you begin) or the **end** (opt-out after the fact). The pipeline checks the first 3 turns and the last 3 turns of every session.

**Examples:**

```
/no-export — let's brainstorm something I don't want saved

Hey Claude, help me draft a birthday message for my wife...
```

```
This conversation was sensitive.
/private
```

---

## Vault folder layout

Notes are organized by domain under `claude-memory/`:

| Folder | Domain | Example topics |
|---|---|---|
| `claude-memory/projects/` | `projects` | openclaw, lead-pass, partner-search |
| `claude-memory/personal/` | `personal` | casa-leos, running-metrics |
| `claude-memory/technical/` | `technical` | reverse-repull, api-design |
| `claude-memory/general/` | `general` | miscellaneous conversations |
| `claude-memory/private/{domain}/` | any | `/private` flagged sessions |

Each note file is named `{topic-slug}.md`. When the same topic appears in multiple conversations, the note is merged (decisions and questions are deduplicated, references are appended, latest summary wins).

---

## Monitoring

```bash
# Watch the log in real time
tail -f ~/.claude-obsidian-pipeline.log

# Inspect what has been processed
cat ~/.claude-obsidian-state.json | python3 -m json.tool | head -60

# Check LaunchAgent is registered
launchctl list | grep claude-obsidian

# Manually trigger the LaunchAgent
launchctl start com.josecasaleos.claude-obsidian

# Stop and unload
launchctl unload ~/Library/LaunchAgents/com.josecasaleos.claude-obsidian.plist
```

---

## Known limitations

- **Claude Desktop transcripts**: Claude Desktop does not expose local conversation files in all versions. The Desktop source scans `~/Library/Application Support/Claude/` for any `.jsonl` files; if none are found, it logs a warning and continues. This does not affect the CLI source.
- **Cross-source deduplication**: Uses MD5 of normalized turn content. If the same conversation was exported at different times (slightly different content), it will produce two notes rather than one.
- **Context window truncation**: Conversations are capped at 12,000 characters before being sent to the API. Very long sessions lose their tails.
- **Topic clustering**: The classifier reuses known slugs from `KNOWN_SLUGS` when possible, but may create near-duplicate notes for closely related topics over time. Use `--force` to reprocess if you want to consolidate.
- **Private notes excluded by default**: `load_memory.py` omits `private/` notes unless `--private` is passed.
- **State file is not concurrent-safe**: Running two export processes simultaneously against the same state file may cause races. Run one at a time.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `Claude Desktop path not found` | Warning only — CLI source still runs. If you know your Desktop conversation path, update `TRANSCRIPT_SOURCES` in the script. |
| `JSONL parse failure` | Logged as a warning with the file path. The conversation is skipped. Check the log for the specific file. |
| `API rate limits / timeout` | The script skips the failed conversation and logs the error. Re-run with `--since` to retry skipped sessions. |
| `Duplicate topic notes` | Run `claude-obsidian-export --force` — the classifier will merge content into the best-matching slug. |
| `anthropic module not found` | `pip3 install -r requirements.txt` |
| `ANTHROPIC_API_KEY not set` | `export ANTHROPIC_API_KEY=sk-ant-...` and re-run `setup_cron.sh` to bake the key into the plist. |
| `launchctl start` does nothing | Check the log: `tail ~/.claude-obsidian-pipeline.log`. The LaunchAgent must be loaded first: `launchctl load ~/Library/LaunchAgents/com.josecasaleos.claude-obsidian.plist` |
| `topic_slug = "skip"` in log | The conversation was too short or trivial. Run with `--force` to reclassify if you think it should be captured. |
| Notes not appearing in Obsidian | iCloud sync delay — wait 30s and refresh Obsidian, or open the vault folder in Finder to trigger sync. |
