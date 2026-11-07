import { Constructable } from '../src/database/db-config.js';
import { CreateSchema1794000000000 } from '../migrations/1794000000000-CreateSchema.js';

/** CLI database settings duplicate the runtime env contract, decorator-free. */
export interface CliDatabaseConfig {
  driver: 'sqlite' | 'mysql';
  dbUrl: string | undefined;
  sqlitePath: string;
}

export class ConfigError extends Error {}

export function readCliDatabaseConfig(): CliDatabaseConfig {
  const driver = (process.env.DB_DRIVER ?? 'sqlite').trim();
  if (driver !== 'sqlite' && driver !== 'mysql') {
    throw new ConfigError('DB_DRIVER must be "sqlite" or "mysql"');
  }
  if (driver === 'mysql') {
    const url = process.env.DB_URL?.trim();
    if (url === undefined || url.length === 0) {
      throw new ConfigError('DB_URL is required when DB_DRIVER=mysql');
    }
    return { driver, dbUrl: url, sqlitePath: process.env.SQLITE_PATH ?? './data/practice.db' };
  }
  return {
    driver,
    dbUrl: undefined,
    sqlitePath: process.env.SQLITE_PATH?.trim() ?? './data/practice.db',
  };
}

export function cliRegistry(): DataSourceRegistry {
  const migrations: Constructable[] = [CreateSchema1794000000000];
  return { entities: [], migrations };
}

/** Full CLI DataSourceOptions (driver-specific knobs + shared policy knobs). */
export function createCliDataSourceOptions(): Record<string, unknown> {
  const config = readCliDatabaseConfig();
  if (config.driver === 'mysql') {
    return {
      type: 'mysql',
      url: config.dbUrl,
      // UTC correctness mirrors the runtime data source configuration.
      timezone: 'Z',
      charset: 'utf8mb4',
      logging: false,
    };
  }
  return { type: 'better-sqlite3', database: config.sqlitePath, logging: false };
}

/** UTC timestamp text accepted by BOTH sqlite and mysql datetime columns. */
export function formatDbTimestamp(date: Date): string {
  return date.toISOString().replace('T', ' ').slice(0, 23);
}
