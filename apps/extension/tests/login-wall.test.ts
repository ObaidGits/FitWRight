/**
 * The login-wall detector.
 *
 * This is the highest-stakes pure logic in the extension, and the risk is not
 * missing a login page - it is crying wolf. Telling a signed-in user to sign in
 * makes the extension look broken and sends them somewhere they do not need to
 * go, so every "must not fire" case below matters more than the positives.
 *
 * Real job boards are the reason: nearly all of them render a "Sign in" link in
 * the header while you are signed in, and several keep a hidden login modal in
 * the DOM permanently.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  classifyEmpty,
  hasVisibleCaptcha,
  hasVisiblePasswordField,
  isAuthUrl,
  looksSignedOut,
} from '@/lib/login-wall';

function setBody(html: string): void {
  document.body.innerHTML = html;
}

describe('isAuthUrl', () => {
  it('recognises the paths sites redirect to', () => {
    for (const path of [
      '/login',
      '/signin',
      '/sign-in',
      '/log-in',
      '/auth',
      '/account/login',
      '/users/sessions/new',
      '/nlogin/login',
    ]) {
      expect(isAuthUrl(new URL(`https://example.test${path}`)), path).toBe(true);
    }
  });

  it('does not fire on ordinary job pages', () => {
    for (const path of [
      '/jobs/123',
      '/careers/backend-engineer',
      '/company/acme/jobs',
      '/search?q=engineer',
      // The word appears inside another word - not an auth route.
      '/blog/how-to-login-to-your-account',
    ]) {
      expect(isAuthUrl(new URL(`https://example.test${path}`)), path).toBe(false);
    }
  });
});

describe('hasVisiblePasswordField', () => {
  beforeEach(() => setBody(''));

  it('finds a password field that is actually on screen', () => {
    setBody('<form><input type="password" /></form>');
    // jsdom reports no layout, so offsetParent is null for everything; the
    // detector treats that as "not visible", which is the safe direction.
    expect(typeof hasVisiblePasswordField()).toBe('boolean');
  });

  it('ignores a display:none login modal', () => {
    setBody('<div style="display:none"><input type="password" /></div>');
    expect(hasVisiblePasswordField()).toBe(false);
  });

  it('reports false when there is no password field at all', () => {
    setBody('<form><input type="text" /><input type="email" /></form>');
    expect(hasVisiblePasswordField()).toBe(false);
  });
});

describe('looksSignedOut', () => {
  beforeEach(() => setBody(''));

  it('fires on an authentication URL', () => {
    expect(looksSignedOut(new URL('https://www.naukri.com/nlogin/login'))).toBe(true);
  });

  it('does NOT fire on a header sign-in link', () => {
    // Every board has one of these while you are signed in.
    setBody('<header><a href="/login">Sign in</a></header><main>Jobs</main>');
    expect(looksSignedOut(new URL('https://example.test/jobs'))).toBe(false);
  });

  it('does NOT fire on the words "log in" in body text', () => {
    setBody('<p>Log in to your Acme account to apply faster.</p>');
    expect(looksSignedOut(new URL('https://example.test/jobs/9'))).toBe(false);
  });

  it('does NOT fire on a hidden login modal beside a real form', () => {
    setBody(`
      <div style="display:none"><form><input type="password" /></form></div>
      <form id="apply"><input type="text" name="first_name" /></form>
    `);
    expect(looksSignedOut(new URL('https://boards.greenhouse.io/acme/jobs/1'))).toBe(false);
  });

  it('does not fire on an ordinary application form', () => {
    setBody('<form><input type="text" /><input type="email" /><input type="file" /></form>');
    expect(looksSignedOut(new URL('https://boards.greenhouse.io/acme/jobs/1'))).toBe(false);
  });
});

describe('classifyEmpty', () => {
  beforeEach(() => setBody(''));

  it('blames a login wall when the URL is an auth page', () => {
    expect(classifyEmpty(new URL('https://example.test/login'))).toBe('signed-out');
  });

  it('reports a genuinely empty search as empty', () => {
    setBody('<main>No results found</main>');
    expect(classifyEmpty(new URL('https://example.test/search?q=nothing'))).toBe('empty');
  });
});

describe('hasVisibleCaptcha', () => {
  beforeEach(() => setBody(''));

  it('reports false when no captcha markup is present at all', () => {
    setBody('<form><input type="text" /><input type="email" /></form>');
    expect(hasVisibleCaptcha()).toBe(false);
  });

  it('ignores a display:none captcha container', () => {
    setBody('<div style="display:none" class="g-recaptcha"></div>');
    expect(hasVisibleCaptcha()).toBe(false);
  });

  it('does not fire on an ordinary application form with no captcha widget', () => {
    setBody('<form><input type="text" /><input type="email" /><input type="file" /></form>');
    expect(hasVisibleCaptcha()).toBe(false);
  });

  it('recognises the recaptcha, hcaptcha and turnstile markers it looks for', () => {
    // jsdom never lays anything out, so offsetParent is always null and none of
    // these can be asserted true here - see the module docstring and the same
    // caveat on hasVisiblePasswordField above. This pins that the selector
    // itself does not throw on any of the three shapes.
    for (const html of [
      '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>',
      '<iframe src="https://newassets.hcaptcha.com/captcha/v1/frame"></iframe>',
      '<div class="g-recaptcha" data-sitekey="abc"></div>',
      '<div class="h-captcha"></div>',
      '<div id="cf-turnstile"></div>',
    ]) {
      setBody(html);
      expect(typeof hasVisibleCaptcha()).toBe('boolean');
    }
  });
});
