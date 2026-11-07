import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { AuditEventEntity } from '../entities/audit-event.entity.js';
import { PracticesModule } from '../practices/practices.module.js';
import { AuditController } from './audit.controller.js';
import { AuditService } from './audit.service.js';

@Module({
  imports: [PracticesModule, TypeOrmModule.forFeature([AuditEventEntity, CallEntity])],
  controllers: [AuditController],
  providers: [AuditService],
  exports: [AuditService],
})
export class AuditModule {}
