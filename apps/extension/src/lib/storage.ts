/**
 * Settings persistence over `chrome.storage`.
 *
 * Split by durability on purpose:
 *  - `sync`  : user settings and form answers - should follow the user's Chrome
 *              profile across machines.
 *  - `local` : per-machine caches (captured fingerprints, last scrape time).
 *              Losing these is harmless; syncing them would burn the small
 *              `sync` quota for no benefit.
 */
import { DEFAULT_PREFERENCES, DEFAULT_SETTINGS } from './types';
import type { ExtensionSettings, LocalPreferences } from './types';

const SETTINGS_KEY = 'settings';

/** Read settings, filling any missing key from defaults. */
export async function getSettings(): Promise<ExtensionSettings> {
  const stored = await chrome.storage.sync.get(SETTINGS_KEY);
  const raw = (stored?.[SETTINGS_KEY] ?? {}) as Partial<ExtensionSettings>;
  return {
    ...DEFAULT_SETTINGS,
    ...raw,
    // Nested object needs its own merge or a partial saved earlier would drop
    // newly added preference keys.
    preferences: { ...DEFAULT_PREFERENCES, ...(raw.preferences ?? {}) },
  };
}

export async function saveSettings(patch: Partial<ExtensionSettings>): Promise<ExtensionSettings> {
  const current = await getSettings();
  const next: ExtensionSettings = {
    ...current,
    ...patch,
    preferences: { ...current.preferences, ...(patch.preferences ?? {}) },
  };
  await chrome.storage.sync.set({ [SETTINGS_KEY]: next });
  return next;
}

export async function savePreferences(patch: Partial<LocalPreferences>): Promise<ExtensionSettings> {
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

export async function setCachedMatch<T>(key: string, value: T): Promise<void> {
  await chrome.storage.local.set({ [CACHE_PREFIX + key]: { at: Date.now(), value } });
}
