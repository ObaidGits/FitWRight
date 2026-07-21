'use client';

/**
 * Segment error boundary for the authenticated app. Catches render/data errors
 * in any (app) route so a single failing page never blanks the whole shell with
 * an unbranded Next.js error screen. Offers retry + a route home.
 */
import * as React from 'react';
import Link from 'next/link';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import { Button } from '@/components/atelier/button';

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    console.error('App route error boundary:', error);
  }, [error]);

  return (
    <div className="atelier flex min-h-[60vh] items-center justify-center px-6">
      <div className="max-w-md text-center">
        <span className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--destructive)]/12 text-[var(--destructive)]">
          <AlertTriangle className="h-7 w-7" />
        </span>
        <h1 className="text-2xl font-semibold">Something went wrong</h1>
        <p className="mt-2 text-[var(--muted-foreground)]">
          This page hit an unexpected error. Try again, or return home - your saved work is safe.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Button onClick={() => reset()}>Try again</Button>
          <Button asChild variant="outline">
            <Link href="/home">Back to home</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
