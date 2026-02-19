"""
CLI entry point for Claude Memory System.

Usage:
    python -m claude_memory brief                              # Generate session brief
    python -m claude_memory brief --project .                  # Also write to current project
    python -m claude_memory index                              # Build/rebuild 30-day session index
    python -m claude_memory timeline                           # Show 48h timeline + flag unlogged sessions
    python -m claude_memory status                             # Show memory stats
    python -m claude_memory add <cat> <sig> "title" "content"  # Add a memory
    python -m claude_memory save-session "summary"             # Save session transcript
    python -m claude_memory sessions                           # View saved sessions (200MB cap)
    python -m claude_memory transcripts                        # View recent session transcripts
    python -m claude_memory transcripts --short                # Only short sessions (quick fixes)
    python -m claude_memory auto-save                          # Called by SessionEnd hook
    python -m claude_memory decay                              # Apply weekly decay
    python -m claude_memory prune                              # Remove forgotten items
    python -m claude_memory search "query"                     # Search memories
    python -m claude_memory export                             # Export all memories as text
    python -m claude_memory init                               # Set up current project (DB + hooks + statusline)
    python -m claude_memory migrate <path>                     # Import from existing DB
    python -m claude_memory bulletin                           # Update my family bulletin and push
    python -m claude_memory family                             # Read other Claudes' statuses
    python -m claude_memory identity                           # Show my identity
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
3. **Check family bulletin** (once per day): `python -m claude_memory family`
   — Quick scan for announcements from sibling Claudes. Note anything relevant, don't deep-read.
4. **Timeline check** — Run `python -m claude_memory timeline`
   Shows all sessions from last 48 hours mapped against real clock time.
   If any sessions are flagged `!!!` (unlogged), read them and update session_log.md.
   The human exchanged TIME for that work — if hours are unaccounted for, investigate.
5. **Check recent short sessions** — Run:
   ```bash
   python -m claude_memory transcripts --short
   ```
   If there are short sessions from the last few hours, read them. Short sessions often contain
   quick fixes or troubleshooting that didn't get saved to memory. The context cost is tiny
   compared to repeating work that was already done.

### Memory Commands
```bash
python -m claude_memory brief --project .   # Generate session brief
python -m claude_memory timeline            # 48h timeline — flag unlogged sessions
python -m claude_memory status              # Memory stats
python -m claude_memory add <cat> <sig> "title" "content"  # Save a memory
python -m claude_memory search "query"      # Search memories
python -m claude_memory sessions            # View saved sessions (200MB storage cap)
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

### Context Window Management (automatic)
- **Statusline** shows real-time context % at bottom of screen
- **At 70%:** Hook triggers — save state (session_log, memories, brief, git push), then keep working. Auto-compact handles the rest.
- **At 80%:** EMERGENCY — save immediately, finish current task only
- **On exit:** SessionEnd hook auto-saves session state to session_log.md
- **Full transcript** always on disk at `~/.claude/projects/*.jsonl` — searchable after compaction

### End of Day Checklist
When the user says they're done:
1. Save important decisions/discoveries: `python -m claude_memory add decision 7 "title" "content"`
2. Regenerate brief: `python -m claude_memory brief --project .`
3. Update family bulletin: `python -m claude_memory bulletin`
4. Commit and push to git
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
    'if [ "$pct" -ge 80 ]; then echo "[$bar] ${pct}% EMERGENCY"; '
    'elif [ "$pct" -ge 70 ]; then echo "[$bar] ${pct}% SAVE"; '
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
        sessions = db.get_sessions(limit=50)
        print(f"\nSaved sessions:     {len(sessions)}")
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
        total = len(db.get_sessions(limit=50))
        print(f"Session #{session_id} saved ({total} sessions stored)")
        print(f"  {summary[:200]}")

    elif command == "sessions":
        sessions = db.get_sessions(limit=50)
        if not sessions:
            print("No sessions saved yet.")
            print('Save one with: python -m claude_memory save-session "what happened"')
            return
        print(f"Last {len(sessions)} sessions (storage-capped at 200MB):\n")
        for sess in sessions:
            ts = sess["created_at"][:16].replace("T", " ")
            project = f" [{sess['project']}]" if sess.get("project") else ""
            print(f"  #{sess['id']} | {ts}{project}")
            print(f"    {sess['summary'][:200]}")
            if sess.get("files_changed"):
                print(f"    Files: {', '.join(sess['files_changed'][:5])}")
            print()

    elif command == "index":
        _build_session_index()

    elif command == "timeline":
        _show_timeline()

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

    elif command == "bulletin":
        from claude_memory.bulletin import update_bulletin
        update_bulletin(db)

    elif command == "family":
        from claude_memory.bulletin import read_family_status, get_identity
        identity = get_identity()
        if identity:
            print(f"I am {identity['claude_name']} ({identity['claude_id']})")
            print(f"Project: {identity['project']} | Machine: {identity['machine']}")
            print()
        statuses = read_family_status()
        if not statuses:
            print("No family bulletins found (or bulletin repo not cloned).")
        else:
            for claude_id, content in statuses.items():
                print(f"{'=' * 50}")
                print(content)
                print()

    elif command == "identity":
        from claude_memory.bulletin import get_identity
        identity = get_identity()
        if identity:
            print(f"Claude ID:  {identity['claude_id']}")
            print(f"Name:       {identity['claude_name']}")
            print(f"Project:    {identity['project']}")
            print(f"Machine:    {identity['machine']}")
            print(f"Bulletin:   {identity.get('bulletin_repo', 'not configured')}")
        else:
            print("No identity configured.")
            print("Create ~/.claude-memory/identity.json with:")
            print('  {"claude_id": "claude-x", "claude_name": "Claude X",')
            print('   "project": "My Project", "machine": "My PC",')
            print('   "bulletin_repo": "C:\\\\Projects\\\\claude-family"}')

    else:
        print(f"Unknown command: {command}")
        print(__doc__)


def _show_timeline():
    """
    Show a 48-hour timeline of sessions mapped against real clock time.
    Cross-references session_log.md to flag unlogged work.

    The human exchanged TIME for this work. If hours are unaccounted for,
    something was forgotten. Flag it loudly.
    """
    from claude_memory.transcript_reader import list_sessions, read_transcript

    sessions = list_sessions(limit=50)
    if not sessions:
        print("No session transcripts found.")
        return

    cutoff = datetime.now().timestamp() - (48 * 3600)

    # Read session_log.md AND session_index.md to find which sessions are logged
    logged_ids = set()
    logged_timestamps = set()  # HH:MM timestamps from index headers
    import re
    for log_name in ("session_log.md", "session_index.md"):
        log_path = Path.cwd() / log_name
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8")
            # Match full UUIDs in session_log.md
            for match in re.finditer(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", log_text):
                logged_ids.add(match.group(0))
            # Match date+time headers in session_index.md (e.g. "### 2026-02-17 ~15:30")
            for match in re.finditer(r"###\s+(\d{4}-\d{2}-\d{2})\s+~?(\d{2}:\d{2})", log_text):
                logged_timestamps.add(f"{match.group(1)} {match.group(2)}")

    # Collect sessions within 48h window
    recent = []
    for session_path in sessions:
        mtime = session_path.stat().st_mtime
        if mtime < cutoff:
            continue
        t = read_transcript(session_path)
        if t.user_message_count < 1:
            continue
        recent.append(t)

    # Sort oldest first (left to right = past to future)
    recent.sort(key=lambda t: t.started_at or "")

    if not recent:
        print("No sessions in last 48 hours.")
        return

    # Calculate total logged-in time
    total_minutes = sum(t.duration_minutes or 0 for t in recent)
    total_hours = total_minutes / 60

    # Print the timeline
    print("=" * 70)
    print("  48-HOUR TIMELINE — Time exchanged for work")
    print("=" * 70)
    print()

    unlogged = []
    for t in recent:
        sid = t.session_id
        start = t.started_at[:16].replace("T", " ") if t.started_at else "?"
        dur = t.duration_minutes or 0
        size_kb = t.file_size // 1024
        msgs = t.user_message_count
        # Check if logged by UUID or by timestamp proximity (within 2 hours)
        is_logged = sid in logged_ids
        if not is_logged and t.started_at:
            try:
                sess_dt = datetime.fromisoformat(t.started_at.replace("Z", "+00:00"))
                sess_date = sess_dt.strftime("%Y-%m-%d")
                sess_hour = sess_dt.hour
                for lts in logged_timestamps:
                    ldate, ltime = lts.split(" ")
                    lhour = int(ltime.split(":")[0])
                    if ldate == sess_date and abs(lhour - sess_hour) <= 2:
                        is_logged = True
                        break
            except (ValueError, TypeError):
                pass

        # Duration display
        if dur >= 60:
            dur_str = f"{dur / 60:.1f}h"
        else:
            dur_str = f"{dur:.0f}m"

        # Status indicator
        if is_logged:
            status = "  OK "
        elif dur < 5:
            status = "  -- "  # Tiny session, not worth flagging
        else:
            status = " !!! "
            unlogged.append(t)

        # First user message as context
        first_msg = ""
        for msg in t.user_messages:
            text = msg.get("text", "").strip()
            if text and not text.startswith("{") and not text.startswith("<"):
                first_msg = text[:60]
                break

        # The timeline bar — width proportional to duration (1 char = 10 min, max 30)
        bar_len = min(int(dur / 10), 30) if dur > 0 else 0
        bar = "#" * max(bar_len, 1)

        print(f"  {start}  [{status}] {bar:<30s}  {dur_str:>5s}  {sid[:8]}")
        if first_msg:
            print(f"                          {first_msg}")
        print()

    # Summary
    print("-" * 70)
    print(f"  Total logged-in time: {total_hours:.1f} hours across {len(recent)} sessions")
    print(f"  Sessions logged in session_log.md: {len(recent) - len(unlogged)}/{len(recent)}")

    if unlogged:
        print()
        print("!" * 70)
        print("  WARNING: UNLOGGED SESSIONS DETECTED")
        print("  You exchanged time for work that isn't in the logs.")
        print("  These sessions have NO entry in session_log.md:")
        print()
        for t in unlogged:
            dur = t.duration_minutes or 0
            if dur >= 60:
                dur_str = f"{dur / 60:.1f} hours"
            else:
                dur_str = f"{dur:.0f} min"
            start = t.started_at[:16].replace("T", " ") if t.started_at else "?"
            print(f"    {t.session_id[:8]}  {start}  {dur_str}  ({t.user_message_count} messages)")
            # Show first meaningful user message
            for msg in t.user_messages[:3]:
                text = msg.get("text", "").strip()
                if text and not text.startswith("{") and not text.startswith("<"):
                    print(f"      > {text[:100]}")
                    break
        print()
        print("  ACTION: Read these sessions and update session_log.md")
        print("!" * 70)
    else:
        print("  All sessions accounted for.")

    # Check for hook errors (context_check.js failures = safety net was down)
    hook_error_log = Path.home() / ".claude" / "hook_errors.log"
    if hook_error_log.exists():
        try:
            errors = hook_error_log.read_text(encoding="utf-8").strip().splitlines()
            # Filter to errors within 48h window
            recent_errors = []
            for line in errors:
                try:
                    err_ts = line.split(" ")[0]
                    err_dt = datetime.fromisoformat(err_ts.replace("Z", "+00:00"))
                    if err_dt.timestamp() > cutoff:
                        recent_errors.append(line)
                except (ValueError, IndexError):
                    pass
            if recent_errors:
                print()
                print("~" * 70)
                print(f"  HOOK ERRORS: {len(recent_errors)} context check failures in last 48h")
                print("  The safety net (save-at-70%) was NOT running during these times.")
                print("  Sessions near these timestamps may have unsaved work.")
                print()
                for err in recent_errors[-10:]:  # Show last 10
                    print(f"    {err[:80]}")
                print("~" * 70)
        except (OSError, UnicodeDecodeError):
            pass

    print()


def _build_session_index(days: int = 30):
    """
    Build session_index.md — a bullet-point summary of all sessions from the last N days.

    This is the "medium-term memory layer" between the full 48h recall and
    on-demand jq searches. Each session gets 3-5 bullet points extracted
    from user messages.

    Args:
        days: How many days of sessions to index (default: 30)
    """
    from claude_memory.transcript_reader import list_sessions, read_transcript

    sessions = list_sessions(limit=200)  # Get all available sessions
    if not sessions:
        print("No session transcripts found.")
        return

    cutoff = datetime.now().timestamp() - (days * 86400)

    lines = [
        "# Session Index (30 days)",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "*Bullet summaries of recent sessions. Full transcripts searchable via jq.*",
        "",
    ]

    indexed_count = 0
    for session_path in reversed(sessions):  # oldest first for chronological order
        # Skip sessions older than cutoff
        mtime = session_path.stat().st_mtime
        if mtime < cutoff:
            continue

        transcript = read_transcript(session_path)

        # Skip tiny sessions (just startup reads)
        if transcript.user_message_count < 2:
            continue

        indexed_count += 1

        # Build entry header
        ts = transcript.started_at[:16].replace("T", " ") if transcript.started_at else "unknown"
        dur = f" ({transcript.duration_minutes:.0f} min)" if transcript.duration_minutes else ""
        size_kb = transcript.file_size // 1024

        lines.append(f"### {ts}{dur} — {size_kb}KB")

        # Extract bullet points from user messages (first 8 messages, deduplicated themes)
        bullets = _extract_session_bullets(transcript)
        for bullet in bullets:
            lines.append(f"- {bullet}")

        if transcript.files_changed:
            files_str = ", ".join(transcript.files_changed[:8])
            lines.append(f"- *Files: {files_str}*")

        lines.append("")

    index_path = Path.cwd() / "session_index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")

    line_count = len(lines)
    print(f"Session index built: {index_path}")
    print(f"  {indexed_count} sessions indexed ({line_count} lines)")
    print(f"  Covering last {days} days")


def _extract_session_bullets(transcript) -> list[str]:
    """
    Extract 3-5 bullet points from a session transcript's user messages.

    Condenses the user messages into actionable summaries.
    """
    bullets = []
    seen_topics = set()

    for msg in transcript.user_messages[:12]:
        text = msg["text"].strip()
        if not text or len(text) < 10:
            continue

        # Take first sentence or first 150 chars
        first_line = text.split("\n")[0]
        if len(first_line) > 150:
            first_line = first_line[:147] + "..."

        # Skip near-duplicate topics (simple dedup by first 30 chars)
        topic_key = first_line[:30].lower()
        if topic_key in seen_topics:
            continue
        seen_topics.add(topic_key)

        bullets.append(first_line)

        if len(bullets) >= 5:
            break

    return bullets


def _append_to_session_index(transcript):
    """Append a single session entry to session_index.md (called during auto-save)."""
    if transcript.user_message_count < 2:
        return

    index_path = Path.cwd() / "session_index.md"

    # Build entry
    ts = transcript.started_at[:16].replace("T", " ") if transcript.started_at else "unknown"
    dur = f" ({transcript.duration_minutes:.0f} min)" if transcript.duration_minutes else ""
    size_kb = transcript.file_size // 1024

    entry_lines = [
        f"### {ts}{dur} — {size_kb}KB",
    ]

    bullets = _extract_session_bullets(transcript)
    for bullet in bullets:
        entry_lines.append(f"- {bullet}")

    if transcript.files_changed:
        files_str = ", ".join(transcript.files_changed[:8])
        entry_lines.append(f"- *Files: {files_str}*")

    entry_lines.append("")
    entry = "\n".join(entry_lines)

    if index_path.exists():
        # Append to existing index
        content = index_path.read_text(encoding="utf-8")
        content = content.rstrip() + "\n\n" + entry
        index_path.write_text(content, encoding="utf-8")
    else:
        # Create new index with header
        header = [
            "# Session Index (30 days)",
            f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
            "*Bullet summaries of recent sessions. Full transcripts searchable via jq.*",
            "",
            entry,
        ]
        index_path.write_text("\n".join(header), encoding="utf-8")


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
    2. Saves a session summary to the DB (200MB storage cap)
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

    # Append to session_index.md
    _append_to_session_index(transcript)


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
                if session_count > 10:  # Keep more previous sessions in log
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
    stats = db.get_stats()
    mem_count = stats.get("total_memories", 0)
    if mem_count > 0:
        print(f"  [1/7] Database: {DB_PATH} ({mem_count} existing memories — KEEPING INTACT)")
    else:
        print(f"  [1/7] Database: {DB_PATH} (fresh)")

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
        if "session_index.md" not in content:
            additions.append("session_index.md")
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
    print("  - Context save point at 70% (saves state, keeps working)")
    print("  - Session transcripts readable via: python -m claude_memory transcripts")
    if mem_count > 0:
        print()
        print(f"  Your {mem_count} existing memories are untouched.")
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

    # UserPromptSubmit hook — context check (uses node for cross-platform support)
    context_check_dst = CLAUDE_DIR / "context_check.js"
    context_cmd_node = f"node {context_check_dst}"
    # Also accept legacy bash command as already-installed
    context_cmd_bash = "bash ~/.claude/context_check.sh"
    if "UserPromptSubmit" not in hooks:
        hooks["UserPromptSubmit"] = []

    ups_installed = any(
        h.get("command", "") in (context_cmd_node, context_cmd_bash)
        for entry in hooks["UserPromptSubmit"]
        for h in entry.get("hooks", [])
    )
    if not ups_installed:
        hooks["UserPromptSubmit"].append({
            "matcher": "",
            "hooks": [{"type": "command", "command": context_cmd_node}]
        })

    # Write context_check.js (embedded — no external file dependency)
    context_check_js = """\
const fs = require('fs');
const path = require('path');

const pctFile = path.join(process.env.HOME || process.env.USERPROFILE, '.claude', 'context_pct.txt');

try {
  const pct = parseInt(fs.readFileSync(pctFile, 'utf8').trim(), 10);
  if (pct >= 80) {
    console.log(`CONTEXT EMERGENCY (${pct}%): Save immediately — update session_log.md, save memories, regenerate brief, commit and push. Finish only your current task, then let auto-compact handle continuation.`);
  } else if (pct >= 70) {
    console.log(`CONTEXT SAVE POINT (${pct}%): Save state now — (1) Update session_log.md with full state, (2) Save memories via python -m claude_memory add, (3) Regenerate brief via python -m claude_memory brief, (4) Commit and push to git. Then keep working — auto-compact will handle continuation when needed.`);
  }
} catch (e) {
  // File doesn't exist or can't be read — no warning needed
}
"""
    if not context_check_dst.exists():
        context_check_dst.write_text(context_check_js, encoding="utf-8")

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
