/**
 * Content script entry point.
 *
 * Runs on every matched job site. Responsibilities, in order:
 *  1. Classify the page via the adapter registry.
 *  2. On a job posting: score it and show the badge.
 *  3. On an application form: watch for submission, expose autofill.
 *  4. On a list page: expose bulk extraction for background scraping.
 *  5. Answer messages from the popup and the service worker.
 *
 * Everything here is best-effort and non-blocking. A content script that throws
 * on a page it did not expect must not break the page it is injected into, so
 * each feature is independently guarded.
 */
import { genericAdapter, resolveAdapter } from '@/adapters/registry';
import type { SiteAdapter } from '@/adapters/types';
import { waitFor } from '@/lib/dom';
import { fail, ok } from '@/lib/messages';
import type { Reply, ToContent } from '@/lib/messages';
import { getCachedMatch, getSettings, setCachedMatch } from '@/lib/storage';
import { sendToWorker } from '@/lib/messages';
import type { CapturedJob, MatchResult, PageContext, PageKind } from '@/lib/types';
import { autofill, draftOpenQuestions, listOpenQuestions } from './autofill';
import { hideBadge, showBadge, showBadgeLoading, toast } from './overlay';
import { watchForSubmission } from './tracking';

let adapter: SiteAdapter = resolveAdapter(new URL(location.href));
let kind: PageKind = 'unknown';
let currentJob: CapturedJob | null = null;
let teardownTracking: (() => void) | null = null;
/** Session-scoped dismissal so the badge stays hidden until navigation. */
let badgeDismissed = false;

/**
 * Extract the page's job.
 *
 * The site adapter is tried first, then the generic JSON-LD reader. The generic
 * result wins only when the site adapter produced nothing usable - a title with
 * no company almost always means the adapter's selectors have drifted after a
 * redesign, and schema.org markup survives redesigns.
 */
function extractJob(): CapturedJob | null {
  const url = new URL(location.href);

  let viaAdapter: CapturedJob | null = null;
  try {
    viaAdapter = adapter.extractJob(url);
  } catch {
    /* adapter selectors broke - generic below */
  }
  if (viaAdapter?.company) return viaAdapter;

  let viaGeneric: CapturedJob | null = null;
  try {
    viaGeneric = genericAdapter.extractJob(url);
  } catch {
    /* no structured data either */
  }

  // Prefer whichever has a company; otherwise keep the adapter's title.
  if (viaGeneric?.company) return viaGeneric;
  return viaAdapter ?? viaGeneric;
}

/** Score the current job and render the badge. */
async function runMatch(job: CapturedJob): Promise<void> {
  const settings = await getSettings();
  if (!settings.showBadge || badgeDismissed) return;
  if (!job.description || job.description.length < 120) return; // too thin to score

  const cacheKey = job.url;
  const cached = await getCachedMatch<MatchResult>(cacheKey);
  if (cached) {
    renderBadge(job, cached);
    return;
  }

  showBadgeLoading();
  const reply = await sendToWorker({
    type: 'match',
    description: job.description,
    title: job.title,
  });

  if (!reply.ok) {
    // Not signed in / API down: the badge is an enhancement, so disappear
    // quietly rather than nagging on every job page.
    hideBadge();
    return;
  }
  await setCachedMatch(cacheKey, reply.data);
  renderBadge(job, reply.data);
}

function renderBadge(job: CapturedJob, match: MatchResult): void {
  showBadge(match, {
    onDismiss: () => {
      badgeDismissed = true;
    },
    onSave: async () => {
      const reply = await sendToWorker({ type: 'capture', job });
      if (!reply.ok) toast(reply.error, 'err');
      else toast(reply.data.duplicate ? 'Already saved' : 'Saved to FitWright', 'ok');
    },
    onTailor: async () => {
      // Capture first so the job exists in the feed the Builder reads from.
      const reply = await sendToWorker({ type: 'capture', job });
      if (!reply.ok) {
        toast(reply.error, 'err');
        return;
      }
      await sendToWorker({ type: 'open-fitwright', path: '/discovery' });
    },
  });
}

/** Wire submission tracking on application pages. */
async function startTracking(): Promise<void> {
  const settings = await getSettings();
  if (!settings.trackApplications) return;

  teardownTracking?.();
  teardownTracking = watchForSubmission({
    onSubmitted: async () => {
      const reply = await sendToWorker({ type: 'applied', url: location.href });
      if (reply.ok && reply.data.updated) toast('Marked as applied in FitWright', 'ok');
    },
  });
}

/** Auto-capture, when the user has opted in. */
async function maybeAutoCapture(job: CapturedJob): Promise<void> {
  const settings = await getSettings();
  if (!settings.autoCapture) return;
  await sendToWorker({ type: 'capture', job });
}

