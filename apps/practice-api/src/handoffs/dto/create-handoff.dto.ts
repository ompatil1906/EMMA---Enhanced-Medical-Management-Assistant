import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { HANDOFF_STATUSES, HandoffStatus } from '../../entities/handoff.entity.js';
import { IsIn, IsOptional, IsString, IsUUID, Length } from 'class-validator';

/** POST /api/v1/handoffs — record that a human hand-off was requested. */
export class CreateHandoffDto {
  /** Owning practice id (must exist). */
  @ApiProperty({ description: 'UUID of the owning practice' })
  @IsUUID('4', { message: 'practiceId must be a UUID v4' })
  public practiceId!: string;

  /** Originating call id, when known (must belong to the same practice). */
  @ApiPropertyOptional({ description: 'UUID of the originating call (same practice)' })
  @IsOptional()
  @IsUUID('4', { message: 'callId must be a UUID v4' })
  public callId?: string;

  /** Why the hand-off was requested, e.g. escalation, operator requested. */
  @ApiProperty({ description: 'Reason for the hand-off', example: 'caller asked for a human' })
  @IsString({ message: 'reason must be a string' })
  @Length(1, 255, { message: 'reason must be 1–255 characters' })
  public reason!: string;

  /** Where the call is being handed to, e.g. a desk, queue or phone number. */
  @ApiProperty({ description: 'Hand-off destination', example: 'Reception desk' })
  @IsString({ message: 'destination must be a string' })
  @Length(1, 255, { message: 'destination must be 1–255 characters' })
  public destination!: string;

  /**
   * Lifecycle status. Only explicit caller input may mark a hand-off as
   * completed: this API never asserts that a transfer actually completed on
   * the caller's behalf (the default is `requested`).
   */
  @ApiPropertyOptional({ enum: HANDOFF_STATUSES, description: 'Initial status', default: 'requested' })
  @IsOptional()
  @IsIn(HANDOFF_STATUSES as unknown as string[], {
    message: 'status must be one of: requested, initiated, completed, failed',
  })
  public status?: HandoffStatus;
}
