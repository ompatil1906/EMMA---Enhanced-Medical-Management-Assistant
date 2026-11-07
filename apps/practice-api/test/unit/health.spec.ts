import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';

describe('GET /api/v1/health', () => {
  let ctx: BootedTestApp;

  beforeAll(async () => {
    ctx = await createTestApp();
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('reports ok/up with uptime seconds inside the canonical envelope', async () => {
    const response = await ctx.http.get('/api/v1/health').expect(200);
    const payload = response.body as {
      data: { status: string; db: string; uptimeSeconds: number };
      meta?: Record<string, unknown>;
    };
    expect(payload.data.status).toBe('ok');
    expect(payload.data.db).toBe('up');
    expect(payload.data.uptimeSeconds).toBeGreaterThanOrEqual(0);
    expect(payload.meta).toBeUndefined();
  });
});
