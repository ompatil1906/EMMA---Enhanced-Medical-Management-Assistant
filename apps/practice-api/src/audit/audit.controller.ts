import { Body, Controller, Post } from '@nestjs/common';
import {
  ApiBadRequestResponse,
  ApiBody,
  ApiCreatedResponse,
  ApiNotFoundResponse,
  ApiOperation,
  ApiTags,
} from '@nestjs/swagger';
import { Envelope } from '../common/api/envelope.js';
import { AuditService } from './audit.service.js';
import { CreateAuditEventDto } from './dto/create-audit-event.dto.js';
import { AuditEventResponseDto } from './dto/audit-responses.dto.js';

/**
 * Audit-only routes. Note the deliberate absence of PATCH / PUT / DELETE
 * handlers on this controller: audit rows are append-only, enforced by the
 * service (repository only ever inserts) and missing endpoints return the
 * canonical 404 envelope when addressed.
 */
@ApiTags('Audit Events')
@Controller('api/v1/audit-events')
export class AuditController {
  constructor(private readonly audit: AuditService) {}

  /** The ONLY audit route: append. */
  @Post()
  @ApiOperation({
    summary: 'Append an audit event (never updated, never deleted)',
    description:
      'Write-once audit trail with a structured JSON payload of primitives. There are intentionally no update or delete endpoints.',
  })
  @ApiBody({ type: CreateAuditEventDto })
  @ApiCreatedResponse({ type: AuditEventResponseDto, description: 'Audit event appended' })
  @ApiBadRequestResponse({ description: 'Validation failed — VALIDATION_FAILED envelope (400)' })
  @ApiNotFoundResponse({ description: 'Unknown practice or call — NOT_FOUND envelope (404)' })
  async append(@Body() dto: CreateAuditEventDto): Promise<Envelope<AuditEventResponseDto>> {
    return new Envelope(await this.audit.append(dto));
  }
}
