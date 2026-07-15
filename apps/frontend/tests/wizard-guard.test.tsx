import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

/**
 * Wizard unsaved-changes guard:
 * - With no progress (intro, empty answer) an in-app link navigates freely.
 * - Once the user types an answer, clicking a link is intercepted and the
 *   "Leave the wizard?" confirm dialog appears; "Keep building" dismisses it
 *   without navigating.
 */

const pushMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), back: vi.fn() }),
}));

vi.mock('@/features/home/hooks', () => ({
  useSystemStatus: () => ({ data: { llm_configured: true } }),
}));

vi.mock('@/components/resume/render-template', () => ({
  RenderTemplate: () => null,
}));

vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: vi.fn() }) };
});

vi.mock('@/lib/api/resume-wizard', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    postResumeWizardTurn: vi.fn(),
    finalizeResumeWizard: vi.fn(),
    prefillResumeWizard: vi.fn(),
  };
});

import WizardPage from '@/app/(app)/wizard/page';

afterEach(() => {
  vi.clearAllMocks();
});

describe('Wizard — unsaved changes guard', () => {
  it('does not guard navigation before any answer is entered', () => {
    render(<WizardPage />);
    const back = screen.getByRole('link', { name: /back to import/i });
    fireEvent.click(back);
    expect(screen.queryByText('Leave the wizard?')).not.toBeInTheDocument();
  });

  it('intercepts an in-app link once a field is edited', () => {
    render(<WizardPage />);
    // Intro is now a structured Identity form (W-P1.1); editing the name field
    // marks the wizard dirty.
    fireEvent.change(screen.getByLabelText(/your name/i), {
      target: { value: 'Jane Doe' },
    });
    fireEvent.click(screen.getByRole('link', { name: /back to import/i }));

    expect(screen.getByText('Leave the wizard?')).toBeInTheDocument();

    // "Keep building" dismisses without navigating.
    fireEvent.click(screen.getByRole('button', { name: /keep building/i }));
    expect(pushMock).not.toHaveBeenCalled();
  });
});
