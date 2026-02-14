"""
Claude Memory Database — SQLite with significance-based decay.

Memories are scored 1-10 for significance and decay at different rates:
- sig 10: Never fades (foundational — project mission, safety rules)
- sig 7-9: Very slow decay (architecture, key decisions)
- sig 4-6: Medium decay (session outcomes, research)
- sig 1-3: Fast decay (routine debugging, one-off questions)

Categories:
    decision     — "We chose X because Y" (decays based on significance)
    current_state — "Feature X is built but not deployed" (no decay, manually updated)
    knowledge    — "The project uses React + Express" (no decay, permanent facts)
    session      — "Feb 9: built the scraper" (decays normally)

Database location: ~/.claude-memory/memory.db (shared across all projects)
"""

import json
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Global database — shared across all projects on this machine
DB_DIR = Path.home() / ".claude-memory"
DB_PATH = DB_DIR / "memory.db"

# Decay rates per week — how much recall_strength drops each week
DECAY_RATES = {
    10: 0.00,   # Never fades — foundational
    9:  0.01,   # Almost never
    8:  0.02,   # Very slow
    7:  0.05,   # Slow
    6:  0.08,   # Medium-slow
    5:  0.10,   # Medium
    4:  0.15,   # Medium-fast
    3:  0.20,   # Fast
    2:  0.30,   # Very fast
    1:  0.50,   # Gone in 2 weeks
}

# Categories that never decay
NO_DECAY_CATEGORIES = {"current_state", "knowledge"}

# Recall boost when a memory is accessed (searched/read)
RECALL_BOOST = 0.15

# Prune threshold — below this, memory can be deleted
PRUNE_THRESHOLD = 0.05

# How many full sessions to keep (oldest auto-deleted when new one saved)
MAX_SESSIONS = 10


@dataclass
class Memory:
    """A single memory item."""
    id: int
    category: str
    title: str
    content: str
    significance: int       # 1-10
    recall_strength: float  # 0.0-1.0
    tags: list[str]
    source: str             # 'session', 'manual', 'auto'
    recalled_count: int
    last_recalled: Optional[str]
    created_at: str
    updated_at: str

    @property
    def state(self) -> str:
        """Memory state: clear, fuzzy, or blank."""
        if self.recall_strength >= 0.7 and self.significance >= 6:
            return "clear"
        elif self.recall_strength >= 0.4:
            return "fuzzy"
        else:
            return "blank"


