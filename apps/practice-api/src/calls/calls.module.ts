import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { HandoffEntity } from '../entities/handoff.entity.js';
import { PatientRequestEntity } from '../entities/patient-request.entity.js';
import { CallsController } from './calls.controller.js';
import { CallsService } from './calls.service.js';
import { PracticesModule } from '../practices/practices.module.js';

@Module({
  imports: [PracticesModule, TypeOrmModule.forFeature([CallEntity, PatientRequestEntity, HandoffEntity])],
  controllers: [CallsController],
  providers: [CallsService],
  exports: [CallsService],
})
export class CallsModule {}
