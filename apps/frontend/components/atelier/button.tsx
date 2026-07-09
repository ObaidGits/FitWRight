'use client';

/**
 * Atelier Button (Task 2.2)
 * Warm, rounded, soft-elevation button on Atelier tokens. One accent per region.
 * State matrix: default · hover · active · focus-visible · disabled · loading.
 */

import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  cn(
    'relative inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'rounded-[var(--radius-at-md)] text-sm font-medium select-none',
    'transition-[background-color,color,box-shadow,transform] duration-[var(--duration-at-base)] ease-[var(--ease-at-out)]',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]',
    'disabled:pointer-events-none disabled:opacity-50',
    "[&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 [&_svg]:shrink-0"
  ),
  {
    variants: {
      variant: {
        primary: cn(
          'bg-[var(--primary)] text-[var(--primary-foreground)] shadow-[var(--shadow-at-e1)]',
          'hover:brightness-110 active:brightness-95'
        ),
        secondary: cn(
          'bg-[var(--secondary)] text-[var(--secondary-foreground)]',
          'hover:bg-[var(--accent)] active:brightness-95'
        ),
        outline: cn(
          'border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]',
          'hover:bg-[var(--accent)] active:brightness-95'
        ),
        ghost: 'text-[var(--foreground)] hover:bg-[var(--accent)]',
        destructive: cn(
          'bg-[var(--destructive)] text-[var(--destructive-foreground)] shadow-[var(--shadow-at-e1)]',
          'hover:brightness-110 active:brightness-95'
        ),
        link: 'text-[var(--primary)] underline-offset-4 hover:underline p-0 h-auto',
        ai: cn(
          'bg-[var(--at-ai)] text-white shadow-[var(--shadow-at-e1)]',
          'hover:brightness-110 active:brightness-95'
        ),
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-10 px-4',
        lg: 'h-11 px-6 text-base',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant, size, asChild = false, loading = false, children, disabled, ...props },
    ref
  ) => {
    const classes = cn(buttonVariants({ variant, size }), className);

    // When asChild, Radix Slot requires exactly ONE child element — do not
    // inject a loading spinner (the consumer's element is passed through).
    if (asChild) {
      return (
        <Slot ref={ref} className={classes} {...props}>
          {children}
        </Slot>
      );
    }

    return (
      <button
        ref={ref}
        className={classes}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
        {children}
      </button>
    );
  }
);
Button.displayName = 'AtelierButton';

export { buttonVariants };
