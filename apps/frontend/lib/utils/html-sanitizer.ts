/**
 * HTML sanitizer — environment-split entry point.
 *
 * Sanitization guards every `dangerouslySetInnerHTML` sink (resume rich-text
 * bullets, LLM output). Whitelist: strong/em/u/a + href/target/rel; everything
 * else — scripts, event handlers, dangerous URL schemes, non-whitelisted tags —
 * is stripped.
 *
 * The real implementation is resolved by the bundler via the
 * `#html-sanitizer-impl` conditional import (see package.json):
 *   - browser  -> ./html-sanitizer.browser.ts  (DOMPurify + native DOM, ~20 KB)
 *   - default  -> ./html-sanitizer.server.ts   (sanitize-html, DOM-free, server-only)
 *
 * This keeps jsdom out of the Server Component graph AND the heavier
 * server sanitizer out of the client bundle. Both paths enforce the same
 * whitelist and pass the same XSS attack battery (tests/html-sanitizer.test.ts).
 */
export { sanitizeHtml } from '#html-sanitizer-impl';
