'use client';

/**
 * RecoveryCenter (P4 R5.2, R5.3, R5.5) — a single coherent surface listing
 * quarantined records and queued/failed outbox entries with non-destructive
 * actions (export / discard / retry). Keyboard-navigable and SR-labelled; shown
 * only when there is something to recover.
 */
import * as React from 'react';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import Download from 'lucide-react/dist/esm/icons/download';
import Trash from 'lucide-react/dist/esm/icons/trash-2';
import RotateCw from 'lucide-react/dist/esm/icons/rotate-cw';
import { Button } from '@/components/atelier/button';
import type { OutboxEntry, QuarantineRecord } from '@/lib/resilience/local-store';

function age(ts: number): string {
  const mins = Math.round((Date.now() - ts) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return new Date(ts).toLocaleDateString();
}

export interface RecoveryCenterProps {
  quarantine: QuarantineRecord[];
  outbox: OutboxEntry[];
  onExportQuarantine: (id: string) => void;
  onDiscardQuarantine: (id: string) => void;
  onDiscardOutbox: (id: string) => void;
  onRetrySync: () => void;
  onClose: () => void;
}

export function RecoveryCenter({
  quarantine,
  outbox,
  onExportQuarantine,
  onDiscardQuarantine,
  onDiscardOutbox,
  onRetrySync,
  onClose,
}: RecoveryCenterProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="recovery-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--background)] p-6 shadow-xl outline-none"
      >
        <div className="flex items-center justify-between">
          <h2 id="recovery-title" className="text-lg font-semibold text-[var(--foreground)]">
            Recovery center
          </h2>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close recovery center">
            Close
          </Button>
        </div>

        {/* Pending / failed offline edits */}
        <section aria-labelledby="recovery-outbox" className="mt-4">
          <div className="flex items-center justify-between">
            <h3 id="recovery-outbox" className="text-sm font-medium">
              Queued offline edits ({outbox.length})
            </h3>
            {outbox.length > 0 && (
              <Button size="sm" variant="secondary" onClick={onRetrySync}>
                <RotateCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                Retry sync
              </Button>
            )}
          </div>
          {outbox.length === 0 ? (
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">Nothing queued.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {outbox.map((e) => (
                <li
                  key={e.id}
                  className="flex items-center justify-between gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] px-3 py-2 text-sm"
                >
                  <span className="min-w-0 flex-1 truncate">
                    Resume {e.resumeId} · queued {age(e.createdAt)}
                    {e.attempts > 0 && (
                      <span className="text-[var(--at-warning)]"> · {e.attempts} attempt(s)</span>
                    )}
                    {e.lastError && (
                      <span className="text-[var(--muted-foreground)]"> · {e.lastError}</span>
                    )}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onDiscardOutbox(e.id)}
                    aria-label={`Discard queued edit ${e.id}`}
                  >
                    <Trash className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Quarantined records */}
        <section aria-labelledby="recovery-quarantine" className="mt-6">
          <h3 id="recovery-quarantine" className="text-sm font-medium">
            Quarantined items ({quarantine.length})
          </h3>
          {quarantine.length === 0 ? (
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              No corrupt records — your local data is healthy.
            </p>
          ) : (
            <ul className="mt-2 space-y-2">
              {quarantine.map((q) => (
                <li
                  key={q.id}
                  className="flex items-center justify-between gap-3 rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/5 px-3 py-2 text-sm"
                >
                  <span className="min-w-0 flex-1 truncate">
                    <AlertTriangle
                      className="mr-1 inline h-3.5 w-3.5 text-[var(--at-warning)]"
                      aria-hidden="true"
                    />
                    {q.kind} · {q.reason} · {age(q.quarantinedAt)}
                  </span>
                  <div className="flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onExportQuarantine(q.id)}
                      aria-label={`Export quarantined record ${q.id}`}
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onDiscardQuarantine(q.id)}
                      aria-label={`Discard quarantined record ${q.id}`}
                    >
                      <Trash className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
