'use client';

/**
 * Import & Merge dialog (P3) — review before anything touches the profile.
 *
 * Flow: pick a source resume → preview (the Merge Engine returns a typed plan) →
 * review each operation side-by-side (existing vs. incoming) and choose a
 * resolution → apply. Defaults are non-destructive (conflicts keep existing),
 * so hitting "Apply" without touching anything can never clobber manual data.
 * Duplicates are collapsed automatically; only meaningful decisions surface.
 */
import * as React from 'react';
import Download from 'lucide-react/dist/esm/icons/download';
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
import { useApplyImport, usePreviewImport } from '@/features/profile/hooks';
import type {
  ImportStatistics,
  MergeOperation,
  MergePlan,
  ProfileData,
} from '@/lib/api/professional-profile';
import { useResumes } from '@/features/home/hooks';

const OP_BADGE: Record<
  string,
  { label: string; variant: 'primary' | 'success' | 'warning' | 'neutral' }
> = {
  add: { label: 'New', variant: 'success' },
  update: { label: 'Update', variant: 'primary' },
  conflict: { label: 'Conflict', variant: 'warning' },
  duplicate: { label: 'Duplicate', variant: 'neutral' },
};

const RESOLUTION_LABELS: Record<string, string> = {
  accept: 'Add',
  reject: 'Skip',
  keep_existing: 'Keep mine',
  replace: 'Replace',
  merge: 'Merge',
};

function preview(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'string') return value || '—';
  if (Array.isArray(value))
    return value.map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(' · ');
  if (typeof value === 'object') {
    const o = value as Record<string, unknown>;
    return (
      (o.title as string) ||
      (o.name as string) ||
      (o.company as string) ||
      (o.displayName as string) ||
      JSON.stringify(o).slice(0, 80)
    );
  }
  return String(value);
}

