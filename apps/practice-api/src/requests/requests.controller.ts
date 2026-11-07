import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Res,
} from '@nestjs/common';
import {
  ApiBadRequestResponse,
  ApiBody,
  ApiCreatedResponse,
  ApiHeaders,
  ApiNotFoundResponse,
  ApiOkResponse,
  ApiOperation,
  ApiParam,
  ApiQuery,
  ApiConflictResponse,
  ApiTags,
} from '@nestjs/swagger';
import { Envelope } from '../common/api/envelope.js';
import type { Response } from 'express';
import { PaginationMeta } from '../common/pagination.js';
import { CreateRequestDto } from './dto/create-request.dto.js';
import { ListRequestsQueryDto } from './dto/list-requests-query.dto.js';
import { PatientRequestResponseDto } from './dto/request-responses.dto.js';
import { RequestsService } from './requests.service.js';

/**
 * Request routes. POST /requests is the idempotent creation endpoint keyed by
 * the `Idempotency-Key` request header (body idempotencyKey is a documented
 * fallback); like POST /calls it must distinguish 201-new from 200-replay, so
 * it replies through the express response directly.
 */
@ApiTags('Requests')
@Controller('api/v1')
export class RequestsController {
  constructor(private readonly requests: RequestsService) {}

  @Post('requests')
  @ApiOperation({
    summary: 'Create a patient request (idempotent via Idempotency-Key)',
    description:
      'Replaying the same `Idempotency-Key` with the same structured payload returns the existing record with HTTP 200 and meta.existing=true. The same key with a different payload produces an IDEMPOTENCY_KEY_CONFLICT (409) envelope. Keys are scoped per practice.',
  })
  @ApiBody({ type: CreateRequestDto })
  @ApiHeaders([
    {
      name: 'Idempotency-Key',
      required: false,
      description:
        'Optional key making the call idempotent (header wins over body.idempotencyKey). Unique per practice together with the payload hash.',
    },
  ])
  @ApiCreatedResponse({ type: PatientRequestResponseDto, description: 'Request created (meta omitted)' })
  @ApiOkResponse({ type: PatientRequestResponseDto, description: 'Existing request replayed (meta.existing=true)' })
  @ApiBadRequestResponse({ description: 'Validation failed — VALIDATION_FAILED envelope (400)' })
  @ApiNotFoundResponse({ description: 'Unknown practice or call — NOT_FOUND envelope (404)' })
  @ApiConflictResponse({ description: 'Different payload under a known key — IDEMPOTENCY_KEY_CONFLICT envelope (409)' })
  async create(
    @Body() dto: CreateRequestDto,
    @Headers('idempotency-key') idempotencyKeyHeader: string | undefined,
    @Res() res: Response,
  ): Promise<void> {
    const result = await this.requests.create(dto, idempotencyKeyHeader);
    if (result.existing) {
      res.status(HttpStatus.OK).json({ data: result.request, meta: { existing: true } });
      return;
    }
    res.status(HttpStatus.CREATED).json({ data: result.request });
  }

  @Get('practices/:practiceId/requests')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'List a practice’s requests with pagination and filters' })
  @ApiParam({ name: 'practiceId', description: 'UUID of the practice', required: true })
  @ApiQuery({ name: 'page', type: Number, required: false, description: '1-based page (default 1)' })
  @ApiQuery({ name: 'limit', type: Number, required: false, description: 'Page size, 1–100 (default 20)' })
  @ApiQuery({ name: 'requestType', required: false, description: 'Exact request type match' })
  @ApiQuery({ name: 'status', required: false, description: 'received | completed | failed' })
  @ApiQuery({ name: 'dateFrom', required: false, description: 'created_at ≥ (UTC); bare dates expand to 00:00:00.000Z' })
  @ApiQuery({ name: 'dateTo', required: false, description: 'created_at ≤ (UTC); bare dates expand to 23:59:59.999Z' })
  @ApiOkResponse({ description: 'Enveloped { data, meta: { page, limit, total, nextPage? } }' })
  @ApiNotFoundResponse({ description: 'Unknown practice — NOT_FOUND envelope (404)' })
  async list(
    @Param('practiceId', new ParseUUIDPipe({ version: '4', errorHttpStatusCode: HttpStatus.BAD_REQUEST }))
    practiceId: string,
    @Query() query: ListRequestsQueryDto,
  ): Promise<Envelope<PatientRequestResponseDto[], PaginationMeta>> {
    return this.requests.list(practiceId, query);
  }
}
