import { Body, Controller, Get, HttpCode, HttpStatus, Param, ParseUUIDPipe, Post, Res } from '@nestjs/common';
import { ApiBody, ApiCreatedResponse, ApiNotFoundResponse, ApiOkResponse, ApiOperation, ApiTags, ApiBadRequestResponse } from '@nestjs/swagger';
import type { Response } from 'express';
import { Envelope } from '../common/api/envelope.js';
import { CallsService } from './calls.service.js';
import { CreateCallDto } from './dto/create-call.dto.js';
import { CallResponseDto, CallWithSummariesDto } from './dto/call-responses.dto.js';

/**
 * POST /api/v1/calls is font-driven by the voice gateway and must be
 * idempotent per (practice, external call id) while returning distinct
 * statuses for "created" (201) versus "replayed existing" (200 +
 * meta.existing). Nest pins the status from static route metadata, so this
 * handler replies through the express response directly.
 */
@ApiTags('Calls')
@Controller('api/v1/calls')
export class CallsController {
  constructor(private readonly calls: CallsService) {}

  @Post()
  @ApiOperation({
    summary: 'Create a call record (idempotent per practice + external call id)',
    description:
      'Returns 201 for a new record. When (practiceId, externalCallId) already exists the existing record is returned with HTTP 200 and meta.existing=true. Safe to call repeatedly.',
  })
  @ApiBody({ type: CreateCallDto })
  @ApiCreatedResponse({ type: CallResponseDto, description: 'Call created (meta omitted)' })
  @ApiOkResponse({ type: CallResponseDto, description: 'Existing call replayed (meta.existing=true)' })
  @ApiBadRequestResponse({ description: 'Validation failed — VALIDATION_FAILED envelope (400)' })
  async create(@Body() dto: CreateCallDto, @Res() res: Response): Promise<void> {
    const result = await this.calls.createOrReturnExisting(dto);
    if (result.existing) {
      res.status(HttpStatus.OK).json({ data: result.call, meta: { existing: true } });
      return;
    }
    res.status(HttpStatus.CREATED).json({ data: result.call });
  }

  @Get(':id')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Fetch a call with request and handoff summaries' })
  @ApiOkResponse({ type: CallWithSummariesDto, description: 'Call record + grouped summaries' })
  @ApiNotFoundResponse({ description: 'Unknown call id — NOT_FOUND envelope (404)' })
  async getCall(
    @Param('id', new ParseUUIDPipe({ version: '4', errorHttpStatusCode: HttpStatus.BAD_REQUEST }))
    id: string,
  ): Promise<Envelope<CallWithSummariesDto>> {
    return this.calls.getWithSummaries(id);
  }
}
