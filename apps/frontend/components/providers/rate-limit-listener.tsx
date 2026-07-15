'use client';

/**
 * Registers the global 429 handler (see `lib/api/client.ts`) so any rate-limited
 * request surfaces a single, debounced toast with a retry hint — consistent UX
 * across every feature instead of a generic per-call failure string.
 *
 * Rendered under the ToastProvider. Renders nothing.
 */
import * as React from 'react';
import { setRateLimitHandler } from '@/lib/api/client';
import { rateLimitMessage } from '@/lib/api/errors';
import { useToast } from '@/components/atelier/toast';

export function RateLimitListener() {
  const { toast } = useToast();

  React.useEffect(() => {
    setRateLimitHandler((retryAfterSeconds) => {
      toast({
        title: 'Slow down a moment',
        description: rateLimitMessage(retryAfterSeconds ?? undefined),
        variant: 'error',
      });
    });
    return () => setRateLimitHandler(null);
  }, [toast]);

  return null;
}
