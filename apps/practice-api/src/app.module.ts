import 'reflect-metadata';
import { Module } from '@nestjs/common';
import { ValidationPipe } from '@nestjs/common';
import { APP_FILTER, APP_INTERCEPTOR, APP_PIPE } from '@nestjs/core';
import { ConfigModule } from '@nestjs/config';
import { validateEnv } from './config/env.js';
import { DatabaseModule } from './database/database.module.js';
import { PracticesModule } from './practices/practices.module.js';
import { CallsModule } from './calls/calls.module.js';
import { RequestsModule } from './requests/requests.module.js';
import { HandoffsModule } from './handoffs/handoffs.module.js';
import { AuditModule } from './audit/audit.module.js';
import { HealthModule } from './health/health.module.js';
import { EnvelopeInterceptor } from './common/api/envelope.interceptor.js';
import { GlobalErrorFilter } from './common/api/global-error.filter.js';
import { APP_CONFIG, buildAppConfig } from './config/app-config.js';

/**
 * Composition root.
 *
 * - Global ValidationPipe: whitelist, forbidNonWhitelisted, transform — every
 *   write DTO and every query DTO is validated/transformed consistently and
 *   invalid shapes produce the canonical 400 envelope.
 * - Global error filter: canonical error envelope on every failure.
 * - Global envelope interceptor: canonical `{ data, meta? }` success shape.
 * - Config: `validateEnv` fails fast with typed, secret-free messages.
 */
@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env'],
      validate: validateEnv,
    }),
    DatabaseModule,
    PracticesModule,
    CallsModule,
    RequestsModule,
    HandoffsModule,
    AuditModule,
    HealthModule,
  ],
  providers: [
    { provide: APP_CONFIG, useFactory: buildAppConfig },
    { provide: APP_PIPE, useValue: createValidationPipe() },
    { provide: APP_FILTER, useValue: new GlobalErrorFilter() },
    { provide: APP_INTERCEPTOR, useValue: new EnvelopeInterceptor() },
  ],
})
export class AppModule {}

function createValidationPipe(): ValidationPipe {
  return new ValidationPipe({
    whitelist: true,
    forbidNonWhitelisted: true,
    transform: true,
    stopAtFirstError: false,
    disableErrorMessages: false,
  });
}
