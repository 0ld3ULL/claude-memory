// UserPromptSubmit hook — checks context usage on every user message.
// Installed by: python -m claude_memory init
// Reads context % from file written by the statusline, warns at thresholds.
const fs = require('fs');
const path = require('path');

const home = process.env.HOME || process.env.USERPROFILE;
const pctFile = path.join(home, '.claude', 'context_pct.txt');

try {
  const pct = parseInt(fs.readFileSync(pctFile, 'utf8').trim(), 10);
  if (isNaN(pct)) process.exit(0);

  if (pct >= 70) {
    console.log(`CONTEXT EMERGENCY (${pct}%): DANGER ZONE. Do NOT do any more work. Immediately update session_log.md with what you were working on, save memories (python -m claude_memory add), regenerate brief (python -m claude_memory brief --project .), then tell the user to restart Claude Code NOW.`);
  } else if (pct >= 55) {
    console.log(`CONTEXT PROTOCOL TRIGGERED (${pct}%): STOP all new work. With remaining context: (1) Update session_log.md with detailed state, (2) Save important memories, (3) Regenerate brief, (4) Commit and push to git, (5) Tell the user to restart Claude Code.`);
  }
} catch {
  // File doesn't exist or can't be read — no warning needed
}
