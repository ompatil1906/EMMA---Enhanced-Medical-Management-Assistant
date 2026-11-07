"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CreateSchema1794000000000 = void 0;
const typeorm_1 = require("typeorm");
/**
 * Initial schema: exactly the five EMMA practice-backend tables
 * (practices, calls, patient_requests, handoffs, audit_events).
 *
 * The migration is driver-agnostic: column types are chosen at run time from
 * the configured driver (JSON on MySQL / TEXT on SQLite, native ENUM on
 * MySQL / length-constrained VARCHAR on SQLite). Callers must apply it via
 * `scripts/migrate.ts` (or `dataSource.runMigrations()` in tests) — no
 * environment ever uses `synchronize`.
 *
 * All timestamps are UTC. Defaults use CURRENT_TIMESTAMP (UTC on both drivers;
 * sqlite CURRENT_TIMESTAMP is always UTC). Services additionally set explicit
 * UTC `Date` values on every write.
 *
 * Deletes: the prototype never deletes domain rows, and audit rows must not
 * disappear with their parents, so every FK is ON DELETE RESTRICT.
 */
class CreateSchema1794000000000 {
    async up(queryRunner) {
        const driver = queryRunner.connection.driver.options.type;
        const isMysql = driver === 'mysql';
        // ---------- practices ----------
        await queryRunner.createTable(new typeorm_1.Table({
            name: 'practices',
            columns: [
                uuid('id'),
                { name: 'name', type: 'varchar', length: '191', isNullable: false },
                { name: 'timezone', type: 'varchar', length: '64', isNullable: false },
                { name: 'created_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
                { name: 'updated_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
            ],
            indexes: [
                { columnNames: ['name'], isUnique: true, name: 'uq_practices_name' },
            ],
        }), true);
        // ---------- calls ----------
        await queryRunner.createTable(new typeorm_1.Table({
            name: 'calls',
            columns: [
                uuid('id'),
                fkId('practice_id'),
                { name: 'external_call_id', type: 'varchar', length: '128', isNullable: false },
                enumColumn(isMysql, 'status', "'in-progress'", 'in-progress', 'completed', 'failed'),
                { name: 'started_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: true },
                { name: 'ended_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: true },
                { name: 'duration_seconds', type: 'int', isNullable: true },
                { name: 'created_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
                { name: 'updated_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
            ],
            indexes: [
                {
                    columnNames: ['practice_id', 'external_call_id'],
                    isUnique: true,
                    name: 'uq_calls_practice_external_call',
                },
            ],
            foreignKeys: [foreignKey('fk_calls_practice', ['practice_id'], 'practices')],
        }), true);
        // ---------- patient_requests ----------
        await queryRunner.createTable(new typeorm_1.Table({
            name: 'patient_requests',
            columns: [
                uuid('id'),
                fkId('practice_id'),
                { name: 'call_id', type: 'varchar', length: '36', isNullable: true },
                { name: 'request_type', type: 'varchar', length: '64', isNullable: false },
                enumColumn(isMysql, 'status', "'received'", 'received', 'completed', 'failed'),
                payloadColumn(isMysql, 'structured_payload'),
                { name: 'idempotency_key', type: 'varchar', length: '128', isNullable: true },
                { name: 'idempotency_payload_hash', type: 'varchar', length: '64', isNullable: false },
                { name: 'created_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
                { name: 'updated_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
            ],
            indexes: [
                {
                    columnNames: ['practice_id', 'idempotency_key'],
                    isUnique: true,
                    name: 'uq_patient_requests_practice_idempotency',
                },
                {
                    columnNames: ['practice_id', 'request_type'],
                    name: 'ix_patient_requests_practice_request_type',
                },
                {
                    columnNames: ['practice_id', 'status'],
                    name: 'ix_patient_requests_practice_status',
                },
                {
                    columnNames: ['practice_id', 'created_at'],
                    name: 'ix_patient_requests_practice_created_at',
                },
            ],
            foreignKeys: [
                foreignKey('fk_patient_requests_practice', ['practice_id'], 'practices'),
                foreignKey('fk_patient_requests_call', ['call_id'], 'calls'),
            ],
        }), true);
        // ---------- handoffs ----------
        await queryRunner.createTable(new typeorm_1.Table({
            name: 'handoffs',
            columns: [
                uuid('id'),
                fkId('practice_id'),
                { name: 'call_id', type: 'varchar', length: '36', isNullable: true },
                { name: 'reason', type: 'varchar', length: '255', isNullable: false },
                { name: 'destination', type: 'varchar', length: '255', isNullable: false },
                enumColumn(isMysql, 'status', "'requested'", 'requested', 'initiated', 'completed', 'failed'),
                { name: 'created_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
                { name: 'updated_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
            ],
            foreignKeys: [
                foreignKey('fk_handoffs_practice', ['practice_id'], 'practices'),
                foreignKey('fk_handoffs_call', ['call_id'], 'calls'),
            ],
        }), true);
        // ---------- audit_events (append-only; no updated_at column) ----------
        await queryRunner.createTable(new typeorm_1.Table({
            name: 'audit_events',
            columns: [
                uuid('id'),
                fkId('practice_id'),
                { name: 'call_id', type: 'varchar', length: '36', isNullable: true },
                { name: 'event_type', type: 'varchar', length: '64', isNullable: false },
                payloadColumn(isMysql, 'event_payload'),
                { name: 'created_at', type: 'datetime', ...datetimeOpts(isMysql), isNullable: false },
            ],
            indexes: [
                {
                    columnNames: ['practice_id', 'created_at'],
                    name: 'ix_audit_events_practice_created_at',
                },
                { columnNames: ['call_id'], name: 'ix_audit_events_call_id' },
            ],
            foreignKeys: [
                foreignKey('fk_audit_events_practice', ['practice_id'], 'practices'),
                foreignKey('fk_audit_events_call', ['call_id'], 'calls'),
            ],
        }), true);
    }
    async down(queryRunner) {
        // Children first: FK dependencies point upward to practices.
        await queryRunner.dropTable('audit_events', true, true);
        await queryRunner.dropTable('handoffs', true, true);
        await queryRunner.dropTable('patient_requests', true, true);
        await queryRunner.dropTable('calls', true, true);
        await queryRunner.dropTable('practices', true, true);
    }
}
exports.CreateSchema1794000000000 = CreateSchema1794000000000;
/* ------------------------------------------------------------------ helpers */
/** Primary-key UUID column (application-generated uuidv4). */
function uuid(name) {
    return {
        name,
        type: 'varchar',
        length: '36',
        isNullable: false,
        isPrimary: true,
    };
}
/** FK id column (nullable = false by default; nullable call ids override). */
function fkId(name) {
    return { name, type: 'varchar', length: '36', isNullable: false };
}
function foreignKey(name, columnNames, referencedTableName) {
    return {
        name,
        columnNames,
        referencedTableName,
        referencedColumnNames: ['id'],
        onDelete: 'RESTRICT',
    };
}
/** datetime: microsecond precision on MySQL; plain affinity on SQLite. */
function datetimeOpts(isMysql) {
    return isMysql ? { precision: 6 } : {};
}
/** enum on MySQL, status-constrained varchar on SQLite. */
function enumColumn(isMysql, name, defaultValue, ...values) {
    return isMysql
        ? {
            name,
            type: 'enum',
            enum: [...values],
            default: defaultValue,
            isNullable: false,
        }
        : {
            name,
            type: 'varchar',
            length: '32',
            default: defaultValue,
            isNullable: false,
        };
}
/** JSON payload column: native JSON on MySQL, TEXT on SQLite. */
function payloadColumn(isMysql, name) {
    return isMysql
        ? { name, type: 'json', isNullable: false }
        : { name, type: 'text', isNullable: false };
}
//# sourceMappingURL=1794000000000-CreateSchema.js.map