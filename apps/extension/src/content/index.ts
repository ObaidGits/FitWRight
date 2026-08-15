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
import { resolveFormRoot } from '@/lib/application-form';
import { waitFor } from '@/lib/dom';
import { collectFields } from '@/lib/fields';
import { t } from '@/lib/i18n';
import { classifyEmpty, looksSignedOut } from '@/lib/login-wall';
import { getSitePreference, isSiteEnabled } from '@/lib/site-prefs';
import { fail, ok } from '@/lib/messages';
import type { Reply, ToContent } from '@/lib/messages';
import { getCachedMatch, getSettings, setCachedMatch } from '@/lib/storage';
import { sendToWorker } from '@/lib/messages';
import type { CapturedJob, MatchResult, PageContext, PageKind } from '@/lib/types';
import {
  autofill,
  planFill,
  draftOpenQuestions,
  labelFor,
  listOpenQuestions,
  optionsFor,
  typeFor,
  type AutofillReport,
} from './autofill';
import {
  hideBadge,
  hideFillPanel,
  showBadge,
  showBadgeLoading,
  showFillPanel,
  toast,
} from './overlay';
import { watchForSubmission } from './tracking';

let adapter: SiteAdapter = resolveAdapter(new URL(location.href));
let kind: PageKind = 'unknown';
let currentJob: CapturedJob | null = null;
let teardownTracking: (() => void) | null = null;
/** Session-scoped dismissal so the badge stays hidden until navigation. */
let badgeDismissed = false;
/**
 * The last resume match computed for this page, so the popup can show the score
 * without paying for a second one.
 */
