'use client';

/**
 * Profile completeness card - a weighted score ring + prioritized next actions.
 *
 * The score and suggestions come from the backend Completion Engine (single
 * source of truth), so the nudge list and the number never disagree. Unmet,
 * highest-impact items surface first; met items are shown checked for a sense
 * of progress. Purely presentational.
 */
import * as React from 'react';
import Check from 'lucide-react/dist/esm/icons/check';
import Circle from 'lucide-react/dist/esm/icons/circle';

import { Card } from '@/components/atelier/card';
import type { CompletenessSuggestion } from '@/lib/api/professional-profile';

function bandLabel(score: number): string {
  if (score >= 90) return 'Excellent';
  if (score >= 70) return 'Strong';
  if (score >= 40) return 'Getting there';
  return 'Just started';
}

export function CompletenessCard({
  score,
  suggestions,
}: {
  score: number;
  suggestions: CompletenessSuggestion[];
}) {
  const clamped = Math.max(0, Math.min(100, score));
  const circumference = 2 * Math.PI * 26;
  const offset = circumference - (clamped / 100) * circumference;
  const unmet = suggestions.filter((s) => !s.done).slice(0, 4);

  return (
    <Card className="p-5">
      <div className="flex items-center gap-4">
        <div
          className="relative h-16 w-16 shrink-0"
          role="img"
          aria-label={`Profile ${clamped}% complete`}
        >
          <svg viewBox="0 0 60 60" className="h-16 w-16 -rotate-90">
            <circle cx="30" cy="30" r="26" fill="none" stroke="var(--secondary)" strokeWidth="6" />
            <circle
              cx="30"
              cy="30"
              r="26"
              fill="none"
              stroke="var(--primary)"
              strokeWidth="6"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              className="transition-[stroke-dashoffset] duration-700 ease-out motion-reduce:transition-none"
            />
          </svg>
          <span className="absolute inset-0 flex items-center justify-center text-sm font-semibold">
            {clamped}%
          </span>
        </div>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">Profile strength</h2>
          <p className="text-xs text-[var(--muted-foreground)]">
            {bandLabel(clamped)} - {clamped}% complete
          </p>
        </div>
      </div>

      {unmet.length > 0 && (
        <div className="mt-4 space-y-1.5">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">Next steps</p>
          <ul className="space-y-1">
            {unmet.map((s) => (
              <li key={s.key} className="flex items-center gap-2 text-sm">
                <Circle className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]" />
                <span className="text-[var(--foreground)]">{s.label}</span>
                <span className="ml-auto text-xs text-[var(--muted-foreground)]">+{s.weight}%</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {unmet.length === 0 && (
        <p className="mt-4 flex items-center gap-2 text-sm text-[var(--at-success,var(--primary))]">
          <Check className="h-4 w-4" /> Your profile is complete.
        </p>
      )}
    </Card>
  );
}
