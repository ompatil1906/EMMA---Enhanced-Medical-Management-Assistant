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
import { HandoffsService } from './handoffs.service.js';
import { CreateHandoffDto } from './dto/create-handoff.dto.js';
import { HandoffResponseDto } from './dto/handoff-responses.dto.js';

@ApiTags('Handoffs')
@Controller('api/v1/handoffs')
export class HandoffsController {
  constructor(private readonly handoffs: HandoffsService) {}

  @Post()
  @ApiOperation({
    summary: 'Record a requested human hand-off',
    description:
      'Creates a hand-off record with status defaulting to `requested`. Passable statuses: requested | initiated | completed | failed — provided explicitly by the caller only; this API never asserts that a transfer completed.',
  })
  @ApiBody({ type: CreateHandoffDto })
  @ApiCreatedResponse({ type: HandoffResponseDto, description: 'Hand-off recorded' })
  @ApiBadRequestResponse({ description: 'Validation failed — VALIDATION_FAILED envelope (400)' })
  @ApiNotFoundResponse({ description: 'Unknown practice or call — NOT_FOUND envelope (404)' })
  async create(@Body() dto: CreateHandoffDto): Promise<Envelope<HandoffResponseDto>> {
    return new Envelope(await this.handoffs.create(dto));
  }
}
