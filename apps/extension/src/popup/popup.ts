/**
 * Popup: the manual control surface.
 *
 * Shows what the extension sees on the current tab and offers only the actions
 * that page actually supports - an "Autofill" button on a page with no form is
 * worse than no button, because it teaches the user the extension is unreliable.
 */
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

  jobBox.innerHTML = job
    ? `<h1>${escapeHtml(job.title)}</h1>
       <p>${escapeHtml([job.company, job.location].filter(Boolean).join(' \u00b7 ')) || 'Unknown company'}</p>
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
    return;
  }
  render(tab.id, reply.data, health);
}

void main();
