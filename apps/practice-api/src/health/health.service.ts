import { Inject, Injectable } from '@nestjs/common';
import { DataSource } from 'typeorm';
import { JiraService } from '../integrations/jira/jira.service.js';
import { HealthResponseDto, JiraHealthDto } from './dto/health-responses.dto.js';

/** Simple DB connectivity probe: DataSource.isInitialized + `SELECT 1`. */
@Injectable()
export class HealthService {
  constructor(
    @Inject(DataSource) private readonly dataSource: DataSource,
    private readonly jira: JiraService,
  ) {}

  public async getHealth(): Promise<HealthResponseDto> {
    const dbUp = await this.probeDatabase();
    const health = new HealthResponseDto();
    health.status = 'ok';
    health.db = dbUp ? 'up' : 'down';
    health.uptimeSeconds = Math.floor(process.uptime());
    const jira = this.jira.describe();
    const jiraHealth = new JiraHealthDto();
    jiraHealth.mode = jira.mode;
    jiraHealth.ready = jira.ready;
    health.jira = jiraHealth;
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
