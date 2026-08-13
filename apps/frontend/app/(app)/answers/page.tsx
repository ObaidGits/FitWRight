'use client';

/**
 * Answers - the questions application forms ask you.
 *
 * A top-level destination rather than a Settings tab. Every application adds
 * questions here, so this is daily work; Settings is where you go once and
 * forget. Burying it there is how the review queue quietly grows to two hundred
 * unanswered rows and the autofill stops being able to finish a form.
 *
 * The readiness card sits above the list because the two are the same problem
 * seen from opposite ends: the list is questions forms already asked, and
 * readiness is the ones they are about to.
 */
import * as React from 'react';
import Link from 'next/link';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import CircleAlert from 'lucide-react/dist/esm/icons/circle-alert';

import { ApplicationAnswers } from '@/components/answers/application-answers';
import { Card } from '@/components/atelier/card';
import { useAutofillReadiness } from '@/features/application-fields/hooks';

/** Plain-language stakes for each group of missing fields. */
const GROUP_COST: Record<string, string> = {
  essential: 'Most forms will not submit without this.',
  common: 'Asked often - you will type it by hand until it is saved.',
  eligibility:
    'Never guessed, on purpose: a wrong visa or salary answer gets an application rejected. ' +
    'Until you store it, every form asks you again.',
};

const GROUP_LABEL: Record<string, string> = {
  essential: 'Essential',
  common: 'Commonly asked',
  eligibility: 'Eligibility',
};

function ReadinessCard() {
  const readiness = useAutofillReadiness();

  if (readiness.isPending || readiness.isError || !readiness.data) return null;

  const { covered, total, missing, has_resume: hasResume } = readiness.data;
  const percent = total ? Math.round((covered / total) * 100) : 0;
  const complete = missing.length === 0;

  // Grouped so the card can lead with what actually costs the user something,
  // rather than listing twenty-one fields in schema order.
  const groups = ['essential', 'eligibility', 'common'].filter((group) =>
    missing.some((field) => field.group === group)
  );

  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">
            Your profile answers {covered} of {total}
          </h2>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {complete
              ? 'Every question forms usually ask is stored. Autofill can finish a typical application.'
              : 'These are the questions application forms ask most. Anything missing is something you retype.'}
          </p>
        </div>
        <Link
          href="/profile"
          className="flex shrink-0 items-center gap-1 text-sm font-medium text-[var(--primary)] hover:underline"
        >
          Edit profile <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>

      <div>
        <div
          className="h-2 overflow-hidden rounded-full bg-[var(--secondary)]"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Profile completeness for application forms"
        >
          <div
            className="h-full rounded-full bg-[var(--primary)] transition-[width]"
            style={{ width: `${percent}%` }}
          />
        </div>
        <p className="mt-1.5 text-xs text-[var(--muted-foreground)]">{percent}% ready</p>
      </div>

      {!hasResume && (
        <p className="flex items-start gap-2 text-sm text-[var(--at-warning)]">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          No resume uploaded yet, so forms that want a file attachment cannot be completed.
        </p>
      )}

      {groups.map((group) => {
        const fields = missing.filter((field) => field.group === group);
        return (
          <div key={group} className="space-y-1.5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
              {GROUP_LABEL[group]} · {fields.length} missing
            </h3>
            <p className="text-xs text-[var(--muted-foreground)]">{GROUP_COST[group]}</p>
            <ul className="flex flex-wrap gap-1.5">
              {fields.map((field) => (
                <li
                  key={field.key}
                  className="rounded-[var(--radius-at-sm)] border border-[var(--border)] px-2 py-0.5 text-xs"
                >
                  {field.label}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </Card>
  );
}

export default function AnswersPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-6">
      <header>
        <h1 className="text-xl font-semibold">Answers</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Answer a question once and every future application form fills it for you.
        </p>
      </header>

      <ReadinessCard />
      <ApplicationAnswers />
    </div>
  );
}
