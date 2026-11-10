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

/** External task-platform modes (Jira). */
export enum JiraMode {
  Disabled = 'disabled',
  Mock = 'mock',
  Live = 'live',
}

/** How much of the intake form may leave the practice for an external issue. */
export enum JiraPiiMode {
  Full = 'full',
  Masked = 'masked',
  None = 'none',
}

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

  // --- Day 19: Jira mirror -------------------------------------------------
  /**
   * `disabled` (off), `mock` (offline/demo — no network) or `live` (Atlassian
   * Cloud REST v3). `live` without credentials fails fast at startup; there is
   * no silent fallback to mock.
   */
  @IsOptional()
  JIRA_MODE: JiraMode = JiraMode.Disabled;

  /** Atlassian site root, e.g. https://example.atlassian.net (no trailing slash). */
  @IsOptional()
  @Matches(/^https:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?(?:\/[^\s]*)?$/, {
    message:
      'JIRA_BASE_URL must be an https:// URL such as https://example.atlassian.net (value not echoed)',
  })
  JIRA_BASE_URL: string | undefined = undefined;

  @IsOptional()
  @IsString({ message: 'JIRA_EMAIL must be the Atlassian account email' })
  JIRA_EMAIL: string | undefined = undefined;

  /** API token — never logged, never echoed in an error message. */
  @IsOptional()
  @IsString({ message: 'JIRA_API_TOKEN must be an Atlassian API token' })
  JIRA_API_TOKEN: string | undefined = undefined;

  @IsOptional()
  @Matches(/^[A-Z][A-Z0-9_]{1,19}$/, {
    message: 'JIRA_PROJECT_KEY must look like a Jira project key (e.g. EMMA)',
  })
  JIRA_PROJECT_KEY: string | undefined = undefined;

  @IsOptional()
  @Matches(/^[A-Z][A-Z0-9_-]{1,63}$/, { message: 'JIRA_EPIC_KEY must be a Jira issue key' })
  JIRA_EPIC_KEY: string | undefined = undefined;

  /** `requestType:IssueType,...` — e.g. `appointment-request:Appointment` */
  @IsOptional()
  @IsString({ message: 'JIRA_ISSUE_TYPE_MAP must be a comma-separated requestType:IssueType list' })
  JIRA_ISSUE_TYPE_MAP: string | undefined = undefined;

  @IsOptional()
  @IsEnum(JiraPiiMode, { message: 'JIRA_PII_MODE must be one of: full, masked, none' })
  JIRA_PII_MODE: JiraPiiMode = JiraPiiMode.Masked;

  @IsOptional()
  @IsInt({ message: 'JIRA_TIMEOUT_MS must be an integer between 500 and 30000' })
  @Min(500, { message: 'JIRA_TIMEOUT_MS must be an integer between 500 and 30000' })
  @Max(30000, { message: 'JIRA_TIMEOUT_MS must be an integer between 500 and 30000' })
  @Transform(
    ({ value }) => {
      if (value === undefined || value === '') return undefined;
      return Number(value);
    },
    { toClassOnly: true },
  )
  JIRA_TIMEOUT_MS: number = 5000;
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

  if (instance.JIRA_MODE !== undefined && !Object.values(JiraMode).includes(instance.JIRA_MODE)) {
    errors.push('JIRA_MODE must be one of: disabled, mock, live');
  }

  if (instance.JIRA_PII_MODE !== undefined && !Object.values(JiraPiiMode).includes(instance.JIRA_PII_MODE)) {
    errors.push('JIRA_PII_MODE must be one of: full, masked, none');
  }

  if (instance.JIRA_BASE_URL) {
    const httpsUrl = /^https:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?(?:\/[^\s]*)?$/;
    if (!httpsUrl.test(instance.JIRA_BASE_URL)) {
      errors.push(
        'JIRA_BASE_URL must be an https:// URL such as https://example.atlassian.net (value not echoed)',
      );
    }
  }

  if (instance.JIRA_PROJECT_KEY && !/^[A-Z][A-Z0-9_]{1,19}$/.test(instance.JIRA_PROJECT_KEY)) {
    errors.push('JIRA_PROJECT_KEY must look like a Jira project key (e.g. EMMA)');
  }

  if (instance.JIRA_EPIC_KEY && !/^[A-Z][A-Z0-9_-]{1,63}$/.test(instance.JIRA_EPIC_KEY)) {
    errors.push('JIRA_EPIC_KEY must be a Jira issue key');
  }

  if (instance.JIRA_MODE === JiraMode.Live) {
    // Fail fast: a live integration without credentials would silently drop
    // every record (no silent fallback to mock).
    if (!instance.JIRA_BASE_URL) {
      errors.push('JIRA_BASE_URL is required when JIRA_MODE=live');
    }
    if (!instance.JIRA_EMAIL) {
      errors.push('JIRA_EMAIL is required when JIRA_MODE=live');
    }
    if (!instance.JIRA_API_TOKEN) {
      errors.push('JIRA_API_TOKEN is required when JIRA_MODE=live');
    }
    if (!instance.JIRA_PROJECT_KEY) {
      errors.push('JIRA_PROJECT_KEY is required when JIRA_MODE=live');
    }
  }

  if (errors.length > 0) {
    throw new Error(`Invalid environment configuration:\n${errors.map((m) => `  - ${m}`).join('\n')}`);
  }

  return instance;
}
