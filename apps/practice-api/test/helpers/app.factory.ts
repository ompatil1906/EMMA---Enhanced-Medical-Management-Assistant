import 'reflect-metadata';
import { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { DataSource } from 'typeorm';
import request from 'supertest';
import { AppModule } from '../../src/app.module.js';

/**
 * Builds a fully isolated test application:
 *
 * - driver: better-sqlite3, `:memory:` by default (each DataSource instance
 *   owns a private database).
 * - schema: built from MIGRATIONS via `dataSource.runMigrations()` — never
 *   from synchronize (the app itself never synchronises anywhere).
 * - global pipe/filter/interceptor come from AppModule exactly like prod.
 *
 * One fresh context per test file keeps file scoping isolated; concurrency
 * tests create an extra context inside a test and close it explicitly.
 */
export interface BootedTestApp {
  app: INestApplication;
  dataSource: DataSource;
  /** supertest bound to the app's HTTP server. */
  http: ReturnType<typeof request>;
  close(): Promise<void>;
}

/** Options: override SQLITE_PATH (e.g. a temporary file) or keep :memory:. */
export interface CreateTestAppOptions {
  sqlitePath?: string;
}

export function applyTestEnvDefaults(overrides: CreateTestAppOptions = {}): void {
  process.env.DB_DRIVER = 'sqlite';
  process.env.DB_URL = '';
  process.env.SQLITE_PATH = overrides.sqlitePath ?? ':memory:';
  delete process.env.PORT;
  process.env.APP_ENV = 'test';
  process.env.CORS_ORIGINS = '';
}

export async function createTestApp(overrides: CreateTestAppOptions = {}): Promise<BootedTestApp> {
  applyTestEnvDefaults(overrides);
  const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
  const app: INestApplication = moduleRef.createNestApplication();
  await app.init();

  const dataSource = app.get(DataSource);
  if (!dataSource.isInitialized) {
    throw new Error('test data source must be initialized after app.init()');
  }
  await dataSource.runMigrations({ transaction: 'each' });

  return {
    app,
    dataSource,
    http: request(app.getHttpServer()),
    close: async (): Promise<void> => {
      await app.close();
    },
  };
}
