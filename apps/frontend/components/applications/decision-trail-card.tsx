'use client';

/**
 * "How this was filled" - the auto-apply-brain audit trail (Phase 0), shown per
 * application.
 *
 * Renders nothing when there is nothing to show: an application filled before
 * this shipped, or one applied to without ever running autofill, has no
 * decision rows, and a panel about "how it was filled" would be a lie in that
 * case. Silence is the correct state, not an error.
 */
import * as React from 'react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/atelier/card';
import { LoadingSkeleton } from '@/components/atelier/states';
import { useApplicationDecisions } from '@/features/application-fields/hooks';
import type { Grade } from '@/lib/api/decisions';

const GRADE_STYLE: Record<Grade, string> = {
  green: 'bg-[var(--at-success)]/10 text-[var(--at-success)]',
  yellow: 'bg-[var(--at-warning)]/10 text-[var(--at-warning)]',
  red: 'bg-[var(--at-danger)]/10 text-[var(--at-danger)]',
};

const GRADE_LABEL: Record<Grade, string> = {
  green: 'Ready to send',
  yellow: 'Needs a look',
  red: 'Needs review',
};

const SOURCE_LABEL: Record<string, string> = {
  exact_rule: 'Matched from your Profile',
  cached_classification: 'Recognised from a past application',
  brain_classification: 'New question - recognised for the first time',
  brain_draft: 'AI-drafted answer',
  user_answer: 'You had already typed this in',
  derived_rule: "Depends on this job's details",
};

export function DecisionTrailCard({ applicationId }: { applicationId: string }) {
  const { data, isLoading, isError } = useApplicationDecisions(applicationId);

  if (isLoading) return <LoadingSkeleton rows={2} />;
  // A 404 (no rows yet) and any other error both mean "nothing to show" here -
  // this panel is supplementary, so it disappears rather than showing an
  // error state for what is usually just "autofill was never run".
  if (isError || !data || data.decisions.length === 0) return null;

  return (
    <Card className="p-5">
      <CardHeader className="flex-row items-center justify-between gap-2 p-0 pb-3">
        <CardTitle className="text-sm font-semibold text-[var(--muted-foreground)]">
          How this was filled
        </CardTitle>
        <span
          className={`rounded-[var(--radius-at-sm)] px-2 py-0.5 text-xs font-medium ${GRADE_STYLE[data.grade]}`}
        >
          {GRADE_LABEL[data.grade]}
        </span>
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {data.held_reasons.length > 0 && (
          <ul className="space-y-1 text-xs text-[var(--muted-foreground)]">
            {data.held_reasons.map((reason) => (
              <li key={reason}>• {reason}</li>
            ))}
          </ul>
        )}
        <ul className="divide-y divide-[var(--border)]">
          {data.decisions.map((decision) => (
            <li
              key={decision.label}
              className="flex items-center justify-between gap-3 py-1.5 text-sm"
            >
              <span className="text-[var(--foreground)]">{decision.label}</span>
              <span className="text-right text-xs text-[var(--muted-foreground)]">
                {!decision.filled
                  ? 'Not filled'
                  : decision.readback_ok === false
                    ? 'Filled, but could not confirm it stuck'
                    : (SOURCE_LABEL[decision.value_source] ?? decision.value_source)}
                {decision.is_knockout && ' · screening question'}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
