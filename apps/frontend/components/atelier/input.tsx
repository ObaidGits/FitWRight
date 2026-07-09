import * as React from 'react';
import { cn } from '@/lib/utils';

const fieldBase = cn(
  'w-full rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]',
  'placeholder:text-[var(--muted-foreground)] text-sm',
  'transition-[border-color,box-shadow] duration-[var(--duration-at-base)]',
  'focus-visible:outline-none focus-visible:border-[var(--ring)] focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20',
  'disabled:cursor-not-allowed disabled:opacity-50',
  'aria-[invalid=true]:border-[var(--destructive)] aria-[invalid=true]:ring-[var(--destructive)]/20'
);

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, type = 'text', ...props }, ref) => (
  <input ref={ref} type={type} className={cn(fieldBase, 'h-10 px-3', className)} {...props} />
));
Input.displayName = 'AtelierInput';

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(fieldBase, 'min-h-24 px-3 py-2 resize-y', className)}
    {...props}
  />
));
Textarea.displayName = 'AtelierTextarea';
