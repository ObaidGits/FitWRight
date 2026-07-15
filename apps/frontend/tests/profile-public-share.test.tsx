import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Public Profile Platform (P7) frontend:
 * - PublicProfileView renders the public projection (name/summary/experience/
 *   skills) and a Save-contact (vCard) link, with no private fields.
 * - ShareDialog publishes, surfaces the share link, and unpublishes.
 */

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

const getPublicationStateMock = vi.fn();
const publishProfileMock = vi.fn();
const unpublishProfileMock = vi.fn();
vi.mock('@/lib/api/professional-profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    getPublicationState: (...a: unknown[]) => getPublicationStateMock(...a),
    publishProfile: (...a: unknown[]) => publishProfileMock(...a),
    unpublishProfile: (...a: unknown[]) => unpublishProfileMock(...a),
  };
});

import { PublicProfileView } from '@/components/public/public-profile-view';
import { ShareDialog } from '@/components/profile/share-dialog';
import type { PublicProfile } from '@/lib/api/professional-profile';

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const PROFILE: PublicProfile = {
  slug: 'ada-lovelace',
  visibility: 'public',
  identity: { name: 'Ada Lovelace', headline: 'Engineer', github: 'https://gh/ada' },
  summary: 'Builds reliable systems.',
  experience: [{ title: 'Engineer', company: 'Acme', years: '2020', description: ['Shipped X'] }],
  projects: [],
  skills: ['Python', 'React'],
  education: [],
};

afterEach(() => vi.clearAllMocks());

describe('PublicProfileView', () => {
  it('renders public content and a vCard link', () => {
    render(
      <PublicProfileView profile={PROFILE} vcardUrl="/api/v1/public/profiles/ada-lovelace/vcard" />
    );
    expect(screen.getByRole('heading', { name: 'Ada Lovelace' })).toBeInTheDocument();
    expect(screen.getByText('Builds reliable systems.')).toBeInTheDocument();
    expect(screen.getByText('Python')).toBeInTheDocument();
    const save = screen.getByRole('link', { name: /Save contact/i });
    expect(save).toHaveAttribute('href', '/api/v1/public/profiles/ada-lovelace/vcard');
  });
});

describe('ShareDialog', () => {
  it('publishes when currently private', async () => {
    getPublicationStateMock.mockResolvedValue({ public_slug: null, visibility: 'private' });
    publishProfileMock.mockResolvedValue({ public_slug: 'ada', visibility: 'public' });

    wrap(<ShareDialog />);
    fireEvent.click(screen.getByRole('button', { name: /Share/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Publish publicly/i }));

    await waitFor(() => expect(publishProfileMock).toHaveBeenCalled());
    expect(publishProfileMock.mock.calls[0][0]).toBe('public');
  });

  it('offers theme selection when live', async () => {
    getPublicationStateMock.mockResolvedValue({
      public_slug: 'ada',
      visibility: 'public',
      public_theme: 'minimal',
    });
    publishProfileMock.mockResolvedValue({
      public_slug: 'ada',
      visibility: 'public',
      public_theme: 'modern',
    });

    wrap(<ShareDialog />);
    fireEvent.click(screen.getByRole('button', { name: /Share/i }));

    const modern = await screen.findByRole('radio', { name: 'Modern' });
    fireEvent.click(modern);
    await waitFor(() => expect(publishProfileMock).toHaveBeenCalled());
    expect(publishProfileMock.mock.calls[0][1]).toMatchObject({ theme: 'modern' });
  });

  it('shows the share link and unpublish when live', async () => {
    getPublicationStateMock.mockResolvedValue({ public_slug: 'ada', visibility: 'public' });
    unpublishProfileMock.mockResolvedValue({ public_slug: 'ada', visibility: 'private' });

    wrap(<ShareDialog />);
    fireEvent.click(screen.getByRole('button', { name: /Share/i }));

    const link = await screen.findByLabelText('Share link');
    expect((link as HTMLInputElement).value).toContain('/p/ada');

    fireEvent.click(screen.getByRole('button', { name: /Unpublish/i }));
    await waitFor(() => expect(unpublishProfileMock).toHaveBeenCalled());
  });
});
