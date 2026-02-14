"""
Claude Memory — Persistent memory for Claude Code sessions.

Memories have significance (1-10) and decay over time.
High-significance memories persist; low-significance ones fade naturally.

Usage:
    python -m claude_memory brief       # Generate session brief
    python -m claude_memory add         # Add a memory
    python -m claude_memory decay       # Apply weekly decay
    python -m claude_memory status      # Show memory stats
    python -m claude_memory search      # Search memories
    python -m claude_memory init        # Set up a project
"""

from claude_memory.memory_db import ClaudeMemoryDB

__all__ = ["ClaudeMemoryDB"]
__version__ = "1.0.0"
