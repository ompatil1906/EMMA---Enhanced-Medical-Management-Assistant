import { Module } from '@nestjs/common';
import { APP_CONFIG, buildAppConfig } from '../../config/app-config.js';
import { JiraService } from './jira.service.js';

/**
 * External task-platform (Jira) module — Day 19.
 *
 * Provides the adapter only. The summary/sync orchestration lives in
 * `CallsService`, which owns the `call_summaries` row and its idempotency rules
 * — a single writer for the issue key, and no circular module dependency.
 *
 * `APP_CONFIG` is re-provided here so the adapter resolves its configuration
 * whether it is imported by `AppModule` (production) or by a test module.
 */
@Module({
  providers: [JiraService, { provide: APP_CONFIG, useFactory: buildAppConfig }],
  exports: [JiraService],
})
export class JiraModule {}
