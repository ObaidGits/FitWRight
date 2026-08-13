/**
 * Per-site control.
 *
 * The point of these is that "off" means off. A setting that quietly still runs
 * something is worse than no setting, because the user believes they have
 * controlled it. The grouping rule matters for the same reason: turning it off
 * "for Greenhouse" and then finding it running on another Greenhouse subdomain
 * reads as the switch being broken.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  clearSitePreference,
  getSitePreference,
  isSiteEnabled,
  listSitePreferences,
  setSitePreference,
  siteKey,
} from '@/lib/site-prefs';

function installFakeStorage(): void {
  const store: Record<string, unknown> = {};
  (globalThis as unknown as { chrome: unknown }).chrome = {
    storage: {
      local: {
        get: async (key: string) => ({ [key]: store[key] }),
        set: async (patch: Record<string, unknown>) => {
          Object.assign(store, patch);
        },
      },
    },
  };
}

describe('siteKey', () => {
  it('groups subdomains of one platform together', () => {
    // Otherwise "off for Greenhouse" would leave it running on the next tenant.
    expect(siteKey('boards.greenhouse.io')).toBe('greenhouse.io');
    expect(siteKey('my.greenhouse.io')).toBe('greenhouse.io');
    expect(siteKey('acme.icims.com')).toBe('icims.com');
  });

  it('handles two-part suffixes without a public-suffix list', () => {
    expect(siteKey('www.glassdoor.co.in')).toBe('glassdoor.co.in');
    expect(siteKey('jobs.example.co.uk')).toBe('example.co.uk');
  });

  it('strips www and is case-insensitive', () => {
    expect(siteKey('WWW.Lever.CO')).toBe('lever.co');
  });

  it('leaves a bare host alone', () => {
    expect(siteKey('localhost')).toBe('localhost');
  });
});

describe('turning a site off', () => {
  beforeEach(installFakeStorage);

  it('is on by default', async () => {
    expect(await isSiteEnabled('boards.greenhouse.io')).toBe(true);
  });

  it('stays off once set, across subdomains', async () => {
    await setSitePreference('www.linkedin.com', { disabled: true });

    expect(await isSiteEnabled('www.linkedin.com')).toBe(false);
    expect(await isSiteEnabled('jobs.linkedin.com')).toBe(false);
  });

  it('does not affect other sites', async () => {
    await setSitePreference('www.linkedin.com', { disabled: true });
    expect(await isSiteEnabled('boards.greenhouse.io')).toBe(true);
  });

  it('can be turned back on', async () => {
    await setSitePreference('www.linkedin.com', { disabled: true });
    await setSitePreference('www.linkedin.com', { disabled: false });
    expect(await isSiteEnabled('www.linkedin.com')).toBe(true);
  });
});

describe('hiding the panel only', () => {
  beforeEach(installFakeStorage);

  it('is a different decision from turning the site off', async () => {
    await setSitePreference('acme.icims.com', { panelHidden: true });

    // Autofill still works here; the box just stops appearing.
    expect(await isSiteEnabled('acme.icims.com')).toBe(true);
    expect((await getSitePreference('acme.icims.com')).panelHidden).toBe(true);
  });

  it('can coexist with being disabled', async () => {
    await setSitePreference('acme.icims.com', { panelHidden: true });
    await setSitePreference('acme.icims.com', { disabled: true });

    const pref = await getSitePreference('acme.icims.com');
    expect(pref.panelHidden).toBe(true);
    expect(pref.disabled).toBe(true);
  });
});

describe('the managed list', () => {
  beforeEach(installFakeStorage);

  it('shows only real decisions, sorted', async () => {
    await setSitePreference('www.linkedin.com', { disabled: true });
    await setSitePreference('acme.icims.com', { panelHidden: true });

    const list = await listSitePreferences();
    expect(list.map((entry) => entry.site)).toEqual(['icims.com', 'linkedin.com']);
  });

  it('drops an entry once nothing is set, rather than keeping an empty row', async () => {
    await setSitePreference('www.linkedin.com', { disabled: true });
    await setSitePreference('www.linkedin.com', { disabled: false });

    expect(await listSitePreferences()).toEqual([]);
  });

  it('can be cleared by site key', async () => {
    await setSitePreference('www.linkedin.com', { disabled: true });
    await clearSitePreference('linkedin.com');

    expect(await listSitePreferences()).toEqual([]);
    expect(await isSiteEnabled('www.linkedin.com')).toBe(true);
  });

  it('survives storage being empty or broken', async () => {
    (globalThis as unknown as { chrome: unknown }).chrome = {
      storage: {
        local: {
          get: async () => {
            throw new Error('storage unavailable');
          },
          set: async () => undefined,
        },
      },
    };
    // A storage failure must not disable the extension everywhere.
    expect(await isSiteEnabled('boards.greenhouse.io')).toBe(true);
  });
});
