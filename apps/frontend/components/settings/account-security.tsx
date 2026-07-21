'use client';

/**
 * Settings -> Account security (Task 8.3 / R7.3, R7.4, R3.2, R3.5).
 *
 * Change password, change email (verify-before-switch), the active-device list
 * with per-device revoke, and log-out-everywhere. Sensitive actions are wrapped
 * with `useStepUp().run(...)`, which transparently handles the backend's
 * `step_up_required` challenge (re-auth modal -> retry). Rendered only in hosted
 * (multi-user) mode; local single-user mode has no password/session surface.
 */
import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Monitor from 'lucide-react/dist/esm/icons/monitor';

import { Card } from '@/components/atelier/card';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { PasswordField } from '@/components/auth/password-field';
import { ErrorBanner, describeAuthError } from '@/components/auth/error-banner';
import { useStepUp } from '@/components/auth/step-up-modal';
import { useSession } from '@/lib/context/session';
import { authApi, type DeviceSession } from '@/lib/api/auth';

const SESSIONS_KEY = ['auth', 'sessions'] as const;

export function AccountSecurity() {
  return (
    <div className="space-y-4">
      <ChangePasswordCard />
      <ChangeEmailCard />
      <DeviceListCard />
    </div>
  );
}

function ChangePasswordCard() {
  const { run } = useStepUp();
  const { toast } = useToast();
  const [current, setCurrent] = React.useState('');
  const [next, setNext] = React.useState('');
  const [confirm, setConfirm] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (next.length < 12) {
      setError('New password must be at least 12 characters.');
      return;
    }
    if (next !== confirm) {
      setError('New passwords do not match.');
      return;
    }
    setPending(true);
    try {
      await run(() => authApi.changePassword({ currentPassword: current, newPassword: next }));
      setCurrent('');
      setNext('');
      setConfirm('');
      toast({ title: 'Password updated', variant: 'success' });
    } catch (err) {
      setCurrent('');
      setNext('');
      setConfirm('');
      if ((err as { code?: string }).code !== 'step_up_cancelled') {
        setError(describeAuthError(err));
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div>
        <p className="text-sm font-medium">Change password</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          You&apos;ll be asked to confirm your current password. Other devices are signed out.
        </p>
      </div>
      <form onSubmit={onSubmit} className="space-y-3" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="current-password">Current password</Label>
          <PasswordField
            id="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-password">New password</Label>
          <PasswordField
            id="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm-new-password">Confirm new password</Label>
          <PasswordField
            id="confirm-new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        <ErrorBanner message={error} />
        <Button type="submit" loading={pending}>
          Update password
        </Button>
      </form>
    </Card>
  );
}

function ChangeEmailCard() {
  const { run } = useStepUp();
  const { toast } = useToast();
  const { user } = useSession();
  const [email, setEmail] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.includes('@')) {
      setError('Enter a valid email address.');
      return;
    }
    setPending(true);
    try {
      await run(() => authApi.beginEmailChange(email));
      setEmail('');
      toast({
        title: 'Confirm your new email',
        description: 'We sent a confirmation link to the new address.',
        variant: 'success',
      });
    } catch (err) {
      if ((err as { code?: string }).code !== 'step_up_cancelled') {
        setError(describeAuthError(err));
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div>
        <p className="text-sm font-medium">Change email</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          Current: {user?.email || '-'}. We&apos;ll verify the new address before switching.
        </p>
      </div>
      <form onSubmit={onSubmit} className="space-y-3" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="new-email">New email</Label>
          <Input
            id="new-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
        </div>
        <ErrorBanner message={error} />
        <Button type="submit" loading={pending}>
          Send confirmation
        </Button>
      </form>
    </Card>
  );
}

function DeviceListCard() {
  const qc = useQueryClient();
  const { run } = useStepUp();
  const { signOut } = useSession();
  const { toast } = useToast();
  const sessions = useQuery({ queryKey: SESSIONS_KEY, queryFn: authApi.listSessions });

  const revoke = useMutation({
    mutationFn: (id: string) => authApi.revokeSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSIONS_KEY }),
    onError: (err) => toast({ title: describeAuthError(err), variant: 'error' }),
  });

  const [loggingOut, setLoggingOut] = React.useState(false);
  async function onLogoutEverywhere() {
    setLoggingOut(true);
    try {
      await run(() => authApi.logoutAll());
      await signOut();
    } catch (err) {
      if ((err as { code?: string }).code !== 'step_up_cancelled') {
        toast({ title: describeAuthError(err), variant: 'error' });
      }
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div>
        <p className="text-sm font-medium">Active sessions</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          Devices where you&apos;re currently signed in.
        </p>
      </div>
      {sessions.isLoading ? (
        <LoadingSkeleton rows={2} />
      ) : sessions.data && sessions.data.length > 0 ? (
        <ul className="divide-y divide-[var(--border)]">
          {sessions.data.map((s: DeviceSession) => (
            <li key={s.id} className="flex items-center justify-between py-3">
              <div className="flex items-center gap-3">
                <Monitor className="h-4 w-4 text-[var(--muted-foreground)]" />
                <div>
                  <p className="text-sm font-medium">
                    {s.deviceLabel || 'Unknown device'}
                    {s.current && (
                      <span className="ml-2 rounded-full bg-[var(--accent)] px-2 py-0.5 text-xs text-[var(--foreground)]">
                        This device
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Last active {new Date(s.lastSeenAt).toLocaleString()}
                  </p>
                </div>
              </div>
              {!s.current && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={revoke.isPending && revoke.variables === s.id}
                  onClick={() => revoke.mutate(s.id)}
                >
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-[var(--muted-foreground)]">No other active sessions.</p>
      )}
      <div className="border-t border-[var(--border)] pt-4">
        <Button variant="destructive" loading={loggingOut} onClick={onLogoutEverywhere}>
          Log out everywhere
        </Button>
      </div>
    </Card>
  );
}
