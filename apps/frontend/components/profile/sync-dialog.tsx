'use client';

/**
 * Sync dialog (P4) — refresh an existing draft resume from the profile.
 *
 * Pick a resume → preview the field-level diff between its current data and a
 * fresh projection of the profile → apply (resume version CAS). Submitted
 * resumes are surfaced as locked/immutable and cannot be changed (the record of
 * what was sent stays truthful); the user generates a new resume instead.
 */
import * as React from 'react';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import Lock from 'lucide-react/dist/esm/icons/lock';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';

import { Button } from '@/components/atelier/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from '@/components/atelier/dialog';
import { Badge } from '@/components/atelier/badge';
import { EmptyState, LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useApplySync, usePreviewSync } from '@/features/profile/hooks';
import { useResumes } from '@/features/home/hooks';
import type { SyncPreviewResponse } from '@/lib/api/professional-profile';

const ACTION_VARIANT: Record<string, 'success' | 'danger' | 'primary'> = {
  added: 'success',
  removed: 'danger',
  changed: 'primary',
};

function summarize(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'string') return value || '—';
  return JSON.stringify(value).slice(0, 60);
}

export function SyncDialog() {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [resumeId, setResumeId] = React.useState('');
  const [preview, setPreview] = React.useState<SyncPreviewResponse | null>(null);

  const resumesQuery = useResumes();
  const previewMutation = usePreviewSync();
  const applyMutation = useApplySync();

  const resumes = (resumesQuery.data ?? []).filter((r) => r.processing_status === 'ready');

  function reset() {
    setPreview(null);
    setResumeId('');
  }

  async function onPreview(id: string) {
    setResumeId(id);
    try {
      const result = await previewMutation.mutateAsync({ resumeId: id });
      setPreview(result);
    } catch (err) {
      toast({
        title: 'Could not preview sync',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onApply() {
    if (!preview) return;
    try {
      await applyMutation.mutateAsync({ resumeId, baseVersion: preview.resume_version });
      toast({ title: 'Resume synced', variant: 'success' });
      setOpen(false);
      reset();
    } catch (err) {
      toast({
        title: 'Could not sync',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="ghost">
          <RefreshCw className="h-4 w-4" /> Sync resume
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Sync a resume from your profile</DialogTitle>
          <DialogDescription>
            Refresh a draft resume with your latest profile. Submitted resumes are locked to keep
            your history accurate.
          </DialogDescription>
        </DialogHeader>

        {!preview ? (
          <div className="max-h-[55vh] space-y-2 overflow-y-auto">
            {resumesQuery.isLoading ? (
              <LoadingSkeleton rows={3} />
            ) : resumes.length === 0 ? (
              <EmptyState
                icon={RefreshCw}
                title="No resumes to sync"
                description="Generate a resume from your profile first."
              />
            ) : (
              resumes.map((r) => (
                <button
                  key={r.resume_id}
                  onClick={() => onPreview(r.resume_id)}
                  disabled={previewMutation.isPending}
                  className="flex w-full items-center justify-between rounded-[var(--radius-at-md)] border border-[var(--border)] p-3 text-left text-sm transition-colors hover:bg-[var(--accent)] disabled:opacity-60"
                >
                  <span className="truncate">{r.title || r.filename || 'Untitled resume'}</span>
                  <ArrowRight className="h-4 w-4 text-[var(--muted-foreground)]" />
                </button>
              ))
            )}
          </div>
        ) : preview.immutable ? (
          <EmptyState
            icon={Lock}
            title="This resume is locked"
            description={preview.reason ?? 'It was submitted with an application and is immutable.'}
          />
        ) : preview.changes.length === 0 ? (
          <EmptyState
            icon={RefreshCw}
            title="Already up to date"
            description="This resume already matches your profile."
          />
        ) : (
          <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            {preview.changes.map((c, i) => (
              <div
                key={`${c.path}-${i}`}
                className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-3 text-xs"
              >
                <div className="flex items-center gap-2">
                  <Badge variant={ACTION_VARIANT[c.action] ?? 'neutral'}>{c.action}</Badge>
                  <span className="truncate font-mono text-[var(--muted-foreground)]">
                    {c.path}
                  </span>
                </div>
                <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
                  <p className="text-[var(--muted-foreground)]">
                    <span className="mr-1 font-medium">Was:</span>
                    {summarize(c.before)}
                  </p>
                  <p className="text-[var(--foreground)]">
                    <span className="mr-1 font-medium">Now:</span>
                    {summarize(c.after)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}

        {preview && !preview.immutable && preview.changes.length > 0 && (
          <DialogFooter>
            <Button variant="ghost" onClick={reset}>
              Back
            </Button>
            <Button onClick={onApply} loading={applyMutation.isPending}>
              Apply to resume
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
