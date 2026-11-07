import 'reflect-metadata';
import { plainToInstance, Transform } from 'class-transformer';
import {
  IsEnum,
  IsInt,
  IsOptional,
  IsString,
  Matches,
  Max,
  Min,
} from 'class-validator';

/**
 * Supported database drivers.
 *
 * - `sqlite` (default): driver-agnostic SQLite via better-sqlite3 — used for all
 *   local development and for the test suites (schema is built from migrations,
 *   never from synchronize).
 * - `mysql`: MySQL 8 via mysql2 — used in prod/compose. Not verifiable on this
 *   machine (no Docker daemon); behaviour is covered by the dual-driver design
 *   and honestly flagged as unverified.
 */
export enum DatabaseDriver {
  Sqlite = 'sqlite',
  Mysql = 'mysql',
}

export enum AppEnv {
  Development = 'development',
  Test = 'test',
  Production = 'production',
}

/**
 * Shape of the validated environment, as exposed through ConfigService.
 * Class-field defaults are applied when a variable is absent or empty.
 */
export class EnvVariables {
  @IsOptional()
  @IsEnum(DatabaseDriver, { message: 'DB_DRIVER must be one of: sqlite, mysql' })
  DB_DRIVER: DatabaseDriver = DatabaseDriver.Sqlite;

  /**
   * MySQL connection URL, required only when DB_DRIVER=mysql (enforced in
   * `validateEnv`). Messages never echo the value: the URL may contain
   * credentials and must not reach logs.
   */
  @IsOptional()
  @Matches(/^mysql:\/\/[^@\s]+:[^@\s]+@[^@\s]+:\d{1,5}\/[A-Za-z0-9_-]+$/, {
    message:
      'DB_URL must be a mysql:// URL of the form mysql://user:pass@host:3306/emma_practice (value not echoed for safety)',
  })
  DB_URL: string | undefined = undefined;

  @IsOptional()
  @IsString({ message: 'SQLITE_PATH must be a SQLite file path or :memory:' })
  SQLITE_PATH: string = './data/practice.db';

  @IsOptional()
  @IsInt({ message: 'PORT must be an integer between 1 and 65535' })
  @Min(1, { message: 'PORT must be an integer between 1 and 65535' })
  @Max(65535, { message: 'PORT must be an integer between 1 and 65535' })
  @Transform(
    ({ value }) => {
      if (value === undefined || value === '') return undefined;
      return Number(value);
    },
    { toClassOnly: true },
  )
  PORT: number = 3000;

  @IsOptional()
  @IsEnum(AppEnv, { message: 'APP_ENV must be one of: development, test, production' })
  APP_ENV: AppEnv = AppEnv.Development;

  @IsOptional()
  @IsString({ message: 'CORS_ORIGINS must be a comma-separated list of origins' })
  CORS_ORIGINS: string = 'http://localhost:5173';
}

/**
 * Validates the raw process environment into a typed `EnvVariables` instance,
 * applying defaults and failing fast with clear, secret-free error messages.
 */
export function validateEnv(rawConfig: Record<string, unknown>): EnvVariables {
  const instance = plainToInstance(EnvVariables, rawConfig, {
    enableImplicitConversion: false,
  });

  // Manual environment validation (keeps class-validator decorators as
  // documentation while avoiding a class-validator/ts-jest decorator-metadata
  // incompatibility in the test runner).
  const errors: string[] = [];

  if (
    instance.DB_DRIVER !== undefined &&
    !Object.values(DatabaseDriver).includes(instance.DB_DRIVER)
  ) {
    errors.push('DB_DRIVER must be one of: sqlite, mysql');
  }

  if (instance.DB_URL) {
    const mysqlUrlPattern = /^mysql:\/\/[^@\s]+:[^@\s]+@[^@\s]+:\d{1,5}\/[A-Za-z0-9_-]+$/;
    if (!mysqlUrlPattern.test(instance.DB_URL)) {
      errors.push(
        'DB_URL must be a mysql:// URL of the form mysql://user:pass@host:3306/emma_practice (value not echoed for safety)',
      );
    }
  }

  if (instance.SQLITE_PATH !== undefined && typeof instance.SQLITE_PATH !== 'string') {
    errors.push('SQLITE_PATH must be a SQLite file path or :memory:');
  }

  if (instance.PORT !== undefined) {
    const port = Number(instance.PORT);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      errors.push('PORT must be an integer between 1 and 65535');
    }
  }

  if (
    instance.APP_ENV !== undefined &&
    !Object.values(AppEnv).includes(instance.APP_ENV)
  ) {
    errors.push('APP_ENV must be one of: development, test, production');
  }

  if (instance.CORS_ORIGINS !== undefined && typeof instance.CORS_ORIGINS !== 'string') {
    errors.push('CORS_ORIGINS must be a comma-separated list of origins');
  }

  if (instance.DB_DRIVER === DatabaseDriver.Mysql && !instance.DB_URL) {
    errors.push(
      'DB_URL is required when DB_DRIVER=mysql (expected mysql://user:pass@host:3306/database)',
    );
  }

  if (errors.length > 0) {
    throw new Error(`Invalid environment configuration:\n${errors.map((m) => `  - ${m}`).join('\n')}`);
  }

  return instance;
}
