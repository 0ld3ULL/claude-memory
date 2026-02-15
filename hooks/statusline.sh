#!/bin/bash
input=$(cat)
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty' 2>/dev/null)
if [ -z "$used" ]; then
    echo "Context: Ready"
    exit 0
fi
pct=$(printf "%.0f" "$used")
echo "$pct" > ~/.claude/context_pct.txt
full="===================="
dots="...................."
filled=$((pct / 5))
[ "$filled" -gt 20 ] && filled=20
empty=$((20 - filled))
bar="${full:0:$filled}${dots:0:$empty}"
if [ "$pct" -ge 70 ]; then
    echo "[$bar] ${pct}% DANGER"
elif [ "$pct" -ge 50 ]; then
    echo "[$bar] ${pct}% SAVE+EXIT"
else
    echo "[$bar] ${pct}%"
fi
