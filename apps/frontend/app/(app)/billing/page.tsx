'use client';

/**
 * Billing - the page that did not exist.
 *
 * Everything about paying for FitWright used to live inside Settings > AI Provider,
 * beside the bring-your-own-key form, which meant a customer had nowhere to answer any
 * of: what plan am I on, what did I pay, what does an action cost, what do I have left.
 *
 * The page is ordered by how urgently a user needs each answer:
 *
 *   1. What can I still do right now.       (the only question during a job hunt)
 *   2. What plan am I on, what else exists.
 *   3. What does each action cost.          (so the balance above is interpretable)
 *   4. What have I paid.                    (the receipt trail)
 *
 * Credits are never the headline. "About 65 applications" is actionable; "1,690 credits"
 * is not - so the credit figure is always present but always secondary, and every number
 * on this page comes from the server rather than being recomputed here, because a page
 * that derives its own prices will eventually disagree with what is charged.
 */

import * as React from 'react';
import Link from 'next/link';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { BuyCredits } from '@/components/settings/buy-credits';
import { PAGE_WIDTH } from '@/lib/layout/page-width';
import {
  formatMoney,
  getMyCredits,
  getMyPurchases,
  getPricing,
  type MyCredits,
  type Pricing,
  type PurchaseRecord,
} from '@/lib/api/credits';

