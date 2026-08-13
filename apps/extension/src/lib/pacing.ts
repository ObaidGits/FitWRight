/**
 * Harvest pacing: how hard we are willing to lean on a job board.
 *
 * This exists to protect the *user's account*, not the board's servers. Naukri,
 * LinkedIn and Indeed all watch for automation, and the penalty lands on the
 * person whose session it is - a restricted account costs them far more than a
 * missed listing. So the defaults here are deliberately timid, and the cap is
 * per board per day rather than per run: ten runs of "just one more search" is
 * the pattern that gets someone flagged.
 *
 * Three rules:
 *
 * 1. **A daily cap per board.** Once hit, that board is skipped until tomorrow
 *    and says so, rather than failing quietly.
 * 2. **Jitter on every gap.** A request exactly every 2000ms is a machine
 *    signature; nothing human is that regular.
 * 3. **The user can slow it down but not remove it.** There is no "no limit"
 *    setting, because the person choosing it cannot see the consequence until
 *    their account is already restricted.
 */

/** Runs per board per calendar day. Conservative on purpose. */
export const DEFAULT_DAILY_CAP = 6;

/** Floor, so a user cannot configure the protection away entirely. */
export const MIN_GAP_MS = 1500;

const USAGE_KEY = 'boardUsage';

interface BoardUsage {
  /** Local calendar day, so the cap resets overnight in the user's own timezone. */
  day: string;
  counts: Record<string, number>;
}

/** Local date key. Uses local time deliberately: "today" is the user's today. */
function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
}

async function readUsage(): Promise<BoardUsage> {
  const raw = await chrome.storage.local.get(USAGE_KEY);
  const stored = raw[USAGE_KEY] as BoardUsage | undefined;
  if (!stored || stored.day !== today()) return { day: today(), counts: {} };
  return stored;
}

/** How many runs this board has left today. */
export async function remainingToday(board: string, cap = DEFAULT_DAILY_CAP): Promise<number> {
  const usage = await readUsage();
  return Math.max(0, cap - (usage.counts[board] ?? 0));
}

/** Record one run against a board's daily budget. */
export async function recordRun(board: string): Promise<void> {
  const usage = await readUsage();
  usage.counts[board] = (usage.counts[board] ?? 0) + 1;
  await chrome.storage.local.set({ [USAGE_KEY]: usage });
}

/**
 * A randomised pause. `base` is the midpoint; the actual wait lands anywhere
 * from 0.6x to 1.6x of it, so a run has no fixed rhythm to fingerprint.
 */
export function jitteredGap(base: number): number {
  const floor = Math.max(MIN_GAP_MS, base * 0.6);
  const spread = Math.max(MIN_GAP_MS, base * 1.6) - floor;
  return Math.round(floor + Math.random() * spread);
}

/** Today's usage per board, for the popup to show honestly. */
export async function usageSummary(): Promise<Record<string, number>> {
  return (await readUsage()).counts;
}