let lastMatch: MatchResult | null = null;

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
    lastMatch = cached;
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
  lastMatch = reply.data;
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
      else toast(reply.data.duplicate ? t('toastAlreadySaved') : t('toastSaved'), 'ok');
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
      if (reply.ok && reply.data.updated) toast(t('toastMarkedApplied'), 'ok');
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

  // Honour a per-site "off" before doing anything else. Off has to mean off: no
  // badge, no capture, no match request - not "quieter".
  if (!(await isSiteEnabled(url.hostname))) return;

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
  if (kind === 'application-form') {
    void startTracking();
    // Wizards advance without a URL change, so watch the form for step changes.
    startStepWatch();
  }
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
        // Whatever we already scored for this page. Never triggers a fresh match:
        // opening the popup should not spend an AI call.
        match: lastMatch,
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
      const scope = resolveFormRoot(adapter.formRoot?.());
      // Sites that gate applying behind an account (Indeed, LinkedIn, Naukri)
      // swap the form for a sign-in when the session lapses. Filling "0 fields"
      // there is technically true and completely useless, so name the cause and
      // stop rather than reporting a failure the user cannot act on.
      if (looksSignedOut()) {
        toast(t('toastSignInFirst', [adapter.label]), 'err');
        return ok({ filled: 0, skipped: 0, questions: [], reason: 'signed-out' });
      }

      // THE most common case on a job board, and the one that used to look like a
      // bug: this is a listing page, not an application form. Saying so - and
      // where the form actually is - is the whole fix. Filling nothing here is
      // correct behaviour, so it must not be reported as a failure, and nothing
      // on the page may be saved to Answers.
      if (!scope.isApplicationForm) {
        toast(
          `No application form on this page. Click "Apply" on ${adapter.label} first, ` +
            `then run autofill on the form that opens.`,
          'err',
        );
        return ok({
          filled: 0,
          skipped: 0,
          questions: [],
          reason: 'no-application-form',
        });
      }

      const root = scope.root;
      // Name the job so the resume tailored for it is the one attached.
      const formJob = currentJob ?? extractJob();
      const report = await autofill(root, {
        company: formJob?.company,
        title: formJob?.title,
      });
      const parts = [`${report.filled} field${report.filled === 1 ? '' : 's'} filled`];
      if (report.resumeAttached) {
        // Say which resume, not just that one was attached. "Tailored resume
        // attached" is the promise being kept; the master resume is the
        // fallback, and conflating them hides a real difference.
        parts.push(
          report.resumeTailored
            ? t('toastTailoredResumeAttached')
            : t('toastMasterResumeAttached'),
        );
      }
      if (report.unrecognised) {
        // Reached only when we ARE on a real application form and still could not
        // read it - which is a genuine adapter gap worth reporting, unlike the
        // listing-page case handled above.
        toast(
          `Could not read this form's ${report.unrecognised} field${
            report.unrecognised === 1 ? '' : 's'
          } - saved as questions in FitWright`,
          'err',
        );
      } else {
        toast(parts.join(', '), report.filled ? 'ok' : 'err');
      }

      // Report what this form asked, and offer to remember whatever the user
      // answers by hand from here.
      void reportAndOfferToLearn(root, report);

      // The auto-apply-brain audit trail (Phase 0): fire-and-forget, same rule
      // as reportAndOfferToLearn above - a failed report must never disrupt an
      // application.
      if (report.decisions.length) {
        void sendToWorker({
          type: 'record-decisions',
          decisions: report.decisions.map((d) => ({
            site_host: location.hostname,
            label: d.label,
            resolved_target: d.resolved_target,
            value_source: d.value_source,
            filled: d.filled,
            readback_ok: d.readback_ok,
            required: d.required,
          })),
        });
      }

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
        unrecognised: report.unrecognised,
      });
    }

    case 'preview-fill': {
      // Reads the page and writes nothing, so the user can see what autofill
      // would put in an employer's form before it goes there.
      //
      // Reports whether a form was even found, because an empty plan means two
      // opposite things: "this form is already complete" or "there is no form
      // here". Returning a bare empty list is what made the preview say "no
      // fields require autofill" about a listing page while the fill path called
      // the same page unreadable.
      const scope = resolveFormRoot(adapter.formRoot?.());
      if (!scope.isApplicationForm) {
        return ok({ plan: [], reason: 'no-application-form' });
      }
      return ok({ plan: await planFill(scope.root), reason: null });
    }

    case 'scrape-list': {
      if (!adapter.extractList) return fail(`${adapter.label} list scraping not supported`);
      const jobs = await harvestList();
      // An empty harvest is worth explaining. On these boards it is usually a
      // login wall, and "0 found" alone reads as a broken extension.
      if (!jobs.length) return ok({ found: 0, saved: 0, reason: classifyEmpty() });
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
 * Report what a form asked, and offer to learn what the user answers.
 *
 * Runs after every fill. The report itself carries labels only; the panel is what
 * turns a blank field into a remembered answer, at the one moment the user
 * actually knows it.
 */
async function reportAndOfferToLearn(root: ParentNode, report: AutofillReport): Promise<void> {
  const company = currentJob?.company || undefined;

  if (report.seen.length) {
    // Fire and forget: a failed report must never disrupt an application.
    void sendToWorker({
      type: 'report-form',
      fields: report.seen,
      company,
      ats: adapter.id,
      url: location.href,
    });
  }

  // Only questions still blank are worth the user's attention.
  const unanswered: { label: string; element: HTMLElement }[] = [];
  for (const el of collectFields(root)) {
    if ((el as HTMLInputElement).value?.trim()) continue;
    if ((el as HTMLInputElement).type === 'password') continue;
    const label = labelFor(el);
    if (label) unanswered.push({ label, element: el as HTMLElement });
  }

  // "Not here" on this site: keep filling, stop drawing the box. Checked at draw
  // time rather than at fill time so autofill itself is unaffected.
  if ((await getSitePreference(location.hostname)).panelHidden) return;

  showFillPanel(
    { filled: report.filled, unanswered },
    {
      onSaveAnswers: async () => {
        // Read the page fresh: the user has been typing since the fill.
        const answers: {
          label: string;
          value: unknown;
          field_type: string;
          options: string[];
        }[] = [];
        for (const el of collectFields(root)) {
          if ((el as HTMLInputElement).type === 'password') continue;
          const value = (el as HTMLInputElement).value?.trim();
          if (!value) continue;
          const label = labelFor(el);
          if (!label) continue;
          answers.push({
            label,
            value,
            field_type: typeFor(el),
            options: optionsFor(el, root),
          });
        }
        if (!answers.length) {
          toast('Nothing filled in to save yet', 'info');
          return;
        }
        const reply = await sendToWorker({
          type: 'save-answers',
          answers,
          company,
          ats: adapter.id,
          url: location.href,
        });
        if (!reply.ok) toast(reply.error, 'err');
        else toast(`${reply.data.saved} answer(s) saved to FitWright`, 'ok');
      },
    },
  );
}

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
  stopStepWatch();
  hideFillPanel();
  void initialise();
}, 1200);

// --------------------------------------------------------------------------- //
// Multi-step application wizards
// --------------------------------------------------------------------------- //

/**
 * Watch a multi-step application form and re-fill when it advances a step.
 *
 * Workday and Indeed Easy Apply are wizards: pressing Next replaces the visible
 * fields WITHOUT changing the URL, so the URL poll above never fires and every
 * step after the first was left unfilled. This watches the form subtree instead.
 *
 * Three problems have to be solved at once, and each guard below exists for one:
 *
 *  1. **Filling mutates the DOM**, so a naive observer would retrigger itself
 *     forever. `filling` suppresses reactions caused by our own writes.
 *  2. **Frameworks re-render in bursts** of dozens of mutations. The debounce
 *     collapses a burst into one reaction.
 *  3. **A re-render is not a new step.** Reacting to any mutation would re-fill
 *     constantly, so we only act when the set of fillable field identities
 *     actually changes - that is what "advanced a step" means in DOM terms.
 *
 * Autofill itself is idempotent (it skips fields that already hold a value), so
 * even a false positive cannot overwrite something the user typed.
 */
