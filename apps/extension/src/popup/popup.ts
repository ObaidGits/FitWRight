/**
 * Popup: the manual control surface.
 *
 * Shows what the extension sees on the current tab and offers only the actions
 * that page actually supports - an "Autofill" button on a page with no form is
 * worse than no button, because it teaches the user the extension is unreliable.
 */
import { lastRunSummary, listErrors } from '@/lib/diagnostics';
import { getSitePreference, setSitePreference } from '@/lib/site-prefs';
import { sendToTab, sendToWorker } from '@/lib/messages';
import type { PageContext } from '@/lib/types';

const connDot = document.getElementById('conn') as HTMLSpanElement;
const jobBox = document.getElementById('job') as HTMLDivElement;
const actionsBox = document.getElementById('actions') as HTMLDivElement;
const statusBox = document.getElementById('status') as HTMLDivElement;

function setStatus(message: string, kind: '' | 'ok' | 'err' = ''): void {
  statusBox.textContent = message;
  statusBox.className = kind;
}

function escapeHtml(value: string): string {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}

/**
 * The active tab, or null.
 *
 * Reads `tab.url` without the `tabs` permission, which Chrome describes to users
 * as "read your browsing history" - a heavy thing to ask for a popup. Every API
 * used here works without it: `create`, `remove` and `sendMessage` never required
 * it, and `url` is populated for tabs the extension already has host permission
 * for. On any other site the popup shows "FitWright does not run here" and never
 * needs the URL.
 */
async function activeTab(): Promise<chrome.tabs.Tab | null> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab ?? null;
}

/** Connection state, shown as a coloured dot rather than a line of text. */
async function checkConnection(): Promise<{ signedIn: boolean; hasResume: boolean } | null> {
  const reply = await sendToWorker({ type: 'ping' });
  if (!reply.ok) {
    connDot.className = 'dot off';
    connDot.title = reply.error;
    return null;
  }
  connDot.className = 'dot on';
  connDot.title = reply.data.hasResume
    ? 'Connected to FitWright'
    : 'Connected - no resume uploaded yet';
  if (!reply.data.versionOk) {
    setStatus('Extension is out of date with this FitWright version.', 'err');
  } else if (!reply.data.buildCurrent) {
    // A newer build exists. Unpacked extensions never auto-update, so without
    // this the user runs a months-old copy and reports fixed bugs.
    const latest = reply.data.latestVersion ? ` (${reply.data.latestVersion})` : '';
    setStatus(`A newer FitWright extension${latest} is available - rebuild and reload it.`, 'err');
  } else if (!reply.data.hasResume) {
    setStatus('Upload a resume in FitWright to enable autofill.', 'err');
  }
  return reply.data;
}

function button(label: string, primary: boolean, onClick: () => Promise<void>): HTMLButtonElement {
  const el = document.createElement('button');
  el.textContent = label;
  if (primary) el.className = 'primary';
  el.addEventListener('click', () => {
    el.disabled = true;
    const original = el.textContent;
    el.textContent = 'Working...';
    void onClick()
      .catch((error: unknown) => setStatus(String(error), 'err'))
      .finally(() => {
        el.disabled = false;
        el.textContent = original;
      });
  });
  return el;
}

