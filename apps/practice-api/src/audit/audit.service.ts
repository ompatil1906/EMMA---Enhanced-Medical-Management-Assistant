import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, type QueryDeepPartialEntity } from 'typeorm';
import { CallEntity } from '../entities/call.entity.js';
import { AuditEventEntity } from '../entities/audit-event.entity.js';
import { ApiError } from '../common/api/api-error.js';
import { CreateAuditEventDto } from './dto/create-audit-event.dto.js';
import { AuditEventResponseDto, mapAuditEvent } from './dto/audit-responses.dto.js';
import { PracticesService } from '../practices/practices.service.js';

/**
 * Append-only audit trail: this service exposes ONLY `append` and
 * conceptually owns a repository that is only ever used with `insert(...)`.
 * Deliberately no update/patch/delete/save methods and no HTTP routes that
 * could mutate or remove audit rows. (Prototype flows never delete call rows,
 * and the migration's FKs use ON DELETE RESTRICT to protect the trail.)
 */
@Injectable()
export class AuditService {
  constructor(
    private readonly practices: PracticesService,
    @InjectRepository(AuditEventEntity)
    private readonly events: Repository<AuditEventEntity>,
    @InjectRepository(CallEntity)
    private readonly calls: Repository<CallEntity>,
  ) {}

  /** Appends one immutable audit event and echoes the stored row. */
  public async append(dto: CreateAuditEventDto): Promise<AuditEventResponseDto> {
    await this.practices.requirePractice(dto.practiceId);
    await this.resolveCallScope(dto.practiceId, dto.callId);

    const entity = new AuditEventEntity();
    entity.id = crypto.randomUUID();
    entity.practiceId = dto.practiceId;
    entity.callId = dto.callId ?? null;
    entity.eventType = dto.eventType;
    entity.eventPayload = dto.eventPayload;
    entity.createdAt = new Date();
    await this.events.insert(entity as QueryDeepPartialEntity<AuditEventEntity>);
    return mapAuditEvent(entity);
  }

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
