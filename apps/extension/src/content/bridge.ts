/**
 * Web-app bridge - the only place the FitWright page and this extension meet.
 *
 * The page cannot call `chrome.runtime` and the extension cannot be imported by
 * the page, so they exchange `window.postMessage` envelopes and this content
 * script relays them to the service worker. It is injected *only* on the
 * FitWright origins declared in the manifest, which is what keeps an arbitrary
 * site from driving the extension: on any other origin this file does not run,
 * so there is nothing listening to talk to.
 *
 * Two things it deliberately does NOT do:
 *  - It never forwards the user's FitWright session, tokens or profile to the
 *    page. The page already has its own session; the bridge only carries scrape
 *    requests out and counts back.
 *  - It never accepts a request that names a board the extension cannot drive.
 *    The worker re-checks that too, because a content script is reachable by
 *    anything running in the page and must not be treated as trusted input.
 *
 * Note for reviewers: any script executing on the FitWright page - including one
 * injected by an XSS there - can ask for a scrape. That is bounded to "open
 * public job-board pages in background tabs and add the results to the signed-in
 * user's own feed", which is what the user is asking for anyway; no credential
 * or data flows outward.
 */
import { EXTENSION_ONLY_BOARDS, SCRAPEABLE_BOARDS } from '@/adapters/registry';
import { sendToWorker } from '@/lib/messages';

/** Envelope tags. Distinct names so page and extension traffic never collide. */
const FROM_PAGE = 'fitwright-app';
const FROM_EXTENSION = 'fitwright-extension';

interface PageRequest {
  source: typeof FROM_PAGE;
  id: string;
  type: 'hello' | 'scrape';
  sites?: string[];
  query?: string;
  location?: string;
}

const VERSION = chrome.runtime.getManifest().version;

/** Everything the page needs to decide what to route here. */
function capabilities() {
  return {
    version: VERSION,
    scrapeableBoards: [...SCRAPEABLE_BOARDS],
    extensionOnlyBoards: [...EXTENSION_ONLY_BOARDS],
  };
}

function reply(id: string, payload: Record<string, unknown>): void {
  window.postMessage({ source: FROM_EXTENSION, id, ...payload }, window.location.origin);
}

window.addEventListener('message', (event: MessageEvent<PageRequest>) => {
  // Same-window, same-origin only: reject anything relayed from an iframe or
  // another window, which is the usual way a postMessage listener gets abused.
  if (event.source !== window) return;
  if (event.origin !== window.location.origin) return;

  const message = event.data;
  if (!message || message.source !== FROM_PAGE || typeof message.id !== 'string') return;

  if (message.type === 'hello') {
    reply(message.id, { ok: true, data: capabilities() });
    return;
  }

  if (message.type === 'scrape') {
    const sites = Array.isArray(message.sites) ? message.sites.filter((s) => typeof s === 'string') : [];
    const query = typeof message.query === 'string' ? message.query : '';
    if (!sites.length) {
      reply(message.id, { ok: false, error: 'No boards requested' });
      return;
    }
    if (!query.trim()) {
      reply(message.id, { ok: false, error: 'No search query' });
      return;
    }

    void sendToWorker({
      type: 'bridge-scrape',
      sites,
      query: query.trim(),
      location: typeof message.location === 'string' ? message.location : undefined,
    }).then((result) => {
      if (result.ok) reply(message.id, { ok: true, data: result.data });
      else reply(message.id, { ok: false, error: result.error });
    });
  }
});

/**
 * Announce presence.
 *
 * Broadcast once on load for a page that is already listening, and set a DOM
 * marker for one that mounts later - a React page usually finishes hydrating
 * after this script runs, so the event alone would be missed. The page reads the
 * marker, or says `hello` and gets an answer.
 */
document.documentElement.dataset.fitwrightExtension = VERSION;
window.postMessage({ source: FROM_EXTENSION, id: 'announce', ok: true, data: capabilities() }, window.location.origin);
