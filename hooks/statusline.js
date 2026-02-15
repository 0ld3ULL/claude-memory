// StatusLine hook for Claude Code (Windows-compatible)
// Shows model name and context usage percentage
// Also writes context % to file for the context_check hook
const fs = require('fs');
const path = require('path');

let d = '';
process.stdin.on('data', c => d += c);
process.stdin.on('end', () => {
  try {
    const j = JSON.parse(d);
    const m = j.model?.display_name || '?';
    const p = Math.floor(j.context_window?.used_percentage || 0);

    // Write pct to file so context_check hook can read it
    const home = process.env.HOME || process.env.USERPROFILE;
    const pctFile = path.join(home, '.claude', 'context_pct.txt');
    fs.writeFileSync(pctFile, String(p));

    // Build progress bar
    const filled = Math.min(Math.floor(p / 5), 20);
    const bar = '='.repeat(filled) + '.'.repeat(20 - filled);

    if (p >= 70) {
      process.stdout.write(`[${bar}] ${p}% DANGER`);
    } else if (p >= 50) {
      process.stdout.write(`[${bar}] ${p}% SAVE+EXIT`);
    } else {
      process.stdout.write(`[${m}] [${bar}] ${p}%`);
    }
  } catch {
    process.stdout.write('Context: Ready');
  }
});
