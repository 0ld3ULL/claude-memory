#!/bin/bash
# SessionEnd hook — auto-saves session state when Claude Code exits.
# Installed by: python -m claude_memory init
# Calls Python to parse the transcript and save to memory DB + session_log.md

python -m claude_memory auto-save 2>/dev/null
