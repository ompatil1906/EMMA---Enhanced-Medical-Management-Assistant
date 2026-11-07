import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { REQUEST_STATUSES, PatientRequestEntity, RequestStatus } from '../../entities/patient-request.entity.js';
import { utcDate } from '../../common/idempotency.util.js';

/** Responses for POST /requests and the list route (`data` payload items). */
export class PatientRequestResponseDto {
  @ApiProperty({ description: 'Internal request UUID' })
  id!: string;

  @ApiProperty({ description: 'Owning practice UUID' })
  practiceId!: string;

  @ApiPropertyOptional({ description: 'Originating call UUID', nullable: true })
  callId!: string | null;

  @ApiProperty({ description: 'Request category' })
  requestType!: string;

  @ApiProperty({ enum: REQUEST_STATUSES, description: 'Request lifecycle status' })
  status!: RequestStatus;

  @ApiProperty({ type: Object, description: 'Structured JSON object of primitives' })
  structuredPayload!: Record<string, unknown>;

  @ApiPropertyOptional({ description: 'Idempotency key as passed', nullable: true })
  idempotencyKey!: string | null;

  @ApiProperty({ description: 'SHA-256 (hex) of the canonical payload' })
  idempotencyPayloadHash!: string;

  @ApiProperty({ description: 'Row creation time (UTC)', type: String, format: 'date-time' })
  createdAt!: Date;

  @ApiProperty({ description: 'Row last-update time (UTC)', type: String, format: 'date-time' })
  updatedAt!: Date;
}

export function mapRequest(entity: PatientRequestEntity): PatientRequestResponseDto {
  const response = new PatientRequestResponseDto();
  response.id = entity.id;
  response.practiceId = entity.practiceId;
  response.callId = entity.callId;
  response.requestType = entity.requestType;
  response.status = entity.status;
  response.structuredPayload = entity.structuredPayload;
  response.idempotencyKey = entity.idempotencyKey;
  response.idempotencyPayloadHash = entity.idempotencyPayloadHash;
  response.createdAt = requiredDate(entity.createdAt, 'patient_requests.created_at');
  response.updatedAt = requiredDate(entity.updatedAt, 'patient_requests.updated_at');
  return response;
}

function requiredDate(value: unknown, column: string): Date {
  const parsed = utcDate(value);
  if (parsed === null) {
    throw new Error(`corrupt timestamp in ${column}`);
  }
  return parsed;
}
