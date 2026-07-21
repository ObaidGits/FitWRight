import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/** Ask-AI dialog shows an honest stage timeline while the rewrite generates. */

const regenerateItemsMock = vi.fn();
vi.mock('@/lib/api/enrichment', () => ({
  regenerateItems: (...a: unknown[]) => regenerateItemsMock(...a),
}));
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: vi.fn() }) };
});

import { AskAiDialog, type AskAiTarget } from '@/components/ai/ask-ai-dialog';

const TARGET: AskAiTarget = {
  resumeId: 'r1',
  itemId: 'e1',
  itemType: 'experience',
  title: 'Engineer at Acme',
  currentContent: ['Did things'],
};

afterEach(() => vi.clearAllMocks());

describe('AskAiDialog - loading', () => {
  it('shows the stage timeline while generating instead of a blank dialog', async () => {
    regenerateItemsMock.mockReturnValue(new Promise(() => {})); // never resolves
    render(<AskAiDialog open onOpenChange={vi.fn()} target={TARGET} onApply={vi.fn()} />);

    // An intent preset runs with a real instruction (freeform Generate needs text).
    fireEvent.click(screen.getByRole('button', { name: /add metrics/i }));

    await waitFor(() =>
      expect(screen.getByText('Reading the current content')).toBeInTheDocument()
    );
    // All stages render in the timeline (pending ones included).
    expect(screen.getByText('Applying your instruction')).toBeInTheDocument();
    expect(screen.getByText('Writing the improved version')).toBeInTheDocument();
  });
});
