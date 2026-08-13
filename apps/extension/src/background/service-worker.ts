/**
 * MV3 service worker - the extension's only networked context.
 *
 * Why all network calls live here rather than in the content script:
 *  - The content script runs on the job site's origin, so its `fetch` to
 *    FitWright is cross-origin and subject to that page's CSP, which many job
 *    boards set strictly. The worker runs on the extension origin and is bound
 *    by `host_permissions` instead.
 *  - Session cookies for FitWright ride along here without exposing the
 *    FitWright session to a third-party page's JavaScript.
 *
 * The worker is ephemeral: Chrome kills it after ~30s idle. Nothing is kept in
 * module scope that matters across restarts - all durable state is in
 * `chrome.storage`.
 */
import { SCRAPEABLE_BOARDS, searchUrlFor } from '@/adapters/registry';
import * as api from '@/lib/api';
import { fail, ok, sendToTab } from '@/lib/messages';
import type { PerSiteResult, Reply, ReplyMap, ToWorker } from '@/lib/messages';
import { getSettings, normalizeBaseUrl, rememberCaptured, wasCaptured } from '@/lib/storage';

const SCRAPE_ALARM = 'fitwright-scrape';

// --------------------------------------------------------------------------- //
// Message routing
// --------------------------------------------------------------------------- //

chrome.runtime.onMessage.addListener(
  (message: ToWorker, sender, sendResponse: (reply: Reply<unknown>) => void) => {
    void handle(message, sender)
      .then((reply) => sendResponse(reply))
      .catch((error) => sendResponse(fail(error)));
    return true; // async reply
  },
);

async function handle(message: ToWorker, sender: chrome.runtime.MessageSender): Promise<Reply<unknown>> {
  switch (message.type) {
    case 'ping': {
      const result = await api.ping();
      return ok({
        signedIn: result.ok,
        hasResume: result.has_resume,
        versionOk: result.versionOk,
      });
    }

    case 'capture': {
      // Skip the round trip for something this browser already saved.
      const key = `${message.job.title}|${message.job.company}|${message.job.url}`;
      if (await wasCaptured(key)) {
        return ok({ saved: false, duplicate: true, fingerprint: '' });
      }
      const result = await api.captureJob(message.job);
      await rememberCaptured(key);
      await flashBadge(result.duplicate ? '=' : '+');
      return ok(result);
    }

    case 'match':
      return ok(await api.matchJob(message.description, message.title));

    case 'draft':
      return ok(
        await api.draftAnswer({
          question: message.question,
          description: message.description,
          company: message.company,
          title: message.title,
        }),
      );

    case 'applied':
      return ok(
        await api.markApplied({ fingerprint: message.fingerprint, url: message.url }),
      );

    case 'scrape-results': {
      const result = await api.sendScrapeResults(message.source, message.jobs);
      await flashBadge(String(result.saved));
      return ok(result);
    }

    case 'bridge-scrape': {
      // On-demand run requested by the FitWright web app. Only boards this
      // extension actually knows how to drive are accepted; anything else is
      // reported back rather than silently dropped, so the page can say why.
      const requested = message.sites.filter((site) =>
        (SCRAPEABLE_BOARDS as readonly string[]).includes(site),
      );
      const rejected = message.sites.filter(
        (site) => !(SCRAPEABLE_BOARDS as readonly string[]).includes(site),
      );

      const results = await scrapeEntries(
        requested.map((source) => ({
          source,
          query: message.query,
          location: message.location,
        })),
      );
      for (const source of rejected) {
        results.push({
          source,
          found: 0,
          saved: 0,
          error: 'Board not supported by the extension',
        });
      }

      const total = results.reduce((sum, r) => sum + r.found, 0);
      const saved = results.reduce((sum, r) => sum + r.saved, 0);
      if (saved > 0) await flashBadge(String(saved));
      return ok({ total, saved, perSite: results });
    }

    case 'report-form':
      return ok(
        await api.reportForm({
          fields: message.fields,
          company: message.company,
          ats: message.ats,
          url: message.url,
        }),
      );

    case 'save-answers': {
      const result = await api.saveAnswers({
        answers: message.answers,
        company: message.company,
        ats: message.ats,
        url: message.url,
      });
      if (result.saved > 0) await flashBadge(String(result.saved));
      return ok(result);
    }

    case 'get-profile':
      return ok(await api.getProfile());

    case 'get-resume-pdf':
      return ok(await api.fetchResumePdf());

    case 'open-fitwright': {
      const settings = await getSettings();
      const url = normalizeBaseUrl(settings.apiBaseUrl) + (message.path ?? '/');
      await chrome.tabs.create({ url, index: (sender.tab?.index ?? 0) + 1 });
      return ok(null);
    }

    default:
      return fail('Unknown message');
  }
}

// --------------------------------------------------------------------------- //
// Toolbar badge
// --------------------------------------------------------------------------- //

/** Brief count/status on the toolbar icon - the only ambient feedback we get. */
async function flashBadge(text: string): Promise<void> {
  try {
    await chrome.action.setBadgeBackgroundColor({ color: '#4f46e5' });
    await chrome.action.setBadgeText({ text });
    setTimeout(() => void chrome.action.setBadgeText({ text: '' }), 2500);
  } catch {
    /* action API unavailable during startup - cosmetic only */
  }
}

// --------------------------------------------------------------------------- //
// Scheduled background scraping
// --------------------------------------------------------------------------- //

/**
 * Rebuild the alarm from settings. Called on install and whenever settings
 * change, because MV3 alarms survive worker restarts but not setting edits.
 */
