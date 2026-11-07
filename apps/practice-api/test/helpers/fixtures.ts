/** Shared literal fixtures + tiny seeding helpers for the test suite. */
import type { BootedTestApp } from './app.factory.js';

const FIXED_PRACTICE_ID = '7d2b4e91-0c11-4fed-9f2a-4b1f6d3c5a77';
const OTHER_PRACTICE_ID = '1a2b3c4d-11aa-4bbb-8ccc-d0e1f2a3b4c5';

export const PRACTICE = {
  id: FIXED_PRACTICE_ID,
  name: 'QA Test Practice',
  timezone: 'Europe/London',
} as const;

export const OTHER_PRACTICE = {
  id: OTHER_PRACTICE_ID,
  name: 'QA Second Practice',
  timezone: 'UTC',
} as const;

/** Builds fresh request bodies per test to avoid shared mutable state. */
export function requestPayload(
  structuredPayload: Record<string, unknown> = { preferredDay: 'tuesday', preferredTime: 'morning' },
): Record<string, unknown> {
  return {
    practiceId: PRACTICE.id,
    requestType: 'appointment-request',
    structuredPayload,
  };
}

/** Seeds fixture practices directly through the data source. */
export async function seedPractices(ctx: BootedTestApp): Promise<void> {
  const ts = new Date().toISOString().slice(0, 23).replace('T', ' ');
  const rows = [PRACTICE, OTHER_PRACTICE] as const;
  for (const row of rows) {
    await ctx.dataSource.query(
      'INSERT INTO practices (id, name, timezone, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
      [row.id, row.name, row.timezone, ts, ts],
    );
  }
}

/** Seeds one call directly through the data source (raw SQL, UTC timestamps). */
export async function seedCall(
  ctx: BootedTestApp,
  externalCallId: string,
  practiceId: string = PRACTICE.id,
): Promise<string> {
  const id = crypto.randomUUID();
  const ts = new Date().toISOString().slice(0, 23).replace('T', ' ');
  await ctx.dataSource.query(
    "INSERT INTO calls (id, practice_id, external_call_id, status, started_at, ended_at, duration_seconds, created_at, updated_at) VALUES (?, ?, ?, 'in-progress', ?, NULL, NULL, ?, ?)",
    [id, practiceId, externalCallId, ts, ts, ts],
  );
  return id;
}

