import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { HandoffEntity } from '../entities/handoff.entity.js';
import { ApiError } from '../common/api/api-error.js';
import { CreateHandoffDto } from './dto/create-handoff.dto.js';
import { mapHandoff, HandoffResponseDto } from './dto/handoff-responses.dto.js';
import { PracticesService } from '../practices/practices.service.js';

@Injectable()
export class HandoffsService {
  constructor(
    private readonly practices: PracticesService,
    @InjectRepository(HandoffEntity)
    private readonly handoffs: Repository<HandoffEntity>,
    @InjectRepository(CallEntity)
    private readonly calls: Repository<CallEntity>,
  ) {}

  /**
   * Records a hand-off request with status defaulting to `requested`. The
   * service never upgrades the status on the caller's behalf: only explicit
   * caller input may set initiated/completed/failed.
   */
  public async create(dto: CreateHandoffDto): Promise<HandoffResponseDto> {
    await this.practices.requirePractice(dto.practiceId);
    await this.resolveCallScope(dto.practiceId, dto.callId);

    const now = new Date();
    const entity = new HandoffEntity();
    entity.id = crypto.randomUUID();
    entity.practiceId = dto.practiceId;
    entity.callId = dto.callId ?? null;
    entity.reason = dto.reason;
    entity.destination = dto.destination;
    entity.status = dto.status ?? 'requested';
    entity.createdAt = now;
    entity.updatedAt = now;
    await this.handoffs.insert(entity);
    return mapHandoff(entity);
  }

  /** Call scoping: must exist and belong to the same practice (else 404). */
  private async resolveCallScope(practiceId: string, callId: string | undefined): Promise<void> {
    if (callId === undefined) {
      return;
    }
    const call = await this.calls.findOneBy({ id: callId });
    if (call === null || call.practiceId !== practiceId) {
      throw ApiError.notFound(`call "${callId}" does not exist for this practice`);
    }
  }
}
