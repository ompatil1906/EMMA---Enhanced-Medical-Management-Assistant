import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { REQUEST_STATUSES, RequestStatus } from '../../entities/patient-request.entity.js';
import { PaginationQueryDto } from '../../common/pagination.js';
import { IsIn, IsISO8601, IsOptional, IsString, Length } from 'class-validator';

/**
 * GET /api/v1/practices/:practiceId/requests — paginated history with filters.
 * `dateFrom`/`dateTo` filter on `createdAt` (UTC); bare `YYYY-MM-DD` values are
 * expanded to the full UTC day (dateTo inclusive of 23:59:59.999Z).
 */
export class ListRequestsQueryDto extends PaginationQueryDto {
  @ApiPropertyOptional({ description: 'Exact requestType match', example: 'appointment-request' })
  @IsOptional()
  @IsString({ message: 'requestType must be a string' })
  @Length(1, 64, { message: 'requestType must be 1–64 characters' })
  public requestType?: string;

  @ApiPropertyOptional({ enum: REQUEST_STATUSES, description: 'Exact status match' })
  @IsOptional()
  @IsIn(REQUEST_STATUSES as unknown as string[], {
    message: 'status must be one of: received, completed, failed',
  })
  public status?: RequestStatus;

  @ApiPropertyOptional({ description: 'created_at ≥ this (UTC). Bare dates expand to 00:00:00.000Z', example: '2026-11-01' })
  @IsOptional()
  @IsString({ message: 'dateFrom must be an ISO 8601 date or timestamp (UTC)' })
  public dateFrom?: string;

  @ApiPropertyOptional({ description: 'created_at ≤ this (UTC). Bare dates expand to 23:59:59.999Z', example: '2026-11-06' })
  @IsOptional()
  @IsString({ message: 'dateTo must be an ISO 8601 date or timestamp (UTC)' })
  public dateTo?: string;
}

/** Resolved UTC createdAt bounds for list queries. */
export interface RequestDateRange {
  from: Date | undefined;
  to: Date | undefined;
}

/**
 * Expands validated ISO inputs to concrete UTC Date bounds; a bare
 * `YYYY-MM-DD` value is expanded to the whole UTC day.
 *
 * NOTE: this function trusts that class-validator ran first; it throws
 * ApiError anyway when a value slips through malformed.
 */
export function resolveRequestQueryDateRange(query: Pick<ListRequestsQueryDto, 'dateFrom' | 'dateTo'>): RequestDateRange {
  return {
    from: query.dateFrom ? resolveBound(query.dateFrom, false) : undefined,
    to: query.dateTo ? resolveBound(query.dateTo, true) : undefined,
  };
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Kept separate from the regex to make the intent testable in isolation. */
export function isIsoDateOnly(value: string): boolean {
  return DATE_ONLY.test(value);
}

function resolveBound(value: string, inclusiveEnd: boolean): Date {
  const normalised = isIsoDateOnly(value)
    ? `${value}T${inclusiveEnd ? '23:59:59.999Z' : '00:00:00.000Z'}`
    : value;
  const parsed = new Date(normalised);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`invalid date bound: ${value}`);
  }
  return parsed;
}
