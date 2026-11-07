import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { REQUEST_STATUSES, RequestStatus } from '../../entities/patient-request.entity.js';
import { IsIn, IsISO8601, IsObject, IsOptional, IsString, IsUUID, Length } from 'class-validator';
import { IsJsonPrimitiveTree } from '../../common/validation/json-primitive-tree.constraint.js';

/** POST /api/v1/requests — idempotent patient-request creation. */
export class CreateRequestDto {
  /** Owning practice id (must exist). */
  @ApiProperty({ description: 'UUID of the owning practice' })
  @IsUUID('4', { message: 'practiceId must be a UUID v4' })
  public practiceId!: string;

  /** Originating call id, if the request came from a call (must belong to the same practice). */
  @ApiPropertyOptional({ description: 'UUID of the originating call (same practice)' })
  @IsOptional()
  @IsUUID('4', { message: 'callId must be a UUID v4' })
  public callId?: string;

  /** Request category, e.g. appointment-request, reschedule, cancellation, prescription-query. */
  @ApiProperty({ description: 'Request category', example: 'appointment-request' })
  @IsString({ message: 'requestType must be a string' })
  @Length(1, 64, { message: 'requestType must be 1–64 characters' })
  public requestType!: string;

  /** Structured JSON object of primitives (no medical details). */
  @ApiProperty({
    description:
      'App-validated JSON object of primitives (strings, numbers, booleans, null, arrays, objects; max depth 8; no medical details)',
    example: { preferredDay: 'tuesday', preferredTime: 'morning', notes: 'evening callback preferred' },
  })
  @IsObject({ message: 'structuredPayload must be a JSON object' })
  @IsJsonPrimitiveTree()
  public structuredPayload!: Record<string, unknown>;

  /** Explicit lifecycle status; defaults to received. */
  @ApiPropertyOptional({ enum: REQUEST_STATUSES, description: 'Initial status', default: 'received' })
  @IsOptional()
  @IsIn(REQUEST_STATUSES as unknown as string[], { message: 'status must be one of: received, completed, failed' })
  public status?: RequestStatus;

  /**
   * Body fallback for the idempotency key. The `Idempotency-Key` request
   * header takes precedence when both are present.
   */
  @ApiPropertyOptional({ description: 'Idempotency key (header takes precedence)', example: 'call-123-request-1' })
  @IsOptional()
  @IsString({ message: 'idempotencyKey must be a string' })
  @Length(1, 128, { message: 'idempotencyKey must be 1–128 characters' })
  public idempotencyKey?: string;
}
