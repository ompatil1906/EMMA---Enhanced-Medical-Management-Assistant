import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { APP_CONFIG, AppConfig, buildAppConfig } from '../config/app-config.js';
import { AuditEventEntity } from '../entities/audit-event.entity.js';
import { CallEntity } from '../entities/call.entity.js';
import { HandoffEntity } from '../entities/handoff.entity.js';
import { PatientRequestEntity } from '../entities/patient-request.entity.js';
import { PracticeEntity } from '../entities/practice.entity.js';
import { CreateSchema1794000000000 } from '../../migrations/1794000000000-CreateSchema.js';
import { Constructable, createDataSourceOptions } from './db-config.js';

export const SCHEMA_MIGRATIONS: Constructable[] = [CreateSchema1794000000000];

export const APP_ENTITIES: Constructable[] = [
  PracticeEntity,
  CallEntity,
  PatientRequestEntity,
  HandoffEntity,
  AuditEventEntity,
];

/**
 * Boots the application-wide TypeORM `DataSource` from the validated app
 * configuration. Never synchronises schemas: migrations are the single source
 * of truth, applied via `scripts/migrate.ts` (and in tests via
 * `dataSource.runMigrations()`).
 */
@Module({
  imports: [
    TypeOrmModule.forRootAsync({
      useFactory: () => {
        const config = buildAppConfig();
        return createDataSourceOptions(
          { driver: config.driver, dbUrl: config.dbUrl, sqlitePath: config.sqlitePath },
          { entities: APP_ENTITIES, migrations: SCHEMA_MIGRATIONS },
        );
      },
    }),
  ],
})
export class DatabaseModule {}
