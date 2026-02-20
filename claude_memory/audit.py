"""
Memory Audit — Weekly review of conversations vs saved memories via The Wall.

Extracts 7 days of chat text (user + assistant messages only — no tool calls,
no tool results, no images, no system reminders) and sends it to Gemini's 1M
context alongside all current memories. Gemini compares the two and identifies
anything important that was discussed but never saved.

Usage:
    python -m claude_memory audit              # Run audit (last 7 days)
    python -m claude_memory audit --days 3     # Custom range
    python -m claude_memory audit --dry-run    # Show stats without calling Gemini
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from claude_memory.memory_db import ClaudeMemoryDB
from claude_memory.transcript_reader import list_sessions


# Max chars for a single tool output or text block before truncation
STDOUT_TRUNCATE = 2000


def extract_chat_text(days: int = 7, project_dir: str = None) -> tuple[str, dict]:
    """
    Extract just the human-readable chat from session transcripts.

    Returns:
        (chat_text, stats_dict)

    The chat text is formatted as:
        === SESSION: 2026-02-20 04:10 (8 min, 50 user msgs) ===
        [04:10:41] USER: bring yourself up pls
        [04:11:02] CLAUDE: Starting up. Let me run through the startup sequence...
        ...
    """
    sessions = list_sessions(project_dir, limit=500)
    if not sessions:
        return "", {"sessions": 0, "chars": 0}

    cutoff = time.time() - (days * 24 * 3600)

    all_chat = []
    stats = {
        "sessions": 0,
        "user_msgs": 0,
        "assistant_msgs": 0,
        "skipped_tool_blocks": 0,
        "skipped_system": 0,
        "skipped_large_outputs": 0,
        "total_raw_bytes": 0,
    }

    # Process oldest first for chronological order
    for session_path in reversed(sessions):
        mtime = session_path.stat().st_mtime
        if mtime < cutoff:
            continue

        raw_size = session_path.stat().st_size
        stats["total_raw_bytes"] += raw_size

        session_lines = []
        first_ts = None
        last_ts = None
        session_user_count = 0
        session_asst_count = 0

        try:
            with open(session_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    entry_type = obj.get("type", "")
                    timestamp = obj.get("timestamp", "")

                    if timestamp:
                        if not first_ts:
                            first_ts = timestamp
                        last_ts = timestamp

                    if entry_type == "user":
                        text = _extract_user_text(obj)
                        if text:
                            ts_short = _short_time(timestamp)
                            session_lines.append(f"[{ts_short}] USER: {text}")
                            session_user_count += 1
                            stats["user_msgs"] += 1
                        else:
                            stats["skipped_system"] += 1

                    elif entry_type == "assistant":
                        text = _extract_assistant_text(obj, stats)
                        if text:
                            ts_short = _short_time(timestamp)
                            session_lines.append(f"[{ts_short}] CLAUDE: {text}")
                            session_asst_count += 1
                            stats["assistant_msgs"] += 1

        except (OSError, UnicodeDecodeError):
            continue

        # Skip empty or near-empty sessions
        if session_user_count < 1:
            continue

        stats["sessions"] += 1

        # Build session header
        start_str = first_ts[:16].replace("T", " ") if first_ts else "?"
        duration = _calc_duration(first_ts, last_ts)
        dur_str = f" ({duration})" if duration else ""

        header = f"\n{'=' * 60}\nSESSION: {start_str}{dur_str} — {session_user_count} user msgs\n{'=' * 60}"
        all_chat.append(header)
        all_chat.extend(session_lines)

    chat_text = "\n".join(all_chat)
    stats["chars"] = len(chat_text)
    stats["est_tokens"] = len(chat_text) // 4

    return chat_text, stats


def run_audit(db: ClaudeMemoryDB, days: int = 7, dry_run: bool = False):
    """
    Run the weekly memory audit.

    1. Extract chat text from last N days
    2. Export all current memories
    3. Send both to Gemini via The Wall
    4. Print Gemini's findings
    """
    print(f"Weekly Memory Audit — last {days} days")
    print("=" * 50)

    # Step 1: Extract chat
    print("\n[1/3] Extracting chat text...")
    chat_text, stats = extract_chat_text(days=days)

    if not chat_text:
        print("No sessions found in the last %d days." % days)
        return

    print(f"  Sessions: {stats['sessions']}")
    print(f"  User messages: {stats['user_msgs']}")
    print(f"  Assistant messages: {stats['assistant_msgs']}")
    print(f"  Skipped (system/tool/large): {stats['skipped_system']} / {stats['skipped_tool_blocks']} / {stats['skipped_large_outputs']}")
    print(f"  Raw transcript size: {stats['total_raw_bytes'] / 1024 / 1024:.1f} MB")
    print(f"  Chat text size: {stats['chars'] / 1024:.0f} KB ({stats['est_tokens']:,} est tokens)")

    # Step 2: Export memories
    print("\n[2/3] Loading current memories...")
    memory_text = db.export_text()
    mem_stats = db.get_stats()
    mem_tokens = len(memory_text) // 4

    print(f"  Memories: {mem_stats['total']}")
    print(f"  Memory text: {len(memory_text) / 1024:.0f} KB ({mem_tokens:,} est tokens)")

    # Total token estimate
    total_tokens = stats["est_tokens"] + mem_tokens
    print(f"\n  Total for Gemini: ~{total_tokens:,} tokens", end="")
    if total_tokens > 900_000:
        print(" — WARNING: may exceed Gemini 1M context!")
    elif total_tokens > 700_000:
        print(" — tight fit, should work")
    else:
        print(" — fits comfortably")

    if dry_run:
        print("\n[DRY RUN] Skipping Gemini call. Use without --dry-run to run the audit.")
        return

    # Step 3: Send to Gemini
    print("\n[3/3] Sending to The Wall for analysis...")

    # Load env for API key
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Try project-local GeminiClient first, fall back to built-in
    client = _get_gemini_client()

    prompt = _build_audit_prompt(chat_text, memory_text, days)
    prompt_tokens = len(prompt) // 4
    print(f"  Prompt size: {prompt_tokens:,} est tokens")
    print(f"  Sending to Gemini ({client['provider']})...")

    response = _call_gemini(client, prompt)

    print(f"  Done — {response['input_tokens']:,} input, {response['output_tokens']:,} output tokens")

    # Save results to file (avoids Windows encoding issues)
    audit_dir = Path.home() / ".claude-memory"
    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    audit_file = audit_dir / f"audit_{timestamp}.md"
    audit_file.write_text(
        f"# Weekly Memory Audit — {timestamp}\n"
        f"*{days} days, {stats['sessions']} sessions, {stats['user_msgs']} user msgs*\n"
        f"*Gemini: {response['input_tokens']:,} input, {response['output_tokens']:,} output tokens*\n\n"
        f"{response['text']}",
        encoding="utf-8",
    )
    print(f"  Saved to: {audit_file}")

    # Print results (handle Windows encoding)
    print("\n" + "=" * 60)
    print("MEMORY AUDIT RESULTS")
    print("=" * 60 + "\n")
    try:
        print(response["text"])
    except UnicodeEncodeError:
        # Windows terminal can't handle some Unicode chars
        safe_text = response["text"].encode("ascii", errors="replace").decode("ascii")
        print(safe_text)


def _build_audit_prompt(chat_text: str, memory_text: str, days: int) -> str:
    """Build the prompt for Gemini memory audit."""
    return f"""You are a MEMORY AUDITOR for a Claude Code AI assistant.

