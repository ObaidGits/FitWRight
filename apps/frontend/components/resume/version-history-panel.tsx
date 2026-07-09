'use client';

/**
 * VersionHistoryPanel (Task 19 / Req 31.1, 31.2, 31.3).
 *
 * Reads from the typed `history` interface so real snapshots can replace the
 * stub with no UI change. Offers the two available-data operations today —
 * "restore original parsed resume" and "undo last AI generation" — behind an
 * explicit confirm; restoration is non-destructive (it re-derives, never wipes
 * silently). Full snapshot list/compare renders when the backend provides it.
 */
import * as React from 'react';
import History from 'lucide-react/dist/esm/icons/history';
import RotateCcw from 'lucide-react/dist/esm/icons/rotate-ccw';
import Undo2 from 'lucide-react/dist/esm/icons/undo-2';

import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/atelier/sheet';
import { Button } from '@/components/atelier/button';
import { Badge } from '@/components/atelier/badge';
import { EmptyState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { historyApi } from '@/lib/api/history';
import type { ResumeVersion } from '@/lib/types/domain';

interface VersionHistoryPanelProps {
  resumeId: string;
  /** Called after a successful restore so the editor can refetch. */
  onRestored?: () => void;
  trigger?: React.ReactNode;
}

export function VersionHistoryPanel({ resumeId, onRestored, trigger }: VersionHistoryPanelProps) {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [versions, setVersions] = React.useState<ResumeVersion[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [busy, setBusy] = React.useState<'original' | 'undo' | null>(null);

  React.useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    historyApi
      .listVersions(resumeId)
      .then((v) => active && setVersions(v))
      .catch(() => active && setVersions([]))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [open, resumeId]);

  async function restoreOriginal() {
    setBusy('original');
    try {
      await historyApi.restoreOriginal(resumeId);
      toast({ title: 'Restored the original parsed resume', variant: 'success' });
      onRestored?.();
      setOpen(false);
    } catch {
      toast({ title: 'Could not restore the original', variant: 'error' });
    } finally {
      setBusy(null);
    }
  }

  async function undoLastAi() {
    setBusy('undo');
    try {
      await historyApi.undoLastAi(resumeId);
      toast({ title: 'Reverted the last AI generation', variant: 'success' });
      onRestored?.();
      setOpen(false);
    } catch {
      toast({ title: 'Could not undo the last AI change', variant: 'error' });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {trigger ?? (
          <Button variant="outline" size="sm">
            <History className="h-4 w-4" /> History
          </Button>
        )}
      </SheetTrigger>
      <SheetContent side="right" className="p-6">
        <SheetTitle className="mb-1 text-lg font-semibold">Version history</SheetTitle>
        <p className="mb-4 text-sm text-[var(--muted-foreground)]">
          Roll back safely — these actions never overwrite your work without confirmation.
        </p>

        <div className="space-y-2">
          <Button
            variant="outline"
            className="w-full justify-start"
            loading={busy === 'original'}
            onClick={restoreOriginal}
          >
            <RotateCcw className="h-4 w-4" /> Restore original parsed resume
          </Button>
          <Button
            variant="outline"
            className="w-full justify-start"
            loading={busy === 'undo'}
            onClick={undoLastAi}
          >
            <Undo2 className="h-4 w-4" /> Undo last AI generation
          </Button>
        </div>

        <div className="mt-6">
          <h3 className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">Snapshots</h3>
          {loading ? (
            <p className="text-sm text-[var(--muted-foreground)]">Loading…</p>
          ) : versions.length === 0 ? (
            <EmptyState
              icon={History}
              title="No saved snapshots yet"
              description="Every AI change can be reverted with the actions above. Full snapshot history is coming soon."
            />
          ) : (
            <ul className="space-y-2">
              {versions.map((v) => (
                <li
                  key={v.id}
                  className="flex items-center justify-between rounded-[var(--radius-at-md)] border border-[var(--border)] px-3 py-2"
                >
                  <div>
                    <p className="text-sm text-[var(--foreground)]">{v.label}</p>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      {new Date(v.createdAt).toLocaleString()}
                    </p>
                  </div>
                  <Badge variant={v.source === 'ai' ? 'ai' : 'neutral'}>{v.source}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
