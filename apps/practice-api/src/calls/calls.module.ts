import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { CallSummaryEntity } from '../entities/call-summary.entity.js';
import { HandoffEntity } from '../entities/handoff.entity.js';
import { PatientRequestEntity } from '../entities/patient-request.entity.js';
import { JiraModule } from '../integrations/jira/jira.module.js';
import { CallsController } from './calls.controller.js';
import { CallsService } from './calls.service.js';
import { JiraSyncController } from './jira-sync.controller.js';
import { PracticesModule } from '../practices/practices.module.js';

@Module({
  imports: [
    PracticesModule,
    JiraModule,
    TypeOrmModule.forFeature([
      CallEntity,
      CallSummaryEntity,
      PatientRequestEntity,
      HandoffEntity,
    ]),
  ],
  controllers: [CallsController, JiraSyncController],
  providers: [CallsService],
  exports: [CallsService],
})
export class CallsModule {}
