import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { PatientRequestEntity } from '../entities/patient-request.entity.js';
import { PracticesModule } from '../practices/practices.module.js';
import { RequestsController } from './requests.controller.js';
import { RequestsService } from './requests.service.js';

@Module({
  imports: [PracticesModule, TypeOrmModule.forFeature([PatientRequestEntity, CallEntity])],
  controllers: [RequestsController],
  providers: [RequestsService],
  exports: [RequestsService],
})
export class RequestsModule {}
