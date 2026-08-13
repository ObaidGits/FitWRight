/**
 * Client half of the FitWright Companion bridge.
 *
 * Some boards cannot be reached from the server at all - Instahyre and Foundit
 * sit behind Cloudflare/Akamai and gate results on a login, and YC's board is
 * client-rendered - so those searches are handed to the browser extension,
 * which runs on the user's own IP inside their own logged-in session. The
 * extension harvests the pages in background tabs, posts the rows to the same
 * `/extension/scrape` endpoint the feed reads from, and reports counts back
 * here.
 *
 * Transport is `window.postMessage`, because a page cannot call `chrome.runtime`
 * directly. The extension injects a content script on this origin that relays
 * our envelopes to its service worker. Everything degrades quietly: with no
 * extension installed, `detectExtension` resolves to null and the caller keeps
 * to the server-side lane.
 */
const FROM_PAGE = 'fitwright-app';
const FROM_EXTENSION = 'fitwright-extension';

export interface ExtensionCapabilities {
  version: string;
  /** Every board the extension can drive a search on. */
  scrapeableBoards: string[];
  /** Boards that ONLY work through the extension. */
  extensionOnlyBoards: string[];
}

export interface ExtensionScrapeResult {
  /** Rows harvested off the boards. */
  total: number;
  /** Rows stored as new; the remainder were already in the feed. */
  saved: number;
  perSite: { source: string; found: number; saved: number; error?: string }[];
}

interface ExtensionReply {
  source: typeof FROM_EXTENSION;
  id: string;
  ok: boolean;
  data?: unknown;
  error?: string;
}

function isReply(value: unknown): value is ExtensionReply {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as ExtensionReply).source === FROM_EXTENSION &&
    typeof (value as ExtensionReply).id === 'string'
  );
}

let requestCounter = 0;

function nextId(): string {
  requestCounter += 1;
  return `fw-${Date.now()}-${requestCounter}`;
}

/**
 * Send one request and wait for the matching reply.
 *
 * Every call carries a fresh id and the listener is removed on settle, so
 * overlapping requests (a scrape running while a hello is in flight) cannot
 * resolve each other's promises.
 */
function ask<T>(
  payload: Record<string, unknown>,
  timeoutMs: number
): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  if (typeof window === 'undefined') {
    return Promise.resolve({ ok: false as const, error: 'Not in a browser' });
  }

  const id = nextId();

  return new Promise((resolve) => {
    const settle = (result: { ok: true; data: T } | { ok: false; error: string }) => {
      window.removeEventListener('message', onMessage);
      clearTimeout(timer);
      resolve(result);
    };

    function onMessage(event: MessageEvent) {
      if (event.source !== window) return;
      if (!isReply(event.data) || event.data.id !== id) return;
      if (event.data.ok) settle({ ok: true, data: event.data.data as T });
      else settle({ ok: false, error: event.data.error ?? 'Extension error' });
    }

    const timer = setTimeout(
      () => settle({ ok: false, error: 'The extension did not respond' }),
      timeoutMs
    );

    window.addEventListener('message', onMessage);
    window.postMessage({ source: FROM_PAGE, id, ...payload }, window.location.origin);
  });
}

/** A job description the extension read from a page in the user's own browser. */
export interface ExtensionJobDescription {
  description: string;
  title: string;
  company: string;
  source: string;
}

/**
 * Ask the extension to read one job posting the server could not fetch.
 *
 * The server is not failing when this is needed - Indeed's robots.txt disallows
 * automated fetching of `/viewjob?`, and the site answers a datacenter IP with 403.
 * Both are correct responses to a server, and neither describes a person opening a
 * link. The extension reads the page the user could read themselves.
 *
 * It also covers the case no fetcher can handle: a posting rendered into a modal or
 * keyed by a query parameter, where the URL names a search page and the job only
 * exists after the page's own scripts run.
 *
 * Slow by nature (a real background tab), so the timeout is generous.
 */
export async function requestJobDescription(
  url: string,
  timeoutMs = 45_000
): Promise<{ ok: true; data: ExtensionJobDescription } | { ok: false; error: string }> {
  return ask<ExtensionJobDescription>({ type: 'read-jd', url }, timeoutMs);
}

/**
 * Is the companion extension present, and what can it do?
 *
 * Resolves to null when it is not installed. The DOM marker is checked first
 * because the content script sets it at `document_start`, well before React
 * hydrates - so in the common case this answers without a round trip, and the
 * `hello` handshake is only needed to learn the board lists.
 */
export async function detectExtension(timeoutMs = 1500): Promise<ExtensionCapabilities | null> {
  if (typeof document === 'undefined') return null;
  const marked = Boolean(document.documentElement.dataset.fitwrightExtension);

  const result = await ask<ExtensionCapabilities>({ type: 'hello' }, marked ? timeoutMs : 800);
  return result.ok ? result.data : null;
}

/**
 * Ask the extension to search *sites* for *query* and push what it finds into
 * the user's feed.
 *
 * Slow by nature - the extension opens each board in a real background tab and
 * waits for it to render - so the default timeout is generous and the caller
 * should show progress rather than blocking the UI.
 */
export async function requestExtensionScrape(request: {
  sites: string[];
  query: string;
  location?: string;
  timeoutMs?: number;
}): Promise<{ ok: true; data: ExtensionScrapeResult } | { ok: false; error: string }> {
  const perBoardBudget = 40_000;
  const timeoutMs = request.timeoutMs ?? Math.max(60_000, request.sites.length * perBoardBudget);

  return ask<ExtensionScrapeResult>(
    {
      type: 'scrape',
      sites: request.sites,
      query: request.query,
      location: request.location ?? '',
    },
    timeoutMs
  );
}

/**
 * Boards that only the extension can reach.
 *
 * Mirrors `EXTENSION_ONLY_BOARDS` in the extension so the Discovery page can
 * label and route them before any handshake has happened. The live list from
 * `detectExtension` takes precedence once it arrives - this is the fallback for
 * "extension not installed yet", where we still want to explain why those
 * boards are unavailable.
 */
export const EXTENSION_ONLY_BOARDS = ['instahyre', 'hirist', 'foundit', 'ycombinator'] as const;

/** Install/setup instructions live with the extension source. */
export const EXTENSION_SETUP_PATH = 'apps/extension/README.md';
