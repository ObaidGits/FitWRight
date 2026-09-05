import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '@/components/atelier/toast';

/**
 * "How do I let Claude Desktop (or any MCP client) see my FitWright data?"
 *
 * Backend Tasks 1-8 added POST/GET/DELETE /api/v1/mcp/tokens behind the
 * MCP_ENABLED kill-switch (disabled -> the whole router 404s). This pins the
 * Settings-side contract:
 * - The section's visibility comes from the token list itself: 200 -> shown,
 *   404 -> hidden. No separate flag endpoint exists (and none is needed).
 * - The raw token appears EXACTLY ONCE, at creation, in memory only. The
 *   refetched list (which never carries token material) must never re-show it,
 *   and dismissing the reveal must destroy it for good.
 * - Revoking goes through a confirmation before DELETE /{id}.
 */

vi.mock('@/lib/context/session', () => ({
  useSession: () => ({ user: { name: 'Test User' }, refresh: vi.fn() }),
}));
// The MCP section uses useTranslations() -> useLanguage(). English messages.
vi.mock('@/lib/context/language-context', () => ({
  useLanguage: () => ({ uiLanguage: 'en' }),
}));
vi.mock('@/components/theme/theme-provider', () => ({
  useTheme: () => ({ theme: 'light', toggleTheme: vi.fn() }),
}));
vi.mock('@/components/settings/account-security', () => ({ AccountSecurity: () => null }));
vi.mock('@/components/settings/buy-credits', () => ({ BuyCredits: () => null }));
vi.mock('@/components/settings/feature-prompts-editor', () => ({
  FeaturePromptsEditor: () => null,
}));
vi.mock('@/components/settings/notification-preferences', () => ({
  NotificationPreferences: () => null,
}));
vi.mock('@/components/settings/profile-settings', () => ({ ProfileSettings: () => null }));

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

vi.mock('@/lib/api/mcp', () => ({
  fetchMcpTokens: vi.fn(),
  createMcpToken: vi.fn(),
  revokeMcpToken: vi.fn(),
}));

const { fetchMcpTokens, createMcpToken, revokeMcpToken } = await import('@/lib/api/mcp');
const { getMyCredits } = await import('@/lib/api/credits');
const { fetchLlmConfig, fetchApiKeyStatus } = await import('@/lib/api/config');
const { ApiError } = await import('@/lib/api/errors');
const SettingsPage = (await import('@/app/(app)/settings/page')).default;

function token(over: Record<string, unknown> = {}) {
  return {
    id: 'tok-1',
    label: 'Claude Desktop',
    created_at: '2026-01-02T03:04:05Z',
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
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

/** Open the Account tab, where the MCP / API access section lives.
 * Radix Tabs activate on mousedown, not click, so a plain .click() is a no-op. */
async function openAccountTab() {
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Account' }), { button: 0 });
  return screen.findByText('MCP / API access');
}

describe('Settings > MCP / API access (token management)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchLlmConfig).mockResolvedValue({
      provider: 'openai',
      model: 'gpt-5-nano-2025-08-07',
      api_key: '',
      api_base: null,
      reasoning_effort: null,
    } as never);
    vi.mocked(fetchApiKeyStatus).mockResolvedValue({ providers: [] } as never);
    vi.mocked(getMyCredits).mockResolvedValue({
      mode: 'credits',
      unlimited: false,
      summary: 'about 4 more tailored resumes',
      actions: [],
      credits_enabled: true,
    } as never);
  });

  it('renders the section with the token list when MCP is enabled', async () => {
    vi.mocked(fetchMcpTokens).mockResolvedValue({
      items: [token(), token({ id: 'tok-2', label: 'Other client' })],
    } as never);

    renderSettings();
    await openAccountTab();

    expect(screen.getByText('Claude Desktop')).toBeInTheDocument();
    expect(screen.getByText('Other client')).toBeInTheDocument();
    // Each active token row offers a revoke action.
    expect(screen.getAllByRole('button', { name: 'Revoke' })).toHaveLength(2);
  });

  it('hides the section entirely when MCP is disabled (404)', async () => {
    vi.mocked(fetchMcpTokens).mockRejectedValue(
      new ApiError('not_found', 'Not found', 404) as never
    );

    renderSettings();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Account' }), { button: 0 });
    await waitFor(() => expect(fetchMcpTokens).toHaveBeenCalled());
    // Give the 404-turned-"disabled" state a beat to settle, then: no section.
    await waitFor(() => expect(screen.queryByText('MCP / API access')).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Create token' })).not.toBeInTheDocument();
  });

  it('create flow reveals the raw token exactly once, never after refetch or dismissal', async () => {
    vi.mocked(fetchMcpTokens)
      .mockResolvedValueOnce({ items: [] } as never)
      // Refetch after creation: the new row, but NO token material (the API
      // never returns it again - only sha256 lives server-side).
      .mockResolvedValue({ items: [token({ id: 'tok-2', label: 'Test client' })] } as never);
    vi.mocked(createMcpToken).mockResolvedValue(
      token({ id: 'tok-2', label: 'Test client', token: 'fw_secret_raw_value' }) as never
    );

    renderSettings();
    await openAccountTab();

    screen.getByRole('button', { name: 'Create token' }).click();
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/token name/i), {
      target: { value: 'Test client' },
    });
    within(dialog).getByRole('button', { name: 'Create' }).click();

    // The one-time reveal: raw value + copy affordance.
    expect(await within(dialog).findByText('fw_secret_raw_value')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Copy' })).toBeInTheDocument();

    // The list refetch lands (new row visible) - and the raw value is still
    // shown ONLY from the in-memory reveal, not re-fetched anywhere.
    await waitFor(() => expect(fetchMcpTokens).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Test client')).toBeInTheDocument();

    // Dismissing the reveal destroys it for good.
    within(dialog).getByRole('button', { name: 'Done' }).click();
    await waitFor(() => expect(screen.queryByText('fw_secret_raw_value')).not.toBeInTheDocument());
    expect(screen.getByText('Test client')).toBeInTheDocument();
  });

  it('revoking asks for confirmation, then calls DELETE with the token id', async () => {
    vi.mocked(fetchMcpTokens).mockResolvedValue({ items: [token()] } as never);
    vi.mocked(revokeMcpToken).mockResolvedValue(undefined as never);

    renderSettings();
    await openAccountTab();

    screen.getByRole('button', { name: 'Revoke' }).click();

    // Destructive action: confirmed, not fired from the row button.
    const dialog = await screen.findByRole('dialog');
    expect(screen.getByText('Revoke token?')).toBeInTheDocument();
    expect(revokeMcpToken).not.toHaveBeenCalled();

    within(dialog).getByRole('button', { name: 'Revoke token' }).click();
    await waitFor(() => expect(revokeMcpToken).toHaveBeenCalledWith('tok-1'));
  });

  it('shows a Revoked badge instead of a revoke action for revoked tokens', async () => {
    vi.mocked(fetchMcpTokens).mockResolvedValue({
      items: [token({ revoked_at: '2026-02-01T00:00:00Z' })],
    } as never);

    renderSettings();
    await openAccountTab();

    expect(await screen.findByText('Revoked')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
  });
});
