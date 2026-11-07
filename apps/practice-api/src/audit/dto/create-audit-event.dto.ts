import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { IsJsonPrimitiveTree } from '../../common/validation/json-primitive-tree.constraint.js';
import { IsOptional, IsString, IsUUID, Length } from 'class-validator';

/** POST /api/v1/audit-events — append (never update/delete) an audit record. */
export class CreateAuditEventDto {
  /** Owning practice id (must exist). */
  @ApiProperty({ description: 'UUID of the owning practice' })
  @IsUUID('4', { message: 'practiceId must be a UUID v4' })
  public practiceId!: string;

  /** Related call id, when known (must belong to the same practice). */
  @ApiPropertyOptional({ description: 'UUID of the related call (same practice)' })
  @IsOptional()
  @IsUUID('4', { message: 'callId must be a UUID v4' })
  public callId?: string;

  /** Machine-readable event type, e.g. request.created, call.escalated. */
  @ApiProperty({ description: 'Event type tag', example: 'request.escalated' })
  @IsString({ message: 'eventType must be a string' })
  @Length(1, 64, { message: 'eventType must be 1–64 characters' })
  public eventType!: string;

  /** Structured JSON object of primitives (no medical details), never mutated after write. */
  @ApiProperty({
    description:
      'App-validated JSON object of primitives (strings, numbers, booleans, null, arrays, objects; max depth 8; no medical details)',
    example: { requestId: 'req_01', reason: 'patient asked for prescription status' },
  })
  @IsJsonPrimitiveTree()
  public eventPayload!: Record<string, unknown>;
}
