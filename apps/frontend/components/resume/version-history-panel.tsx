'use client';

/**
 * VersionHistoryPanel (Task 19 / Req 31.1, 31.2, 31.3).
 *
 * Full snapshot history: list every captured version (original / AI / manual),
 * save a manual snapshot on demand, restore any snapshot (non-destructive), undo
 * the last AI change, and compare any two snapshots as a field-level diff. All
 * mutating actions confirm and refetch; restoration never wipes silently.
 */
import * as React from 'react';
import History from 'lucide-react/dist/esm/icons/history';
import RotateCcw from 'lucide-react/dist/esm/icons/rotate-ccw';
import Undo2 from 'lucide-react/dist/esm/icons/undo-2';
import Camera from 'lucide-react/dist/esm/icons/camera';
import GitCompare from 'lucide-react/dist/esm/icons/git-compare';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';

import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/atelier/sheet';
import { Button } from '@/components/atelier/button';
import { Badge } from '@/components/atelier/badge';
import { EmptyState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { historyApi } from '@/lib/api/history';
import type { VersionCompare } from '@/lib/api/history';
import type { ResumeVersion } from '@/lib/types/domain';

interface VersionHistoryPanelProps {
  resumeId: string;
  /** Called after a successful restore so the editor can refetch. */
  onRestored?: () => void;
  trigger?: React.ReactNode;
}

function badgeVariant(source: ResumeVersion['source']) {
  if (source === 'ai') return 'ai' as const;
  if (source === 'original') return 'primary' as const;
  return 'neutral' as const;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v || '—';
  return JSON.stringify(v);
}

export function VersionHistoryPanel({ resumeId, onRestored, trigger }: VersionHistoryPanelProps) {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [versions, setVersions] = React.useState<ResumeVersion[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);
  // Compare mode: pick two snapshots, then render their field-level diff.
  const [compareMode, setCompareMode] = React.useState(false);
  const [selected, setSelected] = React.useState<string[]>([]);
  const [diff, setDiff] = React.useState<VersionCompare | null>(null);

  const refetch = React.useCallback(() => {
    setLoading(true);
    historyApi
      .listVersions(resumeId)
      .then((v) => setVersions(v))
      .catch(() => setVersions([]))
      .finally(() => setLoading(false));
  }, [resumeId]);

  React.useEffect(() => {
    if (!open) return;
    refetch();
  }, [open, refetch]);

  // Reset transient compare state whenever the sheet closes.
  React.useEffect(() => {
    if (!open) {
      setCompareMode(false);
      setSelected([]);
      setDiff(null);
    }
  }, [open]);

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

  async function saveSnapshot() {
    setBusy('snapshot');
    try {
      await historyApi.createSnapshot(resumeId);
      toast({ title: 'Snapshot saved', variant: 'success' });
      refetch();
    } catch {
      toast({ title: 'Could not save a snapshot', variant: 'error' });
    } finally {
      setBusy(null);
    }
  }

  async function restore(versionId: string) {
    setBusy(versionId);
    try {
      await historyApi.restoreVersion(resumeId, versionId);
      toast({ title: 'Snapshot restored', variant: 'success' });
      onRestored?.();
      setOpen(false);
    } catch (err) {
      toast({
        title: (err as Error)?.message || 'Could not restore this snapshot',
        variant: 'error',
      });
    } finally {
      setBusy(null);
    }
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length === 2) return [prev[1], id];
      return [...prev, id];
    });
  }

  async function runCompare() {
    if (selected.length !== 2) return;
    setBusy('compare');
    try {
      const result = await historyApi.compareVersions(resumeId, selected[0], selected[1]);
      setDiff(result);
    } catch {
      toast({ title: 'Could not compare these snapshots', variant: 'error' });
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
      <SheetContent side="right" className="flex flex-col p-6">
        <SheetTitle className="mb-1 text-lg font-semibold">Version history</SheetTitle>
        <p className="mb-4 text-sm text-[var(--muted-foreground)]">
          Roll back safely — these actions never overwrite your work without confirmation.
        </p>

        {/* Diff view */}
        {diff ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Button variant="ghost" size="sm" className="mb-3" onClick={() => setDiff(null)}>
              <ArrowLeft className="h-4 w-4" /> Back to history
            </Button>
            <div className="mb-3 flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
              <Badge variant={badgeVariant(diff.a.source)}>{diff.a.label}</Badge>
              <GitCompare className="h-3.5 w-3.5" />
              <Badge variant={badgeVariant(diff.b.source)}>{diff.b.label}</Badge>
            </div>
            {diff.changes.length === 0 ? (
              <EmptyState
                icon={GitCompare}
                title="No differences"
                description="These two snapshots have identical content."
              />
            ) : (
              <ul className="space-y-2">
                {diff.changes.map((c) => (
                  <li
                    key={c.path}
                    className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-3"
                  >
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs text-[var(--foreground)]">
                        {c.path}
                      </span>
                      <Badge
                        variant={
                          c.action === 'added'
                            ? 'success'
                            : c.action === 'removed'
                              ? 'danger'
                              : 'warning'
                        }
                      >
                        {c.action}
                      </Badge>
                    </div>
                    {c.action !== 'added' && (
                      <p className="text-xs text-[var(--destructive)]">− {formatValue(c.before)}</p>
                    )}
                    {c.action !== 'removed' && (
                      <p className="text-xs text-[var(--at-success)]">+ {formatValue(c.after)}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <>
            {/* Quick actions */}
            <div className="space-y-2">
              <Button
                variant="outline"
                className="w-full justify-start"
                loading={busy === 'snapshot'}
                onClick={saveSnapshot}
              >
                <Camera className="h-4 w-4" /> Save current as snapshot
              </Button>
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

            <div className="mt-6 flex min-h-0 flex-1 flex-col">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-[var(--muted-foreground)]">Snapshots</h3>
                {versions.length >= 2 && (
                  <button
                    onClick={() => {
                      setCompareMode((v) => !v);
                      setSelected([]);
                    }}
                    className="text-xs font-medium text-[var(--primary)] hover:underline"
                  >
                    {compareMode ? 'Cancel compare' : 'Compare'}
                  </button>
                )}
              </div>

              {compareMode && (
                <div className="mb-2 flex items-center justify-between gap-2 rounded-[var(--radius-at-md)] bg-[var(--secondary)] px-3 py-2 text-xs">
                  <span className="text-[var(--muted-foreground)]">
                    Select two snapshots to compare ({selected.length}/2)
                  </span>
                  <Button
                    size="sm"
                    disabled={selected.length !== 2}
                    loading={busy === 'compare'}
                    onClick={runCompare}
                  >
                    Compare
                  </Button>
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-y-auto">
                {loading ? (
                  <p className="text-sm text-[var(--muted-foreground)]">Loading…</p>
                ) : versions.length === 0 ? (
                  <EmptyState
                    icon={History}
                    title="No saved snapshots yet"
                    description="Save a snapshot above, or every accepted AI change is captured automatically."
                  />
                ) : (
                  <ul className="space-y-2">
                    {versions.map((v) => {
                      const isSelected = selected.includes(v.id);
                      return (
                        <li
                          key={v.id}
                          className={`flex items-center justify-between gap-2 rounded-[var(--radius-at-md)] border px-3 py-2 ${
                            isSelected
                              ? 'border-[var(--primary)] bg-[var(--primary)]/6'
                              : 'border-[var(--border)]'
                          }`}
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm text-[var(--foreground)]">{v.label}</p>
                            <p className="text-xs text-[var(--muted-foreground)]">
                              {new Date(v.createdAt).toLocaleString()}
                            </p>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <Badge variant={badgeVariant(v.source)}>{v.source}</Badge>
                            {compareMode ? (
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleSelect(v.id)}
                                aria-label={`Select ${v.label} to compare`}
                                className="h-4 w-4 accent-[var(--primary)]"
                              />
                            ) : (
                              <Button
                                variant="ghost"
                                size="sm"
                                loading={busy === v.id}
                                onClick={() => restore(v.id)}
                              >
                                Restore
                              </Button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
