import { HttpException } from '@nestjs/common';
import { ApiErrorCode } from './error-codes.js';

/** Nested `details` entries: class-validator messages or extra context. */
export type ApiErrorDetails = unknown;

/**
 * Business exception carrying the canonical error `code` for the global error
 * filter. Always produces an HTTP status + envelope, never leaks internals.
 */
export class ApiError extends HttpException {
  public readonly code: ApiErrorCode;
  public readonly apiDetails?: ApiErrorDetails;

  constructor(
    status: number,
    code: ApiErrorCode,
    message: string,
    details?: ApiErrorDetails,
  ) {
    super(message, status);
    this.code = code;
    this.apiDetails = details;
  }

  public static notFound(message: string): ApiError {
    return new ApiError(404, ApiErrorCode.NotFound, message);
  }

  public static conflict(message: string, details?: ApiErrorDetails): ApiError {
    return new ApiError(409, ApiErrorCode.Conflict, message, details);
  }

  public static idempotencyConflict(
    message: string,
    details?: ApiErrorDetails,
  ): ApiError {
    return new ApiError(409, ApiErrorCode.IdempotencyKeyConflict, message, details);
  }

  public static validation(message: string, details?: ApiErrorDetails): ApiError {
    return new ApiError(400, ApiErrorCode.ValidationFailed, message, details);
  }

  public static internal(message: string, details?: ApiErrorDetails): ApiError {
    return new ApiError(500, ApiErrorCode.InternalError, message, details);
  }
}
