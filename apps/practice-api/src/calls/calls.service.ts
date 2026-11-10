import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { QueryDeepPartialEntity, Repository } from 'typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { CallSummaryEntity } from '../entities/call-summary.entity.js';
import { HandoffEntity, HANDOFF_STATUSES } from '../entities/handoff.entity.js';
import { PatientRequestEntity, REQUEST_STATUSES } from '../entities/patient-request.entity.js';
import { ApiError } from '../common/api/api-error.js';
import { Envelope } from '../common/api/envelope.js';
import { isDuplicateKeyError } from '../common/db-errors.js';
import { utcDate } from '../common/idempotency.util.js';
import { CreateCallDto } from './dto/create-call.dto.js';
import {
  CallResponseDto,
  CallStatusSummaryDto,
  CallSummariesDto,
  CallWithSummariesDto,
  mapCall,
  zeroStatusSummary,
} from './dto/call-responses.dto.js';
import { PracticesService } from '../practices/practices.service.js';
import { JiraService, JiraSyncResult } from '../integrations/jira/jira.service.js';
import { CreateCallSummaryDto } from './dto/create-call-summary.dto.js';
import { CallSummaryResponseDto, mapCallSummary } from './dto/call-summary-responses.dto.js';

/** Result of the idempotent create-by-external-call-id flow. */
export interface CallUpsertResult {
  call: CallResponseDto;
  existing: boolean;
}

/** Result of the idempotent summary submission flow. */
export interface CallSummaryUpsertResult {
  summary: CallSummaryResponseDto;
  existing: boolean;
}

@Injectable()
export class CallsService {
  constructor(
    private readonly practices: PracticesService,
    @InjectRepository(CallEntity)
    private readonly calls: Repository<CallEntity>,
    @InjectRepository(PatientRequestEntity)
    private readonly requests: Repository<PatientRequestEntity>,
    @InjectRepository(HandoffEntity)
    private readonly handoffs: Repository<HandoffEntity>,
    @InjectRepository(CallSummaryEntity)
    private readonly summaries: Repository<CallSummaryEntity>,
    private readonly jira: JiraService,
  ) {}

  /**
   * Creates a call, or (per practice + external call id) replays the existing
   * record — the idempotent endpoint used by the voice gateway. Concurrency
   * falls back to the composite unique constraint + re-read.
   */
  public async createOrReturnExisting(dto: CreateCallDto): Promise<CallUpsertResult> {
    await this.practices.requirePractice(dto.practiceId);
    const existing = await this.findByExternalId(dto.practiceId, dto.externalCallId);
    if (existing !== null) {
      return mapResult(existing, true);
    }

    const now = new Date();
    const call = new CallEntity();
    call.id = crypto.randomUUID();
    call.practiceId = dto.practiceId;
    call.externalCallId = dto.externalCallId;
    call.status = 'in-progress';
    call.startedAt = dto.startedAt === undefined ? now : utcDate(dto.startedAt);
    call.endedAt = null;
    call.durationSeconds = null;
    call.createdAt = now;
    call.updatedAt = now;

    try {
      await this.calls.insert(call);
      return mapResult(call, false);
    } catch (error) {
      if (isDuplicateKeyError(error)) {
        const raced = await this.findByExternalId(dto.practiceId, dto.externalCallId);
        if (raced !== null) {
          return mapResult(raced, true);
        }
      }
      throw error;
    }
  }

  /**
   * Loads a call plus `{ total, byStatus }` summaries for its related requests
   * and handoffs: exactly three queries (call + two grouped counts), no N+1.
   */
  public async getWithSummaries(callId: string): Promise<Envelope<CallWithSummariesDto>> {
    const call = await this.calls.findOneBy({ id: callId });
    if (call === null) {
      throw ApiError.notFound(`call "${callId}" does not exist`);
    }
    const [requestSummary, handoffSummary] = await Promise.all([
      this.summarise(this.requests, REQUEST_STATUSES, call.id),
      this.summarise(this.handoffs, HANDOFF_STATUSES, call.id),
    ]);

    const summaries = new CallSummariesDto();
    summaries.requests = requestSummary;
    summaries.handoffs = handoffSummary;
    const detail = Object.assign(new CallWithSummariesDto(), mapCall(call), { summaries });
    return new Envelope<CallWithSummariesDto>(detail);
  }

