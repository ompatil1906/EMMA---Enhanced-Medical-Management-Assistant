import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { HandoffEntity } from '../entities/handoff.entity.js';
import { PracticesModule } from '../practices/practices.module.js';
import { HandoffsController } from './handoffs.controller.js';
import { HandoffsService } from './handoffs.service.js';

@Module({
  imports: [PracticesModule, TypeOrmModule.forFeature([HandoffEntity, CallEntity])],
  controllers: [HandoffsController],
  providers: [HandoffsService],
  exports: [HandoffsService],
})
export class HandoffsModule {}