/** Render the page summary and the actions that apply to it. */
function render(tabId: number, context: PageContext, health: { hasResume: boolean } | null): void {
  const { job, kind } = context;

  const score = context.match ? Math.round(context.match.match_score) : null;
  // The score already exists on the page badge, but the badge can be dismissed or
  // switched off - and the popup is where someone deciding whether to apply looks.
  const scoreLine =
    score === null
      ? ''
      : `<p class="score">${score}% match to your resume</p>`;

  jobBox.innerHTML = job
    ? `<h1>${escapeHtml(job.title)}</h1>
       <p>${escapeHtml([job.company, job.location].filter(Boolean).join(' \u00b7 ')) || 'Unknown company'}</p>
       ${scoreLine}
       <span class="kind">${escapeHtml(context.adapter)}</span>`
    : `<p class="muted">No job detected on this page.</p>
       <span class="kind">${escapeHtml(context.adapter)}</span>`;

  actionsBox.replaceChildren();

  if (job) {
    actionsBox.appendChild(
      button('Save to FitWright', true, async () => {
        const reply = await sendToTab(tabId, { type: 'capture-current' });
        if (!reply.ok) setStatus(reply.error, 'err');
        else setStatus(reply.data.duplicate ? 'Already in your feed.' : 'Saved.', 'ok');
      }),
    );
  }

  if (kind === 'application-form' || context.hasForm) {
    actionsBox.appendChild(
      button('Preview what will be filled', false, async () => {
        // An application is sent in the user's name, so seeing the values before
        // they land in an employer's form is the difference between a tool that
        // helps and one that has to be trusted blindly.
        const reply = await sendToTab(tabId, { type: 'preview-fill' });
        if (!reply.ok) {
          setStatus(reply.error, 'err');
          return;
        }
        const { plan, reason } = reply.data;
        // These two cases produce the same empty plan and mean opposite things.
        // Reporting both as "every field already has a value" is what made the
        // preview contradict the autofill button on the very same page.
        if (reason === 'no-application-form') {
          setStatus(
            'No application form on this page yet. Click Apply on the job first, then preview.',
            '',
          );
          return;
        }
        if (!plan.length) {
          setStatus('Nothing to fill here - every field we recognise already has a value.', '');
          return;
        }
        showPreview(plan);
      }),
    );

    actionsBox.appendChild(
      button('Autofill this form', !job, async () => {
        if (!health?.hasResume) {
          setStatus('Upload a resume in FitWright first.', 'err');
          return;
        }
        const reply = await sendToTab(tabId, { type: 'autofill' });
        if (!reply.ok) {
          setStatus(reply.error, 'err');
          return;
        }
        const { filled, questions, reason, unrecognised } = reply.data;
        if (reason === 'signed-out') {
          // The most common cause of "it did nothing", and the one the user can
          // fix in one click on a tab they already have open.
          setStatus('You appear signed out of this site. Sign in, then autofill.', 'err');
          return;
        }
        if (reason === 'captcha') {
          setStatus('A captcha is showing on this page. Solve it, then autofill.', 'err');
          return;
        }
        if (reason === 'no-application-form') {
          // The single most common case on a job board, and previously reported as
          // an unreadable form - which sent the user hunting for a bug that was
          // really just "you are on the listing page, not the form yet".
          setStatus(
            'This page has no application form yet. Click Apply on the job, then run autofill on the form that opens.',
            '',
          );
          return;
        }
        if (unrecognised) {
          // Distinguishes a stale adapter from an already-complete form. The
          // fields were still recorded, so the next attempt can fill them.
          setStatus(
            `Could not read this form's ${unrecognised} field(s). They were saved as questions in FitWright - answer them there and try again.`,
            'err',
          );
          return;
        }
        setStatus(
          questions.length
            ? `Filled ${filled}. ${questions.length} question(s) need your review.`
            : `Filled ${filled} field${filled === 1 ? '' : 's'}. Review, then submit yourself.`,
          filled ? 'ok' : 'err',
        );
      }),
    );
  }

  if (kind === 'job-list') {
    actionsBox.appendChild(
      button('Scrape all results on this page', true, async () => {
        const reply = await sendToTab(tabId, { type: 'scrape-list' });
        if (!reply.ok) setStatus(reply.error, 'err');
        else
          setStatus(
            reply.data.found ? `Added ${reply.data.found} job(s) to your feed.` : 'No new jobs found.',
            reply.data.found ? 'ok' : '',
          );
      }),
    );
  }

  if (!actionsBox.children.length) {
    const hint = document.createElement('p');
    hint.className = 'muted';
    hint.textContent = 'Open a job posting, a search results page, or an application form.';
    actionsBox.appendChild(hint);
  }
}

async function main(): Promise<void> {
  document.getElementById('open-app')?.addEventListener('click', () => {
    void sendToWorker({ type: 'open-fitwright', path: '/discovery' });
  });
  document.getElementById('open-options')?.addEventListener('click', () => {
    void chrome.runtime.openOptionsPage();
  });

  const [health, tab] = await Promise.all([checkConnection(), activeTab()]);
  if (!tab?.id) {
    jobBox.innerHTML = '<p class="muted">No active tab.</p>';
    return;
  }

  const reply = await sendToTab(tab.id, { type: 'describe-page' });
  if (!reply.ok) {
    // No content script here - this site is outside our host permissions.
    jobBox.innerHTML =
      '<p class="muted">FitWright does not run on this site. Open a supported job board or an ATS application page.</p>';
    await showDiagnostics();
    return;
  }
  render(tab.id, reply.data, health);
  const host = tab.url ? new URL(tab.url).hostname : '';
  await Promise.all([
    showQueue(),
    showDiagnostics(),
    host ? showSiteToggle(host) : Promise.resolve(),
  ]);
}

