import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Tailor "Analyze fit" flow (Req 15 — explicit, cost-aware AI):
 * - The analysis only runs on an explicit click (never automatically).
 * - Matched/missing keywords and the fit score render after the call resolves.
 * - Editing the JD clears a stale analysis so results never mismatch inputs.
 */

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const analyzeJobMock = vi.fn();
const uploadJobDescriptionsMock = vi.fn();
const previewImproveResumeMock = vi.fn();
const streamImproveResumeMock = vi.fn();
const cancelTailorStreamMock = vi.fn();
vi.mock('@/lib/api/resume', () => {
  class TailorStreamCancelled extends Error {
    constructor() {
      super('cancelled');
      this.name = 'TailorStreamCancelled';
    }
  }
  return {
    analyzeJob: (...args: unknown[]) => analyzeJobMock(...args),
    uploadJobDescriptions: (...args: unknown[]) => uploadJobDescriptionsMock(...args),
    previewImproveResume: (...args: unknown[]) => previewImproveResumeMock(...args),
    streamImproveResume: (...args: unknown[]) => streamImproveResumeMock(...args),
    cancelTailorStream: (...args: unknown[]) => cancelTailorStreamMock(...args),
    TailorStreamCancelled,
    confirmImproveResume: vi.fn(),
  };
});

vi.mock('@/features/tailor/hooks', () => ({
  useTailorResumes: () => ({
    isLoading: false,
    data: [{ resume_id: 'r1', title: 'My Resume', is_master: true, processing_status: 'ready' }],
  }),
  usePromptOptions: () => ({ data: { prompt_options: [] } }),
}));

vi.mock('@/features/home/hooks', () => ({
  useSystemStatus: () => ({ data: { llm_configured: true } }),
}));

vi.mock('@/lib/hooks/use-draft', () => ({
  useDraft: () => ({
    save: vi.fn(),
    clear: vi.fn(),
    recovered: null,
    recoveredAt: null,
    dismissRecovery: vi.fn(),
  }),
}));

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

vi.mock('@/components/ai/explain', () => ({
  Explain: () => null,
}));

import TailorPage from '@/app/(app)/tailor/page';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TailorPage />
    </QueryClientProvider>
  );
}

const LONG_JD =
  'We are hiring a senior backend engineer with deep Python and AWS experience to build scalable services.';

afterEach(() => {
  vi.clearAllMocks();
});

describe('Tailor — Analyze fit', () => {
  it('does not analyze until the user clicks (explicit AI action)', () => {
    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    expect(analyzeJobMock).not.toHaveBeenCalled();
  });

  it('renders matched/missing keywords and fit score after clicking Analyze fit', async () => {
    analyzeJobMock.mockResolvedValue({
      keywords: {
        required_skills: ['Python', 'AWS'],
        preferred_skills: [],
        keywords: ['scalable'],
        experience_requirements: [],
        seniority_level: 'senior',
        experience_years: '5',
      },
      matched: ['Python'],
      missing: ['AWS', 'scalable'],
      fit_score: 33.3,
    });

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /analyze fit/i }));

    await waitFor(() => expect(screen.getByText('Fit analysis')).toBeInTheDocument());
    expect(analyzeJobMock).toHaveBeenCalledWith(LONG_JD, 'r1');
    expect(screen.getByText('AWS')).toBeInTheDocument();
    expect(screen.getByText('Python')).toBeInTheDocument();
    expect(screen.getByText('Missing from your resume')).toBeInTheDocument();
    expect(screen.getByText('Already covered')).toBeInTheDocument();
  });

  const RESULT = {
    data: {
      ats_score: {
        overall_score: 80,
        sub_scores: { keyword_match: 80, skills_coverage: 80, section_completeness: 80 },
        missing_keywords: [],
      },
      diff_summary: { total_changes: 0 },
      improvements: [],
      detailed_changes: [],
      resume_preview: {},
    },
  };

  it('generates on Cmd/Ctrl+Enter using the streaming path', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockResolvedValue(RESULT);

    renderPage();
    const textarea = screen.getByLabelText('Job description');
    fireEvent.change(textarea, { target: { value: LONG_JD } });
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true });

    await waitFor(() => expect(streamImproveResumeMock).toHaveBeenCalled());
    const [rid, jid, promptId] = streamImproveResumeMock.mock.calls[0];
    expect(rid).toBe('r1');
    expect(jid).toBe('job-1');
    expect(promptId).toBeUndefined();
    // Falls through to the review state once the stream resolves.
    await waitFor(() => expect(screen.getByText(/Accept & save/i)).toBeInTheDocument());
    expect(previewImproveResumeMock).not.toHaveBeenCalled();
  });

  it('renders live stage progress and cancels on demand', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    let emitStage: ((e: { stage: string; status: string }) => void) | undefined;
    // Keep the stream pending so the generating UI stays mounted.
    streamImproveResumeMock.mockImplementation((_r, _j, _p, opts) => {
      emitStage = opts.onStage;
      return new Promise(() => {});
    });

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() => expect(streamImproveResumeMock).toHaveBeenCalled());
    expect(screen.getByText('Tailoring your resume…')).toBeInTheDocument();
    // A real backend stage boundary lights up the checklist.
    emitStage?.({ stage: 'keywords', status: 'active' });

    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(cancelTailorStreamMock).toHaveBeenCalled();
  });

  it('falls back to the non-stream path when streaming is unusable', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockRejectedValue(new Error('stream_open_failed:409'));
    previewImproveResumeMock.mockResolvedValue(RESULT);

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() =>
      expect(previewImproveResumeMock).toHaveBeenCalledWith('r1', 'job-1', undefined)
    );
    await waitFor(() => expect(screen.getByText(/Accept & save/i)).toBeInTheDocument());
  });

  it('clears a stale analysis when the job description changes', async () => {
    analyzeJobMock.mockResolvedValue({
      keywords: {
        required_skills: ['Python'],
        preferred_skills: [],
        keywords: [],
        experience_requirements: [],
        seniority_level: null,
        experience_years: null,
      },
      matched: ['Python'],
      missing: [],
      fit_score: 100,
    });

    renderPage();
    const textarea = screen.getByLabelText('Job description');
    fireEvent.change(textarea, { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /analyze fit/i }));
    await waitFor(() => expect(screen.getByText('Fit analysis')).toBeInTheDocument());

    fireEvent.change(textarea, { target: { value: LONG_JD + ' Extra requirement.' } });
    await waitFor(() => expect(screen.queryByText('Fit analysis')).not.toBeInTheDocument());
  });
});
