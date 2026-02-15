// SessionEnd hook — auto-saves session state when Claude Code exits.
// Installed by: python -m claude_memory init
// Calls Python to parse the transcript and save to memory DB + session_log.md
const { execSync } = require('child_process');

try {
  execSync('python -m claude_memory auto-save', { stdio: 'inherit' });
} catch {
  // Silently fail — don't block exit
}
