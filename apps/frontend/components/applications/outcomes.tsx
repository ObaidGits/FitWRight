'use client';

/**
 * Outcomes - which resume actually gets replies.
 *
 * The tracker already knows what was sent and what came back; until now nothing
 * turned that into an answer to the only question that changes behaviour: which
 * version should go out next.
 *
 * Two rules keep this honest rather than merely encouraging:
 *
 * * A rate needs a sample. "100% reply rate" off one application is noise, and
 *   presenting it as a finding would push the user to bet on it. Below the
 *   server's threshold the counts are shown and the rate is withheld.
 * * The denominator is concluded applications, not everything sent. An
 *   application posted yesterday has not failed yet, and counting it as a
 *   non-reply would make every recent resume look worse than it is.
 */
import * as React from 'react';
import ChartNoAxesColumn from 'lucide-react/dist/esm/icons/chart-no-axes-column';
import Download from 'lucide-react/dist/esm/icons/download';

import { Card } from '@/components/atelier/card';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { useOutcomes } from '@/features/applications/queue-hooks';

export function Outcomes() {
  const outcomes = useOutcomes();

  if (outcomes.isPending) return <LoadingSkeleton rows={3} />;
  if (outcomes.isError) {
    return (
      <ErrorState
        description="Could not load your outcomes."
        onRetry={() => void outcomes.refetch()}
      />
    );
  }

  const data = outcomes.data;
  if (!data || data.resumes.length === 0) {
    return (
      <EmptyState
        icon={ChartNoAxesColumn}
        title="No sent applications yet"
        description="Once you have applied to a few jobs, this shows which resume gets the most replies."
      />
    );
  }

  const overall = data.sent
    ? `${data.replied} repl${data.replied === 1 ? 'y' : 'ies'} from ${data.sent} application${
        data.sent === 1 ? '' : 's'
      }`
    : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {overall && <p className="text-sm text-[var(--muted-foreground)]">{overall}</p>}
        {/* Months of history with no way out is a lock-in nobody agreed to. */}
        <a
          href="/api/v1/applications/export.csv"
          className="flex items-center gap-1.5 text-xs font-medium text-[var(--primary)] hover:underline"
        >
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
          Export all applications (CSV)
        </a>
      </div>

      <ul className="space-y-2">
        {data.resumes.map((row) => {
          const percent = row.rate === null ? null : Math.round(row.rate * 100);
          return (
            <li key={row.resume_id}>
              <Card className="flex items-center gap-4 p-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{row.name}</p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {row.sent} sent · {row.replied} replied
                    {row.concluded < row.sent ? ` · ${row.sent - row.concluded} still waiting` : ''}
                  </p>
                </div>

                <div className="shrink-0 text-right">
                  {percent === null ? (
                    <>
                      <p className="text-sm font-medium text-[var(--muted-foreground)]">—</p>
                      <p className="text-[11px] text-[var(--muted-foreground)]">
                        needs {data.min_sample} finished
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-sm font-semibold">{percent}%</p>
                      <p className="text-[11px] text-[var(--muted-foreground)]">reply rate</p>
                    </>
                  )}
                </div>
              </Card>
            </li>
          );
        })}
      </ul>

      <p className="text-xs text-[var(--muted-foreground)]">
        A reply is any response, interview or offer. Rates count only applications that have
        finished one way or the other, and are hidden until {data.min_sample} of them exist — a
        percentage off one or two applications tells you nothing.
      </p>
    </div>
  );
}
