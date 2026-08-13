/**
 * A visible record of what the extension did and what went wrong.
 *
 * Two invisibilities this fixes:
 *
 * * **Background runs were a black box.** The worker recorded its last scrape into
 *   storage and no UI ever read it, so the user could not tell whether scheduled
 *   searching had run today, worked, or quietly stopped months ago.
 * * **Failures left no trace.** If the service worker threw, diagnosing it meant
 *   asking a non-technical person to open `chrome://extensions` and inspect a
 *   service worker. In practice that means the bug is never reported.
 *
 * Deliberately a small ring buffer in `local` storage, not a log service: the goal
 * is a screen the user can read and paste from, not telemetry. Nothing here leaves
 * the machine unless the user copies it themselves.
 */

const RUNS_KEY = 'runHistory';
const ERRORS_KEY = 'errorLog';

/** Enough to see a pattern ("failing every night since Tuesday"), not a database. */
const RUNS_LIMIT = 20;
const ERRORS_LIMIT = 20;

export interface RunRecord {
  at: number;
  /** 'scheduled' | 'manual' - a nightly failure matters more than a one-off. */
  kind: 'scheduled' | 'manual';
  found: number;
  saved: number;
  /** Per-board outcome, so "which board" is answerable without a second screen. */
  boards: { source: string; found: number; error?: string }[];
}

export interface ErrorRecord {
  at: number;
  /** Where it happened, in words a user can repeat back. */
  context: string;
  message: string;
}

async function readList<T>(key: string): Promise<T[]> {
  try {
    const stored = await chrome.storage.local.get(key);
    const value = stored?.[key];
    return Array.isArray(value) ? (value as T[]) : [];
  } catch {
    return [];
  }
}

/** Newest first, oldest dropped. */
async function pushBounded<T>(key: string, entry: T, limit: number): Promise<void> {
  const list = await readList<T>(key);
  await chrome.storage.local.set({ [key]: [entry, ...list].slice(0, limit) });
}

export async function recordRun(record: RunRecord): Promise<void> {
  await pushBounded(RUNS_KEY, record, RUNS_LIMIT);
}

export function listRuns(): Promise<RunRecord[]> {
  return readList<RunRecord>(RUNS_KEY);
}

/**
 * Record a failure. Never throws - a diagnostics write that breaks the thing it
 * is diagnosing would be worse than no diagnostics.
 */
export async function recordError(context: string, error: unknown): Promise<void> {
  try {
    const message =
      error instanceof Error ? error.message : typeof error === 'string' ? error : 'Unknown error';
    await pushBounded<ErrorRecord>(
      ERRORS_KEY,
      { at: Date.now(), context, message: message.slice(0, 300) },
      ERRORS_LIMIT,
    );
  } catch {
    /* nothing to do; losing a diagnostic line is acceptable */
  }
}

export function listErrors(): Promise<ErrorRecord[]> {
  return readList<ErrorRecord>(ERRORS_KEY);
}

export async function clearErrors(): Promise<void> {
  await chrome.storage.local.set({ [ERRORS_KEY]: [] });
}

/** "2 hours ago" - the only format anyone reads on a diagnostics screen. */
export function timeAgo(at: number, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

/**
 * One line summarising the last run, for the popup.
 *
 * Returns null when nothing has run: "never run" belongs to the caller, which
 * knows whether scheduling is even switched on.
 */
export async function lastRunSummary(): Promise<string | null> {
  const [latest] = await listRuns();
  if (!latest) return null;

  const when = timeAgo(latest.at);
  if (latest.found === 0) {
    const failing = latest.boards.filter((b) => b.error).length;
    return failing
      ? `Last run ${when}: nothing found, ${failing} board${failing === 1 ? '' : 's'} had trouble`
      : `Last run ${when}: nothing new`;
  }
  return `Last run ${when}: ${latest.found} found, ${latest.saved} new`;
}
