'use client';

/**
 * Password input with a reveal toggle + caps-lock hint (Task 8.3 / R15.1).
 * Password-manager `autocomplete` is set by the caller.
 */
import * as React from 'react';
import Eye from 'lucide-react/dist/esm/icons/eye';
import EyeOff from 'lucide-react/dist/esm/icons/eye-off';
import { Input } from '@/components/atelier/input';
import { cn } from '@/lib/utils';

interface PasswordFieldProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  id: string;
}

export const PasswordField = React.forwardRef<HTMLInputElement, PasswordFieldProps>(
  ({ id, className, onKeyUp, onKeyDown, ...props }, ref) => {
    const [reveal, setReveal] = React.useState(false);
    const [capsLock, setCapsLock] = React.useState(false);

    const trackCaps = (e: React.KeyboardEvent<HTMLInputElement>) => {
      // getModifierState is available on keyboard events in every modern browser.
      if (typeof e.getModifierState === 'function') {
        setCapsLock(e.getModifierState('CapsLock'));
      }
    };

    return (
      <div className="space-y-1.5">
        <div className="relative">
          <Input
            id={id}
            ref={ref}
            type={reveal ? 'text' : 'password'}
            className={cn('pr-10', className)}
            onKeyUp={(e) => {
              trackCaps(e);
              onKeyUp?.(e);
            }}
            onKeyDown={(e) => {
              trackCaps(e);
              onKeyDown?.(e);
            }}
            {...props}
          />
          <button
            type="button"
            onClick={() => setReveal((v) => !v)}
            aria-label={reveal ? 'Hide password' : 'Show password'}
            aria-pressed={reveal}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded-[var(--radius-at-sm)] p-1 text-[var(--muted-foreground)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          >
            {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        {capsLock && (
          <p role="status" className="text-xs text-[var(--muted-foreground)]">
            Caps Lock is on.
          </p>
        )}
      </div>
    );
  }
);
PasswordField.displayName = 'PasswordField';
