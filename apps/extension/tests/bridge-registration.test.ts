/**
 * Bridge origin registration.
 *
 * The bug this closes: the web-app bridge only ever injected on
 * `localhost:3000`, so anyone running FitWright on another port or a hosted
 * domain saw the Discover page insist the extension was missing while it sat
 * there installed and working. The failure was silent, which is what made it
 * expensive.
 *
 * So the tests care about two things: the origin pattern is derived correctly from
 * whatever the user typed, and the default origin is recognised as already covered
 * so we never register a second script on top of the manifest's own.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  isStaticallyCovered,
  originPattern,
  syncBridgeRegistration,
} from '@/lib/bridge-registration';

describe('originPattern', () => {
  it('reduces a URL to a match pattern for its origin', () => {
    expect(originPattern('http://localhost:3000')).toBe('http://localhost:3000/*');
    expect(originPattern('https://fitwright.example.com')).toBe(
      'https://fitwright.example.com/*',
    );
  });

  it('keeps a non-default port, which is the whole point', () => {
    expect(originPattern('http://localhost:8080')).toBe('http://localhost:8080/*');
    expect(originPattern('http://192.168.1.50:3000')).toBe('http://192.168.1.50:3000/*');
  });

  it('ignores a path, since permission is per origin', () => {
    expect(originPattern('http://localhost:3000/discovery?x=1')).toBe('http://localhost:3000/*');
  });

  it('refuses anything that is not http(s)', () => {
    expect(originPattern('file:///tmp/index.html')).toBeNull();
    expect(originPattern('chrome-extension://abc/page.html')).toBeNull();
    expect(originPattern('not a url')).toBeNull();
    expect(originPattern('')).toBeNull();
  });
});

describe('static coverage', () => {
  it('knows the two origins the manifest already handles', () => {
    expect(isStaticallyCovered('http://localhost:3000/*')).toBe(true);
    expect(isStaticallyCovered('http://127.0.0.1:3000/*')).toBe(true);
  });

  it('does not claim coverage of a different port', () => {
    // This is the case that was broken: same host, different port, no bridge.
    expect(isStaticallyCovered('http://localhost:8080/*')).toBe(false);
    expect(isStaticallyCovered('https://fitwright.example.com/*')).toBe(false);
  });
});

describe('syncBridgeRegistration', () => {
  let registered: unknown[];
  let unregistered: string[][];

  beforeEach(() => {
    registered = [];
    unregistered = [];
    (globalThis as unknown as { chrome: unknown }).chrome = {
      scripting: {
        registerContentScripts: async (scripts: unknown[]) => {
          registered.push(...scripts);
        },
        getRegisteredContentScripts: async () => [],
        unregisterContentScripts: async ({ ids }: { ids: string[] }) => {
          unregistered.push(ids);
        },
      },
      permissions: {
        contains: async () => true,
        request: async () => true,
      },
    };
  });

  it('does not double-register the default origin', async () => {
    // The manifest already injects there; a second registration would run the
    // bridge twice and duplicate every message.
    expect(await syncBridgeRegistration('http://localhost:3000')).toBe('static');
    expect(registered).toHaveLength(0);
  });

  it('registers a custom origin', async () => {
    expect(await syncBridgeRegistration('http://localhost:8080')).toBe('registered');
    expect(registered).toHaveLength(1);
    expect((registered[0] as { matches: string[] }).matches).toEqual(['http://localhost:8080/*']);
    expect((registered[0] as { js: string[] }).js).toEqual(['bridge.js']);
  });

  it('reports when permission is missing instead of failing quietly', async () => {
    (globalThis as unknown as { chrome: { permissions: unknown } }).chrome.permissions = {
      contains: async () => false,
      request: async () => false,
    };
    expect(await syncBridgeRegistration('https://fitwright.example.com')).toBe('needs-permission');
    expect(registered).toHaveLength(0);
  });

  it('rejects an unusable URL', async () => {
    expect(await syncBridgeRegistration('nonsense')).toBe('invalid');
  });

  it('says so when the browser has no scripting API', async () => {
    (globalThis as unknown as { chrome: Record<string, unknown> }).chrome = { permissions: {} };
    expect(await syncBridgeRegistration('http://localhost:8080')).toBe('unsupported');
  });

  it('clears a previous registration before adding one, so switching URLs is clean', async () => {
    (
      globalThis as unknown as {
        chrome: { scripting: { getRegisteredContentScripts: () => Promise<unknown[]> } };
      }
    ).chrome.scripting.getRegisteredContentScripts = async () => [
      { id: 'fitwright-bridge-dynamic' },
    ];
    vi.spyOn(console, 'error').mockImplementation(() => {});

    await syncBridgeRegistration('http://localhost:9999');
    expect(unregistered).toEqual([['fitwright-bridge-dynamic']]);
  });
});
