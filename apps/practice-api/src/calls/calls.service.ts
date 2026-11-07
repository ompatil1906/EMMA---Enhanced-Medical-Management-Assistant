import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { CallEntity } from '../entities/call.entity.js';
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

/** Result of the idempotent create-by-external-call-id flow. */
export interface CallUpsertResult {
  call: CallResponseDto;
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
