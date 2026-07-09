'use client';

/** Reusable Empty / Loading / Error states (Task 2.3). Empty states teach the next action. */
import * as React from 'react';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import { cn } from '@/lib/utils';
import { Button } from '@/components/atelier/button';
import { Skeleton } from '@/components/atelier/skeleton';

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-[var(--radius-at-lg)] border border-dashed border-[var(--border)] p-10 text-center',
        className
      )}
    >
      {Icon && (
        <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[var(--secondary)] text-[var(--muted-foreground)]">
          <Icon className="h-5 w-5" />
        </span>
      )}
      <div className="space-y-1">
        <h3 className="text-base font-semibold text-[var(--foreground)]">{title}</h3>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-[var(--muted-foreground)]">{description}</p>
        )}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function LoadingSkeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-3', className)} role="status" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

export function ErrorState({
  title = 'Something went wrong',
  description,
  onRetry,
  className,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-8 text-center',
        className
      )}
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[var(--destructive)]/12 text-[var(--destructive)]">
        <AlertTriangle className="h-5 w-5" />
      </span>
      <div className="space-y-1">
        <h3 className="text-base font-semibold text-[var(--foreground)]">{title}</h3>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-[var(--muted-foreground)]">{description}</p>
        )}
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
