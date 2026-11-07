import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { AuditEventEntity } from '../../entities/audit-event.entity.js';
import { utcDate } from '../../common/idempotency.util.js';

/**
 * Response for POST /audit-events. Append-only by design: there is no
 * `updatedAt` field because audit rows are written exactly once.
 */
export class AuditEventResponseDto {
  @ApiProperty({ description: 'Internal audit event UUID' })
  id!: string;

  @ApiProperty({ description: 'Owning practice UUID' })
  practiceId!: string;

  @ApiPropertyOptional({ description: 'Related call UUID', nullable: true })
  callId!: string | null;

  @ApiProperty({ description: 'Machine-readable event type' })
  eventType!: string;

  @ApiProperty({ type: Object, description: 'Structured JSON object of primitives' })
  eventPayload!: Record<string, unknown>;

  @ApiProperty({ description: 'Row creation time (UTC)', type: String, format: 'date-time' })
  createdAt!: Date;
}

export function mapAuditEvent(entity: AuditEventEntity): AuditEventResponseDto {
  const response = new AuditEventResponseDto();
  response.id = entity.id;
  response.practiceId = entity.practiceId;
  response.callId = entity.callId;
  response.eventType = entity.eventType;
  response.eventPayload = entity.eventPayload;
  const createdAt = utcDate(entity.createdAt);
  if (createdAt === null) {
    throw new Error('corrupt timestamp in audit_events.created_at');
  }
  response.createdAt = createdAt;
  return response;
}
