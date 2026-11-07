import { validateEnv, AppEnv, DatabaseDriver } from './env.js';

/**
 * Fully typed, pre-validated application configuration shared by main.ts, the
 * database module and the tests. Built once from `EnvVariables` so nothing
 * downstream has to re-coerce environment strings.
 */
export class AppConfig {
  driver: DatabaseDriver = DatabaseDriver.Sqlite;
  dbUrl: string | undefined = undefined;
  sqlitePath: string = './data/practice.db';
  port: number = 3000;
  env: AppEnv = AppEnv.Development;
  corsOrigins: string[] = [];
}

export const APP_CONFIG = 'APP_CONFIG';

/** Builds the typed configuration from the (validated) environment. */
export function buildAppConfig(): AppConfig {
  const env = validateEnv(process.env as Record<string, unknown>);
  const config = new AppConfig();
  config.driver = env.DB_DRIVER;
  config.dbUrl = env.DB_URL;
  config.sqlitePath = env.SQLITE_PATH;
  config.port = env.PORT;
  config.env = env.APP_ENV;
  config.corsOrigins = env.CORS_ORIGINS.split(',')
    .map((origin) => origin.trim())
    .filter((origin) => origin.length > 0);
  return config;
}
