import { createTestApp, BootedTestApp } from '../helpers/app.factory.js';
import { PRACTICE, seedPractices } from '../helpers/fixtures.js';
import { PatientRequestEntity } from '../../src/entities/patient-request.entity.js';
import { CallEntity } from '../../src/entities/call.entity.js';

interface TableEntry {
  name: string;
}

interface ColumnEntry {
  name: string;
  notnull: number;
  dflt_value?: string | null;
}

interface IndexEntry {
  name: string;
}

function buildCall(externalCallId: string): Partial<CallEntity> {
  const now = new Date();
  return {
    id: randomUUIDCall(),
    practiceId: PRACTICE.id,
    externalCallId,
    status: 'in-progress',
    startedAt: now,
    endedAt: null,
    durationSeconds: null,
    createdAt: now,
    updatedAt: now,
  };
}

function buildRequest(overrides: { idempotencyKey: string | null; practiceId?: string }): Partial<PatientRequestEntity> {
  const now = new Date();
  return {
    id: randomUUIDCall(),
    practiceId: overrides.practiceId ?? PRACTICE.id,
    callId: null,
    requestType: 'appointment-request',
    status: 'received',
    structuredPayload: { intent: 'constraint-probe' },
    idempotencyKey: overrides.idempotencyKey,
    idempotencyPayloadHash: '0'.repeat(64),
    createdAt: now,
    updatedAt: now,
  };
}

function randomUUIDCall(): string {
  return globalThis.crypto.randomUUID();
}

/** Persistence constraints: unique indexes, FK violations, schema shape. */
describe('persistence constraints (migrations-built schema)', () => {
  let ctx: BootedTestApp;
  let requestsRepo: { insert: (entity: Partial<PatientRequestEntity>) => Promise<unknown> };
  let callsRepo: { insert: (entity: Partial<CallEntity>) => Promise<unknown> };

  beforeAll(async () => {
    ctx = await createTestApp();
    await seedPractices(ctx);
    // Reuse the repositories annotated inside the app's request DTO wiring by
    // grabbing them straight from the data source (tables exist post-migration).
    requestsRepo = {
      insert: (entity: Partial<PatientRequestEntity>): Promise<unknown> =>
        ctx.dataSource
          .getRepository(PatientRequestEntity)
          .insert(entity as PatientRequestEntity),
    };
    callsRepo = {
      insert: (entity: Partial<CallEntity>): Promise<unknown> =>
        ctx.dataSource.getRepository(CallEntity).insert(entity as CallEntity),
    };
  });

  afterAll(async () => {
    await ctx.close();
  });

  it('stores exactly the six domain tables + the migration bookkeeping table', async () => {
    const tables = (await ctx.dataSource.query<TableEntry[]>(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
    )) as TableEntry[];
    const names = tables.map((t) => t.name).sort();
    expect(names).toEqual(
      [
        'audit_events',
        'call_summaries',
        'calls',
        'handoffs',
        'patient_requests',
        'practices',
        'schema_migrations',
      ].sort(),
    );
  });

  it('audit_events has no updated_at column', async () => {
    const columns = (await ctx.dataSource.query<ColumnEntry[]>(
      "PRAGMA table_info(audit_events)",
    )) as ColumnEntry[];
    const columnNames = columns.map((c) => c.name);
    expect(columnNames).toContain('created_at');
    expect(columnNames).not.toContain('updated_at');
  });

  it('enforces the composite unique (practice_id, external_call_id) on calls', async () => {
    const first = buildCall('CALL-CONSTRAINT-1');
    await callsRepo.insert(first);
    await expect(callsRepo.insert({ ...first })).rejects.toThrow(/UNIQUE constraint failed/i);
  });

  it('enforces the composite unique (practice_id, idempotency_key) with NULL keys exempt', async () => {
    const base = buildRequest({ idempotencyKey: null });
    await requestsRepo.insert(buildRequest({ idempotencyKey: 'k-unique-1' }));
    await expect(requestsRepo.insert({ ...base, idempotencyKey: 'k-unique-1' })).rejects.toThrow(
      /UNIQUE constraint failed/i,
    );
    // NULL keys never collide with each other.
    await callsRepo.insert(buildCall('CALL-CONSTRAINT-NULL-A'));
    await requestsRepo.insert(buildRequest({ idempotencyKey: null }));
    await requestsRepo.insert(buildRequest({ idempotencyKey: null }));
  });

  it('violating a foreign key at the repository level produces a data error', async () => {
    const orphan = buildRequest({ idempotencyKey: 'k-fk-1', practiceId: crypto.randomUUID() });
    await expect(requestsRepo.insert(orphan)).rejects.toThrow(/FOREIGN KEY constraint failed/i);
  });

  it('records the payload hash column NOT NULL alongside keys', async () => {
    const columns = (await ctx.dataSource.query<ColumnEntry[]>(
      'PRAGMA table_info(patient_requests)',
    )) as ColumnEntry[];
    const hashColumn = columns.find((c) => c.name === 'idempotency_payload_hash');
    expect(hashColumn?.notnull).toBe(1);
    const keyColumn = columns.find((c) => c.name === 'idempotency_key');
    expect(keyColumn?.notnull).toBe(0);
  });

  it('registers the (practice_id, request_type), (practice_id, status), (practice_id, created_at) indexes', async () => {
    const indexes = (await ctx.dataSource.query<IndexEntry[]>(
      "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'patient_requests'",
    )) as IndexEntry[];
    const names = indexes.map((row) => row.name);
    expect(names).toContain('ix_patient_requests_practice_request_type');
    expect(names).toContain('ix_patient_requests_practice_status');
    expect(names).toContain('ix_patient_requests_practice_created_at');
    expect(names).toContain('uq_patient_requests_practice_idempotency');
  });
});
