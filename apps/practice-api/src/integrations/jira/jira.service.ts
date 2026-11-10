import { Inject, Injectable, Logger } from '@nestjs/common';
import { AppConfig, APP_CONFIG, JiraConfig } from '../../config/app-config.js';

/**
 * External task platform (Jira) adapter — Day 19.
 *
 * The practice backend is the only component that talks to Jira: credentials
 * stay server-side, the voice gateway never sees a Jira token, and the issue is
 * created exactly once per call (idempotency is enforced by
 * `call_summaries.jira_issue_key` in the service above this adapter).
 *
 * Modes (`JIRA_MODE`):
 * * `disabled` — integration off; nothing is sent.
 * * `mock`     — offline/demo: a deterministic `MOCK-…` key is returned and no
 *                network call happens (tests and demos run credential-free).
 * * `live`     — Atlassian Cloud REST v3 `POST /rest/api/3/issue` with Basic
 *                auth (`email:api_token`). Missing configuration fails fast at
 *                startup instead of silently degrading.
 *
 * Failure policy: a sync failure is recorded on the summary row and reported to
 * the caller, but it can never break the call flow (the call has already ended
 * by the time this runs) and it is never reported as success.
 */

export type JiraSyncStatus = 'disabled' | 'skipped' | 'synced' | 'mock' | 'failed';

export interface JiraSyncResult {
  status: JiraSyncStatus;
  issueKey: string | null;
  issueUrl: string | null;
  /** Sanitised error text (status codes only — never credentials/response bodies). */
  error: string | null;
}

export interface JiraIssueInput {
  /** Existing issue key: a retried sync must never create a second issue. */
  existingIssueKey?: string | null;
  headline: string;
  summary: string;
  bullets: readonly string[];
  tasks: readonly Record<string, unknown>[];
  caller: Record<string, unknown>;
  outcome: string;
  practiceId: string;
  callId: string;
  durationSeconds: number | null;
  turns: number;
  transcriptExcerpt: string | null;
}

const DEFAULT_ISSUE_TYPE = 'Task';

@Injectable()
export class JiraService {
  private readonly logger = new Logger(JiraService.name);

  constructor(@Inject(APP_CONFIG) private readonly config: AppConfig) {}

  public get jiraConfig(): JiraConfig {
    return this.config.jira;
  }

  /** Secret-free status for `/api/v1/health` and logs. */
  public describe(): { mode: string; ready: boolean } {
    const jira = this.config.jira;
    return { mode: jira.mode, ready: jira.mode !== 'live' || jira.ready };
  }

  /** Map a request type to a configured Jira issue type (default: Task). */
  public issueTypeFor(requestType: string): string {
    return this.config.jira.issueTypeMap[requestType] ?? DEFAULT_ISSUE_TYPE;
  }

  /**
   * Create (or replay) the Jira issue for one call summary.
   *
   * Never throws: every failure is returned as `status: "failed"` with a
   * sanitised error so the caller can persist it and keep serving.
   */
  public async syncCallSummary(input: JiraIssueInput): Promise<JiraSyncResult> {
    const jira = this.config.jira;

    if (input.existingIssueKey) {
      return {
        status: 'synced',
        issueKey: input.existingIssueKey,
        issueUrl: this.browseUrl(input.existingIssueKey),
        error: null,
      };
    }
    if (jira.mode === 'disabled') {
      return { status: 'disabled', issueKey: null, issueUrl: null, error: null };
    }
    if (jira.mode === 'mock') {
      const issueKey = mockKeyFor(input.callId);
      return {
        status: 'mock',
        issueKey,
        issueUrl: `mock://jira/${issueKey}`,
        error: null,
      };
    }
    if (!jira.ready) {
      return {
        status: 'failed',
        issueKey: null,
        issueUrl: null,
        error: 'jira integration is not configured',
      };
    }

    const payload = this.buildIssuePayload(input);
    try {
      const response = await this.post(`${jira.baseUrl}/rest/api/3/issue`, payload);
      if (!response.ok) {
        this.logger.warn(`jira issue create failed: status ${response.status}`);
        return {
          status: 'failed',
          issueKey: null,
          issueUrl: null,
          error: `jira returned ${response.status}`,
        };
      }
      const body = (await response.json()) as { key?: unknown };
      const issueKey = typeof body.key === 'string' ? body.key : null;
      if (issueKey === null) {
        return {
          status: 'failed',
          issueKey: null,
          issueUrl: null,
          error: 'jira response contained no issue key',
        };
      }
      return {
        status: 'synced',
        issueKey,
        issueUrl: this.browseUrl(issueKey),
        error: null,
      };
    } catch (error) {
      const timedOut = error instanceof Error && error.name === 'AbortError';
      this.logger.warn(`jira issue create failed: ${timedOut ? 'timeout' : 'network'}`);
      return {
        status: 'failed',
        issueKey: null,
        issueUrl: null,
        error: timedOut ? 'jira request timed out' : 'jira request failed',
      };
    }
  }

  // --- payload construction -------------------------------------------------

