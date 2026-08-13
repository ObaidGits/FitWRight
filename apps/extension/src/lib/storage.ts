/**
 * Settings persistence over `chrome.storage`.
 *
 * Split by durability AND by sensitivity:
 *
 *  - `sync`      : ordinary settings and non-sensitive answers - should follow
 *                  the user's Chrome profile across machines.
 *  - `local`     : per-machine caches (captured fingerprints, last scrape time).
 *  - `local`, by * policy: demographic answers. See `SENSITIVE_KEYS` below.
 *
 * The sensitivity split is not a nicety. `chrome.storage.sync` replicates through
 * the signed-in Google account, so anything written there leaves the machine.
 * Gender, ethnicity, veteran and disability status are special-category personal
 * data; a job-application helper has no business copying them into a cloud
 * account the user was not asked about. They now live in `local` only, and a
 * one-time migration moves any that a previous version already synced - and
 * deletes them from `sync`, because leaving a copy behind would make the fix
 * cosmetic.
 */
import { DEFAULT_PREFERENCES, DEFAULT_SETTINGS, SENSITIVE_KEYS } from './types';
import type { ExtensionSettings, FormAnswers } from './types';

const SETTINGS_KEY = 'settings';
const SENSITIVE_KEY = 'sensitiveAnswers';

/** Pull the sensitive fields out of a preferences object. */
function splitSensitive(prefs: Partial<FormAnswers>): {
  shareable: Partial<FormAnswers>;
  sensitive: Partial<FormAnswers>;
} {
  const shareable: Record<string, unknown> = {};
  const sensitive: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(prefs)) {
    if ((SENSITIVE_KEYS as readonly string[]).includes(key)) sensitive[key] = value;
    else shareable[key] = value;
  }
  return {
    shareable: shareable as Partial<FormAnswers>,
    sensitive: sensitive as Partial<FormAnswers>,
  };
}

/**
 * Move demographic answers an older version synced into local storage, once.
 *
 * Idempotent: after the first run there is nothing sensitive left in `sync` to
 * find. Runs on read rather than on install so a profile that syncs the old shape
 * down from another machine is also cleaned up.
 */
async function migrateSensitiveOutOfSync(
  raw: Partial<ExtensionSettings> & { preferences?: Partial<FormAnswers> },
): Promise<Partial<FormAnswers>> {
  const { sensitive } = splitSensitive(raw.preferences ?? {});
  const hasSyncedSensitive = Object.values(sensitive).some(
    (value) => typeof value === 'string' && value.trim() !== '',
  );
  if (!hasSyncedSensitive) return {};

  const existing = await chrome.storage.local.get(SENSITIVE_KEY);
  const merged = {
    // Anything already on this machine wins: it is the more deliberate answer.
    ...sensitive,
    ...((existing?.[SENSITIVE_KEY] ?? {}) as Partial<FormAnswers>),
  };
  await chrome.storage.local.set({ [SENSITIVE_KEY]: merged });

  // Strip them from the synced copy. Without this the migration would only add a
  // second home rather than remove the exposure.
  const { shareable } = splitSensitive(raw.preferences ?? {});
  await chrome.storage.sync.set({ [SETTINGS_KEY]: { ...raw, preferences: shareable } });
  return merged;
}

/** Read settings, filling any missing key from defaults. */
export async function getSettings(): Promise<ExtensionSettings> {
  const stored = await chrome.storage.sync.get(SETTINGS_KEY);
  let raw = (stored?.[SETTINGS_KEY] ?? {}) as Partial<ExtensionSettings>;

  // Settings that outgrew the sync quota live here instead. Checked second so a
  // successful sync write always wins, and only consulted when sync has nothing -
  // otherwise a stale overflow copy could shadow a good synced one.
  if (!stored?.[SETTINGS_KEY]) {
    const overflow = await chrome.storage.local.get(SETTINGS_KEY);
    if (overflow?.[SETTINGS_KEY]) raw = overflow[SETTINGS_KEY] as Partial<ExtensionSettings>;
  }

  const migrated = await migrateSensitiveOutOfSync(raw);
  const localStore = await chrome.storage.local.get(SENSITIVE_KEY);
  const sensitive = {
    ...((localStore?.[SENSITIVE_KEY] ?? {}) as Partial<FormAnswers>),
    ...migrated,
  };

  return {
    ...DEFAULT_SETTINGS,
    ...raw,
    // Nested object needs its own merge or a partial saved earlier would drop
    // newly added preference keys. Sensitive answers are layered back on from
    // local storage, so callers see one object and cannot tell the difference.
    preferences: { ...DEFAULT_PREFERENCES, ...(raw.preferences ?? {}), ...sensitive },
  };
}

export async function saveSettings(patch: Partial<ExtensionSettings>): Promise<ExtensionSettings> {
  const current = await getSettings();
  const next: ExtensionSettings = {
    ...current,
    ...patch,
    preferences: { ...current.preferences, ...(patch.preferences ?? {}) },
  };

  const { shareable, sensitive } = splitSensitive(next.preferences);
  // Two writes on purpose: the sensitive half never reaches `sync`.
  await chrome.storage.local.set({ [SENSITIVE_KEY]: sensitive });
  await writeSyncedSettings({ ...next, preferences: shareable });
  return next;
}

