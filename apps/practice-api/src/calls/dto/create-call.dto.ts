import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { IsISO8601, IsOptional, IsString, IsUUID, Length } from 'class-validator';

/**
 * POST /api/v1/calls — create a call record, or idempotently return the
 * existing one when (practiceId, externalCallId) already exists.
 */
export class CreateCallDto {
  /** Owning practice id (must exist). */
  @ApiProperty({ description: 'UUID of the owning practice', example: '3f7d1ed8-58ac-4e1a-97e3-759d8f2a4b31' })
  @IsUUID('4', { message: 'practiceId must be a UUID v4' })
  public practiceId!: string;

  /**
   * Provider call id (e.g. Exotel call sid). Unique per practice: repeating it
   * with the same practiceId replays the original 201-created record (HTTP 200
   * + `meta.existing = true`).
   */
  @ApiProperty({
    description: 'External provider call id; idempotent per practice',
    example: 'EXOTEL_3b1f0c9a77',
  })
  @IsString({ message: 'externalCallId must be a string' })
  @Length(1, 128, { message: 'externalCallId must be 1–128 characters' })
  public externalCallId!: string;

  /** Call start timestamp (ISO 8601, UTC). Defaults to now. */
  @ApiPropertyOptional({ description: 'ISO 8601 start time (UTC)', example: '2026-11-06T10:30:00.000Z' })
  @IsOptional()
  @IsISO8601({ strict: false }, { message: 'startedAt must be an ISO 8601 timestamp' })
  public startedAt?: string;
}
