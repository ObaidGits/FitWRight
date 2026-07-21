/**
 * Branded 404 - shown for any unmatched route across the app. Renders within
 * the root layout (fonts + providers available). Scoped to `.atelier` so it
 * uses the same design tokens as the rest of the app.
 */
import Link from 'next/link';
import Compass from 'lucide-react/dist/esm/icons/compass';
import { Button } from '@/components/atelier/button';

export default function NotFound() {
  return (
    <div className="atelier flex min-h-screen items-center justify-center bg-[var(--background)] px-6 text-[var(--foreground)]">
      <div className="max-w-md text-center">
        <span className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--secondary)] text-[var(--muted-foreground)]">
          <Compass className="h-7 w-7" />
        </span>
        <p className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          404
        </p>
        <h1 className="mt-1 text-2xl font-semibold">Page not found</h1>
        <p className="mt-2 text-[var(--muted-foreground)]">
          The page you&apos;re looking for doesn&apos;t exist or may have moved.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Button asChild>
            <Link href="/">Back to home</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/connect">Contact the team</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