/**
 * Write the synced half, falling back to local storage when it will not fit.
 *
 * `chrome.storage.sync` caps a single item at 8 KB, and all settings are one item -
 * saved searches plus free-text custom answers can cross that. The write then
 * rejects, and because nothing caught it the user saw a save that appeared to work
 * and silently did not.
 *
 * Falling back to `local` is the right trade: the settings survive on this machine,
 * which is what the user asked for, and lose only the across-machines sync they
 * probably do not know exists. The alternative - refusing to save - throws away
 * work they just did.
 */
async function writeSyncedSettings(
  // The synced half has the sensitive keys removed, so `preferences` is a subset by
  // construction - typed as partial rather than cast, so that stays visible.
  value: Omit<ExtensionSettings, 'preferences'> & { preferences: Partial<FormAnswers> },
): Promise<void> {
  try {
    await chrome.storage.sync.set({ [SETTINGS_KEY]: value });
    // Clear any earlier overflow copy so the two cannot disagree later.
    await chrome.storage.local.remove(SETTINGS_KEY);
  } catch (error) {
    await chrome.storage.local.set({ [SETTINGS_KEY]: value });
    await recordSyncFallback(error);
  }
}

/**
 * Note that settings outgrew synced storage, so the options page can say so.
 *
 * Best-effort: if even this write fails there is nothing useful left to do, and a
 * diagnostics failure must not take the settings save with it.
 */
async function recordSyncFallback(error: unknown): Promise<void> {
  try {
    const { recordError } = await import('./diagnostics');
    await recordError(
      'Saving settings',
      'Settings are too large to sync across machines, so they were saved on this computer only. ' +
        `Removing some saved searches would restore syncing. (${
          error instanceof Error ? error.message : 'quota exceeded'
        })`,
    );
  } catch {
    /* nothing further to do */
  }
}

export async function savePreferences(patch: Partial<FormAnswers>): Promise<ExtensionSettings> {
  const current = await getSettings();
  return saveSettings({ preferences: { ...current.preferences, ...patch } });
}

/** Normalize the API base so callers can always concatenate a leading-slash path. */
export function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, '');
}

// --------------------------------------------------------------------------- //
// Per-machine caches
// --------------------------------------------------------------------------- //

const CAPTURED_KEY = 'capturedFingerprints';
const CAPTURED_LIMIT = 500;

/**
 * Remember what we already captured so auto-capture does not re-POST on every
 * revisit. The server dedupes too - this just avoids the pointless round trip.
 */
export async function wasCaptured(fingerprint: string): Promise<boolean> {
  const stored = await chrome.storage.local.get(CAPTURED_KEY);
  const list = (stored?.[CAPTURED_KEY] ?? []) as string[];
  return list.includes(fingerprint);
}

export async function rememberCaptured(fingerprint: string): Promise<void> {
  const stored = await chrome.storage.local.get(CAPTURED_KEY);
  const list = (stored?.[CAPTURED_KEY] ?? []) as string[];
  if (list.includes(fingerprint)) return;
  // Bounded FIFO: oldest entries fall off rather than growing without limit.
  const next = [...list, fingerprint].slice(-CAPTURED_LIMIT);
  await chrome.storage.local.set({ [CAPTURED_KEY]: next });
}

const CACHE_PREFIX = 'matchCache:';
const MATCH_TTL_MS = 24 * 60 * 60 * 1000;
/** Cached scores kept at most. Well above a heavy day's browsing. */
const MATCH_CACHE_LIMIT = 300;

/** Cache a match score per job URL - scoring costs an LLM call. */
export async function getCachedMatch<T>(key: string): Promise<T | null> {
  const fullKey = CACHE_PREFIX + key;
  const stored = await chrome.storage.local.get(fullKey);
  const entry = stored?.[fullKey] as { at: number; value: T } | undefined;
  if (!entry) return null;
  if (Date.now() - entry.at > MATCH_TTL_MS) {
    await chrome.storage.local.remove(fullKey);
    return null;
  }
  return entry.value;
}

/**
 * Remove expired match-cache entries.
 *
 * The TTL above only applies to a key that is read again, so a job page visited
 * once and never revisited kept its entry forever. Browse a few hundred postings
 * and that is a few hundred dead keys nothing would ever clear.
 *
 * Also caps the total, oldest first: a user who browses heavily within one day can
 * outgrow the cache before anything expires, and an eviction policy that only
 * handles age would never fire for them.
 */
export async function sweepMatchCache(limit = MATCH_CACHE_LIMIT): Promise<number> {
  try {
    const all = await chrome.storage.local.get(null);
    const entries: { key: string; at: number }[] = [];
    const expired: string[] = [];
    const now = Date.now();

    for (const [key, value] of Object.entries(all)) {
      if (!key.startsWith(CACHE_PREFIX)) continue;
      const at = (value as { at?: number })?.at ?? 0;
      if (now - at > MATCH_TTL_MS) expired.push(key);
      else entries.push({ key, at });
    }

    // Oldest first, so the ones evicted for size are the least likely to be needed.
    entries.sort((a, b) => a.at - b.at);
    const overflow = entries.slice(0, Math.max(0, entries.length - limit)).map((e) => e.key);
    const doomed = [...expired, ...overflow];
    if (doomed.length) await chrome.storage.local.remove(doomed);
    return doomed.length;
  } catch {
    // A cache that fails to shrink is a slow leak, not a broken extension.
    return 0;
  }
}

export async function setCachedMatch<T>(key: string, value: T): Promise<void> {
  await chrome.storage.local.set({ [CACHE_PREFIX + key]: { at: Date.now(), value } });
}
