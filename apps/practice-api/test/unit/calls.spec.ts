import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { OTHER_PRACTICE, PRACTICE, seedPractices } from '../helpers/fixtures.js';

describe('POST /api/v1/calls (idempotent create-for-gateway)', () => {
  let ctx: BootedTestApp;

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('creates a new call with 201 and default status in-progress', async () => {
    const response = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-NEW-1' })
      .expect(201);
    const payload = response.body as {
      data: { id: string; status: string; externalCallId: string; startedAt: string | null };
      meta?: unknown;
    };
    expect(payload.data.id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(payload.data.status).toBe('in-progress');
    expect(payload.data.externalCallId).toBe('CALL-NEW-1');
    expect(payload.data.startedAt).not.toBeNull();
    expect(payload.meta).toBeUndefined();
  });

  it('returns the existing record with 200 + meta.existing when the pair repeats', async () => {
    const first = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-IDEM-1' })
      .expect(201);
    const firstId = (first.body as { data: { id: string } }).data.id;

    const replay = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-IDEM-1' })
      .expect(200);
    const payload = replay.body as { data: { id: string }; meta?: Record<string, unknown> };
    expect(payload.data.id).toBe(firstId);
    expect(payload.meta?.existing).toBe(true);
  });

  it("same externalCallId under a different practice creates a separate call", async () => {
    await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: OTHER_PRACTICE.id, externalCallId: 'CALL-IDEM-1' })
      .expect(201); // unique constraint is the pair, not the external id alone
  });

  it('rejects an unknown practice with the canonical 404 envelope', async () => {
    const response = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: crypto.randomUUID(), externalCallId: 'CALL-MISS-1' })
      .expect(404);
    const payload = response.body as { error: { code: string; message: string } };
    expect(payload.error.code).toBe('NOT_FOUND');
    expect(payload.error.message).toContain('practice');
  });

  it('rejects invalid bodies with the canonical 400 envelope incl. extra keys', async () => {
    const response = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-BAD-1', unknownField: true })
      .expect(400);
    const payload = response.body as {
      error: { code: string; details?: unknown[] };
    };
    expect(payload.error.code).toBe('VALIDATION_FAILED');
    expect(Array.isArray(payload.error.details)).toBe(true);

    const missing = await ctx.http.post('/api/v1/calls').send({ practiceId: PRACTICE.id }).expect(400);
    expect(((missing.body as { error: { code: string } }).error).code).toBe('VALIDATION_FAILED');
  });

  it('accepts an explicit startedAt timestamp', async () => {
    const response = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-TS-1', startedAt: '2026-11-01T09:00:00Z' })
      .expect(201);
    expect((response.body as { data: { startedAt: string } }).data.startedAt).toContain('2026-11-01');
  });
});

describe('GET /api/v1/calls/:id (call + summaries, no N+1)', () => {
  let ctx: BootedTestApp;
  let callId: string;

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);
    const created = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-SUM-1' })
      .expect(201);
    callId = (created.body as { data: { id: string } }).data.id;

    // Two requests (one completed) and one initiated handoff on this call.
    await ctx.http
      .post('/api/v1/requests')
      .send({ practiceId: PRACTICE.id, callId, requestType: 'appointment-request', structuredPayload: { day: 'monday' } })
      .expect(201);
    await ctx.http
      .post('/api/v1/requests')
      .send({
        practiceId: PRACTICE.id,
        callId,
        requestType: 'reschedule',
        status: 'completed',
        structuredPayload: { day: 'friday' },
      })
      .expect(201);
    await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, callId, reason: 'caller asked for reception', destination: 'Reception', status: 'initiated' })
      .expect(201);
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('returns the call with grouped zeroed summaries when empty', async () => {
    const created = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-SUM-EMPTY' })
      .expect(201);
    const emptyId = (created.body as { data: { id: string } }).data.id;

    const response = await ctx.http.get(`/api/v1/calls/${emptyId}`).expect(200);
    const payload = response.body as {
      data: {
        id: string;
        externalCallId: string;
        summaries: { requests: { total: number; byStatus: Record<string, number> }; handoffs: { total: number; byStatus: Record<string, number> } };
      };
    };
    expect(payload.data.id).toBe(emptyId);
    expect(payload.data.externalCallId).toBe('CALL-SUM-EMPTY');
    expect(payload.data.summaries.requests.total).toBe(0);
    expect(payload.data.summaries.requests.byStatus).toEqual({ received: 0, completed: 0, failed: 0 });
    expect(payload.data.summaries.handoffs.total).toBe(0);
    expect(payload.data.summaries.handoffs.byStatus).toEqual({
      requested: 0,
      initiated: 0,
      completed: 0,
      failed: 0,
    });
  });

  it('summarises attached requests and handoffs by status in one query set', async () => {
    const response = await ctx.http.get(`/api/v1/calls/${callId}`).expect(200);
    const payload = response.body as {
      data: {
        id: string;
        summaries: {
          requests: { total: number; byStatus: Record<string, number> };
          handoffs: { total: number; byStatus: Record<string, number> };
        };
      };
    };
    expect(payload.data.id).toBe(callId);
    expect(payload.data.summaries.requests.total).toBe(2);
    expect(payload.data.summaries.requests.byStatus).toEqual({ received: 1, completed: 1, failed: 0 });
    expect(payload.data.summaries.handoffs.total).toBe(1);
    expect(payload.data.summaries.handoffs.byStatus).toEqual({
      requested: 0,
      initiated: 1,
      completed: 0,
      failed: 0,
    });
  });

  it('404s unknown ids with the canonical envelope', async () => {
    const response = await ctx.http.get(`/api/v1/calls/${crypto.randomUUID()}`).expect(404);
    const payload = response.body as { error: { code: string; message: string } };
    expect(payload.error.code).toBe('NOT_FOUND');
    expect(payload.error.message).toContain('call');
  });

  it('rejects malformed ids with VALIDATION_FAILED (400)', async () => {
    const response = await ctx.http.get('/api/v1/calls/not-a-uuid').expect(400);
    const payload = response.body as { error: { code: string } };
    expect(payload.error.code).toBe('VALIDATION_FAILED');
  });
});
