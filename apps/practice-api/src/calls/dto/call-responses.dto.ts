import { ApiProperty } from '@nestjs/swagger';
import { CALL_STATUSES, CallStatus } from '../../entities/call.entity.js';
import { utcDate } from '../../common/idempotency.util.js';

/** Responses for POST /calls and GET /calls/:id (the `data` payload). */
export class CallResponseDto {
  @ApiProperty({ description: 'Internal call UUID' })
  id!: string;

  @ApiProperty({ description: 'Owning practice UUID' })
  practiceId!: string;

  @ApiProperty({ description: 'Provider call id (unique per practice)' })
  externalCallId!: string;

  @ApiProperty({ enum: CALL_STATUSES, description: 'Call lifecycle status' })
  status!: CallStatus;

  @ApiProperty({ description: 'Call start time (UTC)', type: String, format: 'date-time', nullable: true })
  startedAt!: Date | null;

  @ApiProperty({ description: 'Call end time (UTC)', type: String, format: 'date-time', nullable: true })
  endedAt!: Date | null;

  @ApiProperty({ description: 'Duration in seconds once the call has ended', type: Number, nullable: true })
  durationSeconds!: number | null;

  @ApiProperty({ description: 'Row creation time (UTC)', type: String, format: 'date-time' })
  createdAt!: Date;

  @ApiProperty({ description: 'Row last-update time (UTC)', type: String, format: 'date-time' })
  updatedAt!: Date;
}

/** `{ total, byStatus }` block used by GET /calls/:id. */
export class CallStatusSummaryDto {
  @ApiProperty({ description: 'Total number of related rows' })
  total!: number;

  @ApiProperty({ type: Object, description: 'Count per status (missing statuses count as 0)' })
  byStatus!: Record<string, number>;
}

/** Container for the two summary blocks. */
export class CallSummariesDto {
  @ApiProperty({ type: CallStatusSummaryDto }) declare requests: CallStatusSummaryDto;

  @ApiProperty({ type: CallStatusSummaryDto }) declare handoffs: CallStatusSummaryDto;
}

/** GET /calls/:id payload with grouped request/handoff summaries. */
export class CallWithSummariesDto extends CallResponseDto {
  @ApiProperty({ type: CallStatusSummaryDto, description: 'Requests attached to this call' })
  declare summaries: CallSummariesDto;
}

/** Passenger mapping entity -> response DTO (timestamp-tolerant). */
export function mapCall(entity: {
  id: string;
  practiceId: string;
  externalCallId: string;
  status: string;
  startedAt: unknown;
  endedAt: unknown;
  durationSeconds: unknown;
  createdAt: unknown;
  updatedAt: unknown;
}): CallResponseDto {
  const response = new CallResponseDto();
  response.id = entity.id;
  response.practiceId = entity.practiceId;
  response.externalCallId = entity.externalCallId;
  response.status = entity.status as CallStatus;
  response.startedAt = optionalDate(entity.startedAt);
  response.endedAt = optionalDate(entity.endedAt);
  response.durationSeconds = optionalInt(entity.durationSeconds);
  response.createdAt = requiredDate(entity.createdAt, 'calls.created_at');
  response.updatedAt = requiredDate(entity.updatedAt, 'calls.updated_at');
  return response;
}

/** Deterministic zero-filled status buckets. */
export function zeroStatusSummary(statuses: readonly string[]): Record<string, number> {
  const byStatus: Record<string, number> = {};
  for (const status of statuses) {
    byStatus[status] = 0;
  }
  return byStatus;
}

function optionalDate(value: unknown): Date | null {
  if (value === null || value === undefined) return null;
  return requiredDate(value, 'calls timestamp');
}

function optionalInt(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number' && Number.isInteger(value)) return value;
  const parsed = Number(value);
  if (Number.isInteger(parsed)) return parsed;
  throw new Error('duration_seconds is not an integer');
}

function requiredDate(value: unknown, column: string): Date {
  const parsed = utcDate(value);
  if (parsed === null) {
    throw new Error(`corrupt timestamp in ${column}`);
  }
  return parsed;
}
