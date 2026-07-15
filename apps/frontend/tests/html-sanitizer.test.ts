import { describe, expect, it } from 'vitest';
import { sanitizeHtml } from '@/lib/utils/html-sanitizer';

/**
 * sanitizeHtml guards every `dangerouslySetInnerHTML` sink (rich-text bullets,
 * LLM output). Whitelist: strong/em/u/a + href/target/rel. Anything else must go.
 */

describe('sanitizeHtml', () => {
  it('keeps whitelisted formatting tags', () => {
    const out = sanitizeHtml('<strong>bold</strong> <em>italic</em> <u>under</u>');
    expect(out).toContain('<strong>bold</strong>');
    expect(out).toContain('<em>italic</em>');
    expect(out).toContain('<u>under</u>');
  });

  it('keeps anchor href', () => {
    const out = sanitizeHtml('<a href="https://example.com">link</a>');
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('link');
  });

  it('strips <script> entirely (tag + content)', () => {
    const out = sanitizeHtml('<script>alert(1)</script>safe');
    expect(out).not.toContain('script');
    expect(out).not.toContain('alert');
    expect(out).toContain('safe');
  });

  it('strips event-handler attributes but keeps the element text', () => {
    const out = sanitizeHtml('<a href="https://x.com" onclick="evil()">click</a>');
    expect(out).not.toContain('onclick');
    expect(out).toContain('click');
  });

  it('removes a non-whitelisted tag while keeping its text', () => {
    const out = sanitizeHtml('<div>plain text</div>');
    expect(out).not.toContain('<div>');
    expect(out).toContain('plain text');
  });

  it('drops dangerous tags like <img onerror>', () => {
    const out = sanitizeHtml('<img src=x onerror="alert(1)">');
    expect(out).not.toContain('img');
    expect(out).not.toContain('onerror');
  });

  // Expanded XSS attack battery. Guards against regressions from the
  // isomorphic-dompurify (jsdom) -> sanitize-html engine swap.
  const ATTACKS = [
    '<script>alert(1)</script>safe',
    '<a href="https://x.com" onclick="evil()">click</a>',
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">js</a>',
    '<a href="  javascript:alert(1)">js2</a>',
    '<a href="JAVASCRIPT:alert(1)">up</a>',
    '<a href="jAvAsCrIpT:alert(1)">mixed</a>',
    '<a href="java\tscript:alert(1)">tab</a>',
    '<svg/onload=alert(1)>',
    '<iframe src="https://evil.com"></iframe>',
    '<a href="data:text/html,<script>alert(1)</script>">data</a>',
    '<p onmouseover="x()">hover</p>',
    '<a href="https://ok.com"><script>alert(1)</script>text</a>',
    '<a href="https://x.com" style="color:red">styled</a>',
    '<a href="vbscript:msgbox(1)">vb</a>',
    '<form action="/x"><input></form>',
    '<a href="https://x.com" onfocus="a()" autofocus>af</a>',
    '<a href="https://x.com" onClick="e()">cap</a>',
    '<style>body{background:url(javascript:alert(1))}</style>x',
  ];

  it.each(ATTACKS)('neutralizes attack vector: %s', (input) => {
    const out = sanitizeHtml(input).toLowerCase();
    expect(out).not.toContain('<script');
    expect(out).not.toContain('<iframe');
    expect(out).not.toContain('<img');
    expect(out).not.toContain('<svg');
    expect(out).not.toContain('<form');
    expect(out).not.toContain('<style');
    expect(out).not.toContain('javascript:');
    expect(out).not.toContain('vbscript:');
    expect(out).not.toContain('data:');
    expect(out).not.toContain('style=');
    expect(out).not.toMatch(/\son\w+\s*=/);
    expect(out).not.toMatch(/\bautofocus\b/);
  });

  it('preserves link attributes target and rel', () => {
    const out = sanitizeHtml('<a href="https://x.com" target="_blank" rel="noopener">x</a>');
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener"');
    expect(out).toContain('href="https://x.com"');
  });

  it('keeps mailto and fragment links', () => {
    expect(sanitizeHtml('<a href="mailto:t@e.com">m</a>')).toContain('mailto:t@e.com');
    expect(sanitizeHtml('<a href="#anchor">a</a>')).toContain('#anchor');
  });
});
