/**
 * Registering the web-app bridge on whatever origin FitWright is actually served
 * from.
 *
 * The manifest can only declare origins known at build time, so the bridge script
 * was pinned to `localhost:3000`. Anyone running FitWright on another port, behind
 * a Docker port mapping, or on a hosted domain got a silently dead integration:
 * the Discover page would sit there reporting "extension required" while the
 * extension was installed and working perfectly on job boards.
 *
 * The fix is a runtime registration against the configured API base URL, plus an
 * optional host permission the user grants for that one origin. Optional, because
 * "read and change your data on any site" in the install dialog to support a
 * hypothetical port is a bad trade - the permission is requested when the URL is
 * set, for exactly the origin entered.
 */

/** The id we register under, so re-registering replaces rather than duplicates. */
const BRIDGE_SCRIPT_ID = 'fitwright-bridge-dynamic';

/** Match pattern covering one origin. */
export function originPattern(baseUrl: string): string | null {
  try {
    const url = new URL(baseUrl);
    if (!/^https?:$/.test(url.protocol)) return null;
    return `${url.protocol}//${url.host}/*`;
  } catch {
    return null;
  }
}

/** Origins the manifest already covers, so we never ask for what we have. */
const STATIC_ORIGINS = ['http://localhost:3000/*', 'http://127.0.0.1:3000/*'];

export function isStaticallyCovered(pattern: string): boolean {
  return STATIC_ORIGINS.includes(pattern);
}

/** Do we already hold permission for this origin? */
export async function hasPermission(pattern: string): Promise<boolean> {
  try {
    return await chrome.permissions.contains({ origins: [pattern] });
  } catch {
    return false;
  }
}

/**
 * Ask for permission on one origin. Must be called from a user gesture (the
 * options page's save button), which is why this is separate from registration.
 */
export async function requestPermission(pattern: string): Promise<boolean> {
  try {
    return await chrome.permissions.request({ origins: [pattern] });
  } catch {
    return false;
  }
}

/**
 * Make sure the bridge runs on the configured origin.
 *
 * Safe to call on every startup and after every settings change: it removes any
 * previous dynamic registration first, so switching the URL does not leave the
 * old origin registered. Returns what happened, so the options page can tell the
 * user rather than failing quietly - the whole failure mode being fixed here was
 * silence.
 */
export async function syncBridgeRegistration(
  baseUrl: string,
): Promise<'static' | 'registered' | 'needs-permission' | 'invalid' | 'unsupported'> {
  const pattern = originPattern(baseUrl);
  if (!pattern) return 'invalid';

  // `chrome.scripting` is MV3-only; a guard keeps this harmless if it is absent.
  if (!chrome.scripting?.registerContentScripts) return 'unsupported';

  try {
    const existing = await chrome.scripting.getRegisteredContentScripts({
      ids: [BRIDGE_SCRIPT_ID],
    });
    if (existing.length) {
      await chrome.scripting.unregisterContentScripts({ ids: [BRIDGE_SCRIPT_ID] });
    }
  } catch {
    /* nothing registered yet */
  }

  // The manifest already injects here; registering again would run it twice.
  if (isStaticallyCovered(pattern)) return 'static';

  if (!(await hasPermission(pattern))) return 'needs-permission';

  try {
    await chrome.scripting.registerContentScripts([
      {
        id: BRIDGE_SCRIPT_ID,
        js: ['bridge.js'],
        matches: [pattern],
        runAt: 'document_start',
        persistAcrossSessions: true,
      },
    ]);
    return 'registered';
  } catch {
    return 'needs-permission';
  }
}
