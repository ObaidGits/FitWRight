import * as React from 'react';
import { cn } from '@/lib/utils';

/** Skeleton - matches final layout structure; animation respects reduced-motion. */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)]',
        className
      )}
      aria-hidden
      {...props}
    />
  );
}
