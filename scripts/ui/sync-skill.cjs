const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve('node_modules/@playwright/cli/skills/playwright-cli');
fs.cpSync(source, path.resolve('.codex/skills/playwright-cli'), { recursive: true });
console.log('Official Playwright CLI skill synced to .codex/skills/playwright-cli');
