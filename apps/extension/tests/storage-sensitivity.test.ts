/**
 * Storage sensitivity split.
 *
 * The bug this pins: gender, ethnicity, veteran and disability status were written
 * to `chrome.storage.sync`, which replicates through the user's signed-in Google
 * account. Those are special-category personal data, and a job-application helper
 * has no business copying them off the machine.
 *
 * So the assertions here are about *where* data is not: every test that checks the
 * synced half matters more than the ones checking the local half.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { getSettings, savePreferences, saveSettings } from '@/lib/storage';
import { SENSITIVE_KEYS } from '@/lib/types';

let sync: Record<string, unknown>;
let local: Record<string, unknown>;

function installFakeStorage(): void {
  sync = {};
  local = {};
  const area = (store: Record<string, unknown>) => ({
    get: async (key: string) => ({ [key]: store[key] }),
    set: async (patch: Record<string, unknown>) => {
      Object.assign(store, patch);
    },
  });
  (globalThis as unknown as { chrome: unknown }).chrome = {
    storage: { sync: area(sync), local: area(local) },
  };
}

/** The preferences object as it sits in synced storage. */
function syncedAnswers(): Record<string, unknown> {
  const settings = sync.settings as { preferences?: Record<string, unknown> } | undefined;
  return settings?.preferences ?? {};
}

describe('sensitive answers never reach synced storage', () => {
  beforeEach(installFakeStorage);

  it('keeps demographic answers out of sync when saved', async () => {
    await savePreferences({
      gender: 'Female',
      ethnicity: 'Asian',
      veteranStatus: 'No',
      disabilityStatus: 'Prefer not to say',
      noticePeriod: '30 days',
    });

    for (const key of SENSITIVE_KEYS) {
      expect(syncedAnswers(), key).not.toHaveProperty(key);
    }
    // A non-sensitive answer is still synced, so the split is selective rather
    // than a blanket refusal to sync anything.
    expect(syncedAnswers().noticePeriod).toBe('30 days');
  });

  it('stores them locally so forms can still be filled', async () => {
    await savePreferences({ gender: 'Female' });

    const settings = await getSettings();
    expect(settings.preferences.gender).toBe('Female');
    expect((local.sensitiveAnswers as Record<string, unknown>).gender).toBe('Female');
  });

  it('presents one merged object, so callers cannot tell the difference', async () => {
    await savePreferences({ gender: 'Female', noticePeriod: '2 months' });

    const settings = await getSettings();
    expect(settings.preferences.gender).toBe('Female');
    expect(settings.preferences.noticePeriod).toBe('2 months');
  });
});

describe('migrating answers an older version already synced', () => {
  beforeEach(installFakeStorage);

  it('moves them to local storage and deletes the synced copy', async () => {
    // The shape a previous version left behind.
    sync.settings = {
      apiBaseUrl: 'http://localhost:3000',
      preferences: { gender: 'Male', ethnicity: 'Hispanic', noticePeriod: '15 days' },
    };

    const settings = await getSettings();

    // Still usable...
    expect(settings.preferences.gender).toBe('Male');
    // ...but no longer leaving the machine. Leaving a copy behind would have made
    // the whole fix cosmetic.
    expect(syncedAnswers()).not.toHaveProperty('gender');
    expect(syncedAnswers()).not.toHaveProperty('ethnicity');
    expect(syncedAnswers().noticePeriod).toBe('15 days');
  });

  it('is idempotent', async () => {
    sync.settings = { preferences: { gender: 'Male' } };

    await getSettings();
    const afterFirst = JSON.stringify(local.sensitiveAnswers);
    await getSettings();
    expect(JSON.stringify(local.sensitiveAnswers)).toBe(afterFirst);
  });

  it('prefers the answer already on this machine over a synced one', async () => {
    // A machine that has been fixed, receiving the old shape from another device.
    local.sensitiveAnswers = { gender: 'Non-binary' };
    sync.settings = { preferences: { gender: 'Male' } };

    const settings = await getSettings();
    expect(settings.preferences.gender).toBe('Non-binary');
  });

  it('does nothing when there is nothing sensitive to move', async () => {
    sync.settings = { preferences: { noticePeriod: '30 days' } };

    await getSettings();
    expect(local.sensitiveAnswers).toBeUndefined();
  });
});

describe('saveSettings', () => {
  beforeEach(installFakeStorage);

  it('does not resurrect sensitive answers into sync on an unrelated save', async () => {
    await savePreferences({ gender: 'Female' });
    await saveSettings({ showBadge: false });

    for (const key of SENSITIVE_KEYS) {
      expect(syncedAnswers(), key).not.toHaveProperty(key);
    }
    expect((await getSettings()).showBadge).toBe(false);
    // And the answer survives the unrelated save.
    expect((await getSettings()).preferences.gender).toBe('Female');
  });
});
