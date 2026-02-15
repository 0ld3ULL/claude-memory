#!/bin/bash
# UserPromptSubmit hook — checks context usage on every user message.
# Installed by: python -m claude_memory init
# Reads context % from file written by the statusline, warns at thresholds.

PCT_FILE="$HOME/.claude/context_pct.txt"

if [ ! -f "$PCT_FILE" ]; then
    exit 0
fi

pct=$(cat "$PCT_FILE" 2>/dev/null)

if [ -z "$pct" ]; then
    exit 0
fi

if [ "$pct" -ge 70 ] 2>/dev/null; then
    echo "CONTEXT EMERGENCY (${pct}%): DANGER ZONE. Do NOT do any more work. Immediately update session_log.md with what you were working on, save memories (python -m claude_memory add), regenerate brief (python -m claude_memory brief --project .), then tell the user to restart Claude Code NOW."
elif [ "$pct" -ge 55 ] 2>/dev/null; then
    echo "CONTEXT PROTOCOL TRIGGERED (${pct}%): STOP all new work. With remaining context: (1) Update session_log.md with detailed state, (2) Save important memories, (3) Regenerate brief, (4) Commit and push to git, (5) Tell the user to restart Claude Code."
fi
