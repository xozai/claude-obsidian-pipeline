#!/usr/bin/env bash
# setup_cron.sh — macOS LaunchAgent installer for claude-obsidian-pipeline
#
# Installs a nightly 11 PM LaunchAgent and a ~/.local/bin symlink so you can
# run `claude-obsidian-export` from anywhere.
#
# Usage:
#   bash cron/setup_cron.sh
#
# Prerequisites:
#   - Python 3.9+
#   - pip3 install anthropic  (or: pip3 install -r requirements.txt)
#   - ANTHROPIC_API_KEY set in your shell environment

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_ROOT="$(dirname "$SCRIPT_DIR")"
EXPORT_SCRIPT="$PIPELINE_ROOT/scripts/export_to_obsidian.py"
PLIST_LABEL="com.josecasaleos.claude-obsidian"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
LOG_FILE="$HOME/.claude-obsidian-pipeline.log"
SYMLINK_DIR="$HOME/.local/bin"
SYMLINK_PATH="$SYMLINK_DIR/claude-obsidian-export"

# Transcript paths to check
CLI_PATH="$HOME/.claude/projects"
DESKTOP_PATH="$HOME/Library/Application Support/Claude"

# ── Helpers ───────────────────────────────────────────────────────────────────

ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; }
fail() { echo "❌ $*"; exit 1; }
info() { echo "ℹ️  $*"; }

# ── Preflight checks ──────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════"
echo "  claude-obsidian-pipeline — Setup"
echo "═══════════════════════════════════════════════"
echo ""

# Python version check (3.9+)
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]; }; then
    fail "Python 3.9+ required. Found: $PYTHON_VERSION — install via homebrew: brew install python3"
fi
ok "Python $PYTHON_VERSION"

# anthropic SDK check
if ! python3 -c "import anthropic" 2>/dev/null; then
    fail "anthropic SDK not found. Run: pip3 install -r $PIPELINE_ROOT/requirements.txt"
fi
ANTHROPIC_VERSION=$(python3 -c "import anthropic; print(anthropic.__version__)" 2>/dev/null || echo "unknown")
ok "anthropic SDK $ANTHROPIC_VERSION"

# ANTHROPIC_API_KEY check
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    fail "ANTHROPIC_API_KEY is not set. Add it to ~/.zshrc:\n  export ANTHROPIC_API_KEY=sk-ant-..."
fi
ok "ANTHROPIC_API_KEY is set"

# Export script exists
if [ ! -f "$EXPORT_SCRIPT" ]; then
    fail "Export script not found: $EXPORT_SCRIPT"
fi
ok "Export script found: $EXPORT_SCRIPT"

# Transcript path checks (warn, not fail)
if [ -d "$CLI_PATH" ]; then
    ok "Claude CLI transcripts: $CLI_PATH"
else
    warn "Claude CLI transcript path not found: $CLI_PATH (CLI source will be skipped)"
fi

if [ -d "$DESKTOP_PATH" ]; then
    ok "Claude Desktop path: $DESKTOP_PATH"
else
    warn "Claude Desktop path not found: $DESKTOP_PATH (Desktop source will be skipped)"
fi

echo ""

# ── Install symlink ───────────────────────────────────────────────────────────

mkdir -p "$SYMLINK_DIR"
chmod +x "$EXPORT_SCRIPT"
ln -sf "$EXPORT_SCRIPT" "$SYMLINK_PATH"
ok "Symlink installed: $SYMLINK_PATH → $EXPORT_SCRIPT"

# Check if ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$SYMLINK_DIR:"* ]]; then
    warn "$SYMLINK_DIR is not in your PATH."
    info "Add this to ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""

# ── Generate LaunchAgent plist ────────────────────────────────────────────────

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>${EXPORT_SCRIPT}</string>
        <string>--source</string>
        <string>all</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key>
        <string>${ANTHROPIC_API_KEY}</string>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>${PATH}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

ok "LaunchAgent plist created: $PLIST_PATH"

# ── Install crontab fallback ──────────────────────────────────────────────────

CRON_ENTRY="0 23 * * * $(which python3) $EXPORT_SCRIPT --source all >> $LOG_FILE 2>&1 # claude-obsidian"

if crontab -l 2>/dev/null | grep -q "claude-obsidian"; then
    warn "crontab entry already exists — skipping"
else
    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    ok "crontab fallback entry added (23:00 nightly)"
fi

echo ""

# ── Activation instructions ───────────────────────────────────────────────────

echo "═══════════════════════════════════════════════"
echo "  Setup complete! Next steps:"
echo "═══════════════════════════════════════════════"
echo ""
echo "1. Load the LaunchAgent (start nightly schedule):"
echo "   launchctl load $PLIST_PATH"
echo ""
echo "2. Verify it's loaded:"
echo "   launchctl list | grep claude-obsidian"
echo ""
echo "3. Trigger manually RIGHT NOW to test:"
echo "   launchctl start $PLIST_LABEL"
echo "   # or:"
echo "   claude-obsidian-export --dry-run"
echo ""
echo "4. Add shell alias for convenience:"
echo "   echo \"alias claude-export='claude-obsidian-export'\" >> ~/.zshrc && source ~/.zshrc"
echo ""
echo "── Manual export examples ─────────────────────"
echo "   claude-obsidian-export                       # all new conversations"
echo "   claude-obsidian-export --source cli          # CLI only"
echo "   claude-obsidian-export --source desktop      # Desktop only"
echo "   claude-obsidian-export --since 2026-04-01    # since a specific date"
echo "   claude-obsidian-export --dry-run             # preview, no writes"
echo "   claude-obsidian-export --force               # reprocess all"
echo ""
echo "── Opt-out keywords (type in any conversation) ─"
echo "   /no-export   skip this conversation entirely"
echo "   /private     export to claude-memory/private/"
echo ""
echo "── Monitor ─────────────────────────────────────"
echo "   tail -f $LOG_FILE"
echo ""
echo "── Uninstall ────────────────────────────────────"
echo "   launchctl unload $PLIST_PATH"
echo "   rm $PLIST_PATH"
echo ""