This assistant has a memory system that saves important facts, decisions, and state across
sessions. But memories are saved manually and things get missed.

Your job: Compare the ACTUAL CONVERSATIONS from the last {days} days against the SAVED MEMORIES
and identify anything important that was discussed but never saved.

WHAT TO LOOK FOR:
1. **Decisions made** — architecture choices, tool selections, approach decisions
2. **New facts learned** — API endpoints discovered, bugs found, workarounds identified
3. **State changes** — things that were deployed, configured, fixed, or broken
4. **Commitments** — promises made to the user about future work
5. **Key insights** — important realizations about the project or its systems
6. **People/contacts** — new names, emails, accounts mentioned
7. **Blockers found** — issues discovered that block progress

WHAT TO IGNORE:
- Routine debugging steps that were resolved
- Transient status checks ("is this running?")
- Small talk or greetings
- Things that are clearly already in the saved memories
- One-off commands or file reads with no lasting significance

OUTPUT FORMAT:
For each gap found, output:
```
## Gap N: [Short title]
- **Category**: knowledge | decision | current_state
- **Significance**: 1-10
- **What was discussed**: [Brief summary of the conversation]
- **Suggested memory**: [The text that should be saved]
- **Tags**: [comma-separated tags]
```

At the end, provide a summary:
- Total gaps found
- Most critical gaps (significance >= 8)
- Overall assessment: is the memory system capturing things well or missing a lot?

