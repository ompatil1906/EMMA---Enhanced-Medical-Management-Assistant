/** Canonical API error codes surfaced in every error envelope. */
export const ApiErrorCode = {
  ValidationFailed: 'VALIDATION_FAILED',
  NotFound: 'NOT_FOUND',
  Conflict: 'CONFLICT',
  IdempotencyKeyConflict: 'IDEMPOTENCY_KEY_CONFLICT',
  InternalError: 'INTERNAL_ERROR',
} as const;

export type ApiErrorCode = (typeof ApiErrorCode)[keyof typeof ApiErrorCode];
