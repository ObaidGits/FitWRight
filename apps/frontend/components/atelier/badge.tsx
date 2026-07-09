import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'bg-[var(--secondary)] text-[var(--secondary-foreground)]',
        primary: 'bg-[var(--primary)]/12 text-[var(--primary)]',
        success: 'bg-[var(--at-success)]/15 text-[var(--at-success)]',
        warning: 'bg-[var(--at-warning)]/15 text-[var(--at-warning)]',
        danger: 'bg-[var(--destructive)]/12 text-[var(--destructive)]',
        ai: 'bg-[var(--at-ai-surface)] text-[var(--at-ai)]',
        outline: 'border border-[var(--border)] text-[var(--muted-foreground)]',
      },
    },
    defaultVariants: { variant: 'neutral' },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
