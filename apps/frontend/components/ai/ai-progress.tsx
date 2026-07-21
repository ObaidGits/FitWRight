'use client';

/**
 * <AiProgress> - the shared, premium loading timeline for genuine multi-second
 * AI work (Loading Experience audit - P0). Extracted from the Tailor flow so
 * every AI operation reads identically.
 *
 * Two drive modes:
 *  - LIVE: pass `activeKey` (+ optional `doneKeys`) from real SSE stage events.
 *  - DETERMINISTIC: omit `activeKey` and pass `active`/`done`; an honest
 *    decelerating timer drives the stages and holds the final one until `done`.
 *
 * Accessibility: an `aria-live="polite"` status announces the active stage and
 * completion once; animation is gated behind `motion-safe:` so reduced-motion
 * users get a static step list. Never shows a fabricated percentage.
 */
import * as React from 'react';
import Check from 'lucide-react/dist/esm/icons/check';
import LoaderCircle from 'lucide-react/dist/esm/icons/loader-circle';

import { cn } from '@/lib/utils';
import { useDeterministicStages, useRotatingMessages } from '@/lib/hooks/use-ai-progress';

export interface AiStage {
  key: string;
  label: string;
}

export interface AiProgressProps {
  stages: AiStage[];
  /** LIVE mode: the key of the active stage from real events. */
  activeKey?: string;
  /** LIVE mode: keys already completed (defaults to "everything before active"). */
  doneKeys?: string[];
  /** DETERMINISTIC mode: whether the flow is running. */
  active?: boolean;
  /** DETERMINISTIC mode: flip true when the real work resolves. */
  done?: boolean;
  /** Rotating reassurance microcopy (fades under motion-safe). */
  messages?: string[];
  /** Honest, static time hint, e.g. "Usually 5-10 seconds." */
  estimate?: string;
  /** Reassurance shown if the run runs long (deterministic mode only). */
  overdueMessage?: string;
  /** Optional result skeleton rendered beneath the timeline. */
  preview?: React.ReactNode;
  className?: string;
}

function StageIcon({ status }: { status: 'pending' | 'active' | 'done' }) {
  return (
    <span
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
      style={{
        background:
          status === 'done'
            ? 'var(--at-success)'
            : status === 'active'
              ? 'var(--at-ai-surface)'
              : 'var(--secondary)',
        color: status === 'done' ? 'white' : 'var(--at-ai)',
      }}
      aria-hidden
    >
      {status === 'done' ? (
        <Check className="h-3 w-3" />
      ) : status === 'active' ? (
        <LoaderCircle className="h-3 w-3 motion-safe:animate-spin" />
      ) : null}
    </span>
  );
}

export function AiProgress({
  stages,
  activeKey,
  doneKeys,
  active = true,
  done = false,
  messages,
  estimate,
  overdueMessage = 'Still working - this can take a little longer for larger inputs.',
  preview,
  className,
}: AiProgressProps) {
  const isLive = activeKey !== undefined;

  // Deterministic driver (only meaningful when not live).
  const det = useDeterministicStages(stages.length, {
    done,
    active: active && !isLive,
  });

  // Resolve each stage's status from whichever mode is in play.
  const activeIdx = isLive
    ? Math.max(
        0,
        stages.findIndex((s) => s.key === activeKey)
      )
    : det.activeIndex;

  const statusFor = (index: number, key: string): 'pending' | 'active' | 'done' => {
    if (isLive) {
      if (doneKeys?.includes(key)) return 'done';
      if (index < activeIdx) return 'done';
      if (index === activeIdx) return 'active';
      return 'pending';
    }
    if (det.complete) return 'done';
    if (index < activeIdx) return 'done';
    if (index === activeIdx) return 'active';
    return 'pending';
  };

  const activeLabel = stages[activeIdx]?.label ?? '';
  const rotating = useRotatingMessages(messages ?? [], {
    active: active && !det.complete,
  });
  const showOverdue = !isLive && det.overdue && !det.complete;

  return (
    <div className={cn('space-y-4', className)}>
      <div role="status" aria-live="polite" className="sr-only">
        {det.complete ? 'Done.' : activeLabel ? `${activeLabel}.` : 'Working...'}
      </div>

      <ol className="space-y-2.5">
        {stages.map((stage, index) => {
          const status = statusFor(index, stage.key);
          return (
            <li key={stage.key} className="flex items-center gap-2.5 text-sm">
              <StageIcon status={status} />
              <span
                className={
                  status === 'pending'
                    ? 'text-[var(--muted-foreground)]'
                    : 'text-[var(--foreground)]'
                }
              >
                {stage.label}
              </span>
            </li>
          );
        })}
      </ol>

      {(rotating || estimate || showOverdue) && (
        <div className="space-y-1">
          {rotating && (
            <p
              className="text-xs text-[var(--muted-foreground)] motion-safe:transition-opacity"
              aria-hidden
            >
              {rotating}
            </p>
          )}
          {showOverdue ? (
            <p className="text-xs text-[var(--at-warning)]">{overdueMessage}</p>
          ) : (
            estimate && <p className="text-xs text-[var(--muted-foreground)]">{estimate}</p>
          )}
        </div>
      )}

      {preview && <div className="pt-1">{preview}</div>}
    </div>
  );
}

/** A lightweight resume-shaped skeleton for AI loading previews (reusable). */
export function ResumeSkeletonPreview({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        'space-y-3 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4',
        className
      )}
    >
      <div className="space-y-2">
        <div className="h-5 w-1/2 rounded bg-[var(--at-surface-2)] motion-safe:animate-pulse" />
        <div className="h-3 w-2/3 rounded bg-[var(--at-surface-2)] motion-safe:animate-pulse" />
      </div>
      {[0, 1].map((section) => (
        <div key={section} className="space-y-2 pt-2">
          <div className="h-3.5 w-28 rounded bg-[var(--at-surface-2)] motion-safe:animate-pulse" />
          <div className="h-2.5 w-full rounded bg-[var(--at-surface-2)] motion-safe:animate-pulse" />
          <div className="h-2.5 w-11/12 rounded bg-[var(--at-surface-2)] motion-safe:animate-pulse" />
          <div className="h-2.5 w-4/5 rounded bg-[var(--at-surface-2)] motion-safe:animate-pulse" />
        </div>
      ))}
    </div>
  );
}
