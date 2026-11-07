import { Column, Entity, PrimaryColumn } from 'typeorm';

export const REQUEST_STATUSES = ['received', 'completed', 'failed'] as const;
export type RequestStatus = (typeof REQUEST_STATUSES)[number];

/**
 * A structured patient request captured from a call. Contains no medical
 * details by policy: the payload is an app-validated JSON tree of primitives.
 *
 * Idempotency is database-enforced through the composite unique index on
 * (practice_id, idempotency_key) plus a stable payload hash column so that
 * reusing a key with a different payload produces a 409 conflict.
 */
@Entity('patient_requests')
export class PatientRequestEntity {
  /** Application-generated UUID v4. */
  @PrimaryColumn('varchar', { name: 'id', length: 36 })
  public id!: string;

  /** Owning practice. FK → practices.id (ON DELETE RESTRICT). */
  @Column('varchar', { name: 'practice_id', length: 36 })
  public practiceId!: string;

  /** Originating call, when known. FK → calls.id (ON DELETE RESTRICT). */
  @Column({ name: 'call_id', length: 36, nullable: true, type: 'varchar' })
  public callId: string | null = null;

  /** Free-ish request category, e.g. "appointment-request", "reschedule". */
  @Column('varchar', { name: 'request_type', length: 64 })
  public requestType!: string;

  @Column('varchar', { name: 'status', length: 32 })
  public status!: RequestStatus;

  /**
   * JSON object of primitives (app-validated). Declared as a `json` column so
   * TypeORM stringifies on write and parses on read for BOTH drivers — the
   * physical column is JSON on MySQL and TEXT on SQLite, decided by migration.
   */
  @Column('json', { name: 'structured_payload' })
  public structuredPayload!: Record<string, unknown>;

  /** Idempotency key, unique together with practiceId when non-null. */
  @Column({ name: 'idempotency_key', length: 128, nullable: true, type: 'varchar' })
  public idempotencyKey: string | null = null;

  /** SHA-256 (hex) of the canonicalized structured payload. Never null. */
  @Column('varchar', { name: 'idempotency_payload_hash', length: 64 })
  public idempotencyPayloadHash!: string;

  /** UTC creation timestamp. */
  @Column('datetime', { name: 'created_at' })
  public createdAt!: Date;

  /** UTC last-update timestamp. */
  @Column('datetime', { name: 'updated_at' })
  public updatedAt!: Date;
}
