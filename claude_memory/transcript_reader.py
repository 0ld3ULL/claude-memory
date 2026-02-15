"""
Transcript Reader — Reads Claude Code session transcripts (JSONL files).

Claude Code saves full session transcripts at:
    ~/.claude/projects/<project-slug>/<session-id>.jsonl

The project slug is derived from the working directory path:
    C:\\Projects\\MyApp -> C--Projects-MyApp
    /home/user/myapp -> -home-user-myapp

Each line in the JSONL file is a JSON object with:
    - type: "user", "assistant", "system", "progress", etc.
    - message: Contains the actual content
    - timestamp: ISO 8601 timestamp (when available)
    - sessionId: Session UUID

This module extracts structured data from these transcripts:
    - Timestamped user messages
    - Files that were edited/written
    - Session start/end times
    - Session duration and size
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# Where Claude Code stores transcripts
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


@dataclass
class SessionTranscript:
    """Parsed session transcript with timestamped data."""
    session_id: str
    file_path: str
    file_size: int
    started_at: Optional[str] = None      # ISO timestamp
    ended_at: Optional[str] = None        # ISO timestamp
    user_messages: list = field(default_factory=list)   # [{"timestamp": ..., "text": ...}]
    files_changed: list = field(default_factory=list)   # ["/path/to/file.py", ...]
    user_message_count: int = 0
    assistant_message_count: int = 0

    @property
    def duration_minutes(self) -> Optional[float]:
        """Session duration in minutes."""
        if self.started_at and self.ended_at:
            try:
                start = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(self.ended_at.replace("Z", "+00:00"))
                return (end - start).total_seconds() / 60
            except (ValueError, TypeError):
                return None
        return None

    @property
    def is_short(self) -> bool:
        """A short session — likely quick fix or troubleshooting."""
        return self.file_size < 500_000  # Under 500KB

    def summary_text(self) -> str:
        """Human-readable summary of the session."""
        lines = []

        # Time info
        ts = self.started_at[:19].replace("T", " ") if self.started_at else "unknown"
        dur = f" ({self.duration_minutes:.0f} min)" if self.duration_minutes else ""
        size_kb = self.file_size // 1024
        lines.append(f"Session at {ts}{dur} — {size_kb}KB, {self.user_message_count} user messages")

        # User messages with timestamps
        for msg in self.user_messages:
            t = msg["timestamp"][:19].replace("T", " ") if msg.get("timestamp") else "?"
            text = msg["text"][:200]
            lines.append(f"  [{t}] {text}")

        # Files changed
        if self.files_changed:
            lines.append(f"  Files: {', '.join(self.files_changed[:10])}")

        return "\n".join(lines)


def get_project_slug(project_dir: str = None) -> str:
    """
    Convert a project directory path to the slug Claude Code uses.

    Examples:
        C:\\Projects\\MyApp  ->  C--Projects-MyApp
        /home/user/myapp    ->  -home-user-myapp
    """
    if project_dir is None:
        project_dir = os.getcwd()

    # Claude Code replaces path separators with dashes, colons with dashes
    slug = project_dir.replace("\\", "-").replace("/", "-").replace(":", "-")
    # Remove leading dash if present
    slug = slug.strip("-")
    return slug


def find_transcript_dir(project_dir: str = None) -> Optional[Path]:
    """Find the transcript directory for a project."""
    slug = get_project_slug(project_dir)

    # Try exact slug
    candidate = PROJECTS_DIR / slug
    if candidate.exists():
        return candidate

    # Try with leading path separator variations
    for d in PROJECTS_DIR.iterdir():
        if d.is_dir() and slug in d.name:
            return d

    return None


def list_sessions(project_dir: str = None, limit: int = 10) -> list[Path]:
    """
    List recent session transcript files, newest first.

    Returns list of Path objects to .jsonl files.
    """
    transcript_dir = find_transcript_dir(project_dir)
    if not transcript_dir:
        return []

    jsonl_files = sorted(
        transcript_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    return jsonl_files[:limit]


def read_transcript(jsonl_path: Path) -> SessionTranscript:
    """
    Parse a session transcript JSONL file.

    Extracts:
    - User messages with timestamps
    - Files that were edited/written
    - Session start/end times
    """
    session_id = jsonl_path.stem
    file_size = jsonl_path.stat().st_size

    transcript = SessionTranscript(
        session_id=session_id,
        file_path=str(jsonl_path),
        file_size=file_size,
    )

    first_timestamp = None
    last_timestamp = None

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                timestamp = entry.get("timestamp", "")

                # Track timestamps for session duration
                if timestamp:
                    if first_timestamp is None:
                        first_timestamp = timestamp
                    last_timestamp = timestamp

                # Extract user messages
                if entry_type == "user":
                    transcript.user_message_count += 1
                    text = _extract_text(entry.get("message", {}))
                    if text and not text.startswith("{") and not text.startswith("["):
                        transcript.user_messages.append({
                            "timestamp": timestamp,
                            "text": text,
                        })

                # Count assistant messages
                elif entry_type == "assistant":
                    transcript.assistant_message_count += 1

                    # Extract file edits from tool calls
                    message = entry.get("message", {})
                    content = message.get("content", message) if isinstance(message, dict) else message
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "")
                                if name in ("Edit", "Write"):
                                    fp = block.get("input", {}).get("file_path", "")
                                    if fp and fp not in transcript.files_changed:
                                        transcript.files_changed.append(fp)

    except (OSError, UnicodeDecodeError):
        pass

    transcript.started_at = first_timestamp
    transcript.ended_at = last_timestamp

    return transcript


def read_recent_sessions(project_dir: str = None, limit: int = 5, short_only: bool = False) -> list[SessionTranscript]:
    """
    Read and parse recent session transcripts.

    Args:
        project_dir: Project directory (default: cwd)
        limit: Max sessions to read
        short_only: Only return sessions under 500KB (quick fixes)

    Returns:
        List of parsed SessionTranscript objects, newest first
    """
    paths = list_sessions(project_dir, limit=limit)
    transcripts = []

    for path in paths:
        t = read_transcript(path)
        if short_only and not t.is_short:
            continue
        if t.user_message_count < 1:
            continue
        transcripts.append(t)

    return transcripts


def _extract_text(message) -> str:
    """Extract plain text from a message object."""
    if isinstance(message, str):
        return message

    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
            return " ".join(texts)

    return ""
