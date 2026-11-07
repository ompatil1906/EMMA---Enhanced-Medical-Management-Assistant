import type { DataSourceOptions } from 'typeorm';
import type { AppConfig } from '../config/app-config.js';

/** Internal bookkeeping table used by the TypeORM migration executor. */
export const SCHEMA_MIGRATIONS_TABLE = 'schema_migrations';

/** Minimal constructor shape accepted by TypeORM for entities/migrations. */
export type Constructable = abstract new (...args: never[]) => unknown;

/** Entity/migration classes handed to the data source factory at run time. */
export interface DataSourceRegistry {
  entities: readonly Function[];
  migrations: readonly Function[];
}
/**
 * Builds explicit, driver-agnostic `DataSourceOptions`.
 *
 * Automatic schema synchronisation is permanently OFF in every environment:
 * the schema is always built from migrations — in tests via
 * `dataSource.runMigrations()` right after the DataSource is connected.
 *
 * Note: this module is intentionally free of decorator-metadata imports so the
 * scripts under `scripts/` (executed with tsx, which cannot emit decorator
 * metadata) can build a migrations-only data source.
 */
export function createDataSourceOptions(
  config: Pick<AppConfig, 'driver' | 'dbUrl' | 'sqlitePath'>,
  registry: DataSourceRegistry,
): DataSourceOptions {
  const common = {
    entities: registry.entities,
    migrations: registry.migrations,
    migrationsTableName: SCHEMA_MIGRATIONS_TABLE,
    synchronize: false,
    migrationsRun: false,
    logging: false,
  };

  if (config.driver === 'mysql') {
    if (!config.dbUrl) {
      throw new Error('DB_URL is required when DB_DRIVER=mysql');
    }
    return {
      ...common,
      type: 'mysql',
      url: config.dbUrl,
      // UTC correctness: interpret/store datetimes in UTC regardless of the
      // server's session time zone. mysql2 then serialises bound Date params
      // and parses datetime columns in UTC.
      timezone: 'Z',
      charset: 'utf8mb4',
    } as unknown as DataSourceOptions;
  }
  return {
    ...common,
    type: 'better-sqlite3',
    database: config.sqlitePath,
  } as unknown as DataSourceOptions;
}
