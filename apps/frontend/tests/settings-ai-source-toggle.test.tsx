import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '@/components/atelier/toast';

/**
 * "How does a user switch between FitWright's provider and their own?"
 *
 * Before this, Settings -> AI Provider only ever showed the bring-your-own-key
 * form: there was no control that named FitWright as an option, and the only
 * way to get FitWright-funded AI was to happen to leave every field blank. This
 * pins the explicit switch: an "AI source" segmented control that reads the
 * same own-key status GET /credits already derives (ai_metered.user_has_own_key)
 * and, on switching to FitWright, clears the stored key (and base URL, for
 * self-hosted providers) rather than just flipping a client-side flag that
 * could disagree with what the backend actually bills.
 */

vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ user: { name: 'Test User' }, refresh: vi.fn() }),
}));
vi.mock('@/components/theme/theme-provider', () => ({
  useTheme: () => ({ theme: 'light', toggleTheme: vi.fn() }),
}));
vi.mock('@/components/settings/account-security', () => ({ AccountSecurity: () => null }));
vi.mock('@/components/settings/buy-credits', () => ({ BuyCredits: () => null }));
vi.mock('@/components/settings/feature-prompts-editor', () => ({
  FeaturePromptsEditor: () => null,
}));

vi.mock('@/lib/api/config', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/config')>('@/lib/api/config');
  return {
    ...actual,
    fetchLlmConfig: vi.fn(),
    fetchApiKeyStatus: vi.fn(),
    fetchFeatureConfig: vi.fn().mockResolvedValue({
      enable_cover_letter: true,
      enable_outreach_message: true,
      enable_interview_prep: true,
    }),
    fetchLanguageConfig: vi.fn().mockResolvedValue({ language: 'en' }),
    updateLlmConfig: vi.fn(),
    updateApiKeys: vi.fn(),
    deleteApiKey: vi.fn(),
    testLlmConnection: vi.fn(),
  };
});

vi.mock('@/lib/api/credits', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/credits')>('@/lib/api/credits');
  return { ...actual, getMyCredits: vi.fn(), getMyUsage: vi.fn().mockResolvedValue({ items: [] }) };
});

vi.mock('@/lib/api/auth', () => ({ updateProfile: vi.fn() }));

const { fetchLlmConfig, fetchApiKeyStatus, updateLlmConfig, updateApiKeys, deleteApiKey } =
  await import('@/lib/api/config');
const { getMyCredits } = await import('@/lib/api/credits');
const SettingsPage = (await import('@/app/(app)/settings/page')).default;

function llmConfig(over: Partial<Record<string, unknown>> = {}) {
  return {
    provider: 'openai',
    model: 'gpt-5-nano-2025-08-07',
    api_key: '',
    api_base: null,
    reasoning_effort: null,
    ...over,
  };
}

function keyStatus(configured: boolean, over: Partial<Record<string, unknown>> = {}) {
  return {
    providers: [
      {
        provider: 'openai',
        configured,
        masked_key: configured ? 'sk-...abcd' : null,
        unreadable: false,
      },
    ],
    ...over,
  };
}

function credits(mode: 'own_key' | 'credits' | 'unlimited', over: Record<string, unknown> = {}) {
  return {
    mode,
    unlimited: mode !== 'credits',
    summary:
      mode === 'own_key'
        ? "You're using your own AI provider key."
        : 'about 4 more tailored resumes',
    actions: [],
    credits_enabled: true,
    ...over,
  };
}

function renderSettings() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SettingsPage />
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe('Settings > AI Provider: FitWright vs own-provider switch', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows "My own provider" selected when a key is already saved', async () => {
    vi.mocked(fetchLlmConfig).mockResolvedValue(llmConfig() as never);
    vi.mocked(fetchApiKeyStatus).mockResolvedValue(keyStatus(true) as never);
    vi.mocked(getMyCredits).mockResolvedValue(credits('own_key') as never);

    renderSettings();

    const ownTab = await screen.findByRole('tab', { name: 'My own provider' });
    await waitFor(() => expect(ownTab).toHaveAttribute('aria-selected', 'true'));
    // The provider/key form is visible in this mode.
    expect(screen.getByLabelText(/LLM provider/i)).toBeInTheDocument();
  });

  it('shows "FitWright" selected and hides the provider form when no key is saved', async () => {
    vi.mocked(fetchLlmConfig).mockResolvedValue(llmConfig() as never);
    vi.mocked(fetchApiKeyStatus).mockResolvedValue(keyStatus(false) as never);
    vi.mocked(getMyCredits).mockResolvedValue(credits('credits') as never);

    renderSettings();

    const fitwrightTab = await screen.findByRole('tab', { name: 'FitWright' });
    await waitFor(() => expect(fitwrightTab).toHaveAttribute('aria-selected', 'true'));
    // The BYO-key form (provider dropdown, Save/Test buttons) is gone in this mode.
    expect(screen.queryByLabelText(/LLM provider/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });

  it('disables the FitWright tab and explains why when credits are not enabled', async () => {
    vi.mocked(fetchLlmConfig).mockResolvedValue(llmConfig() as never);
    vi.mocked(fetchApiKeyStatus).mockResolvedValue(keyStatus(false) as never);
    vi.mocked(getMyCredits).mockResolvedValue(
      credits('unlimited', { credits_enabled: false }) as never
    );

    renderSettings();

    const fitwrightTab = await screen.findByRole('tab', { name: 'FitWright' });
    expect(fitwrightTab).toBeDisabled();
    expect(
      await screen.findByText(/FitWright's own provider isn't turned on for this install/i)
    ).toBeInTheDocument();
  });

  it('switching to FitWright asks for confirmation, then deletes the stored key', async () => {
    vi.mocked(fetchLlmConfig).mockResolvedValue(llmConfig() as never);
    vi.mocked(fetchApiKeyStatus).mockResolvedValue(keyStatus(true) as never);
    vi.mocked(getMyCredits).mockResolvedValue(credits('own_key') as never);
    vi.mocked(deleteApiKey).mockResolvedValue(undefined as never);

    renderSettings();

    const fitwrightTab = await screen.findByRole('tab', { name: 'FitWright' });
    fitwrightTab.click();

    // A destructive-ish action (clearing a saved key) is confirmed, not fired
    // straight from the tab click.
    expect(await screen.findByText(/Switch to FitWright's provider\?/i)).toBeInTheDocument();
    expect(deleteApiKey).not.toHaveBeenCalled();

    screen.getByRole('button', { name: 'Switch to FitWright' }).click();

    await waitFor(() => expect(deleteApiKey).toHaveBeenCalledWith('openai'));
  });

  it('switching to "My own provider" reveals the form without deleting anything', async () => {
    vi.mocked(fetchLlmConfig).mockResolvedValue(llmConfig() as never);
    vi.mocked(fetchApiKeyStatus).mockResolvedValue(keyStatus(false) as never);
    vi.mocked(getMyCredits).mockResolvedValue(credits('credits') as never);

    renderSettings();

    await screen.findByRole('tab', { name: 'FitWright' });
    expect(screen.queryByLabelText(/LLM provider/i)).not.toBeInTheDocument();

    screen.getByRole('tab', { name: 'My own provider' }).click();

    expect(await screen.findByLabelText(/LLM provider/i)).toBeInTheDocument();
    expect(deleteApiKey).not.toHaveBeenCalled();
    expect(updateLlmConfig).not.toHaveBeenCalled();
    expect(updateApiKeys).not.toHaveBeenCalled();
  });
});
