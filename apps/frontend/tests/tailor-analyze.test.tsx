import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Tailor "Analyze fit" flow (Req 15 - explicit, cost-aware AI):
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
const recoverTailorPreviewMock = vi.fn();
const cancelTailorStreamMock = vi.fn();
const confirmImproveResumeMock = vi.fn();
const downloadResumePdfMock = vi.fn();
const updateResumeTemplateSettingsMock = vi.fn();
vi.mock('@/lib/api/resume', () => {
  class TailorStreamCancelled extends Error {
    constructor() {
      super('cancelled');
      this.name = 'TailorStreamCancelled';
    }
  }
  class TailorStreamError extends Error {
    constructor(
      public readonly code: string,
      message: string,
      public readonly status: number,
      public readonly phase: 'open' | 'before-event' | 'after-event',
      public readonly fallbackSafe: boolean
    ) {
      super(message);
      this.name = 'TailorStreamError';
    }
  }
  return {
    analyzeJob: (...args: unknown[]) => analyzeJobMock(...args),
    uploadJobDescriptions: (...args: unknown[]) => uploadJobDescriptionsMock(...args),
    previewImproveResume: (...args: unknown[]) => previewImproveResumeMock(...args),
    streamImproveResume: (...args: unknown[]) => streamImproveResumeMock(...args),
    recoverTailorPreview: (...args: unknown[]) => recoverTailorPreviewMock(...args),
    cancelTailorStream: (...args: unknown[]) => cancelTailorStreamMock(...args),
    TailorStreamCancelled,
    TailorStreamError,
    confirmImproveResume: (...args: unknown[]) => confirmImproveResumeMock(...args),
    downloadResumePdf: (...args: unknown[]) => downloadResumePdfMock(...args),
    updateResumeTemplateSettings: (...args: unknown[]) => updateResumeTemplateSettingsMock(...args),
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

// Profile query (used for the photo-template avatar) - keep hermetic.
vi.mock('@/lib/api/profile', () => ({
  getProfile: vi.fn(async () => ({ avatar_url: null })),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
}));

// Configurable draft state so tests can simulate a recoverable draft. Uses
// vi.hoisted so it's initialized before the hoisted vi.mock factory runs.
const draftState = vi.hoisted(() => ({ recovered: null as unknown }));
const draftSaveMock = vi.fn();
const draftClearMock = vi.fn();
const draftDismissMock = vi.fn();
vi.mock('@/lib/hooks/use-draft', () => ({
  useDraft: () => ({
    save: draftSaveMock,
    clear: draftClearMock,
    recovered: draftState.recovered,
    recoveredAt: draftState.recovered ? 1000 : null,
    dismissRecovery: draftDismissMock,
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
import { TailorStreamCancelled, TailorStreamError } from '@/lib/api/resume';

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
  draftState.recovered = null;
  // The preview-recovery feature persists a breadcrumb in localStorage; clear
  // it so one test's saved preview never leaks a recovery banner into another.
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe('Tailor - Analyze fit', () => {
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
      request_id: 'preview-request-1',
      preview_id: 'preview-1',
      resume_id: null,
      job_id: 'job-1',
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
    await waitFor(() => expect(screen.getByText(/Save resume/i)).toBeInTheDocument());
    expect(previewImproveResumeMock).not.toHaveBeenCalled();
  });

  it('renders live stage progress and cancellation never falls back', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    let emitStage: ((e: { stage: string; status: string }) => void) | undefined;
    streamImproveResumeMock.mockImplementation((_r, _j, _p, opts) => {
      emitStage = opts.onStage;
      return new Promise((_resolve, reject) => {
        opts.signal.addEventListener('abort', () => reject(new TailorStreamCancelled()));
      });
    });

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() => expect(streamImproveResumeMock).toHaveBeenCalled());
    // The generating state renders in the sticky preview pane (split layout).
    // (The sr-only live announcement also mentions tailoring, so match all.)
    expect(screen.getAllByText(/Tailoring your resume/i).length).toBeGreaterThan(0);
    emitStage?.({ stage: 'keywords', status: 'active' });

    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(cancelTailorStreamMock).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryAllByText(/Tailoring your resume/i)).toHaveLength(0));
    expect(previewImproveResumeMock).not.toHaveBeenCalled();
  });

  it.each(['streaming_disabled', 'streaming_unsupported'])(
    'falls back for safe 409 %s capability negotiation',
    async (code) => {
      uploadJobDescriptionsMock.mockResolvedValue('job-1');
      streamImproveResumeMock.mockRejectedValue(
        new TailorStreamError(code, 'Streaming unavailable.', 409, 'open', true)
      );
      previewImproveResumeMock.mockResolvedValue(RESULT);

      renderPage();
      fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
      fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

      await waitFor(() =>
        expect(previewImproveResumeMock).toHaveBeenCalledWith('r1', 'job-1', undefined, undefined)
      );
      await waitFor(() => expect(screen.getByText(/Save resume/i)).toBeInTheDocument());
    }
  );

  it.each([
    [401, 'not_authenticated'],
    [422, 'validation_error'],
    [429, 'rate_limited'],
  ])('does not fall back for an unsafe HTTP %i stream-open failure', async (status, code) => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockRejectedValue(
      new TailorStreamError(code, 'Request rejected.', status, 'open', false)
    );

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() =>
      expect(screen.getByText('Resume tailoring did not complete')).toBeInTheDocument()
    );
    expect(previewImproveResumeMock).not.toHaveBeenCalled();
  });

  it.each(['stream_terminal_error', 'stream_incomplete'])(
    'does not fall back for unsafe %s stream failure',
    async (code) => {
      uploadJobDescriptionsMock.mockResolvedValue('job-1');
      streamImproveResumeMock.mockRejectedValue(
        new TailorStreamError(code, 'Stream failed after work started.', 0, 'after-event', false)
      );

      renderPage();
      fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
      fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

      await waitFor(() =>
        expect(screen.getByText('Resume tailoring did not complete')).toBeInTheDocument()
      );
      expect(previewImproveResumeMock).not.toHaveBeenCalled();
    }
  );

  it('recovers a completed preview after the terminal SSE event is lost', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockRejectedValue(
      new TailorStreamError('stream_incomplete', 'Final event was lost.', 0, 'after-event', false)
    );
    recoverTailorPreviewMock.mockResolvedValue(RESULT);

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() => expect(recoverTailorPreviewMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByText(/Save resume/i)).toBeInTheDocument());
    expect(previewImproveResumeMock).not.toHaveBeenCalled();
  });

  it('passes preview_id through Save resume', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockResolvedValue(RESULT);
    confirmImproveResumeMock.mockResolvedValue({
      data: { ...RESULT.data, resume_id: 'tailored-1' },
    });

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));
    await waitFor(() => expect(screen.getByText(/Save resume/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Save resume/i));

    await waitFor(() => expect(confirmImproveResumeMock).toHaveBeenCalled());
    expect(confirmImproveResumeMock.mock.calls[0][0]).toMatchObject({
      preview_id: 'preview-1',
      resume_id: 'r1',
      job_id: 'job-1',
    });
  });

  it('saves then downloads the tailored resume PDF from the review screen', async () => {
    // jsdom has no object-URL support; stub it for the blob-save path.
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => 'blob:mock');
    URL.revokeObjectURL = vi.fn();
    try {
      uploadJobDescriptionsMock.mockResolvedValue('job-1');
      streamImproveResumeMock.mockResolvedValue(RESULT);
      confirmImproveResumeMock.mockResolvedValue({
        data: { ...RESULT.data, resume_id: 'tailored-1' },
      });
      downloadResumePdfMock.mockResolvedValue(new Blob(['pdf'], { type: 'application/pdf' }));

      renderPage();
      fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
      fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));
      // "Save and download PDF" is now a menu item rather than a fourth
      // top-level button, so the overflow menu has to be opened first. Radix
      // opens on pointerdown, not click.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /more save options/i })).toBeInTheDocument()
      );
      fireEvent.pointerDown(screen.getByRole('button', { name: /more save options/i }), {
        button: 0,
        ctrlKey: false,
      });
      await waitFor(() =>
        expect(screen.getByRole('menuitem', { name: /save and download pdf/i })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole('menuitem', { name: /save and download pdf/i }));

      // Confirms the preview (persisting the resume) then exports the new id
      // WITH the chosen template settings, and persists that template.
      await waitFor(() => expect(confirmImproveResumeMock).toHaveBeenCalled());
      await waitFor(() => expect(downloadResumePdfMock).toHaveBeenCalled());
      const [dlId, dlSettings] = downloadResumePdfMock.mock.calls[0];
      expect(dlId).toBe('tailored-1');
      expect(dlSettings).toMatchObject({ template: expect.any(String) });
      await waitFor(() =>
        expect(updateResumeTemplateSettingsMock).toHaveBeenCalledWith(
          'tailored-1',
          expect.objectContaining({ template: expect.any(String) })
        )
      );
    } finally {
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
  });

  it('shows a before/after fit delta when the user analyzed before generating', async () => {
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
      missing: ['AWS', 'Kubernetes'],
      fit_score: 40,
    });
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockResolvedValue(RESULT); // ats overall_score 80

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /analyze fit/i }));
    await waitFor(() => expect(screen.getByText('Fit analysis')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));
    await waitFor(() => expect(screen.getByText('What tailoring improved')).toBeInTheDocument());
  });

  it('marks the analysis stale (not wiped) when the job description changes', async () => {
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

    // A trivial edit no longer discards the analysis - it stays visible but is
    // flagged out-of-date with a re-analyze affordance (avoids a forced respend).
    fireEvent.change(textarea, { target: { value: LONG_JD + ' Extra requirement.' } });
    await waitFor(() => expect(screen.getByText(/It may be out of date/i)).toBeInTheDocument());
    expect(screen.getByText('Fit analysis')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /re-analyze/i })).toBeInTheDocument();
  });

  it('requires a second click to discard a tailored result', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockResolvedValue(RESULT);

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^discard$/i })).toBeInTheDocument()
    );

    // First click arms the confirmation (still in review).
    fireEvent.click(screen.getByRole('button', { name: /^discard$/i }));
    expect(screen.getByRole('button', { name: /click again to discard/i })).toBeInTheDocument();
    expect(screen.getByText(/Save resume/i)).toBeInTheDocument();

    // Second click actually discards -> back to input (no review actions).
    fireEvent.click(screen.getByRole('button', { name: /click again to discard/i }));
    await waitFor(() => expect(screen.queryByText(/Save resume/i)).not.toBeInTheDocument());
  });

  it('lets the user restore the previous attempt after a regenerate', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    const FIRST = {
      data: { ...RESULT.data, ats_score: { ...RESULT.data.ats_score, overall_score: 55 } },
    };
    const SECOND = {
      data: { ...RESULT.data, ats_score: { ...RESULT.data.ats_score, overall_score: 90 } },
    };
    streamImproveResumeMock.mockResolvedValueOnce(FIRST).mockResolvedValueOnce(SECOND);

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));
    await waitFor(() => expect(screen.getByText(/Save resume/i)).toBeInTheDocument());

    // Regenerate -> a second attempt; the first becomes the restorable "previous".
    fireEvent.click(screen.getByRole('button', { name: /^regenerate$/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /restore previous attempt/i })).toBeInTheDocument()
    );
    expect(screen.getByText(/match 55/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /restore previous attempt/i }));
    // After restoring, the (now-previous) attempt is the 90 one.
    await waitFor(() => expect(screen.getByText(/match 90/i)).toBeInTheDocument());
  });

  it('restores the full input set (JD + Extra Instructions) from a saved draft', async () => {
    // A structured draft persisted from a prior session (not just the JD).
    draftState.recovered = {
      jd: LONG_JD,
      customInstructions: 'Add project KRIA: voice + desktop control.',
    };

    renderPage();
    // The draft recovery banner offers a plain "Restore".
    fireEvent.click(await screen.findByRole('button', { name: /^restore$/i }));

    // JD comes back...
    await waitFor(() =>
      expect((screen.getByLabelText('Job description') as HTMLTextAreaElement).value).toBe(LONG_JD)
    );
    // ...and the Extra Instructions are restored AND revealed (Options panel
    // auto-expands so the restored content is actually visible, not hidden).
    const extra = screen.getByLabelText(/Extra instructions/i) as HTMLTextAreaElement;
    expect(extra.value).toContain('KRIA');
    expect(draftDismissMock).toHaveBeenCalled();
  });

  it('offers to recover a completed-but-unsaved preview on a later visit', async () => {
    localStorage.setItem(
      'tailor-last-preview',
      JSON.stringify({ requestId: 'req-xyz', savedAt: Date.now() })
    );
    recoverTailorPreviewMock.mockResolvedValue(RESULT);

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /restore tailored resume/i })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole('button', { name: /restore tailored resume/i }));

    await waitFor(() => expect(recoverTailorPreviewMock).toHaveBeenCalledWith('req-xyz'));
    await waitFor(() => expect(screen.getByText(/Save resume/i)).toBeInTheDocument());
  });

  it('forwards custom instructions to the stream tailoring call', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockResolvedValue(RESULT);

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    // Open the Options disclosure, then type per-run steering.
    fireEvent.click(screen.getByRole('button', { name: /options/i }));
    fireEvent.change(screen.getByLabelText(/extra instructions/i), {
      target: { value: 'Emphasize backend and Kubernetes.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() => expect(streamImproveResumeMock).toHaveBeenCalled());
    const opts = streamImproveResumeMock.mock.calls[0][3];
    expect(opts.customInstructions).toBe('Emphasize backend and Kubernetes.');
  });

  it('opens the visual template gallery (not a dropdown) from Options', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /options/i }));
    expect(screen.getByText('Resume template')).toBeInTheDocument();
    // Selection is via the same gallery as /templates, opened by a button.
    fireEvent.click(screen.getByRole('button', { name: /change template/i }));
    await waitFor(() => expect(screen.getByText('Choose a template')).toBeInTheDocument());
    // The gallery's search control is present (proves the gallery mounted).
    expect(screen.getByLabelText(/search templates/i)).toBeInTheDocument();
  });

  it('offers a profile-photo uploader when a photo template is selected', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /options/i }));
    fireEvent.click(screen.getByRole('button', { name: /change template/i }));
    await waitFor(() => expect(screen.getByText('Choose a template')).toBeInTheDocument());

    // Filter the gallery to photo templates so the first card is guaranteed to
    // be photo-capable, then use it.
    fireEvent.click(screen.getAllByRole('button', { name: /^with photo$/i })[0]);
    const useButtons = await screen.findAllByRole('button', { name: /use this template/i });
    fireEvent.click(useButtons[0]);

    // A photo template reveals the profile-photo uploader in Options.
    await waitFor(() => expect(screen.getByText('Profile photo')).toBeInTheDocument());
  });

  it('surfaces instruction result notes on the review screen', async () => {
    uploadJobDescriptionsMock.mockResolvedValue('job-1');
    streamImproveResumeMock.mockResolvedValue({
      data: {
        ...RESULT.data,
        instruction_notes: ['Added project "KRIA" from your instructions.'],
      },
    });

    renderPage();
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: LONG_JD } });
    fireEvent.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() => expect(screen.getByText('Notes on your instructions')).toBeInTheDocument());
    expect(screen.getByText(/Added project "KRIA"/)).toBeInTheDocument();
  });

  it('analyzes fit on Cmd/Ctrl+Shift+Enter', async () => {
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
      missing: ['AWS'],
      fit_score: 50,
    });

    renderPage();
    const textarea = screen.getByLabelText('Job description');
    fireEvent.change(textarea, { target: { value: LONG_JD } });
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true, shiftKey: true });

    await waitFor(() => expect(analyzeJobMock).toHaveBeenCalledWith(LONG_JD, 'r1'));
    expect(streamImproveResumeMock).not.toHaveBeenCalled();
  });
});