let stepObserver: MutationObserver | null = null;
let stepDebounce: number | null = null;
let filling = false;
let lastFieldSignature = '';
/**
 * How many fields the previous pass filled, so a repeat pass can report the
 * difference rather than the total. Reset when the step changes, because on a new
 * step every filled field is genuinely new.
 */
let filledOnLastPass = 0;

/** Identity of the currently visible fillable fields. */
function fieldSignature(root: ParentNode): string {
  const fields = root.querySelectorAll<HTMLElement>('input, textarea, select');
  const parts: string[] = [];
  for (const el of fields) {
    const input = el as HTMLInputElement;
    if (input.type === 'hidden') continue;
    // Skip invisible fields: wizards keep previous steps in the DOM and hide
    // them, so including those would make every step look identical.
    if (!el.offsetParent && el.getClientRects().length === 0) continue;
    parts.push(`${el.tagName}:${input.type ?? ''}:${input.name || el.id || ''}`);
  }
  return parts.join('|');
}

async function fillCurrentStep(): Promise<void> {
  const scope = resolveFormRoot(adapter.formRoot?.());
  // The step watcher observes the whole body so it notices a form appearing, which
  // means it also fires on pages that have none. Writing nothing here is correct -
  // and silent, because this path is automatic rather than user-invoked.
  if (!scope.isApplicationForm) return;
  const root = scope.root;
  filling = true;
  try {
    const stepJob = currentJob ?? extractJob();
    const report = await autofill(root, {
      company: stepJob?.company,
      title: stepJob?.title,
    });
    lastFieldSignature = fieldSignature(root);
    void reportAndOfferToLearn(root, report);

    // What changed on *this* pass. On a multi-step wizard the same panel reappears
    // at every step, and a bare count leaves the user unable to tell a step that
    // was filled from one that was already complete - so a repeat pass that
    // changed nothing says exactly that rather than repeating a number.
    const newlyFilled = report.filled - filledOnLastPass;
    filledOnLastPass = report.filled;
    if (newlyFilled > 0) {
      toast(`${newlyFilled} field${newlyFilled === 1 ? '' : 's'} filled on this step`, 'ok');
    } else if (report.filled > 0) {
      toast(t('toastNothingNewOnStep'), 'info');
    }
  } catch {
    /* a step that is not a form (review, confirmation) is not an error */
  } finally {
    // Release on the next tick so mutations from our own writes are still
    // suppressed when the observer callback runs.
    setTimeout(() => {
      filling = false;
    }, 400);
  }
}

function startStepWatch(): void {
  stopStepWatch();
  const root = adapter.formRoot?.() ?? document.body;
  if (!root || !(root instanceof Node)) return;

  lastFieldSignature = fieldSignature(root as ParentNode);

  stepObserver = new MutationObserver(() => {
    if (filling) return; // guard 1: our own writes
    if (stepDebounce !== null) clearTimeout(stepDebounce);
    stepDebounce = setTimeout(() => {
      // guard 3: only a genuine change of visible fields counts as a new step
      const container = adapter.formRoot?.() ?? document.body;
      const signature = fieldSignature(container as ParentNode);
      if (!signature || signature === lastFieldSignature) return;
      lastFieldSignature = signature;
      // A new step: everything filled there is new, so the diff starts over.
      filledOnLastPass = 0;
      void fillCurrentStep();
    }, 600) as unknown as number; // guard 2: collapse re-render bursts
  });

  stepObserver.observe(root, {
    childList: true,
    subtree: true,
    // Attributes matter as much as nodes. Some wizards replace the step's DOM
    // (childList), but others keep every step mounted and just toggle
    // visibility - a `style="display:none"` flip produces NO childList record at
    // all, so watching nodes alone left those forms unfilled from step 2 on.
    // Filtered to visibility-affecting attributes to keep the noise down; the
    // field-signature check below still decides whether anything really changed.
    attributes: true,
    attributeFilter: ['style', 'class', 'hidden', 'aria-hidden', 'data-step'],
  });
}

function stopStepWatch(): void {
  stepObserver?.disconnect();
  stepObserver = null;
  if (stepDebounce !== null) {
    clearTimeout(stepDebounce);
    stepDebounce = null;
  }
  lastFieldSignature = '';
}

void initialise();
