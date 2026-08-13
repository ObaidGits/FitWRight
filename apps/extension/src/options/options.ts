/**
 * Options page.
 *
 * Owns two kinds of state: connection/behaviour settings, and the application
 * answers a resume cannot supply. The latter live here rather than on the server
 * because they are answers about the user's circumstances, not facts about their
 * career history - and because a blank one must mean "skip this field", which is
 * easier to reason about when the extension owns the value.
 */
import { sendToWorker } from '@/lib/messages';
import {
  originPattern,
  requestPermission,
  syncBridgeRegistration,
} from '@/lib/bridge-registration';
import { clearErrors, listErrors, listRuns, timeAgo } from '@/lib/diagnostics';
import { getSettings, saveSettings } from '@/lib/storage';
import type { FormAnswers, ScrapeQuery } from '@/lib/types';

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

const statusBox = el<HTMLSpanElement>('status');
const queriesBody = el<HTMLTableSectionElement>('queries');

/** Working copy - committed to storage only on Save. */
let queries: ScrapeQuery[] = [];

const PREFERENCE_IDS: Array<keyof Omit<FormAnswers, 'custom'>> = [
  'workAuthorization',
  'requiresSponsorship',
  'noticePeriod',
  'salaryExpectation',
  'gender',
  'ethnicity',
  'veteranStatus',
  'disabilityStatus',
];

const BOARD_LABELS: Record<string, string> = {
  indeed: 'Indeed',
  linkedin: 'LinkedIn',
  instahyre: 'Instahyre',
  hirist: 'Hirist',
  foundit: 'Foundit',
};

/** Escape values before they go into innerHTML on this page. */
function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string,
  );
}

function setStatus(message: string, kind: '' | 'ok' | 'err' = ''): void {
  statusBox.textContent = message;
  statusBox.className = kind;
  if (message) setTimeout(() => (statusBox.textContent = ''), 3500);
}

function renderQueries(): void {
  queriesBody.replaceChildren();

  if (!queries.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.textContent = 'No saved searches yet.';
    cell.style.color = 'var(--muted)';
    row.appendChild(cell);
    queriesBody.appendChild(row);
    return;
  }

  queries.forEach((query, index) => {
    const row = document.createElement('tr');

    // textContent throughout - a search term is user input and must never be
    // interpolated into HTML.
    const board = document.createElement('td');
    board.textContent = BOARD_LABELS[query.source] ?? query.source;
    const terms = document.createElement('td');
    terms.textContent = query.query;
    const location = document.createElement('td');
    location.textContent = query.location || '\u2014';

    const actions = document.createElement('td');
    const remove = document.createElement('button');
    remove.className = 'link';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => {
      queries.splice(index, 1);
      renderQueries();
    });
    actions.appendChild(remove);

    row.append(board, terms, location, actions);
    queriesBody.appendChild(row);
  });
}

async function load(): Promise<void> {
  const settings = await getSettings();

  el<HTMLInputElement>('apiBaseUrl').value = settings.apiBaseUrl;
  el<HTMLInputElement>('showBadge').checked = settings.showBadge;
  el<HTMLInputElement>('autoCapture').checked = settings.autoCapture;
  el<HTMLInputElement>('trackApplications').checked = settings.trackApplications;
  el<HTMLInputElement>('backgroundScrape').checked = settings.backgroundScrape;
  el<HTMLInputElement>('scrapeIntervalMinutes').value = String(settings.scrapeIntervalMinutes);

  for (const id of PREFERENCE_IDS) {
    const input = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
    if (input) input.value = settings.preferences[id] ?? '';
  }

  queries = [...settings.scrapeQueries];
  renderQueries();
}

async function save(): Promise<void> {
  const preferences = {} as Omit<FormAnswers, 'custom'>;
  for (const id of PREFERENCE_IDS) {
    const input = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
    preferences[id] = input?.value.trim() ?? '';
  }

  const rawInterval = Number(el<HTMLInputElement>('scrapeIntervalMinutes').value);
  // Chrome throttles alarms under ~1 minute and there is no reason to run this
  // more than a couple of times an hour; clamp rather than reject.
  const interval = Number.isFinite(rawInterval) ? Math.max(30, Math.round(rawInterval)) : 360;

  const baseUrl = el<HTMLInputElement>('apiBaseUrl').value.trim() || 'http://localhost:3000';

  await saveSettings({
    apiBaseUrl: baseUrl,
    showBadge: el<HTMLInputElement>('showBadge').checked,
    autoCapture: el<HTMLInputElement>('autoCapture').checked,
    trackApplications: el<HTMLInputElement>('trackApplications').checked,
    backgroundScrape: el<HTMLInputElement>('backgroundScrape').checked,
    scrapeIntervalMinutes: interval,
    scrapeQueries: queries,
    preferences: preferences as FormAnswers,
  });

  el<HTMLInputElement>('scrapeIntervalMinutes').value = String(interval);

  // Make the web-app bridge work on whatever URL was just entered. Runs from the
  // save click on purpose: asking for a host permission requires a user gesture,
  // and this is the moment the user chose that origin.
  const bridge = await ensureBridge(baseUrl);
  setStatus(bridge ? `Saved. ${bridge}` : 'Saved.', 'ok');
}

