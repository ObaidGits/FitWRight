'use client';

/**
 * "Can I still do the thing I came here to do?"
 *
 * That is the only question this panel answers, which is why it leads with actions
 * remaining and not a credit count. "37 credits" is not something a job seeker can
 * act on; "about 4 more tailored resumes" is.
 *
 * The out-of-credits state is a FORK, never a wall: their own provider key works
 * forever and costs the operator nothing, so it is offered as a first-class free
 * option rather than buried as a consolation. A wall here would just lose the user -
 * and there is nothing to sell them yet anyway, since pricing follows metering.
 *
 * The four modes are rendered separately on purpose. A user on their own key must
 * never be shown "0 left", and an account the operator disabled must not look like an
 * empty balance - no amount of waiting or topping up fixes it, so saying so plainly
 * is kinder than letting them wait for a refill that will not come.
 */
import * as React from 'react';
import Link from 'next/link';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import KeyRound from 'lucide-react/dist/esm/icons/key-round';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { LoadingSkeleton } from '@/components/atelier/states';
import { featureLabel, getMyCredits, getMyUsage } from '@/lib/api/credits';
import type { MyCredits, UsageItem } from '@/lib/api/credits';

export function AiUsagePanel() {
  const [data, setData] = React.useState<MyCredits | null>(null);
  const [usage, setUsage] = React.useState<UsageItem[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [failed, setFailed] = React.useState(false);
  const [showHistory, setShowHistory] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    getMyCredits()
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  React.useEffect(() => {
    if (!showHistory || usage) return;
    getMyUsage()
      .then((r) => setUsage(r.items))
      .catch(() => setUsage([]));
  }, [showHistory, usage]);

  if (loading) return <LoadingSkeleton rows={2} />;

  // A failed balance read must not imply a balance problem. Saying nothing is
  // better than inventing a number - this codebase has already shipped a bug where
  // an AI credential failure rendered as "You are offline".
  if (failed || !data) return null;

  if (data.mode === 'own_key') {
    return (
      <Card className="flex items-start gap-3 p-4">
        <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-ai)]" />
        <div>
          <p className="text-sm font-medium">You&apos;re using your own AI key</p>
          <p className="text-xs text-[var(--muted-foreground)]">
            FitWright isn&apos;t limiting your usage. Your provider bills you directly.
          </p>
        </div>
      </Card>
    );
  }

  if (data.mode === 'unlimited') {
    // Shipping dark. Inventing a balance now would train people to worry about a
    // limit that does not exist yet.
    return (
      <Card className="flex items-start gap-3 p-4">
        <Sparkles className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-ai)]" />
        <div>
          <p className="text-sm font-medium">AI features are included</p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Everything AI-powered is part of your account.
          </p>
        </div>
      </Card>
    );
  }

  if (data.mode === 'disabled') {
    return (
      <Card className="space-y-2 border-[var(--destructive)]/40 p-4">
        <p className="flex items-center gap-2 text-sm font-medium">
          AI features are turned off <Badge variant="danger">disabled</Badge>
        </p>
        <p className="text-xs text-[var(--muted-foreground)]">
          This isn&apos;t about running out - contact support to have it re-enabled. You can also
          use your own provider key in the meantime.
        </p>
      </Card>
    );
  }

  const refill = data.allowance_period_start ? nextRefillLabel(data.allowance_period_start) : null;

  return (
    <Card className="space-y-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium">Your AI usage</p>
          {/* The headline. Phrased as actions because that is the unit the user
              thinks in. */}
          <p className="text-lg font-semibold">{data.summary}</p>
        </div>
        {data.low && <Badge variant="warning">running low</Badge>}
      </div>

      {data.actions.length > 0 && (
        <ul className="grid gap-2 sm:grid-cols-3">
          {data.actions.map((a) => (
            <li
              key={a.feature}
              className="rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] px-3 py-2"
            >
              <p className="text-base font-semibold">{a.remaining}</p>
              <p className="text-xs text-[var(--muted-foreground)]">{a.label}</p>
            </li>
          ))}
        </ul>
      )}

      {refill && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Your free monthly credits renew {refill}.
          {(data.wallet_credits ?? 0) > 0 && ' Credits you bought never expire.'}
        </p>
      )}

      {/* The free alternative, offered as an equal option rather than a fallback. */}
      {data.low && (
        <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
          <p className="text-sm font-medium">Need more right now?</p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            Add your own AI provider key below and your usage stops counting against this allowance
            entirely. Many providers have a free tier.
          </p>
        </div>
      )}

      <div className="flex items-center justify-between border-t border-[var(--border)] pt-3">
        <button
          type="button"
          onClick={() => setShowHistory((v) => !v)}
          aria-expanded={showHistory}
          className="text-xs font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          {showHistory ? 'Hide' : 'Show'} recent activity
        </button>
        <Button variant="ghost" size="sm" asChild>
          <Link href="/settings">Manage AI settings</Link>
        </Button>
      </div>

      {/* So a user can answer "where did it go?" themselves. A balance that drops
          with no visible history is indistinguishable from a bug. */}
      {showHistory && (
        <div className="space-y-1">
          {usage === null ? (
            <LoadingSkeleton rows={2} />
          ) : usage.length === 0 ? (
            <p className="text-xs text-[var(--muted-foreground)]">
              Nothing yet. AI activity will show up here.
            </p>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {usage.map((u, i) => (
                <li key={i} className="flex items-center justify-between gap-2 py-1.5">
                  <span className="truncate text-xs">{featureLabel(u.feature)}</span>
                  <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                    {u.outcome === 'ok' ? (
                      u.credits_charged > 0 ? (
                        `${u.credits_charged} credits`
                      ) : (
                        'no charge'
                      )
                    ) : (
                      // Naming this matters: a failed call the user was NOT charged
                      // for is otherwise indistinguishable from a silent deduction.
                      <span title="This didn't complete, so you weren't charged">not charged</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

/** "on 1 September" - a date the user can plan around, not a countdown. */
function nextRefillLabel(periodStartIso: string): string {
  const start = new Date(periodStartIso);
  if (Number.isNaN(start.getTime())) return 'next month';
  const next = new Date(start);
  next.setMonth(next.getMonth() + 1);
  return `on ${next.toLocaleDateString(undefined, { day: 'numeric', month: 'long' })}`;
}
