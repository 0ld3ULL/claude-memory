"""
CLI entry point for Claude Memory System.

Usage:
    python -m claude_memory brief                              # Generate session brief
    python -m claude_memory brief --project .                  # Also write to current project
    python -m claude_memory status                             # Show memory stats
    python -m claude_memory add <cat> <sig> "title" "content"  # Add a memory
    python -m claude_memory save-session "summary"             # Save session transcript
    python -m claude_memory sessions                           # View last 10 sessions
    python -m claude_memory transcripts                        # View recent session transcripts
    python -m claude_memory transcripts --short                # Only short sessions (quick fixes)
    python -m claude_memory auto-save                          # Called by SessionEnd hook
    python -m claude_memory decay                              # Apply weekly decay
    python -m claude_memory prune                              # Remove forgotten items
    python -m claude_memory search "query"                     # Search memories
    python -m claude_memory export                             # Export all memories as text
    python -m claude_memory init                               # Set up current project (DB + hooks + statusline)
    python -m claude_memory migrate <path>                     # Import from existing DB
"""

import json
import sys
import shutil
from datetime import datetime
from pathlib import Path

from claude_memory.memory_db import ClaudeMemoryDB, DB_DIR, DB_PATH
from claude_memory.brief_generator import generate_brief


# Where Claude Code stores its settings and hooks
CLAUDE_DIR = Path.home() / ".claude"

# The CLAUDE.md snippet that gets added to projects
CLAUDE_MD_SNIPPET = """
## Memory System

This project uses Claude Memory for persistent context across sessions.

### Session Startup (FIRST THING EVERY SESSION)

1. Read `claude_brief.md` — persistent memory with significance scores and recent sessions
2. Read `session_log.md` — auto-saved session history with timestamps (most recent first)
3. **Check recent short sessions** — Run:
   ```bash
   python -m claude_memory transcripts --short
   ```
   If there are short sessions from the last few hours, read them. Short sessions often contain
   quick fixes or troubleshooting that didn't get saved to memory. The context cost is tiny
   compared to repeating work that was already done.

### Memory Commands
```bash
python -m claude_memory brief --project .   # Generate session brief
python -m claude_memory status              # Memory stats
python -m claude_memory add <cat> <sig> "title" "content"  # Save a memory
python -m claude_memory search "query"      # Search memories
python -m claude_memory sessions            # View last 10 saved sessions
python -m claude_memory transcripts         # View recent chat transcripts
python -m claude_memory decay               # Apply weekly decay
```

### Memory Categories
- **knowledge** — Permanent facts (never decays): "Project uses React + Express"
- **current_state** — Current status (never decays, manually updated): "Auth system is deployed"
- **decision** — Choices made (decays by significance): "We chose PostgreSQL because..."
- **session** — Session history (decays normally): "Feb 14: built the API routes"

### Significance Scale (1-10)
- **10** = Foundational (never fades) — project mission, core architecture
- **7-9** = Important — key decisions, major components
- **4-6** = Medium — session outcomes, research findings
- **1-3** = Low — routine debugging, one-off questions

### Context Management (automatic)
- **Statusline** shows real-time context % at bottom of screen
- **At 55%:** Hook triggers — STOP new work, save everything, tell user to restart
- **At 70%:** EMERGENCY — save immediately
- **On exit:** SessionEnd hook auto-saves session state to session_log.md

### Session End Checklist
Before ending a session:
1. Save important decisions/discoveries: `python -m claude_memory add decision 7 "title" "content"`
2. Regenerate brief: `python -m claude_memory brief --project .`
""".strip()