async function syncAlarm(): Promise<void> {
  const settings = await getSettings();
  await chrome.alarms.clear(SCRAPE_ALARM);
  if (!settings.backgroundScrape || !settings.scrapeQueries.length) return;
  await chrome.alarms.create(SCRAPE_ALARM, {
    periodInMinutes: Math.max(30, settings.scrapeIntervalMinutes),
    delayInMinutes: 1,
  });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== SCRAPE_ALARM) return;
  void runScheduledScrape();
});

/**
 * Open each requested search in a background tab, let the content script
 * harvest it, then close the tab.
 *
 * Serialized on purpose: this drives real page loads in the user's browser, and
 * a burst of parallel tabs both spikes memory and looks like automated traffic.
 * One tab at a time with a settle delay behaves like a person browsing.
 *
 * Shared by the scheduled alarm and by on-demand runs the web app asks for
 * through the bridge, so both behave identically - same pacing, same
 * per-board failure isolation.
 */
async function scrapeEntries(
  entries: { source: string; query: string; location?: string }[],
): Promise<PerSiteResult[]> {
  const results: PerSiteResult[] = [];

  for (const entry of entries) {
    const url = searchUrlFor(entry.source, entry.query, entry.location ?? '');
    if (!url) {
      results.push({
        source: entry.source,
        found: 0,
        saved: 0,
        error: 'No search URL for this board',
      });
      continue;
    }

    let tabId: number | undefined;
    try {
      const tab = await chrome.tabs.create({ url, active: false });
      tabId = tab.id;
      if (tabId === undefined) {
        results.push({ source: entry.source, found: 0, saved: 0, error: 'Could not open tab' });
        continue;
      }

      await waitForTabLoad(tabId);
      // A short settle only; the content script itself polls until the board's
      // results render, because these boards differ by many seconds.
      await sleep(1500);

      const reply = await scrapeTabWithRetry(tabId);
      if (reply.ok) {
        results.push({
          source: entry.source,
          found: reply.data.found,
          saved: reply.data.saved,
          reason: reply.data.found === 0 ? (reply.data.reason ?? 'empty') : undefined,
          // A load that rendered nothing needs a cause, not a shrug. The
          // content script inspected the page and can tell a login wall from a
          // search that genuinely matched nothing - and only one of those is
          // something the user can fix.
          error:
            reply.data.found === 0
              ? reply.data.reason === 'signed-out'
                ? `Signed out of ${entry.source} - sign in to that site, then search again`
                : 'No results on the page for this search'
              : undefined,
        });
      } else {
        results.push({ source: entry.source, found: 0, saved: 0, error: reply.error });
      }
    } catch (error) {
      // One failed board must not abort the run.
      results.push({
        source: entry.source,
        found: 0,
        saved: 0,
        error: error instanceof Error ? error.message : 'Scrape failed',
      });
    } finally {
      if (tabId !== undefined) {
        try {
          await chrome.tabs.remove(tabId);
        } catch {
          /* already closed */
        }
      }
    }
    await sleep(2000);
  }
  return results;
}

/**
 * Ask a tab's content script to harvest, retrying while it is still injecting.
 *
 * Content scripts run at `document_idle`, which on a heavy board (Foundit) lands
 * noticeably after the tab reports `complete`. Messaging that gap fails with
 * "Receiving end does not exist" - indistinguishable, from the outside, from a
 * board that returned nothing. Retrying turns that race into a short wait.
 */
async function scrapeTabWithRetry(
  tabId: number,
  attempts = 6,
): Promise<Reply<ReplyMap['scrape-list']>> {
  let last: Reply<ReplyMap['scrape-list']> = fail('Content script never responded');

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    last = await sendToTab(tabId, { type: 'scrape-list' });
    // A real answer - including a legitimate zero - ends the retries.
    if (last.ok) return last;
    if (!/receiving end|could not establish|message port closed/i.test(last.error)) return last;
    await sleep(1500);
  }
  return last;
}

async function runScheduledScrape(): Promise<void> {
  const settings = await getSettings();
  if (!settings.backgroundScrape) return;

  // Confirm the API is reachable before opening tabs - otherwise we would scrape
  // and then throw the results away.
  try {
    const health = await api.ping();
    if (!health.ok) return;
  } catch {
    return;
  }

  const results = await scrapeEntries(settings.scrapeQueries);
  const total = results.reduce((sum, r) => sum + r.found, 0);
  const saved = results.reduce((sum, r) => sum + r.saved, 0);

  await chrome.storage.local.set({
    lastScrape: { at: Date.now(), found: total, saved },
  });
  // Notify on NEW rows only: a scheduled run that re-harvests the same jobs it
  // saw an hour ago has nothing to tell the user.
  if (saved > 0) {
    await flashBadge(String(saved));
    try {
      await chrome.notifications?.create({
        type: 'basic',
        iconUrl: 'icons/icon-128.png',
        title: 'FitWright',
        message: `${saved} new job${saved === 1 ? '' : 's'} added to your feed`,
      });
    } catch {
      /* notifications permission not granted - badge already showed it */
    }
  }
}

function waitForTabLoad(tabId: number, timeoutMs = 25000): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(); // proceed anyway; a partial page may still yield results
    }, timeoutMs);

    function listener(changedId: number, info: chrome.tabs.TabChangeInfo): void {
      if (changedId !== tabId || info.status !== 'complete') return;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --------------------------------------------------------------------------- //
// Lifecycle
// --------------------------------------------------------------------------- //

chrome.runtime.onInstalled.addListener((details) => {
  void syncAlarm();
  // Open options on first install so the user sets their API URL and answers
  // the questions a resume cannot supply (visa status, notice period).
  if (details.reason === 'install') void chrome.runtime.openOptionsPage();
});

chrome.runtime.onStartup.addListener(() => void syncAlarm());

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'sync' || !changes.settings) return;
  api.resetAuthCache(); // base URL may have changed
  void syncAlarm();
});
