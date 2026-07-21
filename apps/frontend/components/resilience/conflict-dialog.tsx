'use client';

/**
 * ConflictDialog (P4 R3.2, R3.6, R6.5).
 *
 * Shown when a version-CAS write is rejected (409). Presents a readable
 * field-level diff (mine vs latest) and three explicit, keyboard-navigable
 * choices: **keep mine** (re-base + fresh write), **take latest** (adopt
 * server), and **field-merge** (only offered when the changed fields are
 * disjoint). Never a silent overwrite. Focus is trapped and the dialog is
 * labelled for screen readers; motion honors reduced-motion via the token layer.
 */
import * as React from 'react';
import { Button } from '@/components/atelier/button';
import { computeConflictDiff, fieldMerge, type ConflictDiff } from '@/lib/resilience/diff';

export interface ConflictDialogProps {
  /** Local edit (mine). */
  mine: Record<string, unknown>;
  /** Current server state (latest). */
  latest: Record<string, unknown>;
  /** Common base if known (defaults to `latest` so all local changes surface). */
  base?: Record<string, unknown>;
  currentVersion: number;
  onKeepMine: () => void;
  onTakeLatest: () => void;
  onMerge: (merged: Record<string, unknown>) => void;
  onDismiss: () => void;
}

function preview(value: unknown): string {
  if (value == null) return '-';
  if (typeof value === 'string') return value.length > 120 ? `${value.slice(0, 117)}...` : value;
  try {
    const s = JSON.stringify(value);
    return s.length > 120 ? `${s.slice(0, 117)}...` : s;
  } catch {
    return String(value);
  }
}

export function ConflictDialog({
  mine,
  latest,
  base,
  currentVersion,
  onKeepMine,
  onTakeLatest,
  onMerge,
  onDismiss,
}: ConflictDialogProps) {
  const diff: ConflictDiff = React.useMemo(
    () => computeConflictDiff(base ?? latest, mine, latest),
    [base, mine, latest]
  );
  const dialogRef = React.useRef<HTMLDivElement>(null);

  // Focus the dialog on open + trap Escape -> dismiss (keyboard accessible).
  React.useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onDismiss();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onDismiss]);

  const changedFields = React.useMemo(() => {
    const set = new Set<string>([
      ...diff.mineChanged.map((c) => c.field),
      ...diff.latestChanged.map((c) => c.field),
    ]);
    return [...set];
  }, [diff]);

  const mineByField = React.useMemo(
    () => new Map(diff.mineChanged.map((c) => [c.field, c])),
    [diff]
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-title"
      aria-describedby="conflict-desc"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--background)] p-6 shadow-xl outline-none"
      >
        <h2 id="conflict-title" className="text-lg font-semibold text-[var(--foreground)]">
          This resume changed elsewhere
        </h2>
        <p id="conflict-desc" className="mt-1 text-sm text-[var(--muted-foreground)]">
          Another tab or device saved a newer version (v{currentVersion}). Choose how to resolve -
          your work is never discarded without your say-so.
        </p>

        <div className="mt-4 rounded-[var(--radius-at-md)] border border-[var(--border)]">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">
              Field-by-field comparison of your changes versus the latest server version
            </caption>
            <thead>
              <tr className="border-b border-[var(--border)] text-left">
                <th scope="col" className="px-3 py-2 font-medium">
                  Field
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Yours
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Latest
                </th>
              </tr>
            </thead>
            <tbody>
              {changedFields.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-3 py-3 text-[var(--muted-foreground)]">
                    No field-level differences detected.
                  </td>
                </tr>
              )}
              {changedFields.map((field) => {
                const overlapping = diff.overlapping.includes(field);
                return (
                  <tr key={field} className="border-b border-[var(--border)] last:border-0">
                    <th scope="row" className="px-3 py-2 text-left align-top font-medium">
                      {field}
                      {overlapping && (
                        <span className="ml-1 text-xs text-[var(--at-warning)]">
                          (both changed)
                        </span>
                      )}
                    </th>
                    <td className="px-3 py-2 align-top">{preview(mine[field])}</td>
                    <td className="px-3 py-2 align-top">{preview(latest[field])}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" onClick={onDismiss} aria-label="Dismiss and decide later">
            Decide later
          </Button>
          <Button
            variant="ghost"
            onClick={onTakeLatest}
            aria-label="Discard my changes and take the latest server version"
          >
            Take latest
          </Button>
          {diff.mergeable && (
            <Button
              variant="secondary"
              onClick={() => onMerge(fieldMerge(latest, [...mineByField.values()]))}
              aria-label="Merge my changes with the latest version (non-overlapping fields)"
            >
              Merge both
            </Button>
          )}
          <Button
            onClick={onKeepMine}
            aria-label="Keep my changes, re-based onto the latest version"
          >
            Keep mine
          </Button>
        </div>
      </div>
    </div>
  );
}