/** Classify the page and light up the relevant features. */
async function initialise(): Promise<void> {
  const url = new URL(location.href);
  adapter = resolveAdapter(url);

  try {
    kind = adapter.classify(url);
  } catch {
    kind = 'unknown';
  }
  if (kind === 'unknown') return;

  // SPA sites render the posting after load; wait for the adapter's anchor.
  if (adapter.readySelector) await waitFor(adapter.readySelector, 8000);

  if (kind === 'job-posting' || kind === 'application-form') {
    currentJob = extractJob();
    if (currentJob) {
      void maybeAutoCapture(currentJob);
      void runMatch(currentJob);
    }
  }
  if (kind === 'application-form') void startTracking();
}

// --------------------------------------------------------------------------- //
// Message handling
// --------------------------------------------------------------------------- //

chrome.runtime.onMessage.addListener(
  (message: ToContent, _sender, sendResponse: (reply: Reply<unknown>) => void) => {
    void handleMessage(message)
      .then((reply) => sendResponse(reply))
      .catch((error) => sendResponse(fail(error)));
    return true; // keep the channel open for the async reply
  },
);

async function handleMessage(message: ToContent): Promise<Reply<unknown>> {
  switch (message.type) {
    case 'describe-page': {
      const context: PageContext = {
        kind,
        adapter: adapter.id,
        job: currentJob ?? extractJob(),
        hasForm: Boolean(document.querySelector('input[type="file"], form')),
      };
      currentJob = context.job;
      return ok(context);
    }

    case 'capture-current': {
      const job = currentJob ?? extractJob();
      if (!job) return fail('No job found on this page');
      const reply = await sendToWorker({ type: 'capture', job });
      if (!reply.ok) return fail(reply.error);
      toast(reply.data.duplicate ? 'Already saved' : 'Saved to FitWright', 'ok');
      return ok(reply.data);
    }

    case 'autofill': {
      const root = adapter.formRoot?.() ?? document;
      const report = await autofill(root);
      const parts = [`${report.filled} field${report.filled === 1 ? '' : 's'} filled`];
      if (report.resumeAttached) parts.push('resume attached');
      toast(parts.join(', '), report.filled ? 'ok' : 'err');

      // Drafting is opt-in per page and only worth doing when we know the JD.
      const job = currentJob ?? extractJob();
      if (report.questions.length && job?.description) {
        const drafted = await draftOpenQuestions(
          { title: job.title, company: job.company, description: job.description },
          root,
        );
        if (drafted.drafted) {
          toast(`${drafted.drafted} answer(s) drafted - review before submitting`, 'ok');
        }
      }
      return ok({
        filled: report.filled,
        skipped: report.skipped,
        questions: listOpenQuestions(root),
      });
    }

    case 'scrape-list': {
      if (!adapter.extractList) return fail(`${adapter.label} list scraping not supported`);
      const jobs = await harvestList();
      if (!jobs.length) return ok({ found: 0, saved: 0 });
      const reply = await sendToWorker({
        type: 'scrape-results',
        source: adapter.id,
        jobs,
      });
      if (!reply.ok) return fail(reply.error);
      // Report both: `saved` is only the NEW rows, so a re-run of the same
      // search legitimately saves nothing, and reporting that alone reads as a
      // failure. `found` proves the harvest itself worked.
      return ok({ found: jobs.length, saved: reply.data.saved });
    }

    default:
      return fail('Unknown message');
  }
}

/**
 * Extract the list, retrying until the board has actually rendered.
 *
 * These boards render results client-side at wildly different speeds - Foundit
 * is ready in ~2s, Hirist and Instahyre take 8-10s - and a single extraction on
 * a fixed delay silently returned zero rows for the slow ones. Polling until
 * rows appear (or the deadline passes) removes the guesswork; a genuinely empty
 * search still costs only the deadline, in a background tab nobody is watching.
 */
async function harvestList(deadlineMs = 20000): Promise<CapturedJob[]> {
  const url = new URL(location.href);
  const started = Date.now();

  let jobs: CapturedJob[] = [];
  while (Date.now() - started < deadlineMs) {
    try {
      jobs = adapter.extractList?.(url) ?? [];
    } catch {
      jobs = [];
    }
    if (jobs.length) return jobs;
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  return jobs;
}

// --------------------------------------------------------------------------- //
// Lifecycle
// --------------------------------------------------------------------------- //

/**
 * Job boards are SPAs: LinkedIn and Indeed swap the posting without a page load,
 * so re-run on URL change. `popstate` alone misses pushState navigations, hence
 * the polled comparison.
 */
let lastHref = location.href;
setInterval(() => {
  if (location.href === lastHref) return;
  lastHref = location.href;
  hideBadge();
  badgeDismissed = false;
  currentJob = null;
  teardownTracking?.();
  teardownTracking = null;
  void initialise();
}, 1200);

void initialise();
