import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

/**
 * Unsaved-changes guard: an in-app link click while `when` is true is
 * intercepted (capture phase), the confirm dialog appears, "Stay" cancels the
 * navigation, and "Discard" performs the intended navigation via the router.
 * When `when` is false, links navigate normally (no dialog).
 */
const pushMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), back: vi.fn() }),
}));

import { UnsavedChangesGuard } from '@/components/common/unsaved-changes-guard';

function Harness({ when }: { when: boolean }) {
  return (
    <div>
      {/* Intentional raw anchor: exercises the guard's capture-phase interception. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/resumes">Go to resumes</a>
      <UnsavedChangesGuard when={when} />
    </div>
  );
}

describe('UnsavedChangesGuard', () => {
  beforeEach(() => {
    pushMock.mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it('intercepts an internal link click when guarding and shows the dialog', () => {
    render(<Harness when />);
    fireEvent.click(screen.getByText('Go to resumes'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText(/discard unsaved changes/i)).toBeTruthy();
    // Navigation is held until the user decides.
    expect(pushMock).not.toHaveBeenCalled();
  });

  it('"Stay" cancels the navigation', () => {
    render(<Harness when />);
    fireEvent.click(screen.getByText('Go to resumes'));
    fireEvent.click(screen.getByRole('button', { name: /stay/i }));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it('"Discard changes" performs the intended navigation', () => {
    render(<Harness when />);
    fireEvent.click(screen.getByText('Go to resumes'));
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /discard changes/i }));
    });
    expect(pushMock).toHaveBeenCalledWith('/resumes');
  });

  it('does not interfere when there are no unsaved changes', () => {
    render(<Harness when={false} />);
    fireEvent.click(screen.getByText('Go to resumes'));
    // No dialog rendered.
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
