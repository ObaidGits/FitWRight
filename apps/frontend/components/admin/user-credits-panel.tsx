'use client';

/**
 * Per-user AI credits panel (spec: ai-provider-admin, Phase 3).
 *
 * Dropped into the admin user drawer. Shows the balance, the effective limits, and
 * the two controls an operator actually needs: adjust this person's ceiling, or
 * give them credits.
 *
 * Two deliberate UI decisions, both mirroring server semantics rather than
 * inventing client ones:
 *
 * 1. An override field distinguishes EMPTY (inherit the global default) from 0
 *    (this user gets nothing). Those are different intents, and collapsing them
 *    would make it impossible to un-restrict someone. The placeholder shows the
 *    inherited value so an empty field is never ambiguous.
 *
 * 2. A grant requires a reason. Not because the API insists - though it does -
 *    but because an unexplained balance change is indistinguishable from a bug or
 *    an abuse when someone reads the ledger in six months.
 */
import * as React from 'react';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useGrantUserCredits, usePatchUserCredits, useUserCredits } from '@/features/admin/hooks';

export function UserCreditsPanel({ userId }: { userId: string }) {
  const { data, isLoading, isError, refetch } = useUserCredits(userId);
  const patch = usePatchUserCredits();
  const grant = useGrantUserCredits();
  const { toast } = useToast();

  const [allowance, setAllowance] = React.useState('');
  const [velocity, setVelocity] = React.useState('');
  const [grantAmount, setGrantAmount] = React.useState('');
  const [grantReason, setGrantReason] = React.useState('');

  // Seed the inputs from the server once loaded. An override of null stays EMPTY
  // (inherit), which is what makes empty-vs-zero meaningful.
  React.useEffect(() => {
    if (!data) return;
    setAllowance(
      data.monthly_allowance_override === null ? '' : String(data.monthly_allowance_override)
    );
    setVelocity(data.velocity_cap_override === null ? '' : String(data.velocity_cap_override));
  }, [data]);

  if (isLoading) return <LoadingSkeleton rows={3} />;
  if (isError || !data) {
    return (
      <ErrorState description="Could not load this user's credits." onRetry={() => refetch()} />
    );
  }

  async function saveLimits() {
    try {
      await patch.mutateAsync({
        userId,
        patch: {
          // Empty means "clear the override and inherit the global default" - a
          // different instruction from setting it to 0.
          ...(allowance.trim() === ''
            ? { clear_allowance_override: true }
            : { monthly_allowance_override: Number(allowance) }),
          ...(velocity.trim() === ''
            ? { clear_velocity_override: true }
            : { velocity_cap_override: Number(velocity) }),
        },
      });
      toast({ title: 'Limits updated', variant: 'success' });
    } catch (err) {
      toast({
        title: 'Could not update limits',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function toggleDisabled(next: boolean) {
    try {
      await patch.mutateAsync({ userId, patch: { ai_disabled: next } });
      toast({ title: next ? 'AI disabled for this user' : 'AI re-enabled', variant: 'success' });
    } catch (err) {
      toast({
        title: 'Could not change AI access',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function submitGrant() {
    const credits = Number(grantAmount);
    if (!credits || credits <= 0 || grantReason.trim().length < 3) return;
    try {
      await grant.mutateAsync({ userId, credits, reason: grantReason.trim() });
      toast({ title: `Granted ${credits} credits`, variant: 'success' });
      setGrantAmount('');
      setGrantReason('');
    } catch (err) {
      toast({
        title: 'Could not grant credits',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  return (
    <div className="space-y-4">
      {!data.credits_enabled && (
        <Card className="border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-3">
          <p className="text-xs">
            The credit system is switched off, so these limits are recorded but not enforced.
            Nothing is charged until it is enabled.
          </p>
        </Card>
      )}

      <Card className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Balance</h3>
          {data.state === 'blocked' && <Badge variant="danger">blocked</Badge>}
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Stat label="Available" value={data.available_credits} emphasis />
          <Stat label="Free allowance" value={data.allowance_credits} />
          <Stat label="Purchased" value={data.wallet_credits} />
          {/* Held, not spent - shown because a nonzero value here explains an
              "insufficient credits" report that the other numbers contradict. */}
          <Stat label="On hold" value={data.reserved_credits} />
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          Spent {data.lifetime_spent} of {data.lifetime_granted} granted, all time.
        </p>
      </Card>

      <Card className="space-y-4 p-4">
        <h3 className="text-sm font-semibold">Limits</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="cr-allowance">Monthly free credits</Label>
            <Input
              id="cr-allowance"
              type="number"
              min={0}
              value={allowance}
              onChange={(e) => setAllowance(e.target.value)}
              placeholder={`Inherited: ${data.global_monthly_allowance}`}
            />
            <p className="text-xs text-[var(--muted-foreground)]">
              Leave empty to follow the global default. Set 0 to give this user no free credits.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cr-velocity">Credits per hour</Label>
            <Input
              id="cr-velocity"
              type="number"
              min={0}
              value={velocity}
              onChange={(e) => setVelocity(e.target.value)}
              placeholder={`Inherited: ${data.global_velocity_cap}`}
            />
            <p className="text-xs text-[var(--muted-foreground)]">
              A burst ceiling, separate from the balance. Stops a compromised account draining a
              funded wallet in one go.
            </p>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border)] pt-3">
          <div>
            <p className="text-sm font-medium">Turn AI off for this user</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              Blocks every AI feature immediately, whatever their balance.
            </p>
          </div>
          <Switch
            checked={data.ai_disabled}
            onCheckedChange={(v) => void toggleDisabled(v)}
            aria-label="Disable AI for this user"
          />
        </div>
        <div className="flex justify-end">
          <Button size="sm" loading={patch.isPending} onClick={() => void saveLimits()}>
            Save limits
          </Button>
        </div>
      </Card>

      <Card className="space-y-3 p-4">
        <h3 className="text-sm font-semibold">Grant credits</h3>
        <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
          <div className="space-y-1.5">
            <Label htmlFor="cr-amount">Credits</Label>
            <Input
              id="cr-amount"
              type="number"
              min={1}
              value={grantAmount}
              onChange={(e) => setGrantAmount(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cr-reason">Reason</Label>
            <Input
              id="cr-reason"
              value={grantReason}
              onChange={(e) => setGrantReason(e.target.value)}
              placeholder="Goodwill after the outage on the 12th"
            />
          </div>
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          Recorded permanently against this user. The reason is required because an unexplained
          balance change cannot be told apart from a mistake later.
        </p>
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="outline"
            loading={grant.isPending}
            disabled={!Number(grantAmount) || grantReason.trim().length < 3}
            onClick={() => void submitGrant()}
          >
            Grant
          </Button>
        </div>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: number;
  emphasis?: boolean;
}) {
  return (
    <div>
      <p
        className={
          emphasis
            ? 'text-lg font-semibold text-[var(--foreground)]'
            : 'text-lg font-medium text-[var(--foreground)]'
        }
      >
        {value}
      </p>
      <p className="text-xs text-[var(--muted-foreground)]">{label}</p>
    </div>
  );
}
