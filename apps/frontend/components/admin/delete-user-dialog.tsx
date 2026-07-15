'use client';

/**
 * Destructive delete confirmation (R13.5) — pessimistic, typed-email confirm.
 *
 * Requires typing the target's exact email, warns the purge is irreversible
 * after the grace period, and summarizes what will be removed. The mutation is
 * pessimistic (await the server; no optimistic UI for a destructive action —
 * R10.4). Server-mirrored errors (last-active-admin, confirm mismatch) surface
 * inline + as a toast.
 */
import * as React from 'react';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/atelier/dialog';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { useToast } from '@/components/atelier/toast';
import { useDeleteUser } from '@/features/admin/hooks';
import { AdminApiError, type AdminUserRow } from '@/lib/api/admin';

export function DeleteUserDialog({
  user,
  open,
  onOpenChange,
  onDeleted,
}: {
  user: AdminUserRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const { toast } = useToast();
  const del = useDeleteUser();
  const [typed, setTyped] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setTyped('');
      setError(null);
    }
  }, [open, user?.id]);

  if (!user) return null;
  const matches = typed.trim().toLowerCase() === user.email.toLowerCase();

  async function submit() {
    if (!user || !matches) return;
    setError(null);
    try {
      await del.mutateAsync({ id: user.id, email: typed.trim() });
      toast({ variant: 'success', title: 'User scheduled for deletion', description: user.email });
      onOpenChange(false);
      onDeleted?.();
    } catch (e) {
      const code = e instanceof AdminApiError ? e.code : 'error';
      const message =
        code === 'last_active_admin'
          ? 'This would remove the last active admin.'
          : code === 'self_action'
            ? 'You cannot delete your own account.'
            : code === 'confirm_mismatch'
              ? 'The email did not match.'
              : e instanceof Error
                ? e.message
                : 'Delete failed.';
      setError(message);
      toast({ variant: 'error', title: 'Delete failed', description: message });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-[var(--destructive)]">
            <AlertTriangle className="h-5 w-5" /> Delete user
          </DialogTitle>
          <DialogDescription>
            This soft-deletes <strong>{user.email}</strong>. The account is recoverable during the
            grace period, after which it is <strong>permanently purged</strong>. Purging removes the
            user&apos;s resumes, tailored versions, job descriptions, applications, API keys, and
            sessions. The security audit trail is retained.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="confirm-email">
            Type <span className="font-mono">{user.email}</span> to confirm
          </Label>
          <Input
            id="confirm-email"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={user.email}
            autoComplete="off"
            aria-invalid={typed.length > 0 && !matches}
          />
          {error && (
            <p className="text-sm text-[var(--destructive)]" role="alert" aria-live="assertive">
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={del.isPending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={submit}
            disabled={!matches || del.isPending}
            aria-disabled={!matches || del.isPending}
          >
            {del.isPending ? 'Deleting…' : 'Delete user'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
