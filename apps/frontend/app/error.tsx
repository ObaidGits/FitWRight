'use client';

/**
 * Root segment error boundary - covers routes outside the (app) group (auth,
 * admin, marketing). The (app) group has its own boundary with shell-aware
 * copy; the global-error.tsx handles failures in the root layout itself.
 */
import * as React from 'react';
import Link from 'next/link';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import { Button } from '@/components/atelier/button';

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    console.error('Root error boundary:', error);
  }, [error]);

  return (
    <div className="atelier flex min-h-screen items-center justify-center bg-[var(--background)] px-6 text-[var(--foreground)]">
      <div className="max-w-md text-center">
        <span className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--destructive)]/12 text-[var(--destructive)]">
          <AlertTriangle className="h-7 w-7" />
        </span>
        <h1 className="text-2xl font-semibold">Something went wrong</h1>
        <p className="mt-2 text-[var(--muted-foreground)]">
          An unexpected error occurred. Try again, or head back to get started.
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