# Statusline command — shows context % at bottom of Claude Code
# Uses simple chars (=.) that work on all terminals including Windows
STATUSLINE_COMMAND = (
    'input=$(cat); '
    'used=$(echo "$input" | jq -r \'.context_window.used_percentage // empty\' 2>/dev/null); '
    'if [ -z "$used" ]; then echo "Context: Ready"; exit 0; fi; '
    'pct=$(printf "%.0f" "$used"); '
    'echo "$pct" > ~/.claude/context_pct.txt; '
    'full="===================="; dots="...................."; '
    'filled=$((pct / 5)); [ "$filled" -gt 20 ] && filled=20; empty=$((20 - filled)); '
    'bar="${full:0:$filled}${dots:0:$empty}"; '
    'if [ "$pct" -ge 70 ]; then echo "[$bar] ${pct}% DANGER"; '
    'elif [ "$pct" -ge 50 ]; then echo "[$bar] ${pct}% SAVE+EXIT"; '
    'else echo "[$bar] ${pct}%"; fi'
)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]
    db = ClaudeMemoryDB()

    if command == "brief":
        # Check for --project flag
        project_path = None
        if "--project" in sys.argv:
            idx = sys.argv.index("--project")
            if idx + 1 < len(sys.argv):
                project_path = Path(sys.argv[idx + 1]).resolve()
            else:
                project_path = Path.cwd()

        path = generate_brief(db, project_path=project_path)
        stats = db.get_stats()
        print(f"Brief generated: {path}")
        if project_path:
            print(f"Also written to: {project_path / 'claude_brief.md'}")
        print(f"  {stats['total']} memories ({stats['clear']} clear, "
              f"{stats['fuzzy']} fuzzy, {stats['fading']} fading)")

    elif command == "status":
        stats = db.get_stats()
        print("Claude Memory Status")
        print("=" * 40)
        print(f"Database:           {DB_PATH}")
        print(f"Total memories:     {stats['total']}")
        print(f"  Clear (>0.7):     {stats['clear']}")
        print(f"  Fuzzy (0.4-0.7):  {stats['fuzzy']}")
        print(f"  Fading (<0.4):    {stats['fading']}")
        print(f"Avg recall:         {stats['avg_recall_strength']}")
        print(f"Last decay:         {stats['last_decay']}")
        print()
        print("By category:")
        for cat, count in stats.get("by_category", {}).items():
            print(f"  {cat}: {count}")

        # Show session info
        sessions = db.get_sessions(limit=10)
        print(f"\nSaved sessions:     {len(sessions)}/10")
        if sessions:
            latest = sessions[0]
            ts = latest["created_at"][:16].replace("T", " ")
            print(f"Latest session:     {ts}")

    elif command == "add":
        if len(sys.argv) < 6:
            print('Usage: python -m claude_memory add <category> <significance> "title" "content" [tags]')
            print()
            print("Categories: decision, current_state, knowledge, session")
            print("Significance: 1-10 (10=never fades, 1=gone in 2 weeks)")
            print()
            print("Examples:")
            print('  python -m claude_memory add knowledge 10 "Tech stack" "React 18 + Express + PostgreSQL"')
            print('  python -m claude_memory add decision 7 "Chose SQLite" "Picked SQLite for memory DB"')
            print('  python -m claude_memory add session 5 "Built API routes" "Added GET/POST for /tools"')
            return
        category = sys.argv[2]
        significance = int(sys.argv[3])
        title = sys.argv[4]
        content = sys.argv[5]
        tags = sys.argv[6].split(",") if len(sys.argv) > 6 else []

        mem_id = db.add(title, content, category, significance, tags, source="manual")
        print(f"Added memory #{mem_id}: [{category}] sig={significance} — {title}")

    elif command == "decay":
        stats = db.decay()
        pruned = db.prune()
        print(f"Decay applied. {pruned} memories pruned.")
        print(f"  Clear: {stats['clear']}, Fuzzy: {stats['fuzzy']}, Fading: {stats['fading']}")

    elif command == "prune":
        pruned = db.prune()
        print(f"Pruned {pruned} forgotten memories.")

    elif command == "search":
        if len(sys.argv) < 3:
            print('Usage: python -m claude_memory search "query"')
            return
        query = " ".join(sys.argv[2:])
        results = db.recall(query, min_strength=0.0, limit=20)
        if not results:
            print(f"No memories found for: {query}")
            return
        print(f"Found {len(results)} memories for: {query}\n")
        for mem in results:
            ts = mem.created_at[:16].replace("T", " ") if mem.created_at else "?"
            print(f"[{ts}] [{mem.category}] {mem.title} (sig={mem.significance}, "
                  f"strength={mem.recall_strength:.2f}, state={mem.state})")
            print(f"  {mem.content[:200]}")
            print()

    elif command == "save-session":
        if len(sys.argv) < 3:
            print('Usage: python -m claude_memory save-session "summary of what happened"')
            print()
            print("Optional flags:")
            print("  --project <name>         Project name")
            print("  --files <f1,f2,f3>       Files changed")
            print()
            print("Examples:")
            print('  python -m claude_memory save-session "Fixed video bug and deployed"')
            print('  python -m claude_memory save-session "Built API" --project AIpulse --files routes.ts')
            return

        # Parse args
        summary_parts = []
        project = ""
        files_changed = []
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--project" and i + 1 < len(sys.argv):
                project = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--files" and i + 1 < len(sys.argv):
                files_changed = sys.argv[i + 1].split(",")
                i += 2
            else:
                summary_parts.append(sys.argv[i])
                i += 1

        summary = " ".join(summary_parts)
        session_id = db.save_session(summary, project=project, files_changed=files_changed)
        total = len(db.get_sessions(limit=10))
        print(f"Session #{session_id} saved ({total}/10 slots used)")
        print(f"  {summary[:200]}")

    elif command == "sessions":
        sessions = db.get_sessions(limit=10)
        if not sessions:
            print("No sessions saved yet.")
            print('Save one with: python -m claude_memory save-session "what happened"')
            return
        print(f"Last {len(sessions)} sessions (oldest auto-deleted after 10):\n")
        for sess in sessions:
            ts = sess["created_at"][:16].replace("T", " ")
            project = f" [{sess['project']}]" if sess.get("project") else ""
            print(f"  #{sess['id']} | {ts}{project}")
            print(f"    {sess['summary'][:200]}")
            if sess.get("files_changed"):
                print(f"    Files: {', '.join(sess['files_changed'][:5])}")
            print()

    elif command == "transcripts":
        _show_transcripts()

    elif command == "auto-save":
        _auto_save(db)

    elif command == "export":
        text = db.export_text()
        print(text)

    elif command == "init":
        _init_project(db)

    elif command == "migrate":
        if len(sys.argv) < 3:
            print("Usage: python -m claude_memory migrate <path-to-old-memory.db>")
            return
        _migrate(sys.argv[2])

    else:
        print(f"Unknown command: {command}")
        print(__doc__)


