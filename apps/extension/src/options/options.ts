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
import { getSettings, saveSettings } from '@/lib/storage';
import type { LocalPreferences, ScrapeQuery } from '@/lib/types';

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

const statusBox = el<HTMLSpanElement>('status');
const queriesBody = el<HTMLTableSectionElement>('queries');

/** Working copy - committed to storage only on Save. */
let queries: ScrapeQuery[] = [];

const PREFERENCE_IDS: Array<keyof Omit<LocalPreferences, 'custom'>> = [
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
  const preferences = {} as Omit<LocalPreferences, 'custom'>;
  for (const id of PREFERENCE_IDS) {
    const input = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
    preferences[id] = input?.value.trim() ?? '';
  }

  const rawInterval = Number(el<HTMLInputElement>('scrapeIntervalMinutes').value);
  // Chrome throttles alarms under ~1 minute and there is no reason to run this
  // more than a couple of times an hour; clamp rather than reject.
  const interval = Number.isFinite(rawInterval) ? Math.max(30, Math.round(rawInterval)) : 360;

  await saveSettings({
    apiBaseUrl: el<HTMLInputElement>('apiBaseUrl').value.trim() || 'http://localhost:3000',
    showBadge: el<HTMLInputElement>('showBadge').checked,
    autoCapture: el<HTMLInputElement>('autoCapture').checked,
    trackApplications: el<HTMLInputElement>('trackApplications').checked,
    backgroundScrape: el<HTMLInputElement>('backgroundScrape').checked,
    scrapeIntervalMinutes: interval,
    scrapeQueries: queries,
    preferences: preferences as LocalPreferences,
  });

  el<HTMLInputElement>('scrapeIntervalMinutes').value = String(interval);
  setStatus('Saved.', 'ok');
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
