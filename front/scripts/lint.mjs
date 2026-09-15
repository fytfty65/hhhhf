import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const ignored = new Set(['node_modules', '.next', 'public']);
const extensions = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs']);
const issues = [];
const warnings = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ignored.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (extensions.has(path.extname(entry.name))) check(full);
  }
}

function check(file) {
  const text = fs.readFileSync(file, 'utf8');
  const rel = path.relative(root, file).replaceAll('\\', '/');
  const lines = text.split(/\r?\n/);
  lines.forEach((line, index) => {
    if (/http:\/\/(?:localhost|127\.0\.0\.1):\d+/.test(line) && !rel.endsWith('.env.example') && rel !== 'playwright.config.ts') {
      warnings.push(`${rel}:${index + 1}: hardcoded service URL; migrate this call to the shared API client`);
    }
    if (/\bdebugger\s*;/.test(line)) issues.push(`${rel}:${index + 1}: debugger statement is not allowed`);
  });
}

walk(root);
if (warnings.length) console.warn(warnings.join('\n'));
if (issues.length) {
  console.error(issues.join('\n'));
  process.exitCode = 1;
} else {
  console.log('frontend lint passed');
}
