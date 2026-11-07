import { Column, Entity, PrimaryColumn } from 'typeorm';

/**
 * Lifecycle of a call as recorded by the voice gateway.
 *
 * `in-progress` is a single-string status (hyphenated), deliberately not a
 * `snake_case` constant: this exact spelling is part of the API contract.
 */
export const CALL_STATUSES = ['in-progress', 'completed', 'failed'] as const;
export type CallStatus = (typeof CALL_STATUSES)[number];

/** A single phone call, idempotently created per (practice, externalCallId). */
@Entity('calls')
export class CallEntity {
  /** Application-generated UUID v4. */
  @PrimaryColumn('varchar', { name: 'id', length: 36 })
  public id!: string;

  /** Owning practice. FK → practices.id (ON DELETE RESTRICT). */
  @Column('varchar', { name: 'practice_id', length: 36 })
  public practiceId!: string;

  /** Provider call id. Unique per practice: (practice_id, external_call_id). */
  @Column('varchar', { name: 'external_call_id', length: 128 })
  public externalCallId!: string;

  @Column('varchar', { name: 'status', length: 32 })
  public status!: CallStatus;

  @Column({ name: 'started_at', nullable: true, type: 'datetime' })
  public startedAt: Date | null = null;

  @Column({ name: 'ended_at', nullable: true, type: 'datetime' })
  public endedAt: Date | null = null;

  @Column({ name: 'duration_seconds', nullable: true, type: 'int' })
  public durationSeconds: number | null = null;

  /** UTC creation timestamp. */
  @Column('datetime', { name: 'created_at' })
  public createdAt!: Date;

  /** UTC last-update timestamp. */
  @Column('datetime', { name: 'updated_at' })
  public updatedAt!: Date;
}
