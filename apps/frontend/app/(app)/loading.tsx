/**
 * Route-level loading fallback for the authenticated app. Gives an instant
 * skeleton on navigation between (app) routes instead of a blank content area
 * while the destination's data/components stream in.
 */
import { LoadingSkeleton } from '@/components/atelier/states';

export default function AppLoading() {
  return (
    <div className="space-y-6">
      <div className="h-9 w-56 animate-pulse rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)]" />
      <LoadingSkeleton rows={4} />
    </div>
  );
}
