/**
 * Minimal .env loader (no dependency): sets KEY=VALUE lines into
 * process.env when not already present. Comments, blank lines and `export`
 * prefixes are supported; no shell interpolation, no quote munging beyond
 * trimming plain double quotes.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export function loadEnvFileFromCwd(): void {
  const path = join(process.cwd(), '.env');
  let contents: string;
  try {
    contents = readFileSync(path, 'utf8');
  } catch {
    return; // no .env file: rely on process defaults
  }
  for (const rawLine of contents.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith('#')) {
      continue;
    }
    const withoutExport = line.startsWith('export ') ? line.slice(7).trim() : line;
    const separator = withoutExport.indexOf('=');
    if (separator <= 0) continue;
    const key = withoutExport.slice(0, separator).trim();
    let value = withoutExport.slice(separator + 1).trim();
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined && value.length > 0) {
      process.env[key] = value;
    }
  }
}
