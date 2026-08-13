'use client';

/**
 * Feed health: the things about a job feed that are true but invisible.
 *
 * Three facts a user cannot discover for themselves, in one place instead of
 * three scattered controls:
 *
 * * **A board has stopped working.** When a scraper breaks the board just returns
 *   nothing, which is indistinguishable from a narrow search. Three empty runs in
 *   a row is the server's threshold for saying so, and a board that has worked
 *   before is called out differently from one that never has - only the first is
 *   likely fixable by signing in again.
 * * **Most jobs have no match score.** Scores only exist for jobs matched against
 *   a resume; a keyword harvest stores none. Rather than hide that, the panel
 *   offers to score them and says how many - each one costs an AI call, so the
 *   number belongs in front of the button, not in a bill afterwards.
 * * **Old jobs get cleared out.** Saying so once removes the worry that the app
 *   is losing things, and gives a way to do it now.
 *
 * Renders nothing when there is nothing to report. A permanently visible "all is
 * well" panel trains people to ignore the space where the real warning appears.
 */
import * as React from 'react';

import CircleAlert from 'lucide-react/dist/esm/icons/circle-alert';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Trash from 'lucide-react/dist/esm/icons/trash-2';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useToast } from '@/components/atelier/toast';
import { useBoardHealth, useCleanupFeed, useScoreFeed } from '@/features/discovery/hooks';

/** Plain-language cause for each recorded status. */
const STATUS_REASON: Record<string, string> = {
  signed_out: 'You are signed out of it. Sign in on that site, then search again.',
  empty: 'It has returned nothing several times running.',
  error: 'The searches themselves are failing.',
  capped: 'Paused for today by the daily safety limit.',
};

function labelFor(board: string): string {
  return board
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function FeedHealthPanel({ unscored }: { unscored: number }) {
  const health = useBoardHealth();
  const score = useScoreFeed();
  const cleanup = useCleanupFeed();
  const { toast } = useToast();

  const failing = health.data?.needs_attention ?? [];
  const hasSomethingToSay = failing.length > 0 || unscored > 0;
  if (!hasSomethingToSay) return null;

  function runScoring() {
    score.mutate(
      { limit: 40 },
      {
        onSuccess: (data) => {
          toast({
            title: `Scored ${data.scored} job${data.scored === 1 ? '' : 's'}`,
            description: data.remaining
              ? `${data.remaining} still unscored — run it again to continue.`
              : 'Every job in your feed now has a match score.',
          });
        },
        onError: (err) =>
          toast({
            title: 'Could not score your feed',
            // Most likely cause, named rather than left as a raw error.
            description: `${err.message}. A master resume is needed to score against.`,
            variant: 'error',
          }),
      },
    );
  }

  function runCleanup() {
    cleanup.mutate(30, {
      onSuccess: (data) =>
        toast({
          title: data.deleted
            ? `Cleared ${data.deleted} old job${data.deleted === 1 ? '' : 's'}`
            : 'Nothing old enough to clear',
          description: 'Jobs you saved, tailored or applied to are always kept.',
        }),
    });
  }

  return (
    <Card className="space-y-3 p-4">
      {failing.length > 0 && (
        <div className="space-y-1.5">
          <h3 className="flex items-center gap-1.5 text-xs font-semibold text-[var(--at-warning)]">
            <CircleAlert className="h-3.5 w-3.5" aria-hidden="true" />
            {failing.length === 1
              ? '1 board is not returning jobs'
              : `${failing.length} boards are not returning jobs`}
          </h3>
          <ul className="space-y-1">
            {failing.map((board) => (
              <li key={board.board} className="text-xs text-[var(--muted-foreground)]">
                <span className="font-medium text-[var(--foreground)]">
                  {labelFor(board.board)}
                </span>{' '}
                — {STATUS_REASON[board.last_status] ?? 'It has stopped returning results.'}
                {board.worked_before ? '' : ' It has never returned any, so it may not be set up.'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {unscored > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 flex-1 text-xs text-[var(--muted-foreground)]">
            <span className="font-medium text-[var(--foreground)]">
              {unscored} job{unscored === 1 ? '' : 's'} have no match score.
            </span>{' '}
            Scoring compares each one against your resume — it uses one AI call per job, so it is
            not done automatically.
          </p>
          <Button size="sm" variant="outline" onClick={runScoring} disabled={score.isPending}>
            <Sparkles className="h-3.5 w-3.5" />
            {score.isPending ? 'Scoring…' : `Score ${Math.min(unscored, 40)}`}
          </Button>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] pt-2">
        <p className="text-[11px] text-[var(--muted-foreground)]">
          Jobs you never opened are cleared after 30 days. Saved and applied jobs are kept.
        </p>
        <Button
          size="sm"
          variant="ghost"
          onClick={runCleanup}
          disabled={cleanup.isPending}
          className="text-[11px]"
        >
          <Trash className="h-3.5 w-3.5" /> Clear now
        </Button>
      </div>
    </Card>
  );
}
