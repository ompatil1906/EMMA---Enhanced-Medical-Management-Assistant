import { Column, Entity, PrimaryColumn } from 'typeorm';

export const HANDOFF_STATUSES = ['requested', 'initiated', 'completed', 'failed'] as const;
export type HandoffStatus = (typeof HANDOFF_STATUSES)[number];

/**
 * A human hand-off raised during a call. Note: creating this row never claims
 * that a transfer actually completed — the service never defaults or coerces
 * the status to `completed`; that only happens via explicit caller input.
 */
@Entity('handoffs')
export class HandoffEntity {
  /** Application-generated UUID v4. */
  @PrimaryColumn('varchar', { name: 'id', length: 36 })
  public id!: string;

  /** Owning practice. FK → practices.id (ON DELETE RESTRICT). */
  @Column('varchar', { name: 'practice_id', length: 36 })
  public practiceId!: string;

  /** Originating call, when known. FK → calls.id (ON DELETE RESTRICT). */
  @Column('varchar', { name: 'call_id', length: 36, nullable: true })
  public callId: string | null = null;

  /** Why the hand-off was requested, e.g. "escalation", "operator". */
  @Column('varchar', { name: 'reason', length: 255 })
  public reason!: string;

  /** Where the call was handed to, e.g. a desk, queue or phone number. */
  @Column('varchar', { name: 'destination', length: 255 })
  public destination!: string;

  @Column('varchar', { name: 'status', length: 32 })
  public status!: HandoffStatus;

  /** UTC creation timestamp. */
  @Column('datetime', { name: 'created_at' })
  public createdAt!: Date;

  /** UTC last-update timestamp. */
  @Column('datetime', { name: 'updated_at' })
  public updatedAt!: Date;
}
