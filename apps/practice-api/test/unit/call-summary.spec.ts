import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { PRACTICE, seedCall, seedPractices } from '../helpers/fixtures.js';

/**
 * Day 19: the end-of-call summary endpoint.
 *
 * The summary is the operational record of a call — the intake form, the
 * narrative summary, and the tasks derived from captured requests — and it is
 * mirrored to the external task platform. These tests pin:
 *
 * * one summary per call (replay returns 200 + meta.existing, never a duplicate),
 * * the call row is closed out when its summary arrives,
 * * practice isolation (a summary can never be written into another practice),
 * * the Jira link is returned and replay-safe (mock mode: no network).
 */
describe('POST /api/v1/calls/:id/summary (end-of-call record + task platform)', () => {
  let ctx: BootedTestApp;

  beforeAll(async () => {
    process.env.JIRA_MODE = 'mock';
    process.env.JIRA_PII_MODE = 'masked';
    ctx = await createTestApp();
    await seedPractices(ctx);
  });

  afterAll(async () => {
    await ctx.close();
    delete process.env.JIRA_MODE;
    delete process.env.JIRA_PII_MODE;
  });

  const summaryBody = (practiceId: string): Record<string, unknown> => ({
    practiceId,
    headline: 'Appointment request — Sarah J.',
    summary: 'Caller: Sarah J.\nRequests captured: Appointment request (submitted).',
    bullets: ['Caller: Sarah J.', 'Requests captured: Appointment request (submitted).'],
    tasks: [
      {
        type: 'appointment',
        requestType: 'appointment-request',
        title: 'Appointment request — preferred date: Tuesday',
        status: 'submitted',
        details: { preferred_date: 'Tuesday', preferred_time: 'morning' },
      },
    ],
    caller: {
      firstName: 'Sarah',
      lastName: 'Jenkins',
      mobile: '+44 7700 900077',
      reason: 'Book Appointment',
    },
    outcome: 'completed',
    durationSeconds: 42,
    turns: 4,
    transcriptExcerpt: 'Caller: I would like an appointment\nEMMA: Of course.',
  });

  it('stores the summary and mirrors it to the task platform', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-1');
    const response = await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .set('Idempotency-Key', 'summary-key-1')
      .send(summaryBody(PRACTICE.id))
      .expect(201);

    const payload = response.body as {
      data: {
        id: string;
        callId: string;
        headline: string;
        tasks: unknown[];
        caller: Record<string, unknown>;
        outcome: string;
        durationSeconds: number;
        turns: number;
        jira: { status: string; issueKey: string | null; issueUrl: string | null };
      };
      meta?: unknown;
    };
    expect(payload.data.callId).toBe(callId);
    expect(payload.data.headline).toBe('Appointment request — Sarah J.');
    expect(payload.data.tasks).toHaveLength(1);
    expect(payload.data.caller.firstName).toBe('Sarah');
    expect(payload.data.outcome).toBe('completed');
    expect(payload.data.durationSeconds).toBe(42);
    expect(payload.data.turns).toBe(4);
    expect(payload.data.jira.status).toBe('synced');
    expect(payload.data.jira.issueKey).toMatch(/^MOCK-\d{5}$/);
    expect(payload.data.jira.issueUrl).toContain(payload.data.jira.issueKey);
    expect(payload.meta).toBeUndefined();
  });

  it('closes out the call record (completed, ended, duration)', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-2');
    await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send({ ...summaryBody(PRACTICE.id), durationSeconds: 30 })
      .expect(201);

    const response = await ctx.http.get(`/api/v1/calls/${callId}`).expect(200);
    const payload = response.body as {
      data: { status: string; endedAt: string | null; durationSeconds: number | null };
    };
    expect(payload.data.status).toBe('completed');
    expect(payload.data.endedAt).not.toBeNull();
    expect(payload.data.durationSeconds).toBe(30);
  });

  it('replays the stored summary without creating a second issue', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-3');
    const first = await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send(summaryBody(PRACTICE.id))
      .expect(201);
    const firstBody = first.body as {
      data: { id: string; jira: { issueKey: string | null } };
    };

    const replay = await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send({ ...summaryBody(PRACTICE.id), headline: 'Different headline ignored on replay' })
      .expect(200);
    const replayBody = replay.body as {
      data: { id: string; headline: string; jira: { issueKey: string | null } };
      meta?: Record<string, unknown>;
    };

    expect(replayBody.meta?.existing).toBe(true);
    expect(replayBody.data.id).toBe(firstBody.data.id);
    expect(replayBody.data.jira.issueKey).toBe(firstBody.data.jira.issueKey);
    expect(replayBody.data.headline).toBe('Appointment request — Sarah J.');
  });

  it('returns the stored summary through GET', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-4');
    await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send(summaryBody(PRACTICE.id))
      .expect(201);

    const response = await ctx.http.get(`/api/v1/calls/${callId}/summary`).expect(200);
    const payload = response.body as { data: { callId: string; jira: { issueKey: string } } };
    expect(payload.data.callId).toBe(callId);
    expect(payload.data.jira.issueKey).toMatch(/^MOCK-/);
  });

  it('404s for an unknown call and for a summary that does not exist yet', async () => {
    await ctx.http
      .post('/api/v1/calls/00000000-0000-4000-8000-000000000000/summary')
      .send(summaryBody(PRACTICE.id))
      .expect(404);

    const callId = await seedCall(ctx, 'CALL-SUMMARY-NO-ROW');
    await ctx.http.get(`/api/v1/calls/${callId}/summary`).expect(404);
  });

  it("refuses to write a summary into another practice's call", async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-ISOLATION');
    const otherPracticeId = '1a2b3c4d-11aa-4bbb-8ccc-d0e1f2a3b4c5';
    await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send(summaryBody(otherPracticeId))
      .expect(404);
  });

  it('validates the payload (unknown outcome, missing headline)', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-INVALID');
    await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send({ ...summaryBody(PRACTICE.id), outcome: 'nonsense' })
      .expect(400);
    await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send({ ...summaryBody(PRACTICE.id), headline: '' })
      .expect(400);
  });

  it('supports opt-out of the external sync', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-NOSYNC');
    const response = await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send({ ...summaryBody(PRACTICE.id), syncJira: false })
      .expect(201);
    const payload = response.body as {
      data: { jira: { status: string; issueKey: string | null } };
    };
    expect(payload.data.jira.issueKey).toBeNull();
    expect(payload.data.jira.status).toBe('pending');
  });

  it('re-syncs on demand and stays idempotent', async () => {
    const callId = await seedCall(ctx, 'CALL-SUMMARY-RESYNC');
    const created = await ctx.http
      .post(`/api/v1/calls/${callId}/summary`)
      .send({ ...summaryBody(PRACTICE.id), syncJira: false })
      .expect(201);
    const issueKey = (created.body as { data: { jira: { issueKey: string | null } } }).data.jira
      .issueKey;
    expect(issueKey).toBeNull();

    const synced = await ctx.http
      .post('/api/v1/integrations/jira/sync')
      .send({ callId })
      .expect(200);
    const first = (synced.body as { data: { jira: { issueKey: string | null } } }).data.jira
      .issueKey;
    expect(first).toMatch(/^MOCK-/);

    const again = await ctx.http
      .post('/api/v1/integrations/jira/sync')
      .send({ callId })
      .expect(200);
    expect((again.body as { data: { jira: { issueKey: string | null } } }).data.jira.issueKey).toBe(
      first,
    );
  });
});