def _show_transcripts():
    """Show recent session transcripts with timestamps."""
    from claude_memory.transcript_reader import read_recent_sessions

    short_only = "--short" in sys.argv
    limit = 5

    # Check for --limit flag
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        if idx + 1 < len(sys.argv):
            limit = int(sys.argv[idx + 1])

    transcripts = read_recent_sessions(limit=limit, short_only=short_only)

    if not transcripts:
        print("No session transcripts found.")
        return

    label = "short " if short_only else ""
    print(f"Last {len(transcripts)} {label}session transcripts:\n")

    for t in transcripts:
        print(t.summary_text())
        print()


def _auto_save(db: ClaudeMemoryDB):
    """
    Called by SessionEnd hook. Reads the transcript and saves session state.

    1. Parses the current session transcript (from stdin hook input or latest file)
    2. Saves a session summary to the DB (last 10 sessions)
    3. Writes session_log.md to the project directory
    """
    from claude_memory.transcript_reader import read_transcript, list_sessions

    # Try to get transcript path from stdin (hook input is JSON)
    transcript_path = None
    session_id = None
    try:
        hook_input = sys.stdin.read()
        if hook_input.strip():
            data = json.loads(hook_input)
            transcript_path = data.get("transcript_path")
            session_id = data.get("session_id")
    except (json.JSONDecodeError, OSError):
        pass

    # Fallback: use the most recent transcript
    if not transcript_path or not Path(transcript_path).exists():
        recent = list_sessions(limit=1)
        if recent:
            transcript_path = str(recent[0])
            session_id = recent[0].stem

    if not transcript_path or not Path(transcript_path).exists():
        return

    # Parse the transcript
    transcript = read_transcript(Path(transcript_path))

    # Skip tiny sessions (just startup reads, no real work)
    if transcript.user_message_count < 2:
        return

    # Build summary from user messages
    user_texts = []
    for msg in transcript.user_messages[:15]:  # Cap at 15 messages
        text = msg["text"][:300]
        user_texts.append(text)

    summary = " | ".join(user_texts) if user_texts else "Session with no extractable messages"

    # Save to DB
    db.save_session(
        summary=summary[:2000],
        project=Path.cwd().name,
        files_changed=transcript.files_changed[:20],
    )

    # Write session_log.md
    _write_session_log(transcript)


