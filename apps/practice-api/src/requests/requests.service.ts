import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { DataSource, DeepPartial, FindOptionsWhere, QueryRunner, Repository, type QueryDeepPartialEntity } from 'typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { PatientRequestEntity } from '../entities/patient-request.entity.js';
import { ApiError } from '../common/api/api-error.js';
import { Envelope } from '../common/api/envelope.js';
import { isDuplicateKeyError } from '../common/db-errors.js';
import { canonicalPayloadHash } from '../common/idempotency.util.js';
import { buildPaginationMeta, PaginationMeta } from '../common/pagination.js';
import { CreateRequestDto } from './dto/create-request.dto.js';
import {
  ListRequestsQueryDto,
  resolveRequestQueryDateRange,
  RequestDateRange,
} from './dto/list-requests-query.dto.js';
import { mapRequest, PatientRequestResponseDto } from './dto/request-responses.dto.js';
import { PracticesService } from '../practices/practices.service.js';

/** Outcome of an (idempotent) request creation flow. */
export interface RequestUpsertResult {
  request: PatientRequestResponseDto;
  existing: boolean;
}

const QUERY_ALIAS = 'r';
/** Hard cap for the duplicate-race reconciliation loop. */
const MAX_RACE_ATTEMPTS = 2;

@Injectable()
export class RequestsService {
  constructor(
    private readonly practices: PracticesService,
    private readonly dataSource: DataSource,
    @InjectRepository(PatientRequestEntity)
    private readonly requests: Repository<PatientRequestEntity>,
    @InjectRepository(CallEntity)
    private readonly calls: Repository<CallEntity>,
  ) {}

  /**
   * Idempotency key normalisation: the `Idempotency-Key` header takes
   * precedence over a body idempotencyKey; a whitespace-only value means "no
   * key". Keys longer than 128 characters are a 400. Ownership scope is the
   * practice, enforced by the composite unique constraint
   * (practice_id, idempotency_key).
   */
  public static normalizeKey(header: string | undefined, bodyKey: string | undefined | null): string | null {
    const raw = header ?? bodyKey ?? null;
    if (raw === null) return null;
    const trimmed = raw.trim();
    if (trimmed.length === 0) return null;
    if (trimmed.length > 128) {
      throw ApiError.validation('Idempotency-Key must be at most 128 characters');
    }
    return trimmed;
  }

  /**
   * Creation flow:
   *
   * 1. plain read (fast path): hit ⇒ verify payload hash, then replay (200)
   *    or IDEMPOTENCY_KEY_CONFLICT (409).
   * 2. transaction: SELECT behind a lock on MySQL (`FOR UPDATE`); SQLite uses
   *    the same flow without the row lock (single-writer database). On
   *    duplicate from a concurrent racer, the transaction is released and the
   *    flow re-reads with a fresh snapshot (bounded attempts).
   */
  public async create(dto: CreateRequestDto, headerKey: string | undefined): Promise<RequestUpsertResult> {
    await this.practices.requirePractice(dto.practiceId);
    const callId = await this.resolveCallScope(dto.practiceId, dto.callId);
    const payloadHash = canonicalPayloadHash(dto.structuredPayload);
    const key = RequestsService.normalizeKey(headerKey, dto.idempotencyKey ?? null);
    const now = new Date();

    if (key === null) {
      const created = buildEntity(dto, callId, payloadHash, null, now);
      await this.requests.insert(created as QueryDeepPartialEntity<PatientRequestEntity>);
      return { request: mapRequest(created), existing: false };
    }

    // Fast path outside the transaction.
    const existingRow = await this.findOneByKey(dto.practiceId, key);
    if (existingRow !== null) {
      return this.replayOrConflict(existingRow, payloadHash);
    }

    for (let attempt = 1; attempt <= MAX_RACE_ATTEMPTS; attempt++) {
      const runner = this.dataSource.createQueryRunner();
      try {
        await runner.startTransaction();
        const txRepo = runner.manager.getRepository(PatientRequestEntity);
        const locked = await this.selectByKey(txRepo, runner, dto.practiceId, key);
        if (locked !== null) {
          await runner.commitTransaction();
          return this.replayOrConflict(locked, payloadHash);
        }

        const created = buildEntity(dto, callId, payloadHash, key, now);
        await txRepo.insert(created as QueryDeepPartialEntity<PatientRequestEntity>);
        await runner.commitTransaction();
        return { request: mapRequest(created), existing: false };
      } catch (error) {
        await safeRollback(runner);
        if (isDuplicateKeyError(error) && attempt < MAX_RACE_ATTEMPTS) {
          continue; // racer committed first; loop re-reads with a fresh snapshot
        }
        if (isDuplicateKeyError(error)) {
          const reconciled = await this.findOneByKey(dto.practiceId, key);
          if (reconciled !== null) {
            return this.replayOrConflict(reconciled, payloadHash);
          }
        }
        throw error;
      } finally {
        await runner.release();
      }
    }

    // Unreachable in practice (last attempt reconciles), kept for exhaustiveness.
    throw ApiError.internal('could not resolve an idempotency race for the given key');
  }

