import sanitizeHtmlLib from 'sanitize-html';

/**
 * Server-side HTML sanitizer (Node, no DOM required).
 *
 * Uses sanitize-html (htmlparser2-based) instead of DOMPurify+jsdom. The old
 * `isomorphic-dompurify` dragged jsdom (~6.7 MB / 900+ files) into the Server
 * Component graph of every resume/print/builder/wizard route; sanitize-html is
 * a mature, audited, DOM-free allow-list sanitizer (~1 MB with deps) that never
 * ships to the browser (see `#html-sanitizer-impl` in package.json — the client
 * gets native DOMPurify instead).
 *
 * Whitelist and behavior are kept identical to the browser path and verified by
 * the XSS attack battery in tests/html-sanitizer.test.ts.
 */

const ALLOWED_TAGS = ['strong', 'em', 'u', 'a'];
const ALLOWED_ATTR: Record<string, string[]> = { a: ['href', 'target', 'rel'] };
const ALLOWED_SCHEMES = ['http', 'https', 'mailto'];

export function sanitizeHtml(dirty: string): string {
  return sanitizeHtmlLib(dirty, {
    allowedTags: ALLOWED_TAGS,
    allowedAttributes: ALLOWED_ATTR,
    allowedSchemes: ALLOWED_SCHEMES,
    allowProtocolRelative: false,
    // Drop non-whitelisted tags but keep their text; script/style content is
    // dropped entirely (sanitize-html's default nonTextTags).
    disallowedTagsMode: 'discard',
    allowedStyles: {},
    parseStyleAttributes: false,
  });
}
