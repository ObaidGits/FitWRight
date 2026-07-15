import DOMPurify from 'dompurify';

/**
 * Browser-side HTML sanitizer.
 *
 * Uses DOMPurify bound to the native browser DOM (~20 KB, no jsdom). This is
 * the client half of the environment-split sanitizer (see `#html-sanitizer-impl`
 * in package.json); the server half uses sanitize-html. Selected automatically
 * by the bundler's `browser` condition so the heavier server library never
 * enters the client bundle (e.g. the builder's live preview via
 * components/resume/render-template.tsx).
 *
 * Whitelist and behavior are kept identical to the server path and verified by
 * the XSS attack battery in tests/html-sanitizer.test.ts.
 */

const ALLOWED_TAGS = ['strong', 'em', 'u', 'a'];
const ALLOWED_ATTR = ['href', 'target', 'rel'];

export function sanitizeHtml(dirty: string): string {
  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    FORCE_BODY: true,
  });
}
