import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { OTHER_PRACTICE, PRACTICE, seedPractices } from '../helpers/fixtures.js';
import { AuditService } from '../../src/audit/audit.service.js';

describe('POST /api/v1/audit-events (append-only)', () => {
  let ctx: BootedTestApp;
  let callId: string;

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);
    const created = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-AU-1' })
      .expect(201);
    callId = (created.body as { data: { id: string } }).data.id;
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('appends an event with createdAt only — no updatedAt field exists', async () => {
    const response = await ctx.http
      .post('/api/v1/audit-events')
      .send({
        practiceId: PRACTICE.id,
        callId,
        eventType: 'request.escalated',
        eventPayload: { requestId: 'req_01', reason: 'prescription status' },
      })
      .expect(201);
    const payload = response.body as {
      data: {
        id: string;
        eventType: string;
        eventPayload: Record<string, unknown>;
        callId: string | null;
        createdAt: string;
        updatedAt?: unknown;
      };
      meta?: unknown;
    };
    expect(payload.data.id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(payload.data.eventType).toBe('request.escalated');
    expect(payload.data.eventPayload).toEqual({ requestId: 'req_01', reason: 'prescription status' });
    expect(payload.data.callId).toBe(callId);
    expect(typeof payload.data.createdAt).toBe('string');
    expect(Object.hasOwn(payload.data, 'updatedAt')).toBe(false);
    expect(payload.meta).toBeUndefined();
  });

  it('allows a null callId (practice-level event)', async () => {
    const response = await ctx.http
      .post('/api/v1/audit-events')
      .send({ practiceId: PRACTICE.id, eventType: 'system.note', eventPayload: { note: 'closed on bank holiday' } })
      .expect(201);
    expect((response.body as { data: { callId: string | null } }).data.callId).toBeNull();
  });

  it('rejects payload shapes that are not primitive trees', async () => {
    const arrayPayload = await ctx.http
      .post('/api/v1/audit-events')
      .send({ practiceId: PRACTICE.id, eventType: 'x', eventPayload: ['a'] as unknown as Record<string, unknown> })
      .expect(400);
    expect((arrayPayload.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');

    const stringValuesAreFine = await ctx.http
      .post('/api/v1/audit-events')
      .send({ practiceId: PRACTICE.id, eventType: 'x', eventPayload: { when: 'not-empty|medical' } })
      .expect(201); // strings are fine; content is the caller's responsibility
    expect(stringValuesAreFine.status).toBe(201);
  });

  it('404s unknown practice / cross-practice call with envelopes', async () => {
    const unknownPractice = await ctx.http
      .post('/api/v1/audit-events')
      .send({ practiceId: crypto.randomUUID(), eventType: 'x', eventPayload: {} })
      .expect(404);
    expect((unknownPractice.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');

    const otherCall = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: OTHER_PRACTICE.id, externalCallId: 'CALL-AU-OTHER' })
      .expect(201);
    const otherCallId = (otherCall.body as { data: { id: string } }).data.id;
    const cross = await ctx.http
      .post('/api/v1/audit-events')
      .send({ practiceId: PRACTICE.id, callId: otherCallId, eventType: 'x', eventPayload: {} })
      .expect(404);
    expect((cross.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');
  });

  it('has NO update/delete handlers: mutating verbs return the 404 envelope', async () => {
    const eventId = crypto.randomUUID();
    for (const mutate of [
      { method: 'patch', verb: 'patch' },
      { method: 'put', verb: 'put' },
      { method: 'delete', verb: 'delete' },
    ] as const) {
      const response = await ctx.http[mutate.verb](`/api/v1/audit-events/${eventId}`).send({}).expect(404);
      const payload = response.body as { error: { code: string; message: string } };
      expect(payload.error.code).toBe('NOT_FOUND');
    }
  });

  it('the append-only service exposes only read/create-shaped methods', () => {
    const instance = ctx.app.get(AuditService);
    const methodNames = Object.getOwnPropertyNames(Object.getPrototypeOf(instance));
    const forbidden = methodNames.filter((name) =>
      /update|patch|delete|remove|save/i.test(name),
    );
    expect(forbidden).toEqual([]);
    expect(methodNames).toContain('append');
  });
});
