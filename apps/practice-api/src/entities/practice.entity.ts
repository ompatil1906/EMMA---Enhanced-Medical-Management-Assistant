import { Column, Entity, PrimaryColumn } from 'typeorm';

/**
 * A telephony practice. Exactly one row per practice; seeded by `pnpm seed`.
 *
 * Column naming policy: camelCase property names map to explicit snake_case
 * SQL columns for clean, portable SQL across SQLite and MySQL.
 */
@Entity('practices')
export class PracticeEntity {
  /** Application-generated UUID v4. */
  @PrimaryColumn('varchar', { name: 'id', length: 36 })
  public id!: string;

  /** Unique human-readable practice name. */
  @Column('varchar', { name: 'name', length: 191 })
  public name!: string;

  /** IANA time zone of the practice, e.g. "Europe/London". */
  @Column('varchar', { name: 'timezone', length: 64 })
  public timezone!: string;

  /** UTC creation timestamp. */
  @Column('datetime', { name: 'created_at' })
  public createdAt!: Date;

  /** UTC last-update timestamp. */
  @Column('datetime', { name: 'updated_at' })
  public updatedAt!: Date;
}
