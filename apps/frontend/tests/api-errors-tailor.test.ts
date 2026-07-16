/**
 * Regression guard for the Resume Tailor production incident: a 503 from the
 * Heroku router returns an HTML "Application Error" page, and the frontend must
 * NEVER surface that HTML to the user. Covers both the shared `parseError`
 * helper and the `previewImproveResume` API path (which feeds the Tailor page).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, parseError, toMessage, looksLikeHtml } from '@/lib/api/errors';
import { previewImproveResume } from '@/lib/api/resume';

const HEROKU_503_HTML =
  '<!DOCTYPE html>\n<html><head><title>Application Error</title></head>' +
  '<body><div class="message"><h2>Application Error</h2></div></body></html>';

describe('parseError — never leaks HTML', () => {
  it('maps a Heroku 503 HTML page to a clean, status-specific message', async () => {
    const err = await parseError(new Response(HEROKU_503_HTML, { status: 503 }));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(503);
    expect(err.message).not.toContain('<');
    expect(err.message.toLowerCase()).toContain('temporarily unavailable');
  });

  it('maps a 502 HTML page to a clean message', async () => {
    const err = await parseError(new Response('<html>bad gateway</html>', { status: 502 }));
    expect(err.message).not.toContain('<');
    expect(looksLikeHtml(err.message)).toBe(false);
  });

  it('uses the backend ADR-7 envelope message when present', async () => {
    const body = JSON.stringify({
      error: { code: 'llm_unavailable', message: 'The AI provider is down.' },
    });
    const err = await parseError(new Response(body, { status: 500 }));
    expect(err.code).toBe('llm_unavailable');
    expect(err.message).toBe('The AI provider is down.');
  });

  it('uses the FastAPI {detail} message when present', async () => {
    const body = JSON.stringify({ detail: 'Resume tailoring timed out after 240s.' });
    const err = await parseError(new Response(body, { status: 504 }));
    expect(err.message).toBe('Resume tailoring timed out after 240s.');
  });

  it('surfaces a friendly rate-limit message on 429', async () => {
    const err = await parseError(
      new Response('', { status: 429, headers: { 'Retry-After': '30' } })
    );
    expect(err.isRateLimited).toBe(true);
    expect(err.message.toLowerCase()).toContain('fast');
  });
});

describe('toMessage — sanitizes stray HTML', () => {
  it('never returns an HTML string even if an Error carries one', () => {
    expect(toMessage(new Error(HEROKU_503_HTML), 'fallback')).toBe('fallback');
  });
  it('passes through a normal message', () => {
    expect(toMessage(new Error('Real problem'), 'fallback')).toBe('Real problem');
  });
});

describe('previewImproveResume — Tailor path', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('throws a clean ApiError (no HTML) on a 503 Heroku page', async () => {
    fetchMock.mockResolvedValue(new Response(HEROKU_503_HTML, { status: 503 }));
    await expect(previewImproveResume('r1', 'j1')).rejects.toMatchObject({
      status: 503,
    });
    await previewImproveResume('r1', 'j1').catch((e) => {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).message).not.toContain('<');
    });
  });
});
