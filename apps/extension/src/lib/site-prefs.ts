/**
 * Per-site control.
 *
 * Two complaints with one cause: the extension was all-or-nothing. A user who
 * wanted it everywhere except LinkedIn had to disable it everywhere, and the panel
 * that appears bottom-right could be hidden for a moment but never for good on a
 * form whose own buttons live there.
 *
 * Both are answered by remembering a decision per hostname. Kept in `local`
 * storage rather than synced: "off on this site" is usually about *this* machine's
 * setup - a work laptop where the extension should stay out of an internal portal -
 * and syncing it would silently disable things on a machine the user never
 * configured.
 *
 * Two separate switches, because they are different intentions:
 *
 *  - `disabled`: do nothing at all here. No badge, no autofill, no capture.
 *  - `panelHidden`: keep working, just stop drawing the panel. The user still
 *    wants autofill; they want the box out of the way.
 */

const SITE_PREFS_KEY = 'sitePreferences';

export interface SitePreference {
  disabled?: boolean;
  panelHidden?: boolean;
}

type SitePreferences = Record<string, SitePreference>;

/**
 * The key a decision is stored under.
 *
 * Registrable-ish: the last two labels, so `boards.greenhouse.io` and
 * `my.greenhouse.io` share one decision. Turning it off "for Greenhouse" and
 * finding it still running on another Greenhouse subdomain would read as the
 * setting not working.
 */
export function siteKey(hostname: string): string {
  const parts = hostname.toLowerCase().replace(/^www\./, '').split('.');
  if (parts.length <= 2) return parts.join('.');
  // Handles the common two-part public suffixes these boards use (.co.in,
  // .co.uk) without shipping a full public-suffix list for a preference key.
  const tail = parts.slice(-2).join('.');
  if (/^(co|com|net|org|gov|ac)\.[a-z]{2}$/.test(tail)) return parts.slice(-3).join('.');
  return tail;
}

async function readAll(): Promise<SitePreferences> {
  try {
    const stored = await chrome.storage.local.get(SITE_PREFS_KEY);
    const value = stored?.[SITE_PREFS_KEY];
    return value && typeof value === 'object' ? (value as SitePreferences) : {};
  } catch {
    return {};
  }
}

export async function getSitePreference(hostname: string): Promise<SitePreference> {
  const all = await readAll();
  return all[siteKey(hostname)] ?? {};
}

export async function setSitePreference(
  hostname: string,
  patch: SitePreference,
): Promise<SitePreference> {
  const all = await readAll();
  const key = siteKey(hostname);
  const next = { ...(all[key] ?? {}), ...patch };

  // Drop the entry entirely when nothing is set, so the options list shows only
  // real decisions rather than a growing pile of every site ever visited.
  if (!next.disabled && !next.panelHidden) delete all[key];
  else all[key] = next;

  await chrome.storage.local.set({ [SITE_PREFS_KEY]: all });
  return next;
}

/** Should the extension do anything at all on this host? */
export async function isSiteEnabled(hostname: string): Promise<boolean> {
  return !(await getSitePreference(hostname)).disabled;
}

/** Every site with a decision recorded, for the options page to list and undo. */
export async function listSitePreferences(): Promise<
  { site: string; preference: SitePreference }[]
> {
  const all = await readAll();
  return Object.entries(all)
    .map(([site, preference]) => ({ site, preference }))
    .sort((a, b) => a.site.localeCompare(b.site));
}

export async function clearSitePreference(site: string): Promise<void> {
  const all = await readAll();
  delete all[site];
  await chrome.storage.local.set({ [SITE_PREFS_KEY]: all });
}
