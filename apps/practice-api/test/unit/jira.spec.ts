import {
  describeCaller,
  JiraIssueInput,
  JiraService,
  maskMobile,
  mockKeyFor,
  toAdf,
} from '../../src/integrations/jira/jira.service.js';
import { AppConfig, JiraConfig } from '../../src/config/app-config.js';

/**
 * Day 19: Jira adapter contract.
 *
 * The adapter is the only component that talks to the task platform. These
 * tests pin the payload shape (project/summary/description/issue type/labels),
 * the PII policy, the offline mode, and the failure semantics — a sync failure
 * is *reported*, never swallowed and never reported as success.
 */
function config(overrides: Partial<JiraConfig> = {}): AppConfig {
  const app = new AppConfig();
  const jira = new JiraConfig();
  jira.mode = 'mock';
  Object.assign(jira, overrides);
  app.jira = jira;
  return app;
}

function service(overrides: Partial<JiraConfig> = {}): JiraService {
  return new JiraService(config(overrides));
}

function input(overrides: Partial<JiraIssueInput> = {}): JiraIssueInput {
  return {
    headline: 'Appointment request — Sarah J.',
    summary: 'Caller: Sarah J.\nRequests captured: Appointment request (submitted).',
    bullets: ['Caller: Sarah J.', 'Requests captured: Appointment request (submitted).'],
    tasks: [
      {
        type: 'appointment',
        requestType: 'appointment-request',
        title: 'Appointment request — preferred date: Tuesday',
        status: 'submitted',
      },
    ],
    caller: {
      firstName: 'Sarah',
      lastName: 'Jenkins',
      mobile: '+44 7700 900077',
      reason: 'Book Appointment',
    },
    outcome: 'completed',
    practiceId: 'practice-1',
    callId: 'call-1',
    durationSeconds: 42,
    turns: 4,
    transcriptExcerpt: null,
    ...overrides,
  };
}