/**
 * Register the bridge for a non-default FitWright URL, asking for permission if
 * needed. Returns a sentence to append to the save confirmation, or null when
 * there is nothing worth saying.
 *
 * Every branch says something, because the failure this replaces was silence: the
 * Discover page insisting the extension was missing while it sat there installed.
 */
async function ensureBridge(baseUrl: string): Promise<string | null> {
  const pattern = originPattern(baseUrl);
  if (!pattern) return 'That URL could not be read, so the FitWright page link is off.';

  let state = await syncBridgeRegistration(baseUrl);

  if (state === 'needs-permission') {
    const granted = await requestPermission(pattern);
    if (!granted) {
      return `Permission for ${pattern} was declined, so the FitWright page cannot talk to the extension.`;
    }
    state = await syncBridgeRegistration(baseUrl);
  }

  if (state === 'registered') return `Connected to FitWright at ${baseUrl}.`;
  if (state === 'unsupported') return 'This browser cannot register the page link.';
  // 'static': the default localhost origin, already handled by the manifest.
  return null;
}

function wire(): void {
  el<HTMLButtonElement>('save').addEventListener('click', () => void save());

  el<HTMLButtonElement>('q-add').addEventListener('click', () => {
    const query = el<HTMLInputElement>('q-query').value.trim();
    if (!query) {
      setStatus('Enter keywords for the search.', 'err');
      return;
    }
    queries.push({
      source: el<HTMLSelectElement>('q-source').value,
      query,
      location: el<HTMLInputElement>('q-location').value.trim(),
    });
    el<HTMLInputElement>('q-query').value = '';
    el<HTMLInputElement>('q-location').value = '';
    renderQueries();
  });

  el<HTMLButtonElement>('test').addEventListener('click', () => {
    const result = el<HTMLSpanElement>('conn-result');
    result.textContent = 'Checking...';

    // Save first: the worker reads the base URL from storage, so testing an
    // unsaved URL would silently test the old one.
    void save()
      .then(() => sendToWorker({ type: 'ping' }))
      .then((reply) => {
        if (!reply.ok) {
          result.textContent = reply.error;
          result.style.color = 'var(--err)';
          return;
        }
        if (!reply.data.versionOk) {
          result.textContent = 'Connected, but this extension build is out of date.';
          result.style.color = 'var(--err)';
          return;
        }
        result.textContent = reply.data.hasResume
          ? 'Connected. Resume found.'
          : 'Connected, but no resume uploaded yet.';
        result.style.color = reply.data.hasResume ? 'var(--ok)' : 'var(--muted)';
      });
  });
}

wire();
void load();

/**
 * Render the activity and problems lists.
 *
 * Plain text, no interaction beyond copy and clear: this screen exists so a
 * non-technical person can answer "is it working?" and "what went wrong?" without
 * being told to inspect a service worker.
 */
async function renderDiagnostics(): Promise<void> {
  const runsBox = document.getElementById('runs');
  const errorsBox = document.getElementById('errors');
  if (!runsBox || !errorsBox) return;

  const [runs, errors] = await Promise.all([listRuns(), listErrors()]);

  runsBox.innerHTML = runs.length
    ? runs
        .slice(0, 8)
        .map((run) => {
          const trouble = run.boards.filter((b) => b.error);
          const detail = trouble.length
            ? ` &middot; trouble on ${trouble.map((b) => escapeHtml(b.source)).join(', ')}`
            : '';
          return `<p class="hint">${timeAgo(run.at)} &middot; ${run.found} found, ${run.saved} new${detail}</p>`;
        })
        .join('')
    : '<p class="hint">No background runs recorded yet.</p>';

  errorsBox.innerHTML = errors.length
    ? errors
        .slice(0, 8)
        .map(
          (e) =>
            `<p class="hint">${timeAgo(e.at)} &middot; <strong>${escapeHtml(e.context)}</strong>: ${escapeHtml(e.message)}</p>`,
        )
        .join('')
    : '<p class="hint">Nothing has failed. </p>';

  document.getElementById('copy-errors')?.addEventListener('click', () => {
    const text = errors
      .map((e) => `${new Date(e.at).toISOString()} ${e.context}: ${e.message}`)
      .join('\n');
    void navigator.clipboard.writeText(text || 'No problems recorded.');
    setStatus('Copied.', 'ok');
  });

  document.getElementById('clear-errors')?.addEventListener('click', () => {
    void clearErrors().then(() => {
      void renderDiagnostics();
      setStatus('Cleared.', 'ok');
    });
  });
}

void renderDiagnostics();
