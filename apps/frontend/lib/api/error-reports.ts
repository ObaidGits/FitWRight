import { apiPost } from '@/lib/api/client';
import { readJson } from '@/lib/api/errors';

export type ErrorReportStreamPhase = 'open' | 'before-event' | 'after-event';

/** Privacy-minimized, immutable snapshot captured when a Tailor operation fails. */
export interface ErrorReportPayload {
  readonly clientReportId: string;
  readonly issueType: 'tailor_generation_failed';
  readonly message: string;
  readonly errorCode?: string | null;
  readonly httpStatus?: number | null;
  readonly retryable: boolean;
  readonly apiMethod: 'GET' | 'POST';
  readonly apiRoute: string;
  readonly operationRequestId?: string | null;
  readonly apiRequestId?: string | null;
  readonly pipelineStage?: string | null;
  readonly streamPhase?: ErrorReportStreamPhase | null;
  readonly fallbackSafe?: boolean | null;
}

export interface ErrorReportReceipt {
  reportId: string;
  createdAt: string;
}

export async function createErrorReport(report: ErrorReportPayload): Promise<ErrorReportReceipt> {
  const response = await apiPost('/error-reports/', report);
  return readJson<ErrorReportReceipt>(response, 'The error report could not be sent.');
}