export default function BillingPage() {
  const [credits, setCredits] = React.useState<MyCredits | null>(null);
  const [pricing, setPricing] = React.useState<Pricing | null>(null);
  const [purchases, setPurchases] = React.useState<PurchaseRecord[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [failed, setFailed] = React.useState(false);

  const load = React.useCallback(() => {
    setLoading(true);
    setFailed(false);
    Promise.all([getMyCredits(), getPricing(), getMyPurchases()])
      .then(([c, p, h]) => {
        setCredits(c);
        setPricing(p);
        setPurchases(h.items);
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (failed || !credits || !pricing) {
    return (
      <div className={PAGE_WIDTH.CONTENT}>
        <ErrorState
          title="Could not load your billing details"
          description="Please try again in a moment."
          onRetry={load}
        />
      </div>
    );
  }

  return (
    <div className={`${PAGE_WIDTH.CONTENT} space-y-6`}>
      <div>
        <h1 className="text-2xl font-semibold">Plan &amp; billing</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          What you can do, what it costs, and what you&apos;ve paid.
        </p>
      </div>

      <BalanceCard credits={credits} />
      <PlansCard pricing={pricing} />
      <PricesCard pricing={pricing} />
      <HistoryCard purchases={purchases} onChanged={load} />
    </div>
  );
}

/** "Can I still do the thing I came here to do?" - answered before anything else. */
function BalanceCard({ credits }: { credits: MyCredits }) {
  if (credits.mode === 'own_key') {
    return (
      <Card className="space-y-2 p-6">
        <p className="text-sm font-medium">You&apos;re using your own AI key</p>
        <p className="text-sm text-[var(--muted-foreground)]">
          FitWright isn&apos;t limiting or charging you. Your provider bills you directly, so
          nothing on this page applies while that key is in place.
        </p>
        <Button variant="outline" size="sm" asChild className="mt-2 w-fit">
          <Link href="/settings">Change AI source</Link>
        </Button>
      </Card>
    );
  }

  if (credits.mode === 'unlimited') {
    return (
      <Card className="space-y-1 p-6">
        <p className="text-sm font-medium">AI features are included</p>
        <p className="text-sm text-[var(--muted-foreground)]">
          Everything AI-powered is part of your account right now.
        </p>
      </Card>
    );
  }

  if (credits.mode === 'disabled') {
    return (
      <Card className="space-y-2 border-[var(--destructive)]/40 p-6">
        <p className="flex items-center gap-2 text-sm font-medium">
          AI features are turned off <Badge variant="danger">disabled</Badge>
        </p>
        <p className="text-sm text-[var(--muted-foreground)]">
          This isn&apos;t about running out - topping up won&apos;t change it. Please contact
          support to have it re-enabled.
        </p>
      </Card>
    );
  }

  const available = credits.available_credits ?? 0;
  const search = credits.search;

  return (
    <Card className="space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-[var(--muted-foreground)]">You can do</p>
          {/* The headline is the action count, not the credit count. */}
          <p className="text-2xl font-semibold">{credits.summary}</p>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {available.toLocaleString()} credits left
            {credits.credits_per_application
              ? ` · about ${credits.credits_per_application} per application`
              : ''}
          </p>
        </div>
        {credits.low && <Badge variant="warning">running low</Badge>}
      </div>

      {/* Per-action counts. A free action shows "included" rather than a number, because
          "0 left" would be exactly the wrong reading. */}
      {credits.actions.length > 0 && (
        <ul className="grid gap-2 sm:grid-cols-3">
          {credits.actions.map((a) => (
            <li
              key={a.feature}
              className="rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] px-3 py-2"
            >
              {a.is_free ? (
                <p className="text-base font-semibold text-[var(--at-success)]">Included</p>
              ) : (
                <p
                  className={`text-base font-semibold ${
                    (a.remaining ?? 0) <= 0 ? 'text-[var(--destructive)]' : ''
                  }`}
                >
                  {a.remaining ?? 0}
                </p>
              )}
              <p className="text-xs text-[var(--muted-foreground)]">{a.label}</p>
              {/* Naming the price here is what makes the count above interpretable. */}
              {!a.is_free && a.credits_each ? (
                <p className="text-[11px] text-[var(--muted-foreground)]">
                  {a.credits_each} credits each
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {/* Zero of something specific, said plainly. A generic "out of credits" leaves the
          user guessing which thing they can no longer do. */}
      {credits.actions.some((a) => !a.is_free && (a.remaining ?? 0) <= 0) && (
        <div className="rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 p-3">
          <p className="text-sm font-medium">
            You&apos;ve run out of:{' '}
            {credits.actions
              .filter((a) => !a.is_free && (a.remaining ?? 0) <= 0)
              .map((a) => a.label.toLowerCase())
              .join(', ')}
          </p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            Top up below, upgrade your plan, or add your own AI provider key in Settings - your own
            key has no limit and costs you only what your provider charges.
          </p>
        </div>
      )}

      {/* Searches are their own limit with its own remedy. Deliberately not folded into
          the credit balance: buying credits would not give you another search. */}
      {search && search.daily_limit !== null && (
        <div
          className={`rounded-[var(--radius-at-md)] border p-3 ${
            search.exhausted
              ? 'border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10'
              : 'border-[var(--border)]'
          }`}
        >
          <p className="text-sm font-medium">
            {search.exhausted
              ? "You've used all of today's job searches"
              : `${search.remaining} of ${search.daily_limit} job searches left today`}
          </p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            {search.exhausted
              ? 'Searching is free but capped per day. It resets at midnight UTC - or upgrade for a higher daily limit.'
              : 'Searching is free and never uses credits. The daily cap keeps job boards happy.'}
          </p>
        </div>
      )}

      {credits.allowance_period_start && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Your monthly credits renew {nextRenewal(credits.allowance_period_start)}.
          {(credits.wallet_credits ?? 0) > 0 && ' Credits you bought never expire.'}
        </p>
      )}

      <BuyCredits onPurchased={() => window.location.reload()} />
    </Card>
  );
}

function PlansCard({ pricing }: { pricing: Pricing }) {
  if (pricing.plans.length === 0) return null;

  return (
    <Card className="space-y-4 p-6">
      <div>
        <p className="text-sm font-medium">Plans</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          Monthly credits renew each month. Searching is free on every plan, up to a daily cap.
        </p>
      </div>
      <ul className="grid gap-3 sm:grid-cols-3">
        {pricing.plans.map((plan) => (
          <li
            key={plan.id}
            className={`flex flex-col gap-2 rounded-[var(--radius-at-md)] border p-4 ${
              plan.is_current
                ? 'border-[var(--primary)] bg-[var(--primary)]/5'
                : 'border-[var(--border)]'
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium">{plan.label}</p>
              {plan.is_current && <Badge variant="primary">Your plan</Badge>}
            </div>
            <p className="text-xl font-semibold">
              {plan.is_free ? 'Free' : formatMoney(plan.price_minor, plan.currency)}
              {!plan.is_free && (
                <span className="text-sm font-normal text-[var(--muted-foreground)]">/mo</span>
              )}
            </p>
            <p className="text-xs text-[var(--muted-foreground)]">
              {/* Expressed in applications first; the credit figure is the small print. */}
              about{' '}
              {Math.floor(plan.monthly_credits / Math.max(1, pricing.credits_per_application))}{' '}
              applications · {plan.monthly_credits.toLocaleString()} credits
            </p>
            <p className="text-xs text-[var(--muted-foreground)]">
              {plan.search_daily_limit === null
                ? 'Unlimited job searches'
                : `${plan.search_daily_limit} job searches a day`}
            </p>
            {plan.description && (
              <p className="text-xs text-[var(--muted-foreground)]">{plan.description}</p>
            )}
          </li>
        ))}
      </ul>
      {/* Honest about what is not built rather than showing a button that does nothing.
          Changing plans is a payment flow, and a dead "Upgrade" button is worse than an
          explanation of how to actually do it. */}
      <p className="text-xs text-[var(--muted-foreground)]">
        To change plan, top up your credits below or{' '}
        <Link href="/contact" className="underline">
          contact us
        </Link>{' '}
        - including for a custom plan if none of these fit.
      </p>
    </Card>
  );
}

function PricesCard({ pricing }: { pricing: Pricing }) {
  const charged = pricing.features.filter((f) => !f.is_free);
  const free = pricing.features.filter((f) => f.is_free);

  return (
    <Card className="space-y-4 p-6">
      <div>
        <p className="text-sm font-medium">What each action costs</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          One application is about {pricing.credits_per_application} credits - a tailored resume, a
          cover letter, and the answers drafted for you.
        </p>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {charged.map((f) => (
          <li key={f.feature} className="flex items-start justify-between gap-3 py-2">
            <div className="min-w-0">
              <p className="text-sm">{f.label}</p>
              {f.description && (
                <p className="text-xs text-[var(--muted-foreground)]">{f.description}</p>
              )}
            </div>
            <p className="shrink-0 text-sm font-medium">{f.credits} credits</p>
          </li>
        ))}
        {free.map((f) => (
          <li key={f.feature} className="flex items-start justify-between gap-3 py-2">
            <div className="min-w-0">
              <p className="text-sm">{f.label}</p>
              {f.description && (
                <p className="text-xs text-[var(--muted-foreground)]">{f.description}</p>
              )}
            </div>
            <p className="shrink-0 text-sm font-medium text-[var(--at-success)]">Free</p>
          </li>
        ))}
      </ul>
      <p className="text-xs text-[var(--muted-foreground)]">
        Searching for jobs is always free and never uses credits. You&apos;re only charged when AI
        writes something for you - and never when a request fails.
      </p>
    </Card>
  );
}

function HistoryCard({
  purchases,
  onChanged,
}: {
  purchases: PurchaseRecord[] | null;
  onChanged: () => void;
}) {
  return (
    <Card className="space-y-3 p-6">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">Payment history</p>
        <Button variant="ghost" size="sm" onClick={onChanged}>
          Refresh
        </Button>
      </div>
      {purchases === null || purchases.length === 0 ? (
        <EmptyState
          title="No payments yet"
          description="Anything you buy will show up here with its reference number."
        />
      ) : (
        <ul className="divide-y divide-[var(--border)]">
          {purchases.map((p) => (
            <li key={p.id} className="flex flex-wrap items-start justify-between gap-2 py-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  {p.credits.toLocaleString()} credits
                  <span className="ml-2 font-normal text-[var(--muted-foreground)]">
                    {formatMoney(p.amount_minor, p.currency)}
                  </span>
                </p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {formatDate(p.created_at)}
                  {/* The reference a user can quote to support. Without it a payment
                      question becomes a search through timestamps. */}
                  {p.invoice_number ? ` · ${p.invoice_number}` : ''}
                </p>
                {/* A failed attempt the user can SEE is one they will not report as a
                    missing payment. */}
                {p.state === 'failed' && (
                  <p className="text-xs text-[var(--destructive)]">
                    Payment didn&apos;t go through - you weren&apos;t charged.
                  </p>
                )}
              </div>
              <PurchaseStateBadge state={p.state} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function PurchaseStateBadge({ state }: { state: string }) {
  if (state === 'granted') return <Badge variant="success">Credited</Badge>;
  if (state === 'refunded') return <Badge variant="warning">Refunded</Badge>;
  if (state === 'failed') return <Badge variant="danger">Failed</Badge>;
  // created / paid: the money moved but the credits are still landing.
  return <Badge variant="neutral">Processing</Badge>;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** "on 1 September" - a date to plan around, not a countdown. */
function nextRenewal(periodStartIso: string): string {
  const start = new Date(periodStartIso);
  if (Number.isNaN(start.getTime())) return 'next month';
  const next = new Date(start);
  next.setMonth(next.getMonth() + 1);
  return `on ${next.toLocaleDateString(undefined, { day: 'numeric', month: 'long' })}`;
}
