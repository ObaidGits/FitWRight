import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Step-up modal (Task 8.3): a gated action that receives `step_up_required`
 * opens the re-auth modal, and after a successful step-up the ORIGINAL action
 * is transparently retried and its result delivered to the caller.
 */

const { AuthApiError } = vi.hoisted(() => {
  class AuthApiError extends Error {
    code: string;
    status: number;
    constructor(code: string, message: string, status = 401) {
      super(message);
      this.name = 'AuthApiError';
      this.code = code;
      this.status = status;
    }
  }
  return { AuthApiError };
});
const stepUpMock = vi.fn();

vi.mock('@/lib/api/auth', () => ({
  AuthApiError,
  authApi: { stepUp: (...a: unknown[]) => stepUpMock(...a) },
}));

import { StepUpProvider, useStepUp } from '@/components/auth/step-up-modal';

function Consumer({ action }: { action: () => Promise<string> }) {
  const { run } = useStepUp();
  const [msg, setMsg] = React.useState('');
  return (
    <div>
      <button
        onClick={async () => {
          try {
            const r = await run(action);
            setMsg(`ok:${r}`);
          } catch {
            setMsg('err');
          }
        }}
      >
        go
      </button>
      <span data-testid="msg">{msg}</span>
    </div>
  );
}

describe('StepUpProvider', () => {
  beforeEach(() => stepUpMock.mockReset());
  afterEach(() => vi.clearAllMocks());

  it('challenges, then retries the original action after a successful step-up', async () => {
    stepUpMock.mockResolvedValue({ id: 'u1' });
    let attempts = 0;
    const action = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw new AuthApiError('step_up_required', 'step up', 401);
      return 'done';
    });

    render(
      <StepUpProvider>
        <Consumer action={action} />
      </StepUpProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'go' }));

    // The modal opens on the step-up challenge.
    await waitFor(() => expect(screen.getByText(/confirm it's you/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'my-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(screen.getByTestId('msg').textContent).toBe('ok:done'));
    expect(stepUpMock).toHaveBeenCalledWith('my-password');
    expect(action).toHaveBeenCalledTimes(2);
  });

  it('passes a non-step-up error straight through without a modal', async () => {
    const action = vi.fn(async () => {
      throw new AuthApiError('conflict', 'nope', 409);
    });
    render(
      <StepUpProvider>
        <Consumer action={action} />
      </StepUpProvider>
    );
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    await waitFor(() => expect(screen.getByTestId('msg').textContent).toBe('err'));
    expect(screen.queryByText(/confirm it's you/i)).not.toBeInTheDocument();
    expect(stepUpMock).not.toHaveBeenCalled();
  });
});
