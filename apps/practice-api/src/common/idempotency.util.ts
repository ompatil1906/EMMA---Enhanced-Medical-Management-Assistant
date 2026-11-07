import { createHash } from 'node:crypto';

/**
 * Idempotency helpers.
 *
 * Canonicalisation: JSON serialisation with RECURSIVELY ORDERED object keys so
 * that `{ a, b }` and `{ b, a }` share one hash. Arrays keep their order (order
 * is data). Numbers/booleans/null/strings serialise as plain JSON.
 */
export function canonicalJson(value: unknown): string {
  const json: unknown = canonicalize(value);
  return JSON.stringify(json);
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item): unknown => canonicalize(item));
  }
  if (value !== null && typeof value === 'object') {
    const source = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) {
      out[key] = canonicalize(source[key]);
    }
    return out;
  }
  return value;
}

/** Stable SHA-256 (hex) of the canonicalised payload. */
export function canonicalPayloadHash(value: unknown): string {
  return createHash('sha256').update(canonicalJson(value)).digest('hex');
}

/**
 * Tolerant UTC date coercion for hydrated timestamps and query params:
 * accepts Date (returned as-is), ISO strings ("2026-11-06T10:30:00Z", possibly
 * space-separated like sqlite's) and epoch millis. Returns null for nullish.
 */
export function utcDate(value: unknown): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return value;
  if (typeof value === 'number') return new Date(value);
  if (typeof value === 'string') {
    const normalised = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(value)
      ? value.replace(' ', 'T') + (/(Z|[+-]\d{2}:?\d{2})$/.test(value) ? '' : 'Z')
      : value;
    const parsed = new Date(normalised);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  throw new TypeError(`not a recognisable date value: ${String(value)}`);
}