  /**
   * Paginated + filtered history for one practice: 404 when the practice does
   * not exist; exactly two queries (page + total). Filtered columns:
   * requestType, status, createdAt range in UTC.
   */
  public async list(
    practiceId: string,
    query: ListRequestsQueryDto,
  ): Promise<Envelope<PatientRequestResponseDto[], PaginationMeta>> {
    await this.practices.requirePractice(practiceId);
    let range: RequestDateRange;
    try {
      range = resolveRequestQueryDateRange(query);
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('invalid date bound')) {
        throw ApiError.validation(error.message);
      }
      throw error;
    }

    const listQuery = this.buildListQuery(practiceId, query, range.from, range.to)
      .orderBy(`${QUERY_ALIAS}.createdAt`, 'DESC')
      .skip((query.page - 1) * query.limit)
      .take(query.limit);
    const countQuery = this.buildListQuery(practiceId, query, range.from, range.to);

    const [rows, total] = await Promise.all([listQuery.getMany(), countQuery.getCount()]);
    const data = rows.map(mapRequest);
    return new Envelope(data, buildPaginationMeta(query.page, query.limit, total));
  }

  private buildListQuery(
    practiceId: string,
    query: ListRequestsQueryDto,
    from: Date | undefined,
    to: Date | undefined,
  ) {
    const qb = this.requests
      .createQueryBuilder(QUERY_ALIAS)
      .where(`${QUERY_ALIAS}.practiceId = :practiceId`, { practiceId });

    if (query.requestType !== undefined) {
      qb.andWhere(`${QUERY_ALIAS}.requestType = :requestType`, { requestType: query.requestType });
    }
    if (query.status !== undefined) {
      qb.andWhere(`${QUERY_ALIAS}.status = :status`, { status: query.status });
    }
    if (from !== undefined) {
      qb.andWhere(`${QUERY_ALIAS}.createdAt >= :from`, { from });
    }
    if (to !== undefined) {
      qb.andWhere(`${QUERY_ALIAS}.createdAt <= :to`, { to });
    }
    return qb;
  }

  private async selectByKey(
    repo: Repository<PatientRequestEntity>,
    runner: QueryRunner,
    practiceId: string,
    key: string,
  ): Promise<PatientRequestEntity | null> {
    const where: FindOptionsWhere<PatientRequestEntity> = { practiceId, idempotencyKey: key };
    if (this.supportsRowLocks) {
      return repo.findOne({ where, lock: { mode: 'pessimistic_write' } });
    }
    // SQLite: single-writer engine; the unique constraint is the lock.
    void runner;
    return repo.findOne({ where });
  }

  private get supportsRowLocks(): boolean {
    return this.dataSource.options.type === 'mysql';
  }

  /** Same key + same payload hash ⇒ replay (200 existing). Mismatch ⇒ 409. */
  private replayOrConflict(entity: PatientRequestEntity, payloadHash: string): RequestUpsertResult {
    if (entity.idempotencyPayloadHash !== payloadHash) {
      throw ApiError.idempotencyConflict(
        'idempotency key already used with a different structured payload',
        { idempotencyKey: entity.idempotencyKey },
      );
    }
    return { request: mapRequest(entity), existing: true };
  }

  private async findOneByKey(
    practiceId: string,
    key: string,
  ): Promise<PatientRequestEntity | null> {
    return this.requests.findOne({ where: { practiceId, idempotencyKey: key } });
  }

  /**
   * Call scoping: when a callId is supplied it must exist and belong to the
   * same practice (requests never cross practices). Produces a 404 otherwise.
   */
  private async resolveCallScope(practiceId: string, callId: string | undefined): Promise<string | null> {
    if (callId === undefined) return null;
    const call = await this.calls.findOneBy({ id: callId });
    if (call === null || call.practiceId !== practiceId) {
      throw ApiError.notFound(`call "${callId}" does not exist for this practice`);
    }
    return callId;
  }
}

async function safeRollback(runner: QueryRunner): Promise<void> {
  try {
    await runner.rollbackTransaction();
  } catch {
    // Driver surface active on release; rollback is best-effort here.
  }
}

function buildEntity(
  dto: CreateRequestDto,
  callId: string | null,
  payloadHash: string,
  key: string | null,
  now: Date,
): PatientRequestEntity {
  const entity = new PatientRequestEntity();
  entity.id = crypto.randomUUID();
  entity.practiceId = dto.practiceId;
  entity.callId = callId;
  entity.requestType = dto.requestType;
  entity.status = dto.status ?? 'received';
  entity.structuredPayload = dto.structuredPayload;
  entity.idempotencyKey = key;
  entity.idempotencyPayloadHash = payloadHash;
  entity.createdAt = now;
  entity.updatedAt = now;
  return entity;
}
