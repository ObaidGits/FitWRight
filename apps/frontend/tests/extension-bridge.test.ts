/**
 * Bridge protocol tests.
 *
 * The bridge is the one place the web app depends on a `postMessage` contract it
 * cannot typecheck against the other side, so the envelope shape, the id
 * correlation and the failure modes are pinned here. A fake "extension" replies
 * on the same window, exactly as the content script does.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  detectExtension,
  requestExtensionScrape,
  EXTENSION_ONLY_BOARDS,
} from '@/features/discovery/extension-bridge';

type Responder = (request: Record<string, unknown>) => Record<string, unknown> | null;

/**
 * Install a fake extension that answers page requests on the same window.
 *
 * Replies are dispatched as a constructed MessageEvent rather than via
 * `window.postMessage`, because jsdom leaves `event.source` null on
 * postMessage - and the bridge client deliberately drops any message that did
 * not come from this window. Constructing the event lets the test satisfy that
 * check honestly instead of loosening it in production code.
 */
function reply(id: string, payload: Record<string, unknown>): void {
  window.dispatchEvent(
    new MessageEvent('message', {
      data: { source: 'fitwright-extension', id, ...payload },
      origin: window.location.origin,
      source: window,
    })
  );
}

function fakeExtension(responder: Responder): () => void {
  function onMessage(event: MessageEvent) {
    const data = event.data as Record<string, unknown>;
    if (!data || data.source !== 'fitwright-app') return;
    const response = responder(data);
    if (!response) return; // simulate an extension that never answers
    reply(String(data.id), response);
  }
  window.addEventListener('message', onMessage);
  return () => window.removeEventListener('message', onMessage);
}

const CAPABILITIES = {
  version: '0.1.0',
  scrapeableBoards: ['indeed', 'linkedin', 'instahyre', 'hirist', 'foundit', 'ycombinator'],
  extensionOnlyBoards: ['instahyre', 'hirist', 'foundit', 'ycombinator'],
};

afterEach(() => {
  delete document.documentElement.dataset.fitwrightExtension;
  vi.useRealTimers();
});

describe('detectExtension', () => {
  it('returns capabilities when the extension answers', async () => {
    const stop = fakeExtension((req) =>
      req.type === 'hello' ? { ok: true, data: CAPABILITIES } : null
    );
    document.documentElement.dataset.fitwrightExtension = '0.1.0';

    await expect(detectExtension()).resolves.toEqual(CAPABILITIES);
    stop();
  });

  it('resolves null when nothing is listening', async () => {
    await expect(detectExtension(150)).resolves.toBeNull();
  });

  it('resolves null when the extension reports an error', async () => {
    const stop = fakeExtension(() => ({ ok: false, error: 'boom' }));
    await expect(detectExtension(300)).resolves.toBeNull();
    stop();
  });
});

describe('requestExtensionScrape', () => {
  it('forwards sites, query and location, and returns per-site counts', async () => {
    const seen: Record<string, unknown>[] = [];
    const stop = fakeExtension((req) => {
      seen.push(req);
      return {
        ok: true,
        data: {
          total: 7,
          saved: 5,
          perSite: [
            { source: 'instahyre', found: 4, saved: 3 },
            { source: 'foundit', found: 3, saved: 2 },
          ],
        },
      };
    });

    const result = await requestExtensionScrape({
      sites: ['instahyre', 'foundit'],
      query: 'python developer',
      location: 'Bangalore',
      timeoutMs: 1000,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.total).toBe(7);
      expect(result.data.saved).toBe(5);
      expect(result.data.perSite).toHaveLength(2);
    }
    expect(seen[0]).toMatchObject({
      source: 'fitwright-app',
      type: 'scrape',
      sites: ['instahyre', 'foundit'],
      query: 'python developer',
      location: 'Bangalore',
    });
    stop();
  });

  it('reports a timeout instead of hanging when the extension goes silent', async () => {
    const stop = fakeExtension(() => null);
    const result = await requestExtensionScrape({
      sites: ['hirist'],
      query: 'engineer',
      timeoutMs: 150,
    });
    expect(result).toEqual({ ok: false, error: 'The extension did not respond' });
    stop();
  });

  it('ignores replies whose id does not match the request', async () => {
    const stop = fakeExtension(() => {
      // Answer with a wrong id first, then the correct one.
      reply('someone-elses-request', { ok: true, data: { total: 999, perSite: [] } });
      return { ok: true, data: { total: 1, perSite: [{ source: 'hirist', found: 1 }] } };
    });

    const result = await requestExtensionScrape({
      sites: ['hirist'],
      query: 'engineer',
      timeoutMs: 1000,
    });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data.total).toBe(1);
    stop();
  });
});

describe('EXTENSION_ONLY_BOARDS', () => {
  it('matches the boards the backend cannot reach', () => {
    expect([...EXTENSION_ONLY_BOARDS]).toEqual(['instahyre', 'hirist', 'foundit', 'ycombinator']);
  });
});
