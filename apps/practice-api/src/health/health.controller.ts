import { Controller, Get } from '@nestjs/common';
import { ApiOkResponse, ApiOperation, ApiTags } from '@nestjs/swagger';
import { Envelope } from '../common/api/envelope.js';
import { HealthService } from './health.service.js';
import { HealthResponseDto } from './dto/health-responses.dto.js';

@ApiTags('Health')
@Controller('api/v1/health')
export class HealthController {
  constructor(private readonly health: HealthService) {}

  @Get()
  @ApiOperation({
    summary: 'Process + database health',
    description:
      'Always HTTP 200 while the process is serving: db "up" means a SELECT 1 probe succeeded (DataSource.isInitialized honoured), "down" keeps the process alive for the orchestrators.',
  })
  @ApiOkResponse({ type: HealthResponseDto, description: '{ status: "ok", db: "up"|"down", uptimeSeconds }' })
  async getHealth(): Promise<Envelope<HealthResponseDto>> {
    return new Envelope(await this.health.getHealth());
  }
}
