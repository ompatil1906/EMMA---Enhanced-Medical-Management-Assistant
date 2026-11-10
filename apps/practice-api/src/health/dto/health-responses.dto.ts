import { ApiProperty } from '@nestjs/swagger';

/** External task-platform (Jira) mirror state, secret-free. */
export class JiraHealthDto {
  @ApiProperty({ description: 'disabled | mock | live', example: 'mock' })
  mode!: string;

  @ApiProperty({ description: 'True when a live integration is fully configured' })
  ready!: boolean;
}

/** GET /api/v1/health — liveness of the process and database connectivity. */
export class HealthResponseDto {
  @ApiProperty({ description: "Always \"ok\" while the process serves requests" })
  status!: string;

  @ApiProperty({ type: String, description: '"up" when the database answers a probe query, otherwise "down"' })
  db!: string;

  @ApiProperty({ type: Number, description: 'Process uptime in whole seconds' })
  uptimeSeconds!: number;

  @ApiProperty({ type: JiraHealthDto, description: 'External task-platform mirror state (no secrets)' })
  jira!: JiraHealthDto;
}
