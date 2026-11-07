import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { PracticeEntity } from '../entities/practice.entity.js';
import { ApiError } from '../common/api/api-error.js';

/**
 * Practice lookups shared by every feature that must reference an existing
 * practice. Missing practices produce the canonical 404 envelope.
 */
@Injectable()
export class PracticesService {
  constructor(
    @InjectRepository(PracticeEntity)
    private readonly practices: Repository<PracticeEntity>,
  ) {}

  /** Loads the practice or throws the canonical 404 (NOT_FOUND) error. */
  public async requirePractice(practiceId: string): Promise<PracticeEntity> {
    const found = await this.practices.findOneBy({ id: practiceId });
    if (found === null) {
      throw ApiError.notFound(`practice "${practiceId}" does not exist`);
    }
    return found;
  }

  /** Finishes a practice row with generated id + UTC timestamps (seed/dev only). */
  public async createFictionalPractice(name: string, timezone: string): Promise<PracticeEntity> {
    const now = new Date();
    const entity = new PracticeEntity();
    entity.id = crypto.randomUUID();
    entity.name = name;
    entity.timezone = timezone;
    entity.createdAt = now;
    entity.updatedAt = now;
    await this.practices.insert(entity);
    return entity;
  }
}
