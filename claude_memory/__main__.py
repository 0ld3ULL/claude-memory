"""
CLI entry point for Claude Memory System.

Usage:
    python -m claude_memory brief                              # Generate session brief
    python -m claude_memory brief --project .                  # Also write to current project
    python -m claude_memory status                             # Show memory stats
    python -m claude_memory add <cat> <sig> "title" "content"  # Add a memory
    python -m claude_memory decay                              # Apply weekly decay
    python -m claude_memory prune                              # Remove forgotten items
    python -m claude_memory search "query"                     # Search memories
    python -m claude_memory export                             # Export all memories as text
    python -m claude_memory init                               # Set up current project
    python -m claude_memory migrate <path>                     # Import from existing DB
"""

import sys
import shutil
from pathlib import Path

from claude_memory.memory_db import ClaudeMemoryDB, DB_DIR, DB_PATH
from claude_memory.brief_generator import generate_brief


CLAUDE_MD_SNIPPET = """
## Memory System

This project uses Claude Memory for persistent context across sessions.

**FIRST THING EVERY SESSION:** Read `claude_brief.md` if it exists — it contains persistent memory from previous sessions.

### Memory Commands
```bash
python -m claude_memory brief --project .   # Generate session brief (also writes claude_brief.md here)
python -m claude_memory status              # Memory stats
python -m claude_memory add <cat> <sig> "title" "content"  # Save a memory
python -m claude_memory search "query"      # Search memories
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

### Session End Checklist
Before ending a session:
1. Save important decisions/discoveries: `python -m claude_memory add decision 7 "title" "content"`
2. Regenerate brief: `python -m claude_memory brief --project .`
""".strip()


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

    elif command == "add":
        if len(sys.argv) < 6:
            print('Usage: python -m claude_memory add <category> <significance> "title" "content" [tags]')
            print()
            print("Categories: decision, current_state, knowledge, session")
            print("Significance: 1-10 (10=never fades, 1=gone in 2 weeks)")
            print()
            print("Examples:")
            print('  python -m claude_memory add knowledge 10 "Tech stack" "React 18 + Express + PostgreSQL"')
            print('  python -m claude_memory add decision 7 "Chose SQLite" "Picked SQLite for memory DB — no server needed"')
            print('  python -m claude_memory add session 5 "Built API routes" "Added GET/POST endpoints for tools and reviews"')
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
            print(f"[{mem.category}] {mem.title} (sig={mem.significance}, "
                  f"strength={mem.recall_strength:.2f}, state={mem.state})")
            print(f"  {mem.content[:200]}")
            print()

    elif command == "export":
        text = db.export_text()
        print(text)

    elif command == "init":
        _init_project()

    elif command == "migrate":
        if len(sys.argv) < 3:
            print("Usage: python -m claude_memory migrate <path-to-old-memory.db>")
            return
        _migrate(sys.argv[2])

    else:
        print(f"Unknown command: {command}")
        print(__doc__)


def _init_project():
    """Set up the current project for claude-memory."""
    cwd = Path.cwd()
    print(f"Initializing claude-memory for: {cwd}")
    print()

    # 1. Ensure global DB exists
    db = ClaudeMemoryDB()
    print(f"  Database: {DB_PATH}")

    # 2. Generate initial brief
    generate_brief(db, project_path=cwd)
    print(f"  Brief: {cwd / 'claude_brief.md'}")

    # 3. Check for CLAUDE.md
    claude_md = cwd / "CLAUDE.md"
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if "claude_memory" in existing or "Memory Commands" in existing:
            print(f"  CLAUDE.md already has memory instructions — skipping")
        else:
            with open(claude_md, "a", encoding="utf-8") as f:
                f.write("\n\n" + CLAUDE_MD_SNIPPET + "\n")
            print(f"  CLAUDE.md updated with memory instructions")
    else:
        claude_md.write_text(CLAUDE_MD_SNIPPET + "\n", encoding="utf-8")
        print(f"  CLAUDE.md created with memory instructions")

    # 4. Add claude_brief.md to .gitignore if not already there
    gitignore = cwd / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if "claude_brief.md" not in content:
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n# Claude Memory\nclaude_brief.md\n")
            print(f"  .gitignore updated")
    else:
        print(f"  No .gitignore found — consider adding claude_brief.md to it")

    print()
    print("Done! Claude Code will now:")
    print("  1. Read claude_brief.md at the start of each session")
    print("  2. Save memories with: python -m claude_memory add ...")
    print("  3. Regenerate brief with: python -m claude_memory brief --project .")
    print()
    stats = db.get_stats()
    print(f"Current memory: {stats['total']} memories "
          f"({stats['clear']} clear, {stats['fuzzy']} fuzzy, {stats['fading']} fading)")


def _migrate(old_db_path: str):
    """Import memories from an existing claude_memory.db file."""
    old_path = Path(old_db_path)
    if not old_path.exists():
        print(f"File not found: {old_path}")
        return

    if DB_PATH.exists():
        # Backup existing
        backup = DB_PATH.with_suffix(".db.backup")
        shutil.copy2(DB_PATH, backup)
        print(f"Backed up existing DB to: {backup}")

    # Copy old DB to new location
    shutil.copy2(old_path, DB_PATH)
    print(f"Migrated: {old_path} -> {DB_PATH}")

    # Verify
    db = ClaudeMemoryDB()
    stats = db.get_stats()
    print(f"Imported {stats['total']} memories "
          f"({stats['clear']} clear, {stats['fuzzy']} fuzzy, {stats['fading']} fading)")


if __name__ == "__main__":
    main()
