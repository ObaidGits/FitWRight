'use client';

/**
 * The apply queue - the jobs to work through, in the order to open them.
 *
 * What this view is for: turning twenty separate applications into one sitting.
 * You order the list once, then work down it; the extension fills each form as
 * you reach it and you review and submit yourself.
 *
 * What it deliberately is NOT: a place where half-finished applications live. An
 * employer's form cannot be saved across tabs, so nothing here implies resumable
 * progress inside a form - the queue only decides what comes next.
 *
 * Reordering is drag-and-drop, matching the tracker board on the same page, with
 * keyboard sensors so it is usable without a mouse.
 */
import * as React from 'react';
import Link from 'next/link';
import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import GripVertical from 'lucide-react/dist/esm/icons/grip-vertical';
import ExternalLink from 'lucide-react/dist/esm/icons/external-link';
import Bell from 'lucide-react/dist/esm/icons/bell';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useApplyQueue, useReorderQueue, useSubmission } from '@/features/applications/queue-hooks';
import type { QueueItem } from '@/lib/api/apply-queue';

function QueueRow({
  item,
  index,
  onInspect,
}: {
  item: QueueItem;
  index: number;
  onInspect: (id: string) => void;
}) {
  // Destructured at the call site, matching `components/builder/draggable-list-item`.
  // Reading these off the hook's result object during render trips the React
  // Compiler's ref rule, because `setNodeRef` is a ref setter.
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.application_id,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex items-center gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-3"
    >
      <button
        {...attributes}
        {...listeners}
        aria-label={`Reorder ${item.role ?? 'this job'}`}
        className="cursor-grab text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
      >
        <GripVertical className="h-4 w-4" />
      </button>

      {/* Position is 1-based here: "next up" is more useful than an array index. */}
      <span className="w-6 text-center text-xs font-medium tabular-nums text-[var(--muted-foreground)]">
        {index + 1}
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-[var(--foreground)]">
          {item.role || 'Role not recorded'}
        </p>
        <p className="truncate text-xs text-[var(--muted-foreground)]">
          {item.company || 'Company not recorded'}
        </p>
      </div>

      <button
        onClick={() => onInspect(item.application_id)}
        className="text-[11px] text-[var(--primary)] hover:underline"
      >
        What I submitted
      </button>
    </div>
  );
}

/** What was actually sent for one application. */
function SubmissionPanel({
  applicationId,
  onClose,
}: {
  applicationId: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError, error } = useSubmission(applicationId);

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium text-[var(--foreground)]">What I submitted</h3>
        <button
          onClick={onClose}
          aria-label="Close"
          className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          ×
        </button>
      </div>

      {isLoading && <LoadingSkeleton rows={2} className="mt-3" />}
      {isError && (
        <p className="mt-3 text-xs text-[var(--at-danger)]">
          {error?.message ?? 'Could not load it'}
        </p>
      )}

      {data && !data.has_record && (
        <p className="mt-3 text-xs text-[var(--muted-foreground)]">
          No record for this one. It was applied to before FitWright started keeping submission
          records, so there is nothing to show rather than something invented.
        </p>
      )}

      {data?.has_record && (
        <div className="mt-3 space-y-3">
          {/* A sent application with no follow-up is where most applications go
              quiet. The reminder API already exists; the apply flow just never
              offered it at the one moment the user is thinking about this job. */}
          <FollowUpButton applicationId={applicationId} />

          <dl className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <dt className="text-[var(--muted-foreground)]">Submitted via</dt>
              <dd className="text-[var(--foreground)]">{data.submitted_via ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-[var(--muted-foreground)]">Resume version</dt>
              <dd className="text-[var(--foreground)]">{data.resume_version_id ?? '—'}</dd>
            </div>
          </dl>

          {Object.keys(data.answers).length > 0 ? (
            <div className="space-y-1">
              <p className="text-xs font-medium text-[var(--foreground)]">Answers</p>
              {Object.entries(data.answers).map(([label, value]) => (
                <div
                  key={label}
                  className="rounded-[var(--radius-at-sm)] bg-[var(--muted)] px-2 py-1.5 text-xs"
                >
                  <span className="text-[var(--muted-foreground)]">{label}: </span>
                  <span className="text-[var(--foreground)]">{String(value)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-[var(--muted-foreground)]">No answers were recorded.</p>
          )}
        </div>
      )}
    </Card>
  );
}

/**
 * Offer a follow-up reminder at the moment the user records having applied.
 *
 * The reminder machinery already existed; the apply flow simply never mentioned
 * it, and a sent application with no follow-up is where most applications go
 * quiet. One week is the default because it is long enough not to look impatient
 * and short enough that the role is still open.
 */
function FollowUpButton({ applicationId }: { applicationId: string }) {
  const { toast } = useToast();
  const [done, setDone] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  async function set() {
    setSaving(true);
    try {
      const { createReminder } = await import('@/lib/api/scheduling');
      await createReminder(applicationId, {
        preset: 'in_1_week',
        note: 'Follow up on this application',
      });
      setDone(true);
      toast({
        title: 'Follow-up set for a week from now',
        description: 'It will appear in your Agenda.',
      });
    } catch (err) {
      toast({
        title: err instanceof Error ? err.message : 'Could not set the reminder',
        variant: 'error',
      });
    } finally {
      setSaving(false);
    }
  }

  if (done) {
    return (
      <p className="text-xs text-[var(--at-success)]">Follow-up reminder set for next week.</p>
    );
  }

  return (
    <Button size="sm" variant="outline" onClick={() => void set()} disabled={saving}>
      <Bell className="h-3.5 w-3.5" />
      {saving ? 'Setting…' : 'Remind me to follow up in a week'}
    </Button>
  );
}

export function ApplyQueue() {
  const { data, isLoading, isError, error, refetch } = useApplyQueue();
  const reorder = useReorderQueue();
  const { toast } = useToast();
  const [inspecting, setInspecting] = React.useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const items = data?.items ?? [];

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const from = items.findIndex((i) => i.application_id === active.id);
    const to = items.findIndex((i) => i.application_id === over.id);
    if (from < 0 || to < 0) return;

    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);

    reorder.mutate(
      next.map((i) => i.application_id),
      { onError: (err) => toast({ title: err.message, variant: 'error' }) }
    );
  }

  if (isLoading) return <LoadingSkeleton rows={4} />;
  if (isError) {
    return (
      <ErrorState
        title="Could not load your queue"
        description={error?.message}
        onRetry={() => void refetch()}
      />
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title="Nothing queued"
        description="Jobs you save from Discover or the extension land here. Order them once, then work down the list - the extension fills each form as you reach it."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-[var(--muted-foreground)]">
          {items.length} job{items.length === 1 ? '' : 's'} to work through, in order. Drag to
          reprioritise.
        </p>
        {items[0] && (
          <Button size="sm" variant="outline" asChild>
            <Link href={`/applications/${items[0].application_id}`}>
              Open next <ExternalLink className="ml-1 h-3 w-3" />
            </Link>
          </Button>
        )}
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragEnd={handleDragEnd}>
        <SortableContext
          items={items.map((i) => i.application_id)}
          strategy={verticalListSortingStrategy}
        >
          <div className="space-y-2">
            {items.map((item, index) => (
              <QueueRow
                key={item.application_id}
                item={item}
                index={index}
                onInspect={setInspecting}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {inspecting && (
        <SubmissionPanel applicationId={inspecting} onClose={() => setInspecting(null)} />
      )}
    </div>
  );
}
