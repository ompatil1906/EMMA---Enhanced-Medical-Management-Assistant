import { Body, Controller, HttpCode, HttpStatus, Post } from '@nestjs/common';
import { ApiBadRequestResponse, ApiBody, ApiNotFoundResponse, ApiOkResponse, ApiOperation, ApiTags } from '@nestjs/swagger';
import { Envelope } from '../common/api/envelope.js';
import { CallsService } from './calls.service.js';
import { SyncJiraDto } from './dto/create-call-summary.dto.js';
import { CallSummaryResponseDto } from './dto/call-summary-responses.dto.js';

/**
 * Staff-initiated re-sync of a stored call summary into the external task
 * platform (Jira). Lives under `calls` because it delegates to `CallsService`,
 * which owns the summary row and its idempotency rules — a retry can never
 * create a second issue while `jira_issue_key` is set.
 */
@ApiTags('Jira')
@Controller('api/v1/integrations/jira')
export class JiraSyncController {
  constructor(private readonly calls: CallsService) {}

  @Post('sync')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Re-sync a call summary to the external task platform',
    description:
      'Replay-safe: when the summary already carries an issue key the stored link is returned unchanged. Failures are reported in `jira.error` and never reported as success.',
  })
  @ApiBody({ type: SyncJiraDto })
  @ApiOkResponse({ type: CallSummaryResponseDto, description: 'Summary with its task-platform link' })
  @ApiNotFoundResponse({ description: 'Unknown call or no summary yet — NOT_FOUND envelope (404)' })
  @ApiBadRequestResponse({ description: 'Validation failed — VALIDATION_FAILED envelope (400)' })
  async sync(@Body() dto: SyncJiraDto): Promise<Envelope<CallSummaryResponseDto>> {
    return this.calls.resyncSummary(dto.callId);
  }
}
