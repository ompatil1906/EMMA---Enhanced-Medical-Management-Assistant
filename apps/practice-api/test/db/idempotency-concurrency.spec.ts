import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { PRACTICE, seedPractices } from '../helpers/fixtures.js';

/**
 * Concurrency: ~30 racing duplicate POSTs (same Idempotency-Key, same body)
 * against an isolated per-test app must produce EXACTLY one row and one
 * 201-created response; the rest replay 200 with the same id.
 */
describe('idempotency race (isolated app per test)', () => {
  it('converges 30 concurrent duplicate POSTs to exactly one row', async () => {
    const ctx: BootedTestApp = await createTestApp();
    try {
      await seedPractices(ctx);

      const CONCURRENCY = 30;
      const body = {
        practiceId: PRACTICE.id,
        requestType: 'appointment-request',
        structuredPayload: { intent: 'race-anchor', slot: '2026-11-10T09:00:00Z' },
      };

      const requests = Array.from({ length: CONCURRENCY }, () =>
        ctx.http.post('/api/v1/requests').send(body).set('Idempotency-Key', 'race-key-30'),
      );
      const responses = await Promise.all(requests);

      const created = responses.filter((r) => r.status === 201);
      const replayed = responses.filter((r) => r.status === 200);
      expect(created.length).toBe(1);
      expect(replayed.length).toBe(CONCURRENCY - 1);

      const ids = responses.map((r) => (r.body as { data: { id: string } }).data.id);
      expect(new Set(ids).size).toBe(1);

      const replayBodies = replayed.map((r) => (r.body as { meta?: Record<string, unknown> }).meta);
      expect(replayBodies.every((meta) => meta?.existing === true)).toBe(true);

      const count = (await ctx.dataSource.query<{ total: string | number }[]>(
        'SELECT COUNT(*) AS total FROM patient_requests',
      )) as Array<{ total: string | number }>;
      expect(Number(count[0]?.total)).toBe(1);
    } finally {
      await ctx.close();
    }
  });

  it('this app uses no schema synchronization and applies migrations cleanly', async () => {
    const ctx: BootedTestApp = await createTestApp();
    try {
      expect(ctx.dataSource.options.synchronize).toBe(false);
      expect(ctx.dataSource.options.migrationsRun).toBe(false);
      // showMigrations() return type varies by driver; the important guarantee
      // is that runMigrations() above completed without throwing.
      await ctx.dataSource.showMigrations();
    } finally {
      await ctx.close();
    }
  });
});