export function ImportDialog({ baseVersion }: { baseVersion: number }) {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [resumeId, setResumeId] = React.useState<string>('');
  const [plan, setPlan] = React.useState<MergePlan | null>(null);
  const [incoming, setIncoming] = React.useState<ProfileData | null>(null);
  const [stats, setStats] = React.useState<ImportStatistics | null>(null);
  const [warnings, setWarnings] = React.useState<string[]>([]);
  const [resolutions, setResolutions] = React.useState<Record<string, string>>({});

  const resumesQuery = useResumes();
  const previewMutation = usePreviewImport();
  const applyMutation = useApplyImport();

  const resumes = (resumesQuery.data ?? []).filter((r) => r.processing_status === 'ready');

  function reset() {
    setPlan(null);
    setIncoming(null);
    setStats(null);
    setWarnings([]);
    setResolutions({});
    setResumeId('');
  }

  async function onPreview(id: string) {
    setResumeId(id);
    try {
      const result = await previewMutation.mutateAsync({
        source: 'resume',
        payload: { resume_id: id },
      });
      setPlan(result.plan);
      setIncoming(result.incoming);
      setStats(result.statistics ?? null);
      setWarnings(result.warnings ?? []);
      // Seed resolutions with the safe defaults.
      const seed: Record<string, string> = {};
      for (const op of result.plan.operations) seed[op.id] = op.default_resolution;
      setResolutions(seed);
    } catch (err) {
      toast({
        title: 'Could not preview import',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onApply() {
    if (!incoming) return;
    try {
      const result = await applyMutation.mutateAsync({
        incoming,
        resolutions,
        base_version: baseVersion,
      });
      toast({
        title: 'Import applied',
        description: `${result.applied} change${result.applied === 1 ? '' : 's'} added, ${result.skipped} skipped.`,
        variant: 'success',
      });
      setOpen(false);
      reset();
    } catch (err) {
      toast({
        title: 'Could not apply import',
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
          <Download className="h-4 w-4" /> Import
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Import from a resume</DialogTitle>
          <DialogDescription>
            Bring content from a parsed resume into your profile. Review each change — nothing is
            overwritten without your say-so.
          </DialogDescription>
        </DialogHeader>

        {!plan ? (
          <div className="max-h-[55vh] space-y-2 overflow-y-auto">
            {resumesQuery.isLoading ? (
              <LoadingSkeleton rows={3} />
            ) : resumes.length === 0 ? (
              <EmptyState
                icon={Download}
                title="No resumes to import"
                description="Upload and parse a resume first, then import it here."
              />
            ) : (
              resumes.map((r) => (
                <button
                  key={r.resume_id}
                  onClick={() => onPreview(r.resume_id)}
                  disabled={previewMutation.isPending}
                  className="flex w-full items-center justify-between rounded-[var(--radius-at-md)] border border-[var(--border)] p-3 text-left text-sm transition-colors hover:bg-[var(--accent)] disabled:opacity-60"
                >
                  <span className="truncate">
                    {r.title || r.filename || 'Untitled resume'}
                    {r.is_master && (
                      <Badge variant="primary" className="ml-2">
                        Master
                      </Badge>
                    )}
                  </span>
                  {previewMutation.isPending && resumeId === r.resume_id ? (
                    <span className="text-xs text-[var(--muted-foreground)]">Analyzing…</span>
                  ) : (
                    <ArrowRight className="h-4 w-4 text-[var(--muted-foreground)]" />
                  )}
                </button>
              ))
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {stats && (
              <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted-foreground)]">
                <Badge variant={stats.quality_score >= 50 ? 'success' : 'warning'}>
                  Quality {stats.quality_score}%
                </Badge>
                <span>{stats.new_items} new</span>
                <span>· {stats.updates} updates</span>
                <span>· {stats.conflicts} conflicts</span>
                <span>· {stats.duplicates} duplicates</span>
              </div>
            )}
            {warnings.map((w) => (
              <p
                key={w}
                className="rounded-[var(--radius-at-sm)] bg-[var(--at-warning)]/12 px-3 py-2 text-xs text-[var(--at-warning)]"
              >
                {w}
              </p>
            ))}
            <MergePlanReview
              plan={plan}
              resolutions={resolutions}
              onChange={(id, value) => setResolutions((prev) => ({ ...prev, [id]: value }))}
            />
          </div>
        )}

        {plan && (
          <DialogFooter>
            <Button variant="ghost" onClick={reset}>
              Back
            </Button>
            <Button onClick={onApply} loading={applyMutation.isPending}>
              Apply import
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

function MergePlanReview({
  plan,
  resolutions,
  onChange,
}: {
  plan: MergePlan;
  resolutions: Record<string, string>;
  onChange: (id: string, value: string) => void;
}) {
  if (plan.operations.length === 0) {
    return (
      <EmptyState
        icon={ArrowRight}
        title="Nothing new to import"
        description="This resume's content already matches your profile."
      />
    );
  }
  return (
    <div className="max-h-[55vh] space-y-3 overflow-y-auto pr-1">
      {plan.operations.map((op) => (
        <OperationRow
          key={op.id}
          op={op}
          resolution={resolutions[op.id] ?? op.default_resolution}
          onChange={(value) => onChange(op.id, value)}
        />
      ))}
    </div>
  );
}

function OperationRow({
  op,
  resolution,
  onChange,
}: {
  op: MergeOperation;
  resolution: string;
  onChange: (value: string) => void;
}) {
  const badge = OP_BADGE[op.op] ?? OP_BADGE.add;
  return (
    <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
      <div className="flex items-center gap-2">
        <Badge variant={badge.variant}>{badge.label}</Badge>
        <span className="truncate text-sm font-medium">{op.label}</span>
        {op.similarity != null && (
          <span className="ml-auto text-xs text-[var(--muted-foreground)]">
            {Math.round(op.similarity * 100)}% match
          </span>
        )}
      </div>

      {(op.op === 'update' || op.op === 'conflict' || op.op === 'duplicate') && (
        <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
          <div className="rounded bg-[var(--secondary)] p-2">
            <p className="mb-0.5 font-medium text-[var(--muted-foreground)]">Current</p>
            <p className="text-[var(--foreground)]">{preview(op.existing)}</p>
          </div>
          <div className="rounded bg-[var(--at-surface-2,var(--secondary))] p-2">
            <p className="mb-0.5 font-medium text-[var(--muted-foreground)]">Incoming</p>
            <p className="text-[var(--foreground)]">{preview(op.incoming)}</p>
          </div>
        </div>
      )}
      {op.op === 'add' && (
        <p className="mt-2 text-xs text-[var(--foreground)]">{preview(op.incoming)}</p>
      )}

      <div
        className="mt-2 flex flex-wrap gap-1"
        role="radiogroup"
        aria-label={`Resolution for ${op.label}`}
      >
        {op.allowed_resolutions.map((r) => (
          <button
            key={r}
            role="radio"
            aria-checked={resolution === r}
            onClick={() => onChange(r)}
            className={
              'rounded-[var(--radius-at-sm)] px-2.5 py-1 text-xs font-medium transition-colors ' +
              (resolution === r
                ? 'bg-[var(--primary)] text-[var(--primary-foreground)]'
                : 'border border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]')
            }
          >
            {RESOLUTION_LABELS[r] ?? r}
          </button>
        ))}
      </div>
    </div>
  );
}