def _write_session_log(transcript):
    """Write session_log.md with timestamped session data."""
    from claude_memory.transcript_reader import SessionTranscript

    session_log = Path.cwd() / "session_log.md"

    # Read existing content to preserve recent sessions
    prev_sessions = ""
    if session_log.exists():
        content = session_log.read_text(encoding="utf-8")
        lines = content.split("\n")
        collecting = False
        prev_lines = []
        session_count = 0
        for line in lines:
            if line.startswith("### Session:"):
                session_count += 1
                if session_count > 5:  # Keep max 5 previous
                    break
                collecting = True
            if collecting:
                prev_lines.append(line)
        prev_sessions = "\n".join(prev_lines)

    # Build new session entry
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    started = transcript.started_at[:19].replace("T", " ") if transcript.started_at else "?"
    ended = transcript.ended_at[:19].replace("T", " ") if transcript.ended_at else "?"
    duration = f" ({transcript.duration_minutes:.0f} min)" if transcript.duration_minutes else ""
    size_kb = transcript.file_size // 1024

    out = [
        "# Session Log",
        f"*Auto-saved: {now}*",
        "*Purpose: Persistent session state — survives context resets*",
        "",
        "## Recent Sessions (most recent first)",
        "",
        f"### Session: {now}",
        f"*ID: {transcript.session_id}*",
        f"*Started: {started} | Ended: {ended}{duration}*",
        f"*Size: {size_kb}KB | Messages: {transcript.user_message_count} user, {transcript.assistant_message_count} assistant*",
        "",
        "**What was discussed:**",
    ]

    for msg in transcript.user_messages[:20]:
        ts = msg["timestamp"][:19].replace("T", " ") if msg.get("timestamp") else "?"
        text = msg["text"][:300]
        out.append(f"- [{ts}] {text}")

    out.append("")

    if transcript.files_changed:
        out.append("**Files changed:**")
        for f in transcript.files_changed[:15]:
            out.append(f"- {f}")
        out.append("")

    if prev_sessions:
        out.append(prev_sessions)

    session_log.write_text("\n".join(out), encoding="utf-8")


def _init_project(db: ClaudeMemoryDB = None):
    """
    Set up the current project for claude-memory.

    Does everything in one command:
    1. Create global memory database
    2. Generate initial brief
    3. Add memory instructions to CLAUDE.md
    4. Install hooks (SessionEnd, UserPromptSubmit) into ~/.claude/settings.json
    5. Install statusline (context meter)
    6. Add claude_brief.md + session_log.md to .gitignore
    7. Create session_log.md
    """
    cwd = Path.cwd()
    print(f"Initializing claude-memory for: {cwd}")
    print()

    if db is None:
        db = ClaudeMemoryDB()

    # --- 1. Database ---
    print(f"  [1/7] Database: {DB_PATH}")

    # --- 2. Brief ---
    generate_brief(db, project_path=cwd)
    print(f"  [2/7] Brief: {cwd / 'claude_brief.md'}")

    # --- 3. CLAUDE.md ---
    claude_md = cwd / "CLAUDE.md"
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if "claude_memory" in existing or "Memory System" in existing:
            print(f"  [3/7] CLAUDE.md already has memory instructions — skipping")
        else:
            with open(claude_md, "a", encoding="utf-8") as f:
                f.write("\n\n" + CLAUDE_MD_SNIPPET + "\n")
            print(f"  [3/7] CLAUDE.md updated")
    else:
        claude_md.write_text(CLAUDE_MD_SNIPPET + "\n", encoding="utf-8")
        print(f"  [3/7] CLAUDE.md created")

    # --- 4. Install hooks ---
    _install_hooks()
    print(f"  [4/7] Hooks installed (SessionEnd + context check)")

    # --- 5. Statusline ---
    _install_statusline()
    print(f"  [5/7] Statusline installed (context meter)")

    # --- 6. .gitignore ---
    gitignore = cwd / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        additions = []
        if "claude_brief.md" not in content:
            additions.append("claude_brief.md")
        if "session_log.md" not in content:
            additions.append("session_log.md")
        if additions:
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n# Claude Memory\n")
                for item in additions:
                    f.write(f"{item}\n")
            print(f"  [6/7] .gitignore updated")
        else:
            print(f"  [6/7] .gitignore already configured")
    else:
        print(f"  [6/7] No .gitignore — consider adding claude_brief.md and session_log.md")

    # --- 7. session_log.md ---
    session_log = cwd / "session_log.md"
    if not session_log.exists():
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        session_log.write_text(
            f"# Session Log\n*Created: {now}*\n"
            f"*Purpose: Persistent session state — survives context resets*\n\n"
            f"## Recent Sessions (most recent first)\n\nNo sessions recorded yet.\n",
            encoding="utf-8"
        )
        print(f"  [7/7] session_log.md created")
    else:
        print(f"  [7/7] session_log.md already exists")

    print()
    print("Done! Claude Code now has:")
    print("  - Persistent memory with significance-based decay")
    print("  - Context meter at bottom of screen (restart to see it)")
    print("  - Auto-save on every exit (session state preserved)")
    print("  - Context warning at 55% (stops work, saves everything)")
    print("  - Session transcripts readable via: python -m claude_memory transcripts")
    print()
    print("Restart Claude Code to activate the statusline and hooks.")


