import { Module } from '@nestjs/common';
import { JiraModule } from '../integrations/jira/jira.module.js';
import { HealthController } from './health.controller.js';
import { HealthService } from './health.service.js';

@Module({
  imports: [JiraModule],
  controllers: [HealthController],
  providers: [HealthService],
})
export class HealthModule {}
