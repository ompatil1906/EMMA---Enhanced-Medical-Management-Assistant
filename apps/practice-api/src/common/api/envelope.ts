/**
 * Success-envelope carry-over. Feature services return an `Envelope` when a
 * route needs `meta` alongside `data` (e.g. idempotent replay responses and
 * paginated lists); the global interceptor wraps everything else as `{ data }`.
 */
export class Envelope<TData, TMeta = Record<string, unknown>> {
  public readonly data: TData;

  public readonly meta?: TMeta;

  constructor(data: TData, meta?: TMeta) {
    this.data = data;
    if (meta !== undefined) {
      this.meta = meta;
    }
  }
}

export type Enveloped<TData, TMeta extends Record<string, unknown> = Record<string, unknown>> =
  | {
      data: TData;
      meta: TMeta;
    }
  | {
      data: TData;
      meta?: TMeta;
    };
