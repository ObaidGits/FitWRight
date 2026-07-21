import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Profile P3/P5 UIs:
 * - ImportDialog: lists ready resumes, previews the merge plan, lets the user
 *   pick a resolution, and applies it.
 * - VersionHistory: lists snapshots and restores a past version (latest has no
 *   restore button).
 */

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

const previewImportMock = vi.fn();
const applyImportMock = vi.fn();
const listVersionsMock = vi.fn();
const restoreVersionMock = vi.fn();
vi.mock('@/lib/api/professional-profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    previewImport: (...a: unknown[]) => previewImportMock(...a),
    applyImport: (...a: unknown[]) => applyImportMock(...a),
    listProfileVersions: (...a: unknown[]) => listVersionsMock(...a),
    restoreProfileVersion: (...a: unknown[]) => restoreVersionMock(...a),
  };
});

vi.mock('@/features/home/hooks', () => ({
  useResumes: () => ({
    isLoading: false,
    data: [
      {
        resume_id: 'r1',
        title: 'My Resume',
        filename: null,
        is_master: true,
        processing_status: 'ready',
      },
    ],
  }),
}));

import { ImportDialog } from '@/components/profile/import-dialog';
import { VersionHistory } from '@/components/profile/version-history';

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.clearAllMocks());

describe('ImportDialog', () => {
  it('previews and applies a merge plan', async () => {
    previewImportMock.mockResolvedValue({
      source: 'resume',
      incoming: { summary: 'x' },
      plan: {
        operations: [
          {
            id: 'workExperience:inc1',
            section: 'workExperience',
            op: 'add',
            label: 'SWE - Globex',
            confidence: 1,
            similarity: null,
            existing_uid: null,
            existing: null,
            incoming: { title: 'SWE', company: 'Globex' },
            changes: [],
            default_resolution: 'accept',
            allowed_resolutions: ['accept', 'reject'],
          },
        ],
        counts: { add: 1 },
      },
      warnings: [],
    });
    applyImportMock.mockResolvedValue({
      data: {},
      completeness: 10,
      version: 4,
      applied: 1,
      skipped: 0,
    });

    wrap(<ImportDialog baseVersion={3} />);

    fireEvent.click(screen.getByRole('button', { name: /Import/i }));
    // Pick the resume to preview.
    fireEvent.click(await screen.findByText('My Resume'));

    expect(await screen.findByText('SWE - Globex')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Apply import/i }));
    await waitFor(() => expect(applyImportMock).toHaveBeenCalledTimes(1));
    expect(applyImportMock.mock.calls[0][0]).toMatchObject({
      base_version: 3,
      resolutions: { 'workExperience:inc1': 'accept' },
    });
  });
});

describe('VersionHistory', () => {
  it('lists versions and restores a past one', async () => {
    listVersionsMock.mockResolvedValue({
      items: [
        {
          id: 'v2',
          profile_id: 'p1',
          source: 'manual',
          label: 'Edit',
          content_hash: 'h2',
          size_bytes: 10,
          created_at: '2026-01-02T00:00:00Z',
        },
        {
          id: 'v1',
          profile_id: 'p1',
          source: 'migration',
          label: 'Initial profile',
          content_hash: 'h1',
          size_bytes: 8,
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
      next_cursor: null,
    });
    restoreVersionMock.mockResolvedValue({
      data: {},
      completeness: 20,
      version: 3,
      updated_at: null,
    });

    wrap(<VersionHistory />);
    fireEvent.click(screen.getByRole('button', { name: /History/i }));

    // Latest (v2) has no restore; the older (v1) does.
    const restoreButtons = await screen.findAllByRole('button', { name: /Restore version/i });
    expect(restoreButtons).toHaveLength(1);

    fireEvent.click(restoreButtons[0]);
    await waitFor(() => expect(restoreVersionMock).toHaveBeenCalledWith('v1'));
  });
});
