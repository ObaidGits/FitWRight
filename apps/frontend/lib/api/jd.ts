/**
 * JD-from-URL API (P3 §D, Requirement 9 + JD v2 enhancements).
 *
 * Posts a job URL to the SSRF-hardened backend fetcher and returns the extracted
 * description for the tailor flow. ``lowConfidence`` signals the UI to ask the
 * user to verify/edit the text before generating (never auto-tailors). Errors
 * are opaque by design (the backend never reveals *why* a fetch was blocked).
 *
 * The v2 cascade adds optional explainability metadata (``schemaVersion``,
 * ``confidenceLevel``, ``source``, ``suggestions``, ``warnings``, ``errorCode``).
 * These are all optional so a v1 backend response still parses unchanged; the UI
 * detects v2 by the presence of ``schemaVersion``.
 */
import { apiPost } from './client';

export type JdConfidence = 'HIGH' | 'MEDIUM' | 'LOW';

export interface JdFromUrl {
  content: string;
  lowConfidence: boolean;
  sourceUrl: string;
  // v2 (optional)
  schemaVersion?: string;
  confidenceLevel?: JdConfidence;
  confidenceScore?: number;
  source?: string;
  partial?: boolean;
  errorCode?: string;
  language?: string;
  suggestions?: string[];
  warnings?: string[];
}

interface RawJd {
  content: string;
  low_confidence: boolean;
  source_url: string;
  schema_version?: string | null;
  confidence_level?: JdConfidence | null;
  confidence_score?: number | null;
  source?: string | null;
  partial?: boolean | null;
  error_code?: string | null;
  language?: string | null;
  suggestions?: string[] | null;
  warnings?: string[] | null;
}

/** Fetch + extract a JD from a URL. ``useAi`` opts into bounded LLM cleanup. */
export async function fetchJdFromUrl(url: string, useAi = false): Promise<JdFromUrl> {
  const res = await apiPost('/jobs/fetch-url', { url, use_ai: useAi });
  if (res.status === 404) {
    throw new Error('Importing from a URL is currently unavailable.');
  }
  if (res.status === 429) {
    throw new Error('Too many imports right now. Please wait a moment and try again.');
  }
  if (!res.ok) {
    // Opaque backend failure - show a friendly, non-leaky message.
    throw new Error("We couldn't read that job posting. Paste the description instead.");
  }
  const body = (await res.json()) as RawJd;
  return {
    content: body.content,
    lowConfidence: body.low_confidence,
    sourceUrl: body.source_url,
    schemaVersion: body.schema_version ?? undefined,
    confidenceLevel: body.confidence_level ?? undefined,
    confidenceScore: body.confidence_score ?? undefined,
    source: body.source ?? undefined,
    partial: body.partial ?? undefined,
    errorCode: body.error_code ?? undefined,
    language: body.language ?? undefined,
    suggestions: body.suggestions ?? undefined,
    warnings: body.warnings ?? undefined,
  };
}

/** Human-readable label for the extraction source (for the "how" tooltip). */
export function jdSourceLabel(source?: string): string {
  switch (source) {
    case 'platform_api':
      return 'official job-board API';
    case 'json_ld':
      return 'structured page data';
    case 'hydration_json':
      return 'embedded page data';
    case 'dom_semantic':
      return 'page content';
    case 'headless_dom':
      return 'rendered browser page';
    case 'pdf_ocr':
      return 'PDF document';
    default:
      return 'the page';
  }
}
