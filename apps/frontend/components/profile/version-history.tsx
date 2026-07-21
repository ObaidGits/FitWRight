'use client';

/**
 * Profile version history (P5) - a timeline of immutable snapshots with restore.
 *
 * Snapshots are captured automatically on every write (manual/import/merge/ai)
 * plus the initial ``migration`` baseline. Restore is non-destructive: it applies
 * the chosen snapshot as a fresh write (itself snapshotted), so history only ever
 * grows and any state is recoverable. Metadata-only list; nothing heavy loads
 * until the panel opens.
 */
import * as React from 'react';
import History from 'lucide-react/dist/esm/icons/history';
import RotateCcw from 'lucide-react/dist/esm/icons/rotate-ccw';

import { Button } from '@/components/atelier/button';
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from '@/components/atelier/sheet';
import { Badge } from '@/components/atelier/badge';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useProfileVersions, useRestoreProfileVersion } from '@/features/profile/hooks';

const SOURCE_LABELS: Record<string, string> = {
  migration: 'Initial',
  manual: 'Edit',
  import: 'Import',
  merge: 'Merge',
  ai: 'AI',
};

function formatWhen(iso: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
      new Date(iso)
    );
  } catch {
    return iso;
  }
}

export function VersionHistory() {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const versionsQuery = useProfileVersions();
  const restore = useRestoreProfileVersion();

  async function onRestore(id: string) {
    try {
      await restore.mutateAsync(id);
      toast({ title: 'Version restored', variant: 'success' });
      setOpen(false);
    } catch (err) {
      toast({
        title: 'Could not restore',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  const items = versionsQuery.data?.items ?? [];

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost">
          <History className="h-4 w-4" /> History
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="flex w-full max-w-md flex-col overflow-y-auto p-6">
        <SheetTitle className="text-lg font-semibold">Version history</SheetTitle>
        <p className="mb-4 mt-1 text-sm text-[var(--muted-foreground)]">
          Every change is snapshotted. Restore any point without losing later versions.
        </p>

        {versionsQuery.isLoading ? (
          <LoadingSkeleton rows={4} />
        ) : versionsQuery.isError ? (
          <ErrorState
            description="Could not load history."
            onRetry={() => versionsQuery.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState icon={History} title="No history yet" description="Edits will appear here." />
        ) : (
          <ol className="space-y-2" aria-label="Profile versions">
            {items.map((v, idx) => (
              <li
                key={v.id}
                className="flex items-center gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Badge variant={idx === 0 ? 'primary' : 'neutral'}>
                      {SOURCE_LABELS[v.source] ?? v.source}
                    </Badge>
                    {v.label && (
                      <span className="truncate text-sm text-[var(--foreground)]">{v.label}</span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                    {formatWhen(v.created_at)}
                    {idx === 0 && ' - latest'}
                  </p>
                </div>
                {idx !== 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onRestore(v.id)}
                    loading={restore.isPending}
                    aria-label={`Restore version from ${formatWhen(v.created_at)}`}
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> Restore
                  </Button>
                )}
              </li>
            ))}
          </ol>
        )}

        <div className="mt-6 flex justify-end">
          <SheetClose asChild>
            <Button variant="ghost">Close</Button>
          </SheetClose>
        </div>
      </SheetContent>
    </Sheet>
  );
}
