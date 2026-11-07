import { Column, Entity, PrimaryColumn } from 'typeorm';

/**
 * Append-only audit trail. There is deliberately no `updatedAt` column and no
 * update/delete endpoint: rows are written once and never mutated or removed.
 * FKs use ON DELETE RESTRICT to protect the trail from cascade deletion.
 */
@Entity('audit_events')
export class AuditEventEntity {
  /** Application-generated UUID v4. */
  @PrimaryColumn('varchar', { name: 'id', length: 36 })
  public id!: string;

  /** Owning practice. FK → practices.id (ON DELETE RESTRICT). */
  @Column('varchar', { name: 'practice_id', length: 36 })
  public practiceId!: string;

  /** Related call, when known. FK → calls.id (ON DELETE RESTRICT). */
  @Column('varchar', { name: 'call_id', length: 36, nullable: true })
  public callId: string | null = null;

  /** Machine-readable event type, e.g. "request.created". */
  @Column('varchar', { name: 'event_type', length: 64 })
  public eventType!: string;

  /**
   * JSON object of primitives (app-validated). Declared as a `json` column so
   * TypeORM stringifies on write and parses on read for BOTH drivers — the
   * physical column is JSON on MySQL and TEXT on SQLite, decided by migration.
   */
  @Column('json', { name: 'event_payload' })
  public eventPayload!: Record<string, unknown>;

  /** UTC creation timestamp. Append-only: no updatedAt column exists. */
  @Column('datetime', { name: 'created_at' })
  public createdAt!: Date;
}
