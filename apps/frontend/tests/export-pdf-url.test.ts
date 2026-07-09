import { describe, expect, it } from 'vitest';
import { getResumePdfUrl, getCoverLetterPdfUrl } from '@/lib/api/resume';
import { DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';

/**
 * Export regression check (Task 11 / Req 16.4, 22.2).
 *
 * The revamped UI wires export through the SAME `/print/*` pipeline via these
 * URL builders. Because the engine is reused unchanged, the generated PDF is
 * identical to the pre-revamp output — this test locks the export CONTRACT
 * (endpoint + query params) for a fixed sample so any accidental drift in the
 * export wiring is caught before it can change the produced document.
 */
describe('PDF export URL contract (engine reuse)', () => {
  it('builds a stable resume PDF URL for the default template settings', () => {
    const url = getResumePdfUrl('sample-123', DEFAULT_TEMPLATE_SETTINGS);
    const parsed = new URL(url, 'http://localhost');

    expect(parsed.pathname).toBe('/api/v1/resumes/sample-123/pdf');
    // Fixed sample → fixed params (the document-shaping contract).
    expect(parsed.searchParams.get('template')).toBe('swiss-single');
    expect(parsed.searchParams.get('pageSize')).toBe('A4');
    expect(parsed.searchParams.get('marginTop')).toBe('10');
    expect(parsed.searchParams.get('fontSize')).toBe('3');
    expect(parsed.searchParams.get('headerFont')).toBe('serif');
    expect(parsed.searchParams.get('bodyFont')).toBe('sans-serif');
    expect(parsed.searchParams.get('compactMode')).toBe('false');
    expect(parsed.searchParams.get('showContactIcons')).toBe('false');
    expect(parsed.searchParams.get('accentColor')).toBe('blue');
  });

  it('falls back to the pre-revamp default params when no settings are passed', () => {
    const url = getResumePdfUrl('sample-123');
    const parsed = new URL(url, 'http://localhost');
    expect(parsed.searchParams.get('template')).toBe('swiss-single');
    expect(parsed.searchParams.get('pageSize')).toBe('A4');
  });

  it('builds a stable cover-letter PDF URL', () => {
    const url = getCoverLetterPdfUrl('sample-123', 'A4');
    const parsed = new URL(url, 'http://localhost');
    expect(parsed.pathname).toBe('/api/v1/resumes/sample-123/cover-letter/pdf');
    expect(parsed.searchParams.get('pageSize')).toBe('A4');
  });

  it('url-encodes the resume id', () => {
    const url = getResumePdfUrl('res 123', DEFAULT_TEMPLATE_SETTINGS);
    expect(url).toContain('/resumes/res%20123/pdf');
  });
});