=== SAVED MEMORIES ({len(memory_text)} chars) ===

{memory_text}

=== CONVERSATIONS FROM LAST {days} DAYS ({len(chat_text)} chars) ===

{chat_text}

=== YOUR ANALYSIS ===
"""


def _extract_user_text(obj: dict) -> Optional[str]:
    """Extract user message text, skipping system reminders and tool results."""
    msg = obj.get("message", {})
    if isinstance(msg, str):
        if msg.startswith("<system-reminder>") or msg.startswith("<local-command"):
            return None
        return msg.strip()

    if isinstance(msg, dict):
        content = msg.get("content", "")

        if isinstance(content, str):
            if content.startswith("<system-reminder>") or content.startswith("<local-command"):
                return None
            return content.strip() if content.strip() else None

        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        # Skip system reminders
                        if text.startswith("<system-reminder>") or text.startswith("<local-command"):
                            continue
                        # Skip command outputs
                        if text.startswith("<command-name>") or text.startswith("<local-command"):
                            continue
                        if text.strip():
                            texts.append(text.strip())
                    # Skip tool_result blocks entirely (these are huge)
                    elif block.get("type") == "tool_result":
                        pass
            return " ".join(texts) if texts else None

    return None


def _extract_assistant_text(obj: dict, stats: dict) -> Optional[str]:
    """Extract assistant text, skipping tool_use blocks."""
    msg = obj.get("message", {})
    if isinstance(msg, str):
        return msg.strip() if msg.strip() else None

    if isinstance(msg, dict):
        content = msg.get("content", "")

        if isinstance(content, str):
            return content.strip() if content.strip() else None

        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            texts.append(text.strip())
                    elif block.get("type") == "tool_use":
                        stats["skipped_tool_blocks"] += 1
            return "\n".join(texts) if texts else None

    return None


def _short_time(timestamp: str) -> str:
    """Extract just HH:MM:SS from an ISO timestamp."""
    if not timestamp:
        return "??:??:??"
    try:
        # Handle "2026-02-20T04:10:41.123Z"
        t = timestamp.split("T")[1] if "T" in timestamp else timestamp
        return t[:8]  # HH:MM:SS
    except (IndexError, TypeError):
        return "??:??:??"


def _calc_duration(start: str, end: str) -> Optional[str]:
    """Calculate human-readable duration between two ISO timestamps."""
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        mins = (e - s).total_seconds() / 60
        if mins >= 60:
            return f"{mins / 60:.1f}h"
        return f"{mins:.0f} min"
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Built-in Gemini client (no external dependency on voice/gemini_client.py)
# Requires: GOOGLE_API_KEY in env (Google AI Studio)
# Optional: pip install httpx (falls back to urllib if missing)
# ---------------------------------------------------------------------------

def _get_gemini_client() -> dict:
    """Get a Gemini API client config. Tries project-local GeminiClient first."""
    # Try project-local client (e.g. voice/gemini_client.py in TDP)
    try:
        from voice.gemini_client import GeminiClient
        client = GeminiClient()
        return {"type": "project", "client": client, "provider": client.provider}
    except (ImportError, Exception):
        pass

    # Fall back to built-in
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            "No GOOGLE_API_KEY found. Set it in your .env file or environment.\n"
            "Get one free at: https://aistudio.google.com/apikey"
        )
    return {"type": "builtin", "api_key": api_key, "provider": "google"}


def _call_gemini(client: dict, prompt: str) -> dict:
    """Call Gemini API. Returns dict with text, input_tokens, output_tokens."""
    if client["type"] == "project":
        response = client["client"].analyze(
            context="",
            question=prompt,
            max_tokens=65536,
            timeout=600,
        )
        return {
            "text": response.text,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }

    # Built-in: direct Google AI Studio call via httpx or urllib
    api_key = client["api_key"]
    model = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 65536,
            "thinkingConfig": {"thinkingBudget": 8192},
        },
    }

    try:
        import httpx
        with httpx.Client(timeout=600) as http:
            resp = http.post(url, json=payload, headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            result = resp.json()
    except ImportError:
        # Fallback to urllib (no extra deps needed)
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            url,
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = _json.loads(resp.read().decode("utf-8"))

    text = result["candidates"][0]["content"]["parts"][0]["text"]
    usage = result.get("usageMetadata", {})
    return {
        "text": text,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
    }
