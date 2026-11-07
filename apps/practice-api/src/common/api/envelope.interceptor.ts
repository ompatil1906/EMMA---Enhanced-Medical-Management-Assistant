import { CallHandler, ExecutionContext, Injectable, NestInterceptor } from '@nestjs/common';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { Envelope } from './envelope.js';

/** Serialised success envelope shape: `{ data, meta? }` with `meta` optional. */
export interface SuccessEnvelopeBody<MetaT = Record<string, unknown>> {
  data: unknown;
  meta?: MetaT;
}

/**
 * Wraps every successful response in the canonical envelope
 * `{ data: ..., meta?: ... }`. Handlers emit an `Envelope` themselves when a
 * `meta` block is required (idempotent replays, paginated lists).
 *
 * Pass-through exceptions are not affected: only 2xx flows through here.
 */
@Injectable()
export class EnvelopeInterceptor implements NestInterceptor {
  public intercept(_context: ExecutionContext, next: CallHandler): Observable<unknown> {
    return next.handle().pipe(
      map((body: unknown): SuccessEnvelopeBody | unknown => {
        if (body instanceof Envelope) {
          const bodyMeta = body.meta as Record<string, unknown> | undefined;
          if (bodyMeta !== undefined) {
            return { data: body.data, meta: bodyMeta };
          }
          return { data: body.data };
        }
        return { data: body };
      }),
    );
  }
}