class ClaudeMemoryDB:
    """
    Persistent memory database for Claude Code sessions.

    Features:
    - Significance-based decay (important stuff persists, noise fades)
    - FTS5 full-text search
    - Category-based organization
    - Recall boost on access (memories get stronger when used)
    - Export for brief generation
    """

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'decision',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                significance INTEGER DEFAULT 5,
                recall_strength REAL DEFAULT 1.0,
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT 'manual',
                recalled_count INTEGER DEFAULT 0,
                last_recalled TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # FTS5 for full-text search
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                title, content, tags,
                content='memories',
                content_rowid='id'
            )
        """)

        # Triggers to keep FTS in sync
        c.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, title, content, tags)
                VALUES (new.id, new.title, new.content, new.tags);
            END
        """)

        c.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                VALUES('delete', old.id, old.title, old.content, old.tags);
            END
        """)

        c.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                VALUES('delete', old.id, old.title, old.content, old.tags);
                INSERT INTO memories_fts(rowid, title, content, tags)
                VALUES (new.id, new.title, new.content, new.tags);
            END
        """)

        # Track metadata (last decay time, etc.)
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Session transcripts — auto-captured at end of each session
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                project TEXT DEFAULT '',
                files_changed TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Add / Update
    # ------------------------------------------------------------------

    def add(
        self,
        title: str,
        content: str,
        category: str = "decision",
        significance: int = 5,
        tags: list[str] = None,
        source: str = "manual",
    ) -> int:
        """Add a new memory. Returns the memory ID."""
        significance = max(1, min(10, significance))
        now = datetime.now().isoformat()

        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            INSERT INTO memories (category, title, content, significance,
                                  recall_strength, tags, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1.0, ?, ?, ?, ?)
        """, (category, title, content, significance,
              json.dumps(tags or []), source, now, now))

        mem_id = c.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"Added [{category}] sig={significance}: {title}")
        return mem_id

    def update_content(self, memory_id: int, content: str):
        """Update the content of an existing memory."""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE memories SET content = ?, updated_at = ? WHERE id = ?
        """, (content, now, memory_id))
        conn.commit()
        conn.close()

    def update_state(self, title_pattern: str, new_content: str):
        """
        Update a current_state memory by title pattern.
        If not found, creates it.
        """
        conn = self._get_conn()
        c = conn.cursor()
        now = datetime.now().isoformat()

        c.execute("""
            SELECT id FROM memories
            WHERE category = 'current_state' AND title LIKE ?
            LIMIT 1
        """, (f"%{title_pattern}%",))

        row = c.fetchone()
        if row:
            c.execute("""
                UPDATE memories SET content = ?, updated_at = ? WHERE id = ?
            """, (new_content, now, row["id"]))
        else:
            c.execute("""
                INSERT INTO memories (category, title, content, significance,
                                      recall_strength, tags, source, created_at, updated_at)
                VALUES ('current_state', ?, ?, 8, 1.0, '[]', 'manual', ?, ?)
            """, (title_pattern, new_content, now, now))

        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Recall / Search
    # ------------------------------------------------------------------

    def recall(self, query: str, min_strength: float = 0.3, limit: int = 10) -> list[Memory]:
        """Search memories by query. Boosts recall strength on match."""
        conn = self._get_conn()
        c = conn.cursor()

        # Escape FTS5 special chars
        safe_query = query.replace('"', '""')
        safe_query = f'"{safe_query}"'

        try:
            c.execute("""
                SELECT m.* FROM memories m
                JOIN memories_fts fts ON m.id = fts.rowid
                WHERE memories_fts MATCH ? AND m.recall_strength >= ?
                ORDER BY m.significance DESC, m.recall_strength DESC
                LIMIT ?
            """, (safe_query, min_strength, limit))
            memories = [self._to_memory(row) for row in c.fetchall()]
        except sqlite3.OperationalError:
            # FTS failed, fall back to LIKE
            c.execute("""
                SELECT * FROM memories
                WHERE (title LIKE ? OR content LIKE ?) AND recall_strength >= ?
                ORDER BY significance DESC, recall_strength DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", min_strength, limit))
            memories = [self._to_memory(row) for row in c.fetchall()]

        conn.close()

        # Boost recall for accessed memories
        for mem in memories:
            self._boost_recall(mem.id)

        return memories

    def _boost_recall(self, memory_id: int):
        """Boost recall strength when a memory is accessed."""
        conn = self._get_conn()
        c = conn.cursor()
        now = datetime.now().isoformat()

        c.execute("""
            UPDATE memories
            SET recall_strength = MIN(1.0, recall_strength + ?),
                recalled_count = recalled_count + 1,
                last_recalled = ?
            WHERE id = ?
        """, (RECALL_BOOST, now, memory_id))

        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Session Capture (last N sessions auto-saved)
    # ------------------------------------------------------------------

    def save_session(self, summary: str, project: str = "", files_changed: list[str] = None) -> int:
        """
        Save a session summary. Called at end of each Claude Code session.
        Keeps the last MAX_SESSIONS entries, auto-prunes older ones.
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            INSERT INTO sessions (summary, project, files_changed, created_at)
            VALUES (?, ?, ?, ?)
        """, (summary, project, json.dumps(files_changed or []), now))

        session_id = c.lastrowid

        # Auto-prune: keep only last MAX_SESSIONS
        c.execute("""
            DELETE FROM sessions WHERE id NOT IN (
                SELECT id FROM sessions ORDER BY id DESC LIMIT ?
            )
        """, (MAX_SESSIONS,))

        conn.commit()
        conn.close()

        logger.info(f"Session #{session_id} saved: {summary[:80]}...")
        return session_id

    def get_sessions(self, limit: int = MAX_SESSIONS) -> list[dict]:
        """Get recent sessions, newest first."""
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            SELECT * FROM sessions ORDER BY id DESC LIMIT ?
        """, (limit,))

        sessions = []
        for row in c.fetchall():
            sessions.append({
                "id": row["id"],
                "summary": row["summary"],
                "project": row["project"],
                "files_changed": json.loads(row["files_changed"]) if row["files_changed"] else [],
                "created_at": row["created_at"],
            })

        conn.close()
        return sessions

    # ------------------------------------------------------------------
    # Decay / Prune
    # ------------------------------------------------------------------

    def decay(self):
        """
        Apply weekly decay to all memories based on significance.
        Skips no-decay categories (current_state, knowledge).
        """
        conn = self._get_conn()
        c = conn.cursor()

        for significance, decay_rate in DECAY_RATES.items():
            if decay_rate == 0:
                continue

            placeholders = ",".join("?" for _ in NO_DECAY_CATEGORIES)
            c.execute(f"""
                UPDATE memories
                SET recall_strength = MAX(0, recall_strength - ?)
                WHERE significance = ?
                  AND recall_strength > 0
                  AND category NOT IN ({placeholders})
            """, (decay_rate, significance, *NO_DECAY_CATEGORIES))

        # Record when decay was applied
        now = datetime.now().isoformat()
        c.execute("""
            INSERT OR REPLACE INTO meta (key, value) VALUES ('last_decay', ?)
        """, (now,))

        conn.commit()
        conn.close()

        stats = self.get_stats()
        logger.info(
            f"Decay applied. {stats['fading']} memories fading, "
            f"{stats['total']} total."
        )
        return stats

    def prune(self, threshold: float = PRUNE_THRESHOLD):
        """
        Remove memories that have decayed below threshold.
        Only prunes significance < 5 (important stuff just goes fuzzy, never deleted).
        """
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            SELECT id, title, significance FROM memories
            WHERE recall_strength < ? AND significance < 5
              AND category NOT IN ('current_state', 'knowledge')
        """, (threshold,))
        to_prune = c.fetchall()

        if to_prune:
            ids = [row["id"] for row in to_prune]
            placeholders = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)

            for row in to_prune:
                logger.info(f"Pruned: [{row['significance']}] {row['title']}")

        conn.commit()
        conn.close()
        return len(to_prune)

    # ------------------------------------------------------------------
    # Export (for brief generation)
    # ------------------------------------------------------------------

    def export_all(self, min_strength: float = 0.0) -> list[Memory]:
        """Export all memories above a strength threshold."""
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            SELECT * FROM memories
            WHERE recall_strength >= ?
            ORDER BY category, significance DESC, recall_strength DESC
        """, (min_strength,))

        memories = [self._to_memory(row) for row in c.fetchall()]
        conn.close()
        return memories

    def export_by_category(self, category: str) -> list[Memory]:
        """Export all memories in a category."""
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("""
            SELECT * FROM memories
            WHERE category = ?
            ORDER BY significance DESC, recall_strength DESC
        """, (category,))

        memories = [self._to_memory(row) for row in c.fetchall()]
        conn.close()
        return memories

    def export_text(self) -> str:
        """Export all memories as readable text."""
        memories = self.export_all(min_strength=0.0)

        lines = ["# Claude Memory Export", ""]
        for mem in memories:
            lines.append(f"## [{mem.category}] {mem.title} (sig={mem.significance}, "
                         f"strength={mem.recall_strength:.2f}, state={mem.state})")
            lines.append(mem.content)
            if mem.tags:
                lines.append(f"Tags: {', '.join(mem.tags)}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM memories")
        total = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM memories WHERE recall_strength >= 0.7")
        clear = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM memories WHERE recall_strength >= 0.4 AND recall_strength < 0.7")
        fuzzy = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM memories WHERE recall_strength < 0.4")
        fading = c.fetchone()[0]

        c.execute("SELECT category, COUNT(*) as cnt FROM memories GROUP BY category")
        by_category = {row["category"]: row["cnt"] for row in c.fetchall()}

        c.execute("SELECT AVG(recall_strength) FROM memories")
        avg_strength = c.fetchone()[0] or 0

        c.execute("SELECT value FROM meta WHERE key = 'last_decay'")
        row = c.fetchone()
        last_decay = row["value"] if row else "never"

        conn.close()

        return {
            "total": total,
            "clear": clear,
            "fuzzy": fuzzy,
            "fading": fading,
            "by_category": by_category,
            "avg_recall_strength": round(avg_strength, 2),
            "last_decay": last_decay,
        }

    def get_last_meta(self, key: str) -> Optional[str]:
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _to_memory(self, row) -> Memory:
        return Memory(
            id=row["id"],
            category=row["category"],
            title=row["title"],
            content=row["content"],
            significance=row["significance"],
            recall_strength=row["recall_strength"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            source=row["source"],
            recalled_count=row["recalled_count"],
            last_recalled=row["last_recalled"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
