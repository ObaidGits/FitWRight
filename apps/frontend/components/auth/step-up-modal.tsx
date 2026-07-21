'use client';

/**
 * Step-up ("sudo") modal (Task 8.3 / R9.1, R15.4).
 *
 * Sensitive actions (password/email change, log-out-everywhere) require a recent
 * re-authentication. Wrap such an action with `useStepUp().run(action)`: it runs
 * the action, and if the backend answers `401 step_up_required` it opens this
 * modal to re-enter the password, then transparently retries the original
 * action and resolves with its result.
 */
import * as React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/atelier/dialog';
import { Button } from '@/components/atelier/button';
import { Label } from '@/components/atelier/label';
import { PasswordField } from '@/components/auth/password-field';
import { ErrorBanner, describeAuthError } from '@/components/auth/error-banner';
import { authApi, AuthApiError } from '@/lib/api/auth';

/** True when an error is the backend asking for a fresh step-up. */
export function isStepUpRequired(err: unknown): boolean {
  return err instanceof AuthApiError && err.code === 'step_up_required';
}

interface PendingAction<T = unknown> {
  action: () => Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

interface StepUpContextValue {
  /** Run a sensitive action, transparently handling a step-up challenge. */
  run: <T>(action: () => Promise<T>) => Promise<T>;
}

const StepUpContext = React.createContext<StepUpContextValue | undefined>(undefined);

export function StepUpProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  const [password, setPassword] = React.useState('');
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const pendingRef = React.useRef<PendingAction | null>(null);

  const settle = React.useCallback((fn: () => void) => {
    setOpen(false);
    setPassword('');
    setError(null);
    setPending(false);
    fn();
  }, []);

  const run = React.useCallback(<T,>(action: () => Promise<T>): Promise<T> => {
    return (async () => {
      try {
        return await action();
      } catch (err) {
        if (!isStepUpRequired(err)) throw err;
        return await new Promise<T>((resolve, reject) => {
          pendingRef.current = {
            action,
            resolve: resolve as (v: unknown) => void,
            reject,
          };
          setError(null);
          setPassword('');
          setOpen(true);
        });
      }
    })();
  }, []);

  async function onConfirm(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await authApi.stepUp(password);
    } catch (err) {
      setPending(false);
      setError(describeAuthError(err));
      return;
    }
    // Step-up succeeded - retry the original action and resolve the caller.
    const job = pendingRef.current;
    pendingRef.current = null;
    try {
      const result = await job!.action();
      settle(() => job!.resolve(result));
    } catch (err) {
      settle(() => job!.reject(err));
    }
  }

  function onCancel() {
    const job = pendingRef.current;
    pendingRef.current = null;
    settle(() => job?.reject(new AuthApiError('step_up_cancelled', 'Step-up cancelled.', 401)));
  }

  const value = React.useMemo<StepUpContextValue>(() => ({ run }), [run]);

  return (
    <StepUpContext.Provider value={value}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) onCancel();
        }}
      >
        <DialogContent showClose={false}>
          <DialogHeader>
            <DialogTitle>Confirm it&apos;s you</DialogTitle>
            <DialogDescription>
              For your security, re-enter your password to continue.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={onConfirm} className="space-y-3" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="stepup-password">Password</Label>
              <PasswordField
                id="stepup-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus
              />
            </div>
            <ErrorBanner message={error} />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onCancel}>
                Cancel
              </Button>
              <Button type="submit" loading={pending}>
                Confirm
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </StepUpContext.Provider>
  );
}

export function useStepUp(): StepUpContextValue {
  const ctx = React.useContext(StepUpContext);
  if (!ctx) throw new Error('useStepUp must be used within a StepUpProvider');
  return ctx;
}
