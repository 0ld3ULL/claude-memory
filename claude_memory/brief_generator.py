"""
Brief Generator — Produces a concise claude_brief.md for session startup.

Reads the memory database, applies decay if due, and generates a
markdown file organized by category and significance.

This is what Claude Code reads at the start of every session —
a compact summary of everything it needs to remember.
"""

from datetime import datetime, timedelta
from pathlib import Path

from claude_memory.memory_db import ClaudeMemoryDB, DB_DIR

# Default brief goes to global location
BRIEF_PATH = DB_DIR / "brief.md"

# How many days between automatic decay applications
DECAY_INTERVAL_DAYS = 7


def generate_brief(db: ClaudeMemoryDB, output_path: Path = None, project_path: Path = None) -> str:
    """
    Generate a session brief from the memory database.

    Args:
        db: Memory database instance
        output_path: Where to write the brief (default: ~/.claude-memory/brief.md)
        project_path: If provided, also writes claude_brief.md to this directory

    Steps:
    1. Check if decay is due (weekly) — apply if needed
    2. Prune forgotten items
    3. Export memories organized by category
    4. Write concise brief to file(s)

    Returns:
        Path to the generated brief file
    """
    if output_path is None:
        output_path = BRIEF_PATH

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Auto-decay if due ---
    last_decay = db.get_last_meta("last_decay")
    if last_decay:
        last_dt = datetime.fromisoformat(last_decay)
        if datetime.now() - last_dt > timedelta(days=DECAY_INTERVAL_DAYS):
            db.decay()
            db.prune()
    else:
        # First run — apply initial decay
        db.decay()

    # --- Step 2: Gather memories ---
    stats = db.get_stats()

    all_memories = db.export_all(min_strength=0.0)

    # Split by category
    categories = {}
    for mem in all_memories:
        if mem.category not in categories:
            categories[mem.category] = []
        categories[mem.category].append(mem)

    # --- Step 3: Build brief ---
    lines = []
    lines.append("# Claude Session Brief")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append(f"*Memories: {stats['total']} total — "
                 f"{stats['clear']} clear, {stats['fuzzy']} fuzzy, "
                 f"{stats['fading']} fading*")
    lines.append(f"*Last decay: {stats['last_decay']}*")
    lines.append("")

    # Category order: knowledge first, then current_state, decisions, sessions
    category_order = ["knowledge", "current_state", "decision", "session"]
    category_labels = {
        "knowledge": "Permanent Knowledge (never fades)",
        "current_state": "Current State (manually updated)",
        "decision": "Decisions",
        "session": "Session History",
    }

    for cat in category_order:
        memories = categories.get(cat, [])
        if not memories:
            continue

        label = category_labels.get(cat, cat.title())
        lines.append(f"## {label}")
        lines.append("")

        for mem in memories:
            # Skip blank memories in the brief (they're fading out)
            if mem.state == "blank" and cat not in ("knowledge", "current_state"):
                continue

            # Format based on state
            if mem.state == "clear":
                prefix = ""
            elif mem.state == "fuzzy":
                prefix = "[fuzzy] "
            else:
                prefix = "[fading] "

            sig_indicator = "*" * min(mem.significance, 5)  # Visual significance

            lines.append(f"### {prefix}{mem.title} {sig_indicator}")
            lines.append(mem.content)

            if mem.tags:
                lines.append(f"*Tags: {', '.join(mem.tags)}*")

            lines.append("")

    # Handle any categories not in our order
    for cat, memories in categories.items():
        if cat in category_order:
            continue
        lines.append(f"## {cat.title()}")
        lines.append("")
        for mem in memories:
            if mem.state == "blank":
                continue
            lines.append(f"### {mem.title}")
            lines.append(mem.content)
            lines.append("")

    # --- Step 3b: Recent Sessions (last 10 auto-captured) ---
    sessions = db.get_sessions(limit=10)
    if sessions:
        lines.append("## Recent Sessions (auto-captured, oldest auto-deleted after 10)")
        lines.append("")
        for sess in sessions:
            ts = sess["created_at"][:16].replace("T", " ")
            project = f" [{sess['project']}]" if sess.get("project") else ""
            lines.append(f"### {ts}{project}")
            lines.append(sess["summary"])
            if sess.get("files_changed"):
                lines.append(f"*Files: {', '.join(sess['files_changed'][:10])}*")
            lines.append("")

    # --- Step 4: Footer ---
    lines.append("---")
    lines.append("## Memory Commands")
    lines.append("```")
    lines.append("python -m claude_memory brief          # Regenerate this file")
    lines.append("python -m claude_memory status         # Memory stats")
    lines.append('python -m claude_memory add <cat> <sig> "title" "content"')
    lines.append('python -m claude_memory search "query" # Search memories')
    lines.append("python -m claude_memory decay          # Apply decay manually")
    lines.append("```")

    # --- Write ---
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")

    # Also write to project directory if specified
    if project_path:
        project_brief = Path(project_path) / "claude_brief.md"
        project_brief.write_text(content, encoding="utf-8")

    return str(output_path)
