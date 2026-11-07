#!/usr/bin/env -S pnpm tsx
/**
 * Seeds the single fictional practice:
 *   "Riverbend Medical Practice" (Europe/London)
 *
 * Works against either driver (env contract: DB_DRIVER/DB_URL/SQLITE_PATH):
 * 1. applies any pending migrations (the DB is created if missing);
 * 2. inserts the one practice row when absent (idempotent);
 * 3. prints the generated practice id as JSON.
 */
import 'reflect-metadata';
import { randomUUID } from 'node:crypto';
import { DataSource } from 'typeorm';
import { CreateSchema1794000000000 } from '../migrations/1794000000000-CreateSchema.js';
import { cliRegistry, createCliDataSourceOptions, formatDbTimestamp } from './cli-db-config.js';

const PRACTICE_NAME = 'Riverbend Medical Practice';
const PRACTICE_TIMEZONE = 'Europe/London';

interface PracticeRow {
  id: string;
  name: string;
  timezone: string;
  created_at?: unknown;
  updated_at?: unknown;
}

async function seed(): Promise<void> {
  const dataSource = new DataSource({
    ...createCliDataSourceOptions(),
    migrations: [CreateSchema1794000000000],
    entities: cliRegistry().entities,
    migrationsTableName: 'schema_migrations',
    synchronize: false,
    migrationsRun: false,
    logging: false,
  });
  await dataSource.initialize();
  try {
    // Order matters: pending migrations first, then the row.
    const applied = await dataSource.runMigrations({ transaction: 'each' });
    console.log(`migrations at seed time: ${applied.length} applied`);

    const existing = await dataSource.query<PracticeRow[]>(
      'SELECT id, name, timezone FROM practices WHERE name = ? LIMIT 1',
      [PRACTICE_NAME],
    );
    const nowText = formatDbTimestamp(new Date());
    if (existing.length > 0) {
      const practice = existing[0] as PracticeRow;
      console.log(`practice already present (unchanged):`);
      console.log(JSON.stringify({ id: practice.id, name: practice.name, timezone: practice.timezone }, null, 2));
      return;
    }

    const id = randomUUID();
    await dataSource.query(
      'INSERT INTO practices (id, name, timezone, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
      [id, PRACTICE_NAME, PRACTICE_TIMEZONE, nowText, nowText],
    );
    console.log(`seeded practice:`);
    console.log(JSON.stringify({ id, name: PRACTICE_NAME, timezone: PRACTICE_TIMEZONE }, null, 2));
  } finally {
    await dataSource.destroy();
  }
}

process.exitCode = 0;
seed().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`seed failed: ${message}`);
  process.exitCode = 1;
});
