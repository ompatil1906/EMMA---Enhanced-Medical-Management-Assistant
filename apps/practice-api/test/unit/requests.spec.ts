import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { OTHER_PRACTICE, PRACTICE, requestPayload, seedPractices } from '../helpers/fixtures.js';

type RequestBodyShape = Record<string, unknown>;

describe('POST /api/v1/requests + idempotency matrix', () => {
  let ctx: BootedTestApp;
  let callId: string;

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);
    const created = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: PRACTICE.id, externalCallId: 'CALL-REQ-1' })
      .expect(201);
    callId = (created.body as { data: { id: string } }).data.id;
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('creates a request without an idempotency key → 201, hash stored', async () => {
    const response = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(), callId })
      .expect(201);
    const payload = response.body as {
      data: {
        id: string;
        status: string;
        structuredPayload: Record<string, unknown>;
        idempotencyPayloadHash: string;
        idempotencyKey: string | null;
      };
      meta?: unknown;
    };
    expect(payload.data.id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(payload.data.status).toBe('received');
    expect(payload.data.structuredPayload).toEqual({ preferredDay: 'tuesday', preferredTime: 'morning' });
    expect(payload.data.idempotencyPayloadHash).toMatch(/^[0-9a-f]{64}$/);
    expect(payload.data.idempotencyKey).toBeNull();
    expect(payload.meta).toBeUndefined();
  });

  it('replays with 200 + same id for a duplicated body idempotencyKey', async () => {
    const body: RequestBodyShape = {
      ...requestPayload({ preferredDay: 'wednesday' }),
      callId,
      idempotencyKey: 'existence-key-body-1',
    };
    const first = await ctx.http.post('/api/v1/requests').send(body).expect(201);
    const firstId = (first.body as { data: { id: string } }).data.id;

    const replay = await ctx.http.post('/api/v1/requests').send(body).expect(200);
    const payload = replay.body as { data: { id: string }; meta?: Record<string, unknown> };
    expect(payload.data.id).toBe(firstId);
    expect(payload.meta?.existing).toBe(true);
  });

  it('replays with 200 + same id when only the header carried the key', async () => {
    const body: RequestBodyShape = { ...requestPayload({ preferredDay: 'thursday' }), callId };
    const first = await ctx.http
      .post('/api/v1/requests')
      .send(body)
      .set('Idempotency-Key', 'existence-key-header-1')
      .expect(201);
    const firstId = (first.body as { data: { id: string } }).data.id;
    expect((first.body as { data: { idempotencyKey: string } }).data.idempotencyKey).toBe('existence-key-header-1');

    const replay = await ctx.http
      .post('/api/v1/requests')
      .send(body)
      .set('Idempotency-Key', 'existence-key-header-1')
      .expect(200);
    const payload = replay.body as { data: { id: string }; meta?: Record<string, unknown> };
    expect(payload.data.id).toBe(firstId);
    expect(payload.meta?.existing).toBe(true);
  });

  it('header wins over body key (both set) — header scoped row is used', async () => {
    const response = await ctx.http
      .post('/api/v1/requests')
      .send({
        ...requestPayload({ preferredDay: 'friday' }),
        callId,
        idempotencyKey: 'body-loses',
      })
      .set('Idempotency-Key', 'header-wins')
      .expect(201);
    expect((response.body as { data: { idempotencyKey: string } }).data.idempotencyKey).toBe('header-wins');
  });

  it('same key + different payload → 409 IDEMPOTENCY_KEY_CONFLICT envelope', async () => {
    const conflict = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload({ preferredDay: 'saturday', WHY: 'changed' }), idempotencyKey: 'existence-key-body-1' })
      .expect(409);
    const payload = conflict.body as { error: { code: string; message: string; details?: Record<string, unknown> } };
    expect(payload.error.code).toBe('IDEMPOTENCY_KEY_CONFLICT');
    expect(payload.error.message).toContain('different');
  });

  it('same key is scoped per practice — same key under another practice creates new', async () => {
    const response = await ctx.http
      .post('/api/v1/requests')
      .send({
        practiceId: OTHER_PRACTICE.id,
        requestType: 'cancellation',
        structuredPayload: { preferredDay: 'monday' },
        idempotencyKey: 'existence-key-header-1',
      })
      .expect(201);
    const createdId = (response.body as { data: { id: string } }).data.id;
    expect(createdId).toMatch(/^[0-9a-f-]{36}$/i);
  });

  it('404s when the practice is unknown', async () => {
    const response = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(), practiceId: crypto.randomUUID() })
      .expect(404);
    expect((response.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');
  });

  it('404s when the call is unknown or belongs to another practice', async () => {
    const unknownCall = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(), callId: crypto.randomUUID() })
      .expect(404);
    expect((unknownCall.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');

    const otherCall = await ctx.http
      .post('/api/v1/calls')
      .send({ practiceId: OTHER_PRACTICE.id, externalCallId: 'CALL-OTHER-1' })
      .expect(201);
    const otherCallId = (otherCall.body as { data: { id: string } }).data.id;

    const crossPractice = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(), callId: otherCallId })
      .expect(404);
    expect((crossPractice.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');
  });

  it('400s validation failures: array payload, bad status, unknown keys, deep tree', async () => {
    const arrayPayload = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload([1, 2] as unknown as Record<string, unknown>) })
      .expect(400);
    expect((arrayPayload.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');

    const badStatus = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(), status: 'archived' })
      .expect(400);
    expect((badStatus.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');

    const deep = buildDeepObject(10);
    const deepPayload = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(deep) })
      .expect(400);
    expect((deepPayload.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');

    const queryStringsDoNotHijackBodyValidation = await ctx.http
      .post('/api/v1/requests')
      .send(requestPayload())
      .query({ extra: 'x' })
      .expect(201);
    expect(queryStringsDoNotHijackBodyValidation.status).toBe(201);

    // A whitespace-only header key means "no key": two calls make two rows.
    const noKeyFirst = await ctx.http
      .post('/api/v1/requests')
      .send(requestPayload({ whitespace: 'a' }))
      .set('Idempotency-Key', '   ')
      .expect(201);
    const noKeySecond = await ctx.http
      .post('/api/v1/requests')
      .send(requestPayload({ whitespace: 'b' }))
      .set('Idempotency-Key', '   ')
      .expect(201);
    expect((noKeyFirst.body as { data: { id: string } }).data.id).not.toBe(
      (noKeySecond.body as { data: { id: string } }).data.id,
    );
    expect((noKeyFirst.body as { data: { idempotencyKey: null } }).data.idempotencyKey).toBeNull();

    const unknownField = await ctx.http
      .post('/api/v1/requests')
      .send({ ...requestPayload(), notAField: true })
      .expect(400);
    expect((unknownField.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');
  });

  it('accepts a primitive-tree payload with nested arrays and objects', async () => {
    const response = await ctx.http
      .post('/api/v1/requests')
      .send({
        ...requestPayload({
          name: 'Riverbend',
          count: 2,
          ok: false,
          nothing: null,
          tags: ['a', 'b'],
          nested: { list: [1, 2, { deep: 'value' }] },
        }),
      })
      .expect(201);
    const payload = response.body as { data: { structuredPayload: Record<string, unknown> } };
    expect(payload.data.structuredPayload).toEqual({
      name: 'Riverbend',
      count: 2,
      ok: false,
      nothing: null,
      tags: ['a', 'b'],
      nested: { list: [1, 2, { deep: 'value' }] },
    });
  });
});

function buildDeepObject(depth: number): Record<string, unknown> {
  let current: Record<string, unknown> = { leaf: 'value' };
  for (let i = 0; i < depth; i++) {
    current = { nested: current };
  }
  return current;
}
