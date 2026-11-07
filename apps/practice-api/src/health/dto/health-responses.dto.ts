import { ApiProperty } from '@nestjs/swagger';

/** GET /api/v1/health — liveness of the process and database connectivity. */
export class HealthResponseDto {
  @ApiProperty({ description: "Always \"ok\" while the process serves requests" })
  status!: string;

  @ApiProperty({ type: String, description: '"up" when the database answers a probe query, otherwise "down"' })
  db!: string;

  @ApiProperty({ type: Number, description: 'Process uptime in whole seconds' })
  uptimeSeconds!: number;
}