  /** Build the REST v3 create-issue body (plain text as Atlassian doc format). */
  public buildIssuePayload(input: JiraIssueInput): Record<string, unknown> {
    const jira = this.config.jira;
    const primaryTask = input.tasks[0];
    const requestType =
      primaryTask && typeof primaryTask.requestType === 'string'
        ? primaryTask.requestType
        : 'general-enquiry';
    const labels = ['emma', `outcome-${input.outcome}`];
    for (const task of input.tasks) {
      const type = typeof task.requestType === 'string' ? task.requestType : null;
      if (type !== null && !labels.includes(type)) labels.push(type);
    }

    const fields: Record<string, unknown> = {
      project: { key: jira.projectKey },
      summary: `[EMMA] ${input.headline}`.slice(0, 250),
      description: toAdf(this.describeIssue(input)),
      issuetype: { name: this.issueTypeFor(requestType) },
      labels,
    };
    if (jira.epicKey) {
      // Jira Cloud next-gen: attach to an epic via `parent`.
      fields.parent = { key: jira.epicKey };
    }
    return { fields };
  }

  /** Human-readable issue description (masked per `JIRA_PII_MODE`). */
  public describeIssue(input: JiraIssueInput): string {
    const jira = this.config.jira;
    const lines: string[] = [];

    lines.push('Caller (intake form)');
    const callerLines = describeCaller(input.caller, jira.piiMode);
    lines.push(callerLines.length > 0 ? callerLines.join('\n') : 'Not captured.');

    lines.push('', 'Call summary');
    lines.push(input.summary);

    if (input.tasks.length > 0) {
      lines.push('', 'Tasks');
      for (const task of input.tasks) {
        const title = typeof task.title === 'string' ? task.title : 'Task';
        const status = typeof task.status === 'string' ? task.status : 'captured';
        lines.push(`- [${status}] ${title}`);
      }
    }

    lines.push('', 'Call record');
    lines.push(`Call id: ${input.callId}`);
    lines.push(`Practice id: ${input.practiceId}`);
    lines.push(`Outcome: ${input.outcome}`);
    lines.push(`Duration: ${input.durationSeconds ?? 0}s`);
    lines.push(`Turns: ${input.turns}`);

    if (input.transcriptExcerpt) {
      lines.push('', 'Transcript excerpt', input.transcriptExcerpt);
    }
    lines.push('', 'Generated by EMMA (prototype) — no clinical advice, no diagnosis.');
    return lines.join('\n');
  }

  private browseUrl(issueKey: string): string | null {
    const base = this.config.jira.baseUrl;
    return base ? `${base}/browse/${issueKey}` : null;
  }

  private async post(url: string, body: unknown): Promise<Response> {
    const jira = this.config.jira;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), jira.timeoutMs);
    try {
      return await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          Authorization: `Basic ${Buffer.from(`${jira.email ?? ''}:${jira.apiToken ?? ''}`).toString('base64')}`,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }
}

/** Deterministic offline issue key so mock runs are reproducible. */
export function mockKeyFor(callId: string): string {
  let hash = 0;
  for (let i = 0; i < callId.length; i += 1) {
    hash = (hash * 31 + callId.charCodeAt(i)) % 100000;
  }
  return `MOCK-${String(hash).padStart(5, '0')}`;
}

/**
 * Render the caller block for the issue, honouring the PII policy:
 * * `full`   — every captured field;
 * * `masked` — first name + last initial, mobile with only the last 3 digits;
 * * `none`   — no caller details at all.
 */
export function describeCaller(
  caller: Record<string, unknown>,
  piiMode: 'full' | 'masked' | 'none',
): string[] {
  if (piiMode === 'none') {
    return [];
  }
  const first = typeof caller.firstName === 'string' ? caller.firstName : null;
  const last = typeof caller.lastName === 'string' ? caller.lastName : null;
  const mobile = typeof caller.mobile === 'string' ? caller.mobile : null;
  const reason = typeof caller.reason === 'string' ? caller.reason : null;

  const lines: string[] = [];
  if (first) {
    lines.push(piiMode === 'full' ? `Name: ${first}${last ? ` ${last}` : ''}` : `Name: ${first}${last ? ` ${last.slice(0, 1)}.` : ''}`);
  } else {
    lines.push('Name: not captured.');
  }
  if (mobile) {
    lines.push(
      piiMode === 'full' ? `Mobile: ${mobile}` : `Mobile: ${maskMobile(mobile)}`,
    );
  }
  if (reason) {
    lines.push(`Reason for call: ${reason}`);
  }
  return lines;
}

export function maskMobile(mobile: string, style: 'stars' | 'dots' = 'stars'): string {
  const digits = mobile.replace(/\D/g, '');
  if (digits.length <= 3) {
    return digits;
  }
  const hidden = style === 'dots' ? '\u2022' : '*';
  return `${hidden.repeat(Math.min(digits.length - 3, 7))}${digits.slice(-3)}`;
}

/** Convert plain text to a minimal Atlassian Document Format body. */
export function toAdf(text: string): Record<string, unknown> {
  const paragraphs = text.split('\n').map((line) =>
    line.length === 0
      ? { type: 'paragraph', content: [] }
      : { type: 'paragraph', content: [{ type: 'text', text: line }] },
  );
  return { type: 'doc', version: 1, content: paragraphs };
}
