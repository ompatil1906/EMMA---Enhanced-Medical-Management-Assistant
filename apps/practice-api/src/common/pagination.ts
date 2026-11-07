import { Transform } from 'class-transformer';
import { IsInt, IsOptional, Max, Min } from 'class-validator';

/**
 * Shared pagination contract for list routes. Query params arrive as strings;
 * both are transformed and validated here so handlers receive real numbers.
 * `limit` is capped at 100.
 */
export class PaginationQueryDto {
  /** 1-based page number. */
  @IsOptional()
  @Transform(pageTransform, { toClassOnly: true })
  @IsInt({ message: 'page must be an integer ≥ 1' })
  @Min(1, { message: 'page must be an integer ≥ 1' })
  page: number = 1;

  /** Page size, 1–100. */
  @IsOptional()
  @Transform(pageTransform, { toClassOnly: true })
  @IsInt({ message: 'limit must be an integer between 1 and 100' })
  @Min(1, { message: 'limit must be an integer between 1 and 100' })
  @Max(100, { message: 'limit must be an integer between 1 and 100' })
  limit: number = 20;
}

function pageTransform({ value }: { value: unknown }): number | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' && typeof value !== 'number') {
    return undefined; // class-validator rejects non-numeric shapes below
  }
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isNaN(numeric) ? (Number.NaN as number) : numeric;
}

/** Pagination metadata block for `{ data, meta }` list envelopes. */
export interface PaginationMeta {
  page: number;
  limit: number;
  total: number;
  nextPage?: number;
}

export function buildPaginationMeta(page: number, limit: number, total: number): PaginationMeta {
  const meta: PaginationMeta = { page, limit, total };
  const nextPage = page * limit < total ? page + 1 : undefined;
  if (nextPage !== undefined) {
    meta.nextPage = nextPage;
  }
  return meta;
}
