import { MigrationInterface, QueryRunner, Table, TableColumnOptions } from 'typeorm';

/**
 * Day 19: `call_summaries` — one deterministic end-of-call summary per call,
 * plus the external task-platform (Jira) mirror state.
 *
 * Design notes:
 * * one row per call (`uq_call_summaries_call`), enforced in the schema so a
 *   retried submission can never create a second summary or a second issue;
 * * the Jira key/URL live on the summary row: sync is idempotent by call, and
 *   the issue link is returned to the practice UI without a second lookup;
 * * JSON columns follow the existing driver-agnostic policy (native JSON on
 *   MySQL, TEXT on SQLite);
 * * timestamps are UTC; `CURRENT_TIMESTAMP` is UTC on both drivers.
 */
export class CreateCallSummaries1795000000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    const driver = queryRunner.connection.driver.options.type;
    const isMysql = driver === 'mysql';

    await queryRunner.createTable(
      new Table({
        name: 'call_summaries',
        columns: [
          uuid('id'),
          { name: 'practice_id', type: 'varchar', length: '36', isNullable: false },
          { name: 'call_id', type: 'varchar', length: '36', isNullable: false },
          { name: 'headline', type: 'varchar', length: '255', isNullable: false },
          { name: 'summary', type: 'text', isNullable: false },
          jsonColumn(isMysql, 'bullets'),
          jsonColumn(isMysql, 'tasks'),
          jsonColumn(isMysql, 'caller'),
          { name: 'outcome', type: 'varchar', length: '32', isNullable: false },
          { name: 'duration_seconds', type: 'int', isNullable: true },
          { name: 'turns', type: 'int', isNullable: false },
          { name: 'transcript_excerpt', type: 'text', isNullable: true },
          { name: 'idempotency_key', type: 'varchar', length: '128', isNullable: true },
          { name: 'jira_issue_key', type: 'varchar', length: '64', isNullable: true },
          { name: 'jira_issue_url', type: 'varchar', length: '512', isNullable: true },
          { name: 'jira_synced_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: true },
          { name: 'jira_sync_error', type: 'varchar', length: '255', isNullable: true },
          { name: 'created_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
          { name: 'updated_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
        ],
        indices: [
          { columnNames: ['call_id'], isUnique: true, name: 'uq_call_summaries_call' },
          {
            columnNames: ['practice_id', 'created_at'],
            name: 'ix_call_summaries_practice_created_at',
          },
        ],
        foreignKeys: [
          foreignKey('fk_call_summaries_practice', ['practice_id'], 'practices'),
          foreignKey('fk_call_summaries_call', ['call_id'], 'calls'),
        ],
      }),
      true,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropTable('call_summaries', true, true);
  }
}

/* ------------------------------------------------------------------ helpers */

function uuid(name: string): TableColumnOptions {
  return { name, type: 'varchar', length: '36', isNullable: false, isPrimary: true };
}

function foreignKey(
  name: string,
  columnNames: string[],
  referencedTableName: string,
): {
  name: string;
  columnNames: string[];
  referencedTableName: string;
  referencedColumnNames: string[];
  onDelete: string;
} {
  return {
    name,
    columnNames,
    referencedTableName,
    referencedColumnNames: ['id'],
    onDelete: 'RESTRICT',
  };
}

function datetimeOpts(isMysql: boolean): { precision: number } | Record<string, never> {
  return isMysql ? { precision: 6 } : {};
}

function jsonColumn(isMysql: boolean, name: string): TableColumnOptions {
  return isMysql
    ? { name, type: 'json', isNullable: false }
    : { name, type: 'text', isNullable: false };
}
