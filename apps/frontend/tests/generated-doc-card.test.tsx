import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * GeneratedDocCard (cover letter + outreach, ported into the atelier editor):
 * - Master (non-tailored) resumes see an explainer, no generate control.
 * - Tailored resumes generate via streaming; a completed draft is an editable,
 *   unsaved preview that Save persists via the kind's update API.
 * - Cancelling a stream discards the partial draft (no silent apply).
 * - Cover letter offers PDF export; outreach offers copy-to-clipboard.
 */

const updateCoverLetterMock = vi.fn();
const updateOutreachMessageMock = vi.fn();
vi.mock('@/lib/api/resume', () => ({
  updateCoverLetter: (...a: unknown[]) => updateCoverLetterMock(...a),
  updateOutreachMessage: (...a: unknown[]) => updateOutreachMessageMock(...a),
}));

vi.mock('@/components/resume/export-button', () => ({
  ExportButton: () => <button type="button">Export PDF</button>,
}));

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

let streamState: {
  text: string;
  status: string;
  isStreaming: boolean;
  error: string | null;
  start: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
  reset: ReturnType<typeof vi.fn>;
};
vi.mock('@/lib/hooks/use-stream', () => ({
  useStream: () => streamState,
}));

import { GeneratedDocCard } from '@/components/resume/generated-doc-card';

function freshStream(overrides: Partial<typeof streamState> = {}) {
  streamState = {
    text: '',
    status: 'idle',
    isStreaming: false,
    error: null,
    start: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('GeneratedDocCard — cover letter', () => {
  it('shows an explainer and no generate control for a non-tailored resume', () => {
    freshStream();
    render(
      <GeneratedDocCard
        kind="cover-letter"
        resumeId="r1"
        initialContent={null}
        isTailored={false}
      />
    );
    expect(screen.getByText(/Tailor this resume to a job first/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /generate/i })).not.toBeInTheDocument();
  });

  it('generates a draft, marks it unsaved, and saves via updateCoverLetter', async () => {
    freshStream({ start: vi.fn().mockResolvedValue('Dear Hiring Manager, I am thrilled…') });
    updateCoverLetterMock.mockResolvedValue(undefined);
    const onSaved = vi.fn();
    render(
      <GeneratedDocCard
        kind="cover-letter"
        resumeId="r1"
        initialContent={null}
        isTailored
        onSaved={onSaved}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /generate/i }));
    await waitFor(() =>
      expect(screen.getByLabelText('Cover letter content')).toHaveValue(
        'Dear Hiring Manager, I am thrilled…'
      )
    );
    expect(streamState.start).toHaveBeenCalledWith('cover-letter');
    expect(screen.getByText('Unsaved')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() =>
      expect(updateCoverLetterMock).toHaveBeenCalledWith(
        'r1',
        'Dear Hiring Manager, I am thrilled…'
      )
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('discards a cancelled stream (no content applied)', async () => {
    let resolveStart: (v: string) => void = () => {};
    freshStream({
      start: vi.fn().mockImplementation(() => new Promise<string>((r) => (resolveStart = r))),
      isStreaming: true,
    });
    render(<GeneratedDocCard kind="cover-letter" resumeId="r1" initialContent={null} isTailored />);

    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(streamState.cancel).toHaveBeenCalled();
    resolveStart('partial draft that should be discarded');

    await waitFor(() => expect(screen.queryByText('Unsaved')).not.toBeInTheDocument());
  });

  it('offers export for an already-saved letter', () => {
    freshStream();
    render(
      <GeneratedDocCard
        kind="cover-letter"
        resumeId="r1"
        initialContent={'Existing saved letter.'}
        isTailored
      />
    );
    expect(screen.getByRole('button', { name: /export pdf/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeInTheDocument();
  });
});

describe('GeneratedDocCard — outreach', () => {
  it('saves via updateOutreachMessage and offers copy instead of export', async () => {
    freshStream();
    updateOutreachMessageMock.mockResolvedValue(undefined);
    render(
      <GeneratedDocCard
        kind="outreach"
        resumeId="r1"
        initialContent={'Hi, I noticed…'}
        isTailored
      />
    );

    // Outreach uses copy, not PDF export.
    expect(screen.queryByRole('button', { name: /export pdf/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();

    // Edit then save routes to the outreach update API.
    fireEvent.change(screen.getByLabelText('Outreach message content'), {
      target: { value: 'Hi, I noticed your opening…' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() =>
      expect(updateOutreachMessageMock).toHaveBeenCalledWith('r1', 'Hi, I noticed your opening…')
    );
  });
});