def _install_hooks():
    """Install SessionEnd and UserPromptSubmit hooks into ~/.claude/settings.json."""
    settings_path = CLAUDE_DIR / "settings.json"

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}

    if "hooks" not in settings:
        settings["hooks"] = {}

    hooks = settings["hooks"]

    # SessionEnd hook — auto-save on exit
    session_end_cmd = "python -m claude_memory auto-save"
    if "SessionEnd" not in hooks:
        hooks["SessionEnd"] = []

    se_installed = any(
        h.get("command", "") == session_end_cmd
        for entry in hooks["SessionEnd"]
        for h in entry.get("hooks", [])
    )
    if not se_installed:
        hooks["SessionEnd"].append({
            "matcher": "",
            "hooks": [{"type": "command", "command": session_end_cmd}]
        })

    # UserPromptSubmit hook — context check
    context_cmd = "bash ~/.claude/context_check.sh"
    if "UserPromptSubmit" not in hooks:
        hooks["UserPromptSubmit"] = []

    ups_installed = any(
        h.get("command", "") == context_cmd
        for entry in hooks["UserPromptSubmit"]
        for h in entry.get("hooks", [])
    )
    if not ups_installed:
        hooks["UserPromptSubmit"].append({
            "matcher": "",
            "hooks": [{"type": "command", "command": context_cmd}]
        })

    # Copy context_check.sh to ~/.claude/
    context_check_src = Path(__file__).parent.parent / "hooks" / "context_check.sh"
    context_check_dst = CLAUDE_DIR / "context_check.sh"
    if context_check_src.exists():
        shutil.copy2(context_check_src, context_check_dst)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _install_statusline():
    """Install the context percentage statusline into settings.json."""
    settings_path = CLAUDE_DIR / "settings.json"

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}

    settings["statusLine"] = {
        "type": "command",
        "command": STATUSLINE_COMMAND
    }

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _migrate(old_db_path: str):
    """Import memories from an existing claude_memory.db file."""
    old_path = Path(old_db_path)
    if not old_path.exists():
        print(f"File not found: {old_path}")
        return

    if DB_PATH.exists():
        backup = DB_PATH.with_suffix(".db.backup")
        shutil.copy2(DB_PATH, backup)
        print(f"Backed up existing DB to: {backup}")

    shutil.copy2(old_path, DB_PATH)
    print(f"Migrated: {old_path} -> {DB_PATH}")

    db = ClaudeMemoryDB()
    stats = db.get_stats()
    print(f"Imported {stats['total']} memories "
          f"({stats['clear']} clear, {stats['fuzzy']} fuzzy, {stats['fading']} fading)")


if __name__ == "__main__":
    main()
