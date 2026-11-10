import { Column, Entity, PrimaryColumn } from 'typeorm';

/**
 * One deterministic end-of-call summary per call (Day 19).
 *
 * The voice gateway builds this from server-authoritative state (intake form,
 * captured requests, request statuses) and submits it once, idempotently, at
 * call end. The row also carries the external task-platform (Jira) mirror state
 * so syncing is idempotent per call and the issue link is readable later.
 *
 * `summary` is the plain-text record (the same text mirrored into the issue
 * description). `bullets` is the display form; `tasks` holds the operational
 * task list derived from confirmed/captured requests.
 */
@Entity('call_summaries')
export class CallSummaryEntity {
  @PrimaryColumn('varchar', { name: 'id', length: 36 })
  public id!: string;

  @Column('varchar', { name: 'practice_id', length: 36 })
  public practiceId!: string;

  /** Unique per call (`uq_call_summaries_call`). */
  @Column('varchar', { name: 'call_id', length: 36 })
  public callId!: string;

  @Column('varchar', { name: 'headline', length: 255 })
  public headline!: string;

  @Column('text', { name: 'summary' })
  public summary!: string;

  /**
   * Summary lines. TypeORM serialises on write and parses on read for BOTH
   * drivers — physically JSON on MySQL and TEXT on SQLite (decided by migration).
   */
  @Column('json', { name: 'bullets' })
  public bullets!: string[];

  /** `{ type, requestType, title, status, details }` per captured request. */
  @Column('json', { name: 'tasks' })
  public tasks!: Array<Record<string, unknown>>;

  /** Intake form as normalised by the gateway (masked before Jira sync). */
  @Column('json', { name: 'caller' })
  public caller!: Record<string, unknown>;

  @Column('varchar', { name: 'outcome', length: 32 })
  public outcome!: string;

  @Column({ name: 'duration_seconds', nullable: true, type: 'int' })
  public durationSeconds: number | null = null;

  @Column({ name: 'turns', type: 'int' })
  public turns!: number;

  @Column({ name: 'transcript_excerpt', nullable: true, type: 'text' })
  public transcriptExcerpt: string | null = null;

  @Column({ name: 'idempotency_key', nullable: true, type: 'varchar', length: 128 })
  public idempotencyKey: string | null = null;

  @Column({ name: 'jira_issue_key', nullable: true, type: 'varchar', length: 64 })
  public jiraIssueKey: string | null = null;

  @Column({ name: 'jira_issue_url', nullable: true, type: 'varchar', length: 512 })
  public jiraIssueUrl: string | null = null;

  @Column({ name: 'jira_synced_at', nullable: true, type: 'datetime' })
  public jiraSyncedAt: Date | null = null;

  /** Last sync failure, sanitised (status/detail — never a credential). */
  @Column({ name: 'jira_sync_error', nullable: true, type: 'varchar', length: 255 })
  public jiraSyncError: string | null = null;

  @Column('datetime', { name: 'created_at' })
  public createdAt!: Date;

  @Column('datetime', { name: 'updated_at' })
  public updatedAt!: Date;
}
