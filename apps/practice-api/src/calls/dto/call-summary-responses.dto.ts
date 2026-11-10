import { ApiProperty } from '@nestjs/swagger';
import { utcDate } from '../../common/idempotency.util.js';
import { CallSummaryOutcome } from './create-call-summary.dto.js';

/** External task-platform mirror state stored with a summary. */
export class JiraLinkDto {
  @ApiProperty({
    description: 'Sync status: disabled | mock | synced | failed',
    example: 'synced',
  })
  status!: string;

  @ApiProperty({ description: 'Issue key, e.g. EMMA-123 (null when not synced)', nullable: true })
  issueKey!: string | null;

  @ApiProperty({ description: 'Browsable issue URL (null when not synced)', nullable: true })
  issueUrl!: string | null;

  @ApiProperty({ description: 'Sanitised failure reason (null when synced)', nullable: true })
  error!: string | null;
}

/** One stored call summary (GET / POST response payload). */
export class CallSummaryResponseDto {
  @ApiProperty({ description: 'Summary UUID' })
  id!: string;

  @ApiProperty({ description: 'Owning practice UUID' })
  practiceId!: string;

  @ApiProperty({ description: 'Call UUID this summary belongs to' })
  callId!: string;

  @ApiProperty({ description: 'One-line headline' })
  headline!: string;

  @ApiProperty({ description: 'Plain-text call summary' })
  summary!: string;

  @ApiProperty({ description: 'Summary lines', type: [String] })
  bullets!: string[];

  @ApiProperty({ description: 'Tasks derived from captured requests', type: [Object] })
  tasks!: Array<Record<string, unknown>>;

  @ApiProperty({ description: 'Intake form captured before the call', type: Object })
  caller!: Record<string, unknown>;

  @ApiProperty({ description: 'How the call ended' })
  outcome!: CallSummaryOutcome;

  @ApiProperty({ description: 'Call duration in seconds', nullable: true })
  durationSeconds!: number | null;

  @ApiProperty({ description: 'Number of conversation turns' })
  turns!: number;

  @ApiProperty({ description: 'Transcript excerpt when enabled', nullable: true })
  transcriptExcerpt!: string | null;

  @ApiProperty({ description: 'External task-platform mirror', type: JiraLinkDto })
  jira!: JiraLinkDto;

  @ApiProperty({ description: 'Row creation time (UTC)', type: String, format: 'date-time' })
  createdAt!: Date;

  @ApiProperty({ description: 'Row last-update time (UTC)', type: String, format: 'date-time' })
  updatedAt!: Date;
}

/** Maps a stored entity onto the response DTO (timestamp-tolerant). */
export function mapCallSummary(entity: {
  id: string;
  practiceId: string;
  callId: string;
  headline: string;
  summary: string;
  bullets: unknown;
  tasks: unknown;
  caller: unknown;
  outcome: string;
  durationSeconds: unknown;
  turns: unknown;
  transcriptExcerpt: unknown;
  jiraIssueKey: unknown;
  jiraIssueUrl: unknown;
  jiraSyncError: unknown;
  createdAt: unknown;
  updatedAt: unknown;
}): CallSummaryResponseDto {
  const response = new CallSummaryResponseDto();
  response.id = entity.id;
  response.practiceId = entity.practiceId;
  response.callId = entity.callId;
  response.headline = entity.headline;
  response.summary = entity.summary;
  response.bullets = Array.isArray(entity.bullets) ? (entity.bullets as string[]) : [];
  response.tasks = Array.isArray(entity.tasks)
    ? (entity.tasks as Array<Record<string, unknown>>)
    : [];
  response.caller = isRecord(entity.caller) ? entity.caller : {};
  response.outcome = entity.outcome as CallSummaryOutcome;
  response.durationSeconds =
    entity.durationSeconds === null || entity.durationSeconds === undefined
      ? null
      : Number(entity.durationSeconds);
  response.turns = Number(entity.turns ?? 0);
  response.transcriptExcerpt =
    typeof entity.transcriptExcerpt === 'string' ? entity.transcriptExcerpt : null;

  const jira = new JiraLinkDto();
  jira.issueKey = typeof entity.jiraIssueKey === 'string' ? entity.jiraIssueKey : null;
  jira.issueUrl = typeof entity.jiraIssueUrl === 'string' ? entity.jiraIssueUrl : null;
  jira.error = typeof entity.jiraSyncError === 'string' ? entity.jiraSyncError : null;
  jira.status = jira.issueKey !== null ? 'synced' : jira.error !== null ? 'failed' : 'pending';
  response.jira = jira;

  response.createdAt = requiredDate(entity.createdAt, 'call_summaries.created_at');
  response.updatedAt = requiredDate(entity.updatedAt, 'call_summaries.updated_at');
  return response;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requiredDate(value: unknown, column: string): Date {
  const parsed = utcDate(value);
  if (parsed === null) {
    throw new Error(`corrupt timestamp in ${column}`);
  }
  return parsed;
}
