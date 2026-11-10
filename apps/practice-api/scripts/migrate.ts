#!/usr/bin/env -S pnpm tsx
/**
 * Migration runner used via pnpm `migration:run | migration:revert |
 * migration:status` (up | revert | status).
 *
 * Uses the runtime env contract (DB_DRIVER, DB_URL, SQLITE_PATH) and imports
 * only decorator-free modules: tsx cannot emit decorator metadata. Applies
 * migrations transactionally and never synchronises schemas.
 */
import 'reflect-metadata';
import { DataSource } from 'typeorm';
import { CreateSchema1794000000000 } from '../migrations/1794000000000-CreateSchema.js';
import { CreateCallSummaries1795000000000 } from '../migrations/1795000000000-CreateCallSummaries.js';
import {
  cliRegistry,
  ConfigError,
  createCliDataSourceOptions,
} from './cli-db-config.js';

type Command = 'up' | 'revert' | 'status';

async function run(): Promise<number> {
  const command = (process.argv[2] ?? 'status') as Command;
  if (command !== 'up' && command !== 'revert' && command !== 'status') {
    // eslint-disable-next-line no-console
    console.error('usage: pnpm migration:run | migration:revert | migration:status');
    return 2;
  }

  const dataSource = new DataSource({
    ...createCliDataSourceOptions(),
    migrations: [CreateSchema1794000000000, CreateCallSummaries1795000000000],
    entities: cliRegistry().entities,
    migrationsTableName: 'schema_migrations',
    synchronize: false,
    migrationsRun: false,
    logging: false,
  });
  await dataSource.initialize();
  try {
    if (command === 'up') {
      const applied = await dataSource.runMigrations({ transaction: 'each' });
      console.log(`${applied.length} migration(s) applied`);
      for (const migration of applied) {
        console.log(`  applied: ${migration.name}`);
      }
      if (applied.length === 0) {
        console.log('no pending migrations');
      }
      return 0;
    }
    if (command === 'revert') {
      await dataSource.undoLastMigration();
      console.log('last migration reverted');
      return 0;
    }
    const allExecuted = await dataSource.showMigrations();
    if (allExecuted) {
      console.log('database is up to date');
    } else {
      console.log('pending migrations exist — run `pnpm migration:run`');
    }
    return 0;
  } finally {
    await dataSource.destroy();
  }
}

process.exitCode = 0;
run().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`migration command failed${error instanceof ConfigError ? '' : ''}: ${message}`);
  process.exitCode = 1;
});
