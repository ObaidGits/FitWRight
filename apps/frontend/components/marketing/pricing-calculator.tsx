'use client';

/**
 * The homepage pricing section - interactive, because the question a visitor actually has
 * is not "what are your tiers?" but "which one do I need?"
 *
 * A static price table makes them do arithmetic: how many credits is an application, how
 * many applications will I send, does 2,000 cover it. The slider answers it directly - move
 * it to the number of applications you expect and the plan that covers you is highlighted.
 * That is the whole reason this is a client component; everything else on the marketing
 * page is static and server-rendered on purpose.
 *
 * Prices are passed in from the server (which read them from the admin-editable rows), so
 * this never invents a number. If the fetch failed upstream it renders nothing rather than
 * a table of zeroes.
 */

import * as React from 'react';
import Link from 'next/link';

export interface CalculatorPlan {
  id: string;
  label: string;
  price_minor: number;
  currency: string;
  monthly_credits: number;
  search_daily_limit: number | null;
  is_free: boolean;
  approx_applications: number;
}

function money(minor: number, currency: string): string {
  const symbol = currency === 'INR' ? '₹' : `${currency} `;
  return `${symbol}${(minor / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function PricingCalculator({
  plans,
  creditsPerApplication,
}: {
  plans: CalculatorPlan[];
  creditsPerApplication: number;
}) {
  const [perMonth, setPerMonth] = React.useState(20);

  if (plans.length === 0 || creditsPerApplication <= 0) return null;

  const ordered = [...plans].sort((a, b) => a.price_minor - b.price_minor);
  const needed = perMonth * creditsPerApplication;

  // The cheapest plan that actually covers the chosen volume. Falling back to the most
  // generous one rather than to "none" matters: a heavy user must still be shown
  // something they can buy, with the shortfall stated honestly below.
  const recommended =
    ordered.find((p) => p.monthly_credits >= needed) ?? ordered[ordered.length - 1];
  const covers = recommended.monthly_credits >= needed;

  return (
    <div className="mt-12">
      <div className="mx-auto max-w-xl">
        <label htmlFor="apps-per-month" className="text-sm font-medium">
          How many jobs will you apply to each month?
        </label>
        <div className="mt-3 flex items-center gap-4">
          <input
            id="apps-per-month"
            type="range"
            min={1}
            max={200}
            step={1}
            value={perMonth}
            onChange={(e) => setPerMonth(Number(e.target.value))}
            className="h-2 w-full cursor-pointer appearance-none rounded-full bg-[var(--secondary)] accent-[var(--primary)]"
            aria-describedby="apps-per-month-value"
          />
          <output
            id="apps-per-month-value"
            htmlFor="apps-per-month"
            className="w-16 shrink-0 text-right text-lg font-semibold tabular-nums"
          >
            {perMonth}
          </output>
        </div>
        <p className="mt-2 text-xs text-[var(--muted-foreground)]">
          That&apos;s about {needed.toLocaleString()} credits a month, at {creditsPerApplication}{' '}
          credits per application (tailored resume + cover letter + drafted answers).
        </p>
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-3">
        {ordered.map((plan) => {
          const isPick = plan.id === recommended.id;
          return (
            <div
              key={plan.id}
              aria-current={isPick ? 'true' : undefined}
              className={`rounded-[var(--radius-at-lg)] border p-5 transition-colors ${
                isPick ? 'border-[var(--primary)] bg-[var(--primary)]/5' : 'border-[var(--border)]'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">{plan.label}</p>
                {isPick && (
                  <span className="rounded-full bg-[var(--primary)]/12 px-2 py-0.5 text-[11px] font-medium text-[var(--primary)]">
                    {covers ? 'Fits you' : 'Closest fit'}
                  </span>
                )}
              </div>
              <p className="mt-2 text-2xl font-semibold">
                {plan.is_free ? 'Free' : money(plan.price_minor, plan.currency)}
                {!plan.is_free && (
                  <span className="text-sm font-normal text-[var(--muted-foreground)]">/mo</span>
                )}
              </p>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                ~{plan.approx_applications} applications ·{' '}
                {plan.search_daily_limit === null
                  ? 'unlimited searches'
                  : `${plan.search_daily_limit} searches/day`}
              </p>
            </div>
          );
        })}
      </div>

      {/* Said plainly rather than hidden: pretending the top tier covers any volume would
          be a surprise the user discovers after paying. */}
      {!covers && (
        <p className="mx-auto mt-6 max-w-xl text-center text-sm text-[var(--muted-foreground)]">
          At {perMonth} applications a month you&apos;d go beyond {recommended.label} - top up with
          extra credits any time, or{' '}
          <Link href="/contact" className="underline">
            ask us about a custom plan
          </Link>
          .
        </p>
      )}

      <div className="mt-8 text-center">
        <Link href="/pricing" className="text-sm font-medium text-[var(--primary)] underline">
          See the full price list
        </Link>
      </div>
    </div>
  );
}