/**
 * What the extension has been doing when nobody was watching.
 *
 * Background searching used to leave no visible trace at all, so a user could not
 * tell a working schedule from one that stopped weeks ago. A recent failure is
 * shown ahead of the run summary, because it is the more actionable of the two.
 */
/**
 * List what autofill would put in the form.
 *
 * Values are shown truncated but not masked: the point is to let the user read
 * what an employer is about to receive. Rendered into the popup rather than the
 * page, so a form cannot restyle it.
 */
function showPreview(plan: { label: string; value: string }[]): void {
  const box = document.createElement('div');
  box.className = 'preview';
  box.innerHTML = `
    <p class="muted">${plan.length} field${plan.length === 1 ? '' : 's'} would be filled:</p>
    ${plan
      .slice(0, 12)
      .map(
        (row) =>
          `<p><span class="pl">${escapeHtml(row.label)}</span>${escapeHtml(
            row.value.length > 40 ? `${row.value.slice(0, 40)}\u2026` : row.value,
          )}</p>`,
      )
      .join('')}
    ${plan.length > 12 ? `<p class="muted">and ${plan.length - 12} more</p>` : ''}
  `;
  document.querySelector('.preview')?.remove();
  actionsBox.after(box);
}

/**
 * What to do after this page.
 *
 * The popup used to answer only "what is this page", which makes it a launcher.
 * A queue of jobs waiting to be applied to is the actual next action, and it was
 * invisible from here.
 */
/**
 * Turn the extension off for the current site.
 *
 * Offered here rather than only in options, because "not on this site" is a
 * decision made *while looking at* the site - the user is on an internal careers
 * portal, or a page where the badge is in the way. Making them find a settings
 * page to express it means they disable the whole extension instead.
 */
async function showSiteToggle(hostname: string): Promise<void> {
  const pref = await getSitePreference(hostname);
  const box = document.createElement('div');
  box.className = 'sitetoggle';

  const link = document.createElement('button');
  link.className = 'linkish';
  link.textContent = pref.disabled
    ? `Turn FitWright back on for ${hostname}`
    : `Turn off on ${hostname}`;
  link.addEventListener('click', () => {
    void setSitePreference(hostname, { disabled: !pref.disabled }).then(() => {
      setStatus(
        pref.disabled
          ? 'Back on for this site. Reload the page.'
          : 'Off for this site. Reload the page.',
        'ok',
      );
      link.disabled = true;
    });
  });

  box.appendChild(link);
  document.body.appendChild(box);
}

async function showQueue(): Promise<void> {
  const reply = await sendToWorker({ type: 'get-queue' });
  if (!reply.ok || !reply.data.total) return;

  const [next] = reply.data.items;
  const box = document.createElement('div');
  box.className = 'queue';
  const label = [next?.role, next?.company].filter(Boolean).join(' \u00b7 ') || 'a saved job';
  box.innerHTML = `
    <p class="muted">Next in your queue (${reply.data.total} waiting)</p>
    <p><strong>${escapeHtml(label)}</strong></p>
  `;
  box.addEventListener('click', () => {
    void sendToWorker({ type: 'open-fitwright', path: '/applications?view=queue' });
  });
  document.body.appendChild(box);
}

async function showDiagnostics(): Promise<void> {
  const [summary, errors] = await Promise.all([lastRunSummary(), listErrors()]);
  const recent = errors[0];

  const lines: string[] = [];
  if (recent) {
    lines.push(
      `<p class="muted err-line">${escapeHtml(recent.context)}: ${escapeHtml(recent.message)}</p>`,
    );
  }
  if (summary) lines.push(`<p class="muted">${escapeHtml(summary)}</p>`);
  if (!lines.length) return;

  const box = document.createElement('div');
  box.className = 'diag';
  box.innerHTML = lines.join('');
  document.body.appendChild(box);
}

void main();
