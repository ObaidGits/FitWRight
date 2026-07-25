'use client';

/**
 * Admin > Invites (secure admin signup - Option B).
 *
 * An existing admin issues a single-use, email-bound invitation that creates a
 * NEW admin account on redemption at `/signup?invite=...`. The raw token is
 * shown ONCE (in the shareable URL) and never retrievable again. Access is
 * enforced server-side by `admin.manage`; this UI is convenience only.
 */
import * as React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Copy from 'lucide-react/dist/esm/icons/copy';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import Check from 'lucide-react/dist/esm/icons/check';
import { Card } from '@/components/atelier/card';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Badge } from '@/components/atelier/badge';
import { LoadingSkeleton, ErrorState, EmptyState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import {
  listInvites,
  createInvite,
  revokeInvite,
  type AdminInviteView,
  type CreatedInvite,
} from '@/lib/api/admin';

const INVITES_KEY = ['admin', 'invites'] as const;

function CreatedInviteCard({ invite }: { invite: CreatedInvite }) {
  const { toast } = useToast();
  const [copied, setCopied] = React.useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(invite.inviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast({ title: 'Could not copy - select and copy the link manually.', variant: 'error' });
    }
  }

  return (
    <Card className="space-y-2 border-[var(--primary)]/30 bg-[var(--primary)]/8 p-4">
      <p className="text-sm font-medium">Invite created for {invite.email}</p>
      <p className="text-xs text-[var(--muted-foreground)]">
        Share this single-use link. It is shown only once and expires on{' '}
        {new Date(invite.expiresAt).toLocaleString()}.
      </p>
      <div className="flex items-center gap-2">
        <Input readOnly value={invite.inviteUrl} className="font-mono text-xs" />
        <Button type="button" size="sm" variant="outline" onClick={copy}>
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
    </Card>
  );
}

const INVITE_STATUS_PRESENTATION: Record<
  AdminInviteView['status'],
  React.ComponentProps<typeof Badge>['variant']
> = {
  active: 'success',
  used: 'neutral',
  expired: 'outline',
  revoked: 'danger',
  superseded: 'warning',
};

function InviteStatusBadge({ status }: { status: AdminInviteView['status'] }) {
  return <Badge variant={INVITE_STATUS_PRESENTATION[status]}>{status}</Badge>;
}

function InviteLifecycle({ invite }: { invite: AdminInviteView }) {
  if (invite.status === 'used' && invite.usedAt) {
    return (
      <p className="text-xs text-[var(--muted-foreground)]">
        Redeemed {new Date(invite.usedAt).toLocaleString()}
        {invite.usedBy ? ` · by ${invite.usedBy}` : ''}
      </p>
    );
  }
  if ((invite.status === 'revoked' || invite.status === 'superseded') && invite.revokedAt) {
    return (
      <p className="text-xs text-[var(--muted-foreground)]">
        {invite.status === 'superseded' ? 'Superseded' : 'Revoked'}{' '}
        {new Date(invite.revokedAt).toLocaleString()}
        {invite.revokedBy ? ` · by ${invite.revokedBy}` : ''}
        {invite.revokeReason ? ` · reason: ${invite.revokeReason}` : ''}
      </p>
    );
  }
  if (invite.status === 'expired') {
    return <p className="text-xs text-[var(--muted-foreground)]">Expired without redemption</p>;
  }
  return null;
}

export default function AdminInvitesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [email, setEmail] = React.useState('');
  const [created, setCreated] = React.useState<CreatedInvite | null>(null);

  const invitesQuery = useQuery({ queryKey: INVITES_KEY, queryFn: listInvites });

  const createMut = useMutation({
    mutationFn: (target: string) => createInvite(target),
    onSuccess: (res) => {
      setCreated(res);
      setEmail('');
      toast({ title: 'Invite created', variant: 'success' });
      qc.invalidateQueries({ queryKey: INVITES_KEY });
    },
    onError: (e) =>
      toast({ title: (e as Error).message || 'Could not create invite', variant: 'error' }),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    onSuccess: (result) => {
      toast({
        title: result.changed ? 'Invite revoked' : 'Invite was no longer active',
        variant: result.changed ? 'success' : 'info',
      });
      qc.invalidateQueries({ queryKey: INVITES_KEY });
    },
    onError: (e) =>
      toast({ title: (e as Error).message || 'Could not revoke invite', variant: 'error' }),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const target = email.trim();
    if (!target.includes('@')) {
      toast({ title: 'Enter a valid email address.', variant: 'error' });
      return;
    }
    createMut.mutate(target);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Admin invites</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Invite a new administrator by email. The link is single-use, tied to that email, and
          expires automatically. To make an existing user an admin, change their role on the Users
          page instead.
        </p>
      </div>

      <Card className="space-y-4 p-5">
        <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1 space-y-1.5">
            <Label htmlFor="invite-email">Invite email</Label>
            <Input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="new.admin@example.com"
              autoComplete="off"
            />
          </div>
          <Button type="submit" loading={createMut.isPending} disabled={!email.trim()}>
            Create invite
          </Button>
        </form>
        {created && <CreatedInviteCard invite={created} />}
      </Card>

      <Card className="space-y-3 p-5">
        <div>
          <p className="text-sm font-medium">Recent invite history</p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Bounded recent history includes active, redeemed, expired, revoked, and superseded
            invites. The single-use link is never returned after creation.
          </p>
        </div>
        {invitesQuery.isLoading ? (
          <LoadingSkeleton rows={2} />
        ) : invitesQuery.isError ? (
          <ErrorState
            title="Couldn't load invites"
            description={(invitesQuery.error as Error)?.message}
            onRetry={() => invitesQuery.refetch()}
          />
        ) : (invitesQuery.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="No invite history"
            description="Create one above to invite an admin."
          />
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {invitesQuery.data!.items.map((inv) => (
              <li key={inv.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="space-y-0.5">
                  <p className="text-sm font-medium">{inv.email}</p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {inv.role} · expires {new Date(inv.expiresAt).toLocaleString()}
                  </p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Created {new Date(inv.createdAt).toLocaleString()}
                    {inv.createdBy ? ` · by ${inv.createdBy}` : ''}
                  </p>
                  <InviteLifecycle invite={inv} />
                </div>
                <div className="flex items-center gap-2">
                  <InviteStatusBadge status={inv.status} />
                  {inv.status === 'active' && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => revokeMut.mutate(inv.id)}
                      loading={revokeMut.isPending && revokeMut.variables === inv.id}
                    >
                      <Trash2 className="h-4 w-4" /> Revoke
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