describe('JiraService', () => {
  it('is disabled by default and reports readiness without secrets', () => {
    const jira = service({ mode: 'disabled' });
    expect(jira.describe()).toEqual({ mode: 'disabled', ready: true });
  });

  it('returns a deterministic mock key offline (no network)', async () => {
    const jira = service({ mode: 'mock' });
    const result = await jira.syncCallSummary(input());
    expect(result.status).toBe('mock');
    expect(result.issueKey).toBe(mockKeyFor('call-1'));
    expect(result.issueUrl).toBe(`mock://jira/${result.issueKey}`);
    expect(result.error).toBeNull();
  });

  it('replays an existing issue key instead of creating a second issue', async () => {
    const jira = service({ mode: 'mock' });
    const result = await jira.syncCallSummary(input({ existingIssueKey: 'EMMA-7' }));
    expect(result.status).toBe('synced');
    expect(result.issueKey).toBe('EMMA-7');
  });

  it('fails closed when live mode is not fully configured', async () => {
    const jira = service({ mode: 'live', projectKey: 'EMMA' });
    const result = await jira.syncCallSummary(input());
    expect(result.status).toBe('failed');
    expect(result.error).toBe('jira integration is not configured');
  });

  it('builds a REST v3 create-issue payload', () => {
    const jira = service({
      mode: 'live',
      baseUrl: 'https://example.atlassian.net',
      email: 'reception@example.com',
      apiToken: 'token-not-real',
      projectKey: 'EMMA',
      issueTypeMap: { 'appointment-request': 'Appointment' },
      epicKey: 'EMMA-1',
    });
    const payload = jira.buildIssuePayload(input()) as {
      fields: {
        project: { key: string };
        summary: string;
        description: { type: string; version: number; content: unknown[] };
        issuetype: { name: string };
        labels: string[];
        parent?: { key: string };
      };
    };

    expect(payload.fields.project.key).toBe('EMMA');
    expect(payload.fields.summary).toBe('[EMMA] Appointment request — Sarah J.');
    expect(payload.fields.issuetype.name).toBe('Appointment');
    expect(payload.fields.labels).toEqual([
      'emma',
      'outcome-completed',
      'appointment-request',
    ]);
    expect(payload.fields.parent).toEqual({ key: 'EMMA-1' });
    expect(payload.fields.description.type).toBe('doc');
    expect(payload.fields.description.version).toBe(1);
    expect(payload.fields.description.content.length).toBeGreaterThan(0);
  });

  it('falls back to Task when no issue-type mapping exists', () => {
    const jira = service({ mode: 'mock' });
    const payload = jira.buildIssuePayload(input()) as {
      fields: { issuetype: { name: string } };
    };
    expect(payload.fields.issuetype.name).toBe('Task');
  });

  it('masks contact details by default', () => {
    const jira = service({ mode: 'mock', piiMode: 'masked' });
    const description = jira.describeIssue(input());
    expect(description).toContain('Name: Sarah J.');
    expect(description).not.toContain('Jenkins');
    expect(description).toContain('Mobile: *******077');
    expect(description).not.toContain('7700 900077');
    expect(description).toContain('Reason for call: Book Appointment');
    expect(description).toContain('Tasks');
    expect(description).toContain('- [submitted] Appointment request — preferred date: Tuesday');
    expect(description).toContain('Call id: call-1');
    expect(description).toContain('Outcome: completed');
  });

  it('includes full contact details only when explicitly configured', () => {
    const jira = service({ mode: 'mock', piiMode: 'full' });
    const description = jira.describeIssue(input());
    expect(description).toContain('Name: Sarah Jenkins');
    expect(description).toContain('Mobile: +44 7700 900077');
  });

  it('omits the caller block entirely when PII is disabled', () => {
    const description = describeCaller({ firstName: 'Sarah' }, 'none');
    expect(description).toEqual([]);
  });

  it('masks a short number without inventing digits', () => {
    expect(maskMobile('123')).toBe('123');
    expect(maskMobile('+44 7700 900077')).toBe('*******077');
  });

  it('reports an HTTP failure with a sanitised error', async () => {
    const jira = service({
      mode: 'live',
      baseUrl: 'https://example.atlassian.net',
      email: 'reception@example.com',
      apiToken: 'token-not-real',
      projectKey: 'EMMA',
    });
    const fetchSpy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{"errorMessages":["nope"]}', { status: 401 }));

    const result = await jira.syncCallSummary(input());
    expect(result.status).toBe('failed');
    expect(result.error).toBe('jira returned 401');
    expect(result.issueKey).toBeNull();

    const request = fetchSpy.mock.calls[0];
    expect(String(request?.[0])).toBe('https://example.atlassian.net/rest/api/3/issue');
    expect((request?.[1]?.headers as Record<string, string>).Authorization).toMatch(/^Basic /);
    fetchSpy.mockRestore();
  });

  it('reports a missing issue key in the response as a failure', async () => {
    const jira = service({
      mode: 'live',
      baseUrl: 'https://example.atlassian.net',
      email: 'reception@example.com',
      apiToken: 'token-not-real',
      projectKey: 'EMMA',
    });
    const fetchSpy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 201 }));

    const result = await jira.syncCallSummary(input());
    expect(result.status).toBe('failed');
    expect(result.error).toBe('jira response contained no issue key');
    fetchSpy.mockRestore();
  });

  it('creates the issue in live mode and returns the browse URL', async () => {
    const jira = service({
      mode: 'live',
      baseUrl: 'https://example.atlassian.net',
      email: 'reception@example.com',
      apiToken: 'token-not-real',
      projectKey: 'EMMA',
    });
    const fetchSpy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{"key":"EMMA-42"}', { status: 201 }));

    const result = await jira.syncCallSummary(input());
    expect(result).toEqual({
      status: 'synced',
      issueKey: 'EMMA-42',
      issueUrl: 'https://example.atlassian.net/browse/EMMA-42',
      error: null,
    });
    fetchSpy.mockRestore();
  });

  it('reports a network failure without leaking the exception', async () => {
    const jira = service({
      mode: 'live',
      baseUrl: 'https://example.atlassian.net',
      email: 'reception@example.com',
      apiToken: 'token-not-real',
      projectKey: 'EMMA',
    });
    const fetchSpy = jest
      .spyOn(globalThis, 'fetch')
      .mockRejectedValue(new Error('getaddrinfo ENOTFOUND example.atlassian.net'));

    const result = await jira.syncCallSummary(input());
    expect(result.status).toBe('failed');
    expect(result.error).toBe('jira request failed');
    fetchSpy.mockRestore();
  });

  it('reports a timeout distinctly', async () => {
    const jira = service({
      mode: 'live',
      baseUrl: 'https://example.atlassian.net',
      email: 'reception@example.com',
      apiToken: 'token-not-real',
      projectKey: 'EMMA',
      timeoutMs: 500,
    });
    const abortError = new Error('aborted');
    abortError.name = 'AbortError';
    const fetchSpy = jest.spyOn(globalThis, 'fetch').mockRejectedValue(abortError);

    const result = await jira.syncCallSummary(input());
    expect(result.status).toBe('failed');
    expect(result.error).toBe('jira request timed out');
    fetchSpy.mockRestore();
  });

  it('converts plain text into Atlassian document format paragraphs', () => {
    const adf = toAdf('line one\n\nline two') as {
      type: string;
      version: number;
      content: Array<{ type: string; content: Array<{ type: string; text: string }> }>;
    };
    expect(adf.type).toBe('doc');
    expect(adf.version).toBe(1);
    expect(adf.content).toHaveLength(3);
    expect(adf.content[0]?.content[0]?.text).toBe('line one');
    expect(adf.content[1]?.content).toEqual([]);
    expect(adf.content[2]?.content[0]?.text).toBe('line two');
  });
});
