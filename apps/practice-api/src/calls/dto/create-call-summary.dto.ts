import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { Type } from 'class-transformer';
import {
  IsArray,
  IsBoolean,
  IsIn,
  IsInt,
  IsNotEmpty,
  IsObject,
  IsOptional,
  IsString,
  IsUUID,
  Length,
  Max,
  Min,
  ValidateNested,
} from 'class-validator';

/** One caller detail block as normalised by the voice gateway. */
export class CallerDetailsDto {
  @ApiPropertyOptional({ description: 'Caller first name captured on the intake form' })
  @IsOptional()
  @IsString()
  @Length(1, 80)
  public firstName?: string;

  @ApiPropertyOptional({ description: 'Caller last name captured on the intake form' })
  @IsOptional()
  @IsString()
  @Length(1, 80)
  public lastName?: string;

  @ApiPropertyOptional({ description: 'Contact number captured on the intake form' })
  @IsOptional()
  @IsString()
  @Length(1, 40)
  public mobile?: string;

  @ApiPropertyOptional({ description: 'Reason for calling as stated on the intake form' })
  @IsOptional()
  @IsString()
  @Length(1, 300)
  public reason?: string;
}

/** One operational task derived from a captured request. */
export class CallTaskDto {
  @ApiPropertyOptional({ description: 'Task category (appointment, prescription, callback, …)' })
  @IsOptional()
  @IsString()
  @Length(1, 64)
  public type?: string;

  @ApiPropertyOptional({ description: 'Admin request type the task came from' })
  @IsOptional()
  @IsString()
  @Length(1, 64)
  public requestType?: string;

  @ApiPropertyOptional({ description: 'Human-readable task title' })
  @IsOptional()
  @IsString()
  @Length(1, 255)
  public title?: string;

  @ApiPropertyOptional({ description: 'Task status (captured, submitted, failed, …)' })
  @IsOptional()
  @IsString()
  @Length(1, 64)
  public status?: string;

  @ApiPropertyOptional({ description: 'Structured details captured for the task' })
  @IsOptional()
  @IsObject()
  public details?: Record<string, string>;

  @ApiPropertyOptional({ description: 'Why a submission failed, when it did' })
  @IsOptional()
  @IsString()
  @Length(1, 255)
  public failureReason?: string;
}

export const CALL_SUMMARY_OUTCOMES = ['completed', 'emergency', 'failed', 'abandoned'] as const;
export type CallSummaryOutcome = (typeof CALL_SUMMARY_OUTCOMES)[number];

/**
 * POST /api/v1/calls/:id/summary — the deterministic end-of-call record plus the
 * tasks it produced. One summary per call: replaying returns the stored record
 * (and never creates a second Jira issue).
 */
export class CreateCallSummaryDto {
  @ApiProperty({ description: 'UUID of the owning practice', example: '3f7d1ed8-58ac-4e1a-97e3-759d8f2a4b31' })
  @IsUUID('4', { message: 'practiceId must be a UUID v4' })
  public practiceId!: string;

  @ApiProperty({ description: 'One-line summary headline', example: 'Appointment request — Sarah J.' })
  @IsString()
  @IsNotEmpty()
  @Length(1, 255)
  public headline!: string;

  @ApiProperty({ description: 'Plain-text call summary (the record shown to staff)' })
  @IsString()
  @Length(1, 8000)
  public summary!: string;

  @ApiPropertyOptional({ description: 'Summary lines as displayed', type: [String] })
  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  public bullets?: string[];

  @ApiPropertyOptional({ description: 'Tasks derived from captured requests', type: [CallTaskDto] })
  @IsOptional()
  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => CallTaskDto)
  public tasks?: CallTaskDto[];

  @ApiPropertyOptional({ description: 'Intake form captured before the call', type: CallerDetailsDto })
  @IsOptional()
  @ValidateNested()
  @Type(() => CallerDetailsDto)
  public caller?: CallerDetailsDto;

  @ApiProperty({ enum: CALL_SUMMARY_OUTCOMES, description: 'How the call ended' })
  @IsIn(CALL_SUMMARY_OUTCOMES, { message: `outcome must be one of: ${CALL_SUMMARY_OUTCOMES.join(', ')}` })
  public outcome!: CallSummaryOutcome;

  @ApiPropertyOptional({ description: 'Call duration in seconds when known' })
  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(86_400)
  public durationSeconds?: number;

  @ApiPropertyOptional({ description: 'Number of conversation turns' })
  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(10_000)
  public turns?: number;

  @ApiPropertyOptional({ description: 'Short transcript excerpt (only when explicitly enabled)' })
  @IsOptional()
  @IsString()
  @Length(0, 4000)
  public transcriptExcerpt?: string;

  @ApiPropertyOptional({
    description: 'Mirror this summary into the external task platform (default true)',
    default: true,
  })
  @IsOptional()
  @IsBoolean()
  public syncJira?: boolean;
}

/** Manual re-sync request for staff. */
export class SyncJiraDto {
  @ApiProperty({ description: 'UUID of the call whose summary should be re-synced' })
  @IsUUID('4', { message: 'callId must be a UUID v4' })
  public callId!: string;
}
