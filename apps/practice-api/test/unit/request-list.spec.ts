import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { PRACTICE, requestPayload, seedPractices } from '../helpers/fixtures.js';

interface ListEnvelope {
  data: Array<{ id: string; requestType: string; status: string; structuredPayload: Record<string, unknown> }>;
  meta: { page: number; limit: number; total: number; nextPage?: number };
}

describe('GET /api/v1/practices/:practiceId/requests (pagination + filters)', () => {
  let ctx: BootedTestApp;

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);

    // 3 appointment-requests + 2 cancellations + 1 completed reschedule.
    const bodies: object[] = [
      { ...requestPayload({ n: 1 }), callId: undefined },
      { ...requestPayload({ n: 2 }) },
      { ...requestPayload({ n: 3 }) },
      { ...requestPayload({ n: 4 }), requestType: 'cancellation' },
      { ...requestPayload({ n: 5 }), requestType: 'cancellation' },
      {
        ...requestPayload({ n: 6 }),
        requestType: 'reschedule',
        status: 'completed',
      },
    ];
    for (const body of bodies) {
      await ctx.http.post('/api/v1/requests').send(body).expect(201);
    }
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('pages through results with meta { page, limit, total, nextPage }', async () => {
    const page1 = await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?page=1&limit=2`).expect(200);
    const first = page1.body as ListEnvelope;
    expect(first.meta.page).toBe(1);
    expect(first.meta.limit).toBe(2);
    expect(first.meta.total).toBe(6);
    expect(first.meta.nextPage).toBe(2);
    expect(first.data).toHaveLength(2);

    const page2 = await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?page=2&limit=2`).expect(200);
    const second = page2.body as ListEnvelope;
    expect(second.data).toHaveLength(2);
    expect(second.meta.nextPage).toBe(3);

    const page3 = await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?page=3&limit=2`).expect(200);
    const third = page3.body as ListEnvelope;
    expect(third.data).toHaveLength(2);
    expect(third.meta.nextPage).toBeUndefined();

    const page4 = await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?page=4&limit=2`).expect(200);
    const empty = page4.body as ListEnvelope;
    expect(empty.data).toHaveLength(0);
    expect(empty.meta.total).toBe(6);
    expect(empty.meta.nextPage).toBeUndefined();
  });

  it('orders by createdAt descending (newest first)', async () => {
    const response = await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?limit=100`).expect(200);
    const body = response.body as ListEnvelope;
    expect(body.data).toHaveLength(6);
    // newest = n:6 reschedule; oldest = n:1
    expect(body.data[0]?.structuredPayload).toEqual({ n: 6 });
    expect(body.data[body.data.length - 1]?.structuredPayload).toEqual({ n: 1 });
  });

  it('filters by requestType', async () => {
    const response = await ctx.http
      .get(`/api/v1/practices/${PRACTICE.id}/requests?requestType=cancellation`)
      .expect(200);
    const body = response.body as ListEnvelope;
    expect(body.meta.total).toBe(2);
    expect(body.data.every((row) => row.requestType === 'cancellation')).toBe(true);
  });

  it('filters by status', async () => {
    const response = await ctx.http
      .get(`/api/v1/practices/${PRACTICE.id}/requests?status=completed`)
      .expect(200);
    const body = response.body as ListEnvelope;
    expect(body.meta.total).toBe(1);
    expect(body.data[0]?.status).toBe('completed');
  });

  it('filters with dateFrom/dateTo (bare dates cover a full UTC day)', async () => {
    const allToday = await ctx.http
      .get(`/api/v1/practices/${PRACTICE.id}/requests?dateFrom=2000-01-01`)
      .expect(200);
    expect((allToday.body as ListEnvelope).meta.total).toBe(6);

    const noneFuture = await ctx.http
      .get(`/api/v1/practices/${PRACTICE.id}/requests?dateFrom=2999-01-01`)
      .expect(200);
    expect((noneFuture.body as ListEnvelope).meta.total).toBe(0);

    const bounded = await ctx.http
      .get(`/api/v1/practices/${PRACTICE.id}/requests?dateFrom=2000-01-01&dateTo=2000-01-02`)
      .expect(200);
    expect((bounded.body as ListEnvelope).meta.total).toBe(0);
  });

  it('combines filters with pagination', async () => {
    const response = await ctx.http
      .get(`/api/v1/practices/${PRACTICE.id}/requests?requestType=cancellation&status=received&page=1&limit=1`)
      .expect(200);
    const body = response.body as ListEnvelope;
    expect(body.meta.total).toBe(2);
    expect(body.data).toHaveLength(1);
    expect(body.meta.nextPage).toBe(2);
  });

  it('404s unknown practices with the canonical envelope', async () => {
    const response = await ctx.http.get(`/api/v1/practices/${crypto.randomUUID()}/requests`).expect(404);
    expect((response.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');
  });

  it('rejects invalid pagination and filter inputs', async () => {
    await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?limit=0`).expect(400);
    await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?limit=101`).expect(400);
    await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?page=-3`).expect(400);
    await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?status=weird`).expect(400);
    await ctx.http.get(`/api/v1/practices/${PRACTICE.id}/requests?dateFrom=not-a-date`).expect(400);
  });
});
