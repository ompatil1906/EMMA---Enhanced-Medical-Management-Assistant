import { Inject, Injectable } from '@nestjs/common';
import { DataSource } from 'typeorm';
import { HealthResponseDto } from './dto/health-responses.dto.js';

/** Simple DB connectivity probe: DataSource.isInitialized + `SELECT 1`. */
@Injectable()
export class HealthService {
  constructor(@Inject(DataSource) private readonly dataSource: DataSource) {}

  public async getHealth(): Promise<HealthResponseDto> {
    const dbUp = await this.probeDatabase();
    const health = new HealthResponseDto();
    health.status = 'ok';
    health.db = dbUp ? 'up' : 'down';
    health.uptimeSeconds = Math.floor(process.uptime());
    return health;
  }

  private async probeDatabase(): Promise<boolean> {
    if (!this.dataSource.isInitialized) {
      return false;
    }
    try {
      await this.dataSource.query('SELECT 1');
      return true;
    } catch {
      // A downed database keeps the process alive; the field reports it.
      return false;
    }
  }
}
