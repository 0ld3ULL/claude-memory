"""
Claude Family Bulletin Board — shared cross-awareness between Claude instances.

Each Claude (D, J, Y) maintains its own status file in a shared git repo.
They can read each other's status for cursory awareness without sharing full memories.

Identity file: ~/.claude-memory/identity.json
Bulletin repo: configured in identity.json (e.g. C:\\Projects\\claude-family)
"""

import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

IDENTITY_PATH = Path.home() / ".claude-memory" / "identity.json"


def get_identity() -> Optional[dict]:
    """Read this Claude's identity from ~/.claude-memory/identity.json."""
    if not IDENTITY_PATH.exists():
        return None
    try:
        return json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read identity: {e}")
        return None


def generate_status(db) -> str:
    """Build a markdown status from recent memories and sessions."""
    identity = get_identity()
    if not identity:
        return "# Unknown Claude\n\nNo identity configured.\n"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    claude_name = identity.get("claude_name", "Unknown")
    project = identity.get("project", "Unknown")
    machine = identity.get("machine", "Unknown")

    lines = [
        f"# {claude_name} — Status",
        f"*Updated: {now}*",
        f"*Project: {project} | Machine: {machine}*",
        "",
    ]

    # Recent sessions from DB
    sessions = db.get_sessions(limit=5)
    if sessions:
        lines.append("## Recent Activity")
        lines.append("")
        for sess in sessions:
            ts = sess["created_at"][:16].replace("T", " ")
            proj = f" [{sess['project']}]" if sess.get("project") else ""
            summary = sess["summary"][:150]
            lines.append(f"- **{ts}**{proj} — {summary}")
        lines.append("")

    # Active blockers (high-sig current_state memories)
    blockers = db.recall("blocker", min_strength=0.4, limit=5)
    active = [m for m in blockers if m.category in ("current_state", "knowledge") and m.significance >= 8]
    if active:
        lines.append("## Current Focus")
        lines.append("")
        for mem in active[:3]:
            lines.append(f"- **{mem.title}** — {mem.content[:120]}")
        lines.append("")

    # Stats
    stats = db.get_stats()
    lines.append("## Stats")
    lines.append(f"- Memories: {stats['total']} ({stats['clear']} clear, {stats['fuzzy']} fuzzy)")
    lines.append(f"- Last decay: {stats.get('last_decay', 'never')}")
    lines.append("")

    return "\n".join(lines)


def update_bulletin(db) -> Optional[str]:
    """Write this Claude's status to the bulletin repo, commit and push."""
    identity = get_identity()
    if not identity:
        print("No identity configured. Run: python -m claude_memory identity")
        return None

    bulletin_repo = Path(identity.get("bulletin_repo", ""))
    if not bulletin_repo.exists():
        print(f"Bulletin repo not found: {bulletin_repo}")
        print("Clone it first: gh repo clone 0ld3ULL/claude-family")
        return None

    claude_id = identity["claude_id"]
    bulletin_dir = bulletin_repo / "bulletin"
    bulletin_dir.mkdir(exist_ok=True)

    status_file = bulletin_dir / f"{claude_id}.md"
    status = generate_status(db)
    status_file.write_text(status, encoding="utf-8")

    # Git commit and push
    try:
        subprocess.run(["git", "add", "."], cwd=bulletin_repo, check=True,
                       capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", f"{claude_id}: status update"],
            cwd=bulletin_repo, check=True, capture_output=True, text=True
        )
        subprocess.run(["git", "push"], cwd=bulletin_repo, check=True,
                       capture_output=True, text=True)
        print(f"Bulletin updated and pushed: {status_file}")
    except subprocess.CalledProcessError as e:
        # No changes to commit is OK
        if "nothing to commit" in (e.stdout or "") + (e.stderr or ""):
            print(f"Bulletin file written (no changes to push): {status_file}")
        else:
            print(f"Git error: {e.stderr or e.stdout}")
            print(f"Bulletin file written locally: {status_file}")

    return str(status_file)


def read_family_status() -> dict:
    """Read all sibling Claude bulletin files. Returns {claude_id: markdown_content}."""
    identity = get_identity()
    if not identity:
        print("No identity configured.")
        return {}

    bulletin_repo = Path(identity.get("bulletin_repo", ""))
    bulletin_dir = bulletin_repo / "bulletin"

    if not bulletin_dir.exists():
        print(f"Bulletin directory not found: {bulletin_dir}")
        return {}

    # Pull latest
    try:
        subprocess.run(["git", "pull", "--rebase"], cwd=bulletin_repo,
                       capture_output=True, text=True, timeout=15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # Offline is fine, just read what we have

    my_id = identity["claude_id"]
    statuses = {}

    for md_file in sorted(bulletin_dir.glob("*.md")):
        claude_id = md_file.stem
        if claude_id == my_id:
            continue  # Skip self
        try:
            statuses[claude_id] = md_file.read_text(encoding="utf-8")
        except OSError:
            statuses[claude_id] = "(unreadable)"

    return statuses
