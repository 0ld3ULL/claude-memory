# Claude Memory

Persistent memory system for Claude Code. Memories have significance (1-10) and decay over time — important stuff persists, noise fades naturally.

## What It Does

- **Significance-based decay** — sig 10 never fades, sig 1 is gone in 2 weeks
- **Full-text search** — find memories fast with SQLite FTS5
- **Session briefs** — generates a compact `claude_brief.md` that Claude reads at startup
- **Context meter** — real-time context % at bottom of screen, auto-warning at 55%/70%
- **Auto-save** — session state saved to `session_log.md` on every exit
- **Global database** — memories stored at `~/.claude-memory/memory.db`, shared across all projects
- **Windows + Mac/Linux** — auto-detects OS, uses Node.js hooks on Windows, bash on Mac/Linux
- **Zero dependencies** — just Python 3.10+ and Node.js (uses only stdlib)

## Install

### Option A: pip install (recommended)

```bash
git clone https://github.com/0ld3ULL/claude-memory.git
cd claude-memory
pip install -e .
```

### Option B: Just use it directly

```bash
git clone https://github.com/0ld3ULL/claude-memory.git
# Then run with: python -m claude_memory (from the claude-memory directory)
# Or add the parent directory to PYTHONPATH
```

## Setup a Project

Navigate to any project and run:

```bash
cd C:\Projects\YourProject
python -m claude_memory init
```

This will:
1. Create the global database at `~/.claude-memory/memory.db`
2. Generate `claude_brief.md` in your project
3. Add memory instructions to your project's `CLAUDE.md`
4. Add `claude_brief.md` to `.gitignore`

## Usage

### Add Memories

```bash
# Format: python -m claude_memory add <category> <significance> "title" "content" [tags]

# Permanent knowledge (never decays)
python -m claude_memory add knowledge 10 "Tech stack" "React 18, Express, PostgreSQL, Drizzle ORM"

# Key decision (slow decay)
python -m claude_memory add decision 8 "Chose SQLite for memory" "No server needed, FTS5 built-in, portable"

# Session work (normal decay)
python -m claude_memory add session 5 "Built video pipeline" "ElevenLabs TTS + Hedra lip-sync + FFmpeg"

# Current state (no decay, manually updated)
python -m claude_memory add current_state 8 "Auth system" "Deployed and working. Session-based."
```

### Generate Session Brief

```bash
# Brief to ~/.claude-memory/brief.md only
python -m claude_memory brief

# Brief + copy to current project
python -m claude_memory brief --project .
```

### Other Commands

```bash
python -m claude_memory status         # Memory stats
python -m claude_memory search "video" # Search memories
python -m claude_memory decay          # Apply weekly decay manually
python -m claude_memory prune          # Remove forgotten items
python -m claude_memory export         # Dump all memories as text
```

### Migrate Existing Database

If you already have a `claude_memory.db` from another project:

```bash
python -m claude_memory migrate C:\Projects\OldProject\data\claude_memory.db
```

This copies the database to the global location (`~/.claude-memory/memory.db`).

## How It Works

### Significance Scale

| Sig | Decay Rate | Example |
|-----|-----------|---------|
| 10 | Never | Project mission, core architecture |
| 9 | 1%/week | Agent roster, key API patterns |
| 8 | 2%/week | Major system components |
| 7 | 5%/week | Important implementation details |
| 6 | 8%/week | Session decisions affecting ongoing work |
| 5 | 10%/week | General session outcomes |
| 4 | 15%/week | Routine debugging |
| 3 | 20%/week | Casual discussions |
| 2 | 30%/week | One-off questions |
| 1 | 50%/week | Noise — gone in 2 weeks |

### Categories

- **knowledge** — Permanent facts (never decays regardless of significance)
- **current_state** — Current status (never decays, update manually when things change)
- **decision** — Choices and reasoning (decays based on significance)
- **session** — Session history and work log (decays normally)

### Memory States

- **Clear** — recall >= 0.7 AND significance >= 6 (fresh and important)
- **Fuzzy** — recall >= 0.4 (still accessible but fading)
- **Blank** — recall < 0.4 (nearly forgotten, excluded from briefs)

### Recall Boost

When you search for a memory, it gets a +0.15 recall boost. Frequently accessed memories stay strong. Ignored memories fade.

## Windows Notes

On Windows, Claude Code runs hook commands through `cmd.exe`, not bash. This means bash scripts fail silently. The `init` command auto-detects Windows and installs Node.js hooks (`.js` files) instead of bash scripts (`.sh` files).

**Requirements on Windows:**
- Python 3.10+
- Node.js (comes with Claude Code — `node.exe` must be in PATH)
- Git Bash is NOT required for the hooks (but useful for the terminal)

If you previously installed with bash hooks and they weren't working, just run `python -m claude_memory init` again — it will replace them with Node.js hooks.

## File Locations

| File | Location |
|------|----------|
| Database | `~/.claude-memory/memory.db` |
| Global brief | `~/.claude-memory/brief.md` |
| Project brief | `<project>/claude_brief.md` |
| Session log | `<project>/session_log.md` |
| Hook scripts | `~/.claude/statusline.js` (Win) or `~/.claude/statusline.sh` (Mac/Linux) |
