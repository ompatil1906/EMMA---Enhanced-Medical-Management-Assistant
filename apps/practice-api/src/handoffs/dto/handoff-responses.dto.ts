import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { HandoffEntity, HANDOFF_STATUSES, HandoffStatus } from '../../entities/handoff.entity.js';
import { utcDate } from '../../common/idempotency.util.js';

/** Response for POST /handoffs (`data` payload). */
export class HandoffResponseDto {
  @ApiProperty({ description: 'Internal hand-off UUID' })
  id!: string;

  @ApiProperty({ description: 'Owning practice UUID' })
  practiceId!: string;

  @ApiPropertyOptional({ description: 'Originating call UUID', nullable: true })
  callId!: string | null;

  @ApiProperty({ description: 'Reason for the hand-off' })
  reason!: string;

  @ApiProperty({ description: 'Hand-off destination' })
  destination!: string;

  @ApiProperty({ enum: HANDOFF_STATUSES, description: 'Hand-off lifecycle status' })
  status!: HandoffStatus;

  @ApiProperty({ description: 'Row creation time (UTC)', type: String, format: 'date-time' })
  createdAt!: Date;

  @ApiProperty({ description: 'Row last-update time (UTC)', type: String, format: 'date-time' })
  updatedAt!: Date;
}

export function mapHandoff(entity: HandoffEntity): HandoffResponseDto {
  const response = new HandoffResponseDto();
  response.id = entity.id;
  response.practiceId = entity.practiceId;
  response.callId = entity.callId;
  response.reason = entity.reason;
  response.destination = entity.destination;
  response.status = entity.status;
  response.createdAt = requiredTimestamp(entity.createdAt, 'handoffs.created_at');
  response.updatedAt = requiredTimestamp(entity.updatedAt, 'handoffs.updated_at');
  return response;
}

function requiredTimestamp(value: unknown, column: string): Date {
  const parsed = utcDate(value);
  if (parsed === null) {
    throw new Error(`corrupt timestamp in ${column}`);
  }
  return parsed;
}
