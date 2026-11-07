import {
  ArgumentsHost,
  Catch,
  ExceptionFilter,
  HttpException,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import { Response } from 'express';
import { ApiError } from './api-error.js';
import { ApiErrorCode } from './error-codes.js';
import { isDuplicateKeyError, isForeignKeyViolationError } from '../db-errors.js';
import { QueryFailedError } from 'typeorm';

interface ErrorEnvelope {
  error: {
    code: ApiErrorCode;
    message: string;
    details?: unknown;
  };
}

interface HttpExceptionBody {
  message?: string | string[];
  error?: string;
  statusCode?: number;
}

/**
 * Global exception filter. Produces the canonical error envelope for every
 * failure:
 *
 * ```json
 * { "error": { "code": "NOT_FOUND", "message": "…", "details": … } }
 * ```
 *
 * Codes: VALIDATION_FAILED (400), NOT_FOUND (404), CONFLICT (409),
 * IDEMPOTENCY_KEY_CONFLICT (409), INTERNAL_ERROR (500). Driver duplicate/FK
 * errors are defensive fallbacks — services pre-check foreign keys for 404s.
 */
@Catch()
export class GlobalErrorFilter implements ExceptionFilter {
  private readonly logger = new Logger(GlobalErrorFilter.name);

  public catch(exception: unknown, host: ArgumentsHost): void {
    const response = host.switchToHttp().getResponse<Response>();
    const { status, body } = this.describe(exception);
    if (status >= 500) {
      this.logger.error(
        `Unhandled error: ${exception instanceof Error ? exception.message : String(exception)}`,
      );
    }
    response.status(status).json(body);
  }

  private describe(exception: unknown): { status: number; body: ErrorEnvelope } {
    if (exception instanceof ApiError) {
      return { status: exception.getStatus(), body: this.envelopeOf(exception) };
    }

    if (exception instanceof QueryFailedError) {
      if (isDuplicateKeyError(exception)) {
        return {
          status: 409,
          body: { error: { code: ApiErrorCode.Conflict, message: 'resource already exists' } },
        };
      }
      if (isForeignKeyViolationError(exception)) {
        return {
          status: 409,
          body: {
            error: {
              code: ApiErrorCode.Conflict,
              message: 'operation violates a foreign-key constraint',
            },
          },
        };
      }
      return this.internal();
    }

    if (exception instanceof HttpException) {
      return this.describeHttpException(exception);
    }

    return this.internal();
  }

  private envelopeOf(exception: ApiError): ErrorEnvelope {
    const envelope: ErrorEnvelope = {
      error: { code: exception.code, message: exception.message },
    };
    if (exception.apiDetails !== undefined) {
      envelope.error.details = exception.apiDetails;
    }
    return envelope;
  }

  private describeHttpException(exception: HttpException): { status: number; body: ErrorEnvelope } {
    const status = exception.getStatus();
    const raw = exception.getResponse();
    const body: HttpExceptionBody =
      typeof raw === 'object' && raw !== null ? (raw as HttpExceptionBody) : { message: raw };

    if (status === HttpStatus.NOT_FOUND) {
      return {
        status,
        body: { error: { code: ApiErrorCode.NotFound, message: messageOf(body) } },
      };
    }
    if (status === HttpStatus.BAD_REQUEST) {
      return {
        status,
        body: {
          error: { code: ApiErrorCode.ValidationFailed, message: 'request validation failed', details: body.message },
        },
      };
    }
    if (status === HttpStatus.CONFLICT) {
      return {
        status,
        body: { error: { code: ApiErrorCode.Conflict, message: messageOf(body) } },
      };
    }
    return this.internal();
  }

  private internal(): { status: number; body: ErrorEnvelope } {
    return {
      status: 500,
      body: { error: { code: ApiErrorCode.InternalError, message: 'internal error' } },
    };
  }
}

function messageOf(body: HttpExceptionBody): string {
  const message = body.message;
  if (Array.isArray(message)) {
    return 'request validation failed';
  }
  if (typeof message === 'string' && message.length > 0) {
    return message;
  }
  if (typeof body.error === 'string' && body.error.length > 0) {
    return body.error;
  }
  return 'request failed';
}
