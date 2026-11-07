import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { PracticeEntity } from '../entities/practice.entity.js';
import { PracticesService } from './practices.service.js';

/** Shared lookup helpers reaching practices from every other module. */
@Module({
  imports: [TypeOrmModule.forFeature([PracticeEntity])],
  providers: [PracticesService],
  exports: [PracticesService, TypeOrmModule],
})
export class PracticesModule {}
