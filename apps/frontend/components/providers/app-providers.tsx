'use client';

/**
 * Combined client providers (Task 3.5/3.6). Order: Query → Theme → Session →
 * Toast → Tooltip → CommandPalette. Wrapped once near the root so both new and
 * legacy routes get theme + data layer; new (app) routes additionally opt into
 * `.atelier` styling via their layout.
 */
import * as React from 'react';
import { QueryProvider } from '@/lib/query/client';
import { ThemeProvider } from '@/components/theme/theme-provider';
import { SessionProvider } from '@/lib/context/session';
import { ToastProvider } from '@/components/atelier/toast';
import { TooltipProvider } from '@/components/atelier/misc';
import { CommandPaletteProvider } from '@/components/command/command-palette';
import { StepUpProvider } from '@/components/auth/step-up-modal';
import { RateLimitListener } from '@/components/providers/rate-limit-listener';
import type { SafeUser } from '@/lib/api/auth';

export function AppProviders({
  children,
  initialUser = null,
  initialSessionResolved = false,
}: {
  children: React.ReactNode;
  initialUser?: SafeUser | null;
  initialSessionResolved?: boolean;
}) {
  return (
    <QueryProvider>
      <ThemeProvider>
        <SessionProvider initialUser={initialUser} initialResolved={initialSessionResolved}>
          <ToastProvider>
            <RateLimitListener />
            <TooltipProvider delayDuration={200}>
              <StepUpProvider>
                <CommandPaletteProvider>{children}</CommandPaletteProvider>
              </StepUpProvider>
            </TooltipProvider>
          </ToastProvider>
        </SessionProvider>
      </ThemeProvider>
    </QueryProvider>
  );
}
