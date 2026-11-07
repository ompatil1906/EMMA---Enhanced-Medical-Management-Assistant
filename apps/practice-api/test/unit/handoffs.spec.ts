import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { OTHER_PRACTICE, PRACTICE, seedPractices } from '../helpers/fixtures.js';

describe('POST /api/v1/handoffs', () => {
  let ctx: BootedTestApp;
  let callId: string;

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);
    const created = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-HO-1' })
      .expect(201);
    callId = (created.body as { data: { id: string } }).data.id;
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('creates a handoff with status defaulting to requested', async () => {
    const response = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, callId, reason: 'caller asked for a human', destination: 'Reception desk' })
      .expect(201);
    const payload = response.body as {
      data: { id: string; status: string; callId: string | null; reason: string; destination: string };
      meta?: unknown;
    };
    expect(payload.data.id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(payload.data.status).toBe('requested');
    expect(payload.data.callId).toBe(callId);
    expect(payload.data.reason).toBe('caller asked for a human');
    expect(payload.data.destination).toBe('Reception desk');
    expect(payload.meta).toBeUndefined();
  });

  it('accepts explicit initiated/failed statuses from the allowed enum', async () => {
    const initiated = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, callId, reason: 'wants billing desk', destination: 'Billing', status: 'initiated' })
      .expect(201);
    expect((initiated.body as { data: { status: string } }).data.status).toBe('initiated');

    const failed = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, reason: 'nobody picked up', destination: 'Ringdown', status: 'failed' })
      .expect(201);
    expect((failed.body as { data: { status: string } }).data.status).toBe('failed');
  });

  it('rejects statuses outside the exact enum', async () => {
    const response = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, reason: 'x', destination: 'y', status: 'paused' })
      .expect(400);
    expect((response.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');
  });

  it('404s unknown practice / unknown call / cross-practice call', async () => {
    const unknownPractice = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: crypto.randomUUID(), reason: 'r', destination: 'd' })
      .expect(404);
    expect((unknownPractice.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');

    const unknownCall = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, callId: crypto.randomUUID(), reason: 'r', destination: 'd' })
      .expect(404);
    expect((unknownCall.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');

    const otherCall = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: OTHER_PRACTICE.id, externalCallId: 'CALL-HO-OTHER' })
      .expect(201);
    const otherCallId = (otherCall.body as { data: { id: string } }).data.id;
    const crossPractice = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, callId: otherCallId, reason: 'r', destination: 'd' })
      .expect(404);
    expect((crossPractice.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');
  });

  it('400s when required fields are missing', async () => {
    const noReason = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, destination: 'desk' })
      .expect(400);
    expect((noReason.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');

    const noDestination = await ctx.http
      .post('/api/v1/handoffs')
      .send({ practiceId: PRACTICE.id, reason: 'r' })
      .expect(400);
    expect((noDestination.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');
  });
});
