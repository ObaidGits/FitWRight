import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Import/parse loading experience (Loading audit - P0): while a resume uploads
 * and is structured, the user sees the honest stage timeline + skeleton, not the
 * old bare "Uploading & parsing..." spinner.
 */

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
let statusData: { llm_configured: boolean; llm_healthy: boolean | null } | undefined = {
  llm_configured: true,
  llm_healthy: null,
};
vi.mock('@/features/home/hooks', () => ({
  useSystemStatus: () => ({ data: statusData }),
}));
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: vi.fn() }) };
});

const uploadResumeFile = vi.fn();
const streamUploadResumeFile = vi.fn();
const validateResumeFile = vi.fn();
vi.mock('@/features/resumes/upload', () => ({
  uploadResumeFile: (...a: unknown[]) => uploadResumeFile(...a),
  streamUploadResumeFile: (...a: unknown[]) => streamUploadResumeFile(...a),
  validateResumeFile: (...a: unknown[]) => validateResumeFile(...a),
  STREAM_UNAVAILABLE: 'stream_unavailable',
}));

import ImportPage from '@/app/(app)/import/page';

afterEach(() => {
  vi.clearAllMocks();
  statusData = { llm_configured: true, llm_healthy: null };
});

describe('Import page - parse loading experience', () => {
  it('does not treat unknown provider health as a failed health check', () => {
    statusData = { llm_configured: true, llm_healthy: null };
    render(<ImportPage />);

    expect(screen.queryByText(/your AI provider key isn't responding/i)).not.toBeInTheDocument();
  });

  it('still warns after an explicit failed provider check', () => {
    statusData = { llm_configured: true, llm_healthy: false };
    render(<ImportPage />);

    expect(screen.getByText(/your AI provider key isn't responding/i)).toBeInTheDocument();
  });

  it('blocks upload requests while AI status is unresolved', () => {
    statusData = undefined;
    const { container } = render(<ImportPage />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    expect(input).toBeDisabled();
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'resume.pdf', { type: 'application/pdf' })] },
    });
    expect(validateResumeFile).not.toHaveBeenCalled();
    expect(streamUploadResumeFile).not.toHaveBeenCalled();
    expect(uploadResumeFile).not.toHaveBeenCalled();
  });

  it('shows the deterministic stage timeline when streaming is unavailable', async () => {
    validateResumeFile.mockReturnValue(null); // valid file
    // Streaming off -> falls back to the non-stream path (deterministic timeline).
    streamUploadResumeFile.mockRejectedValue(new Error('stream_unavailable'));
    // Never resolves -> stays in the uploading/parsing state.
    uploadResumeFile.mockReturnValue(new Promise(() => {}));

    const { container } = render(<ImportPage />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['x'], 'resume.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    // Stage timeline copy appears...
    await waitFor(() =>
      expect(screen.getByText(/turning your resume into an editable draft/i)).toBeInTheDocument()
    );
    expect(screen.getByText('Reading your document')).toBeInTheDocument();
    expect(screen.getByText('Detecting your skills')).toBeInTheDocument();
    // ...and there's an accessible live status.
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
    // The old bare copy is gone.
    expect(screen.queryByText(/this can take a few seconds/i)).not.toBeInTheDocument();
  });

  it('drives the LIVE stage timeline from real streaming stage events', async () => {
    validateResumeFile.mockReturnValue(null);
    // Emit real backend stage boundaries, then hang (never resolve) so the
    // component stays in the LIVE progress state for assertion.
    streamUploadResumeFile.mockImplementation(
      (_file: File, opts: { onStage?: (e: { stage: string; status: string }) => void }) => {
        opts.onStage?.({ stage: 'received', status: 'done' });
        opts.onStage?.({ stage: 'extracting', status: 'active' });
        return new Promise(() => {});
      }
    );

    const { container } = render(<ImportPage />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['x'], 'resume.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    // LIVE stage labels (from PARSE_STREAM_STAGES) are shown.
    await waitFor(() => expect(screen.getByText('Resume received')).toBeInTheDocument());
    expect(screen.getByText('Building your editable resume')).toBeInTheDocument();
    // The non-stream fallback must not have been used.
    expect(uploadResumeFile).not.toHaveBeenCalled();
  });
});