  /**
   * Stores the deterministic end-of-call summary and mirrors it to the external
   * task platform. One summary per call (`uq_call_summaries_call`): a replay
   * returns the stored record and never creates a second Jira issue.
   */
  public async upsertSummary(
    callId: string,
    dto: CreateCallSummaryDto,
    idempotencyKey?: string,
  ): Promise<CallSummaryUpsertResult> {
    const call = await this.calls.findOneBy({ id: callId });
    if (call === null) {
      throw ApiError.notFound(`call "${callId}" does not exist`);
    }
    if (call.practiceId !== dto.practiceId) {
      // Practice identity is server-authoritative: a summary can never be
      // written into another practice's call.
      throw ApiError.notFound(`call "${callId}" does not exist`);
    }

    const existing = await this.summaries.findOneBy({ callId });
    if (existing !== null) {
      return { summary: mapCallSummary(existing), existing: true };
    }

    const now = new Date();
    const entity = new CallSummaryEntity();
    entity.id = crypto.randomUUID();
    entity.practiceId = call.practiceId;
    entity.callId = call.id;
    entity.headline = dto.headline;
    entity.summary = dto.summary;
    entity.bullets = dto.bullets ?? [];
    entity.tasks = (dto.tasks ?? []) as unknown as Array<Record<string, unknown>>;
    entity.caller = (dto.caller ?? {}) as unknown as Record<string, unknown>;
    entity.outcome = dto.outcome;
    entity.durationSeconds = dto.durationSeconds ?? null;
    entity.turns = dto.turns ?? 0;
    entity.transcriptExcerpt = dto.transcriptExcerpt ?? null;
    entity.idempotencyKey = idempotencyKey ?? null;
    entity.jiraIssueKey = null;
    entity.jiraIssueUrl = null;
    entity.jiraSyncedAt = null;
    entity.jiraSyncError = null;
    entity.createdAt = now;
    entity.updatedAt = now;

    try {
      await this.summaries.insert(entity as QueryDeepPartialEntity<CallSummaryEntity>);
    } catch (error) {
      if (isDuplicateKeyError(error)) {
        const raced = await this.summaries.findOneBy({ callId });
        if (raced !== null) {
          return { summary: mapCallSummary(raced), existing: true };
        }
      }
      throw error;
    }

    // Close out the call record so the practice view is coherent.
    await this.calls.update(
      { id: call.id },
      {
        status: dto.outcome === 'failed' ? 'failed' : 'completed',
        endedAt: now,
        durationSeconds: dto.durationSeconds ?? call.durationSeconds,
        updatedAt: now,
      },
    );

    const synced = dto.syncJira === false ? null : await this.syncSummaryToJira(entity);
    return { summary: mapCallSummary(synced ?? entity), existing: false };
  }

  /** Reads one call's stored summary (404 when the call or summary is unknown). */
  public async getSummary(callId: string): Promise<Envelope<CallSummaryResponseDto>> {
    const call = await this.calls.findOneBy({ id: callId });
    if (call === null) {
      throw ApiError.notFound(`call "${callId}" does not exist`);
    }
    const summary = await this.summaries.findOneBy({ callId });
    if (summary === null) {
      throw ApiError.notFound(`call "${callId}" has no summary yet`);
    }
    return new Envelope(mapCallSummary(summary));
  }

  /** Manual re-sync (staff action); replay-safe because the key is stored. */
  public async resyncSummary(callId: string): Promise<Envelope<CallSummaryResponseDto>> {
    const summary = await this.summaries.findOneBy({ callId });
    if (summary === null) {
      throw ApiError.notFound(`call "${callId}" has no summary to sync`);
    }
    const synced = await this.syncSummaryToJira(summary);
    return new Envelope(mapCallSummary(synced));
  }

  /** Mirrors the stored summary to Jira and persists the outcome. */
  private async syncSummaryToJira(summary: CallSummaryEntity): Promise<CallSummaryEntity> {
    const result: JiraSyncResult = await this.jira.syncCallSummary({
      existingIssueKey: summary.jiraIssueKey,
      headline: summary.headline,
      summary: summary.summary,
      bullets: summary.bullets,
      tasks: summary.tasks,
      caller: summary.caller,
      outcome: summary.outcome,
      practiceId: summary.practiceId,
      callId: summary.callId,
      durationSeconds: summary.durationSeconds,
      turns: summary.turns,
      transcriptExcerpt: summary.transcriptExcerpt,
    });

    summary.jiraIssueKey = result.issueKey ?? summary.jiraIssueKey;
    summary.jiraIssueUrl = result.issueUrl ?? summary.jiraIssueUrl;
    summary.jiraSyncError = result.error;
    summary.jiraSyncedAt = result.issueKey !== null ? new Date() : summary.jiraSyncedAt;
    summary.updatedAt = new Date();
    await this.summaries.update({ id: summary.id }, {
      jiraIssueKey: summary.jiraIssueKey,
      jiraIssueUrl: summary.jiraIssueUrl,
      jiraSyncError: summary.jiraSyncError,
      jiraSyncedAt: summary.jiraSyncedAt,
      updatedAt: summary.updatedAt,
    });
    return summary;
  }

  private async summarise(
    repo: Repository<PatientRequestEntity> | Repository<HandoffEntity>,
    statuses: readonly string[],
    callId: string,
  ): Promise<CallStatusSummaryDto> {
    const rows = await repo
      .createQueryBuilder('row')
      .select('row.status', 'status')
      .addSelect('COUNT(*)', 'count')
      .where('row.callId = :callId', { callId })
      .groupBy('row.status')
      .getRawMany<{ status: string; count: string | number }>();

    const summary = new CallStatusSummaryDto();
    const byStatus = zeroStatusSummary(statuses);
    let total = 0;
    for (const row of rows) {
      const count = typeof row.count === 'number' ? row.count : Number(row.count);
      byStatus[row.status] = count;
      total += count;
    }
    summary.total = total;
    summary.byStatus = byStatus;
    return summary;
  }

  private async findByExternalId(
    practiceId: string,
    externalCallId: string,
  ): Promise<CallEntity | null> {
    return this.calls.findOne({ where: { practiceId, externalCallId } });
  }
}

function mapResult(call: CallEntity, existing: boolean): CallUpsertResult {
  return { call: mapCall(call), existing };
}
