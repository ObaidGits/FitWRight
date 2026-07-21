'use client';

/**
 * Agenda (P3 §G / Requirement 12) - one place for everything coming up.
 *
 * A merged, time-ordered feed of upcoming reminders + interviews across all
 * applications, grouped into Overdue / Today / This week / Later. Quick actions
 * (open, snooze, mark done / cancel) act inline with optimistic updates; each
 * item deep-links to its application, and interviews expose an ICS download.
 * Explicit loading / empty / error states; keyboard- and touch-friendly.
 */
import * as React from 'react';
import Link from 'next/link';
import CalendarClock from 'lucide-react/dist/esm/icons/calendar-clock';
import Bell from 'lucide-react/dist/esm/icons/bell';
import Users from 'lucide-react/dist/esm/icons/users';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import Clock from 'lucide-react/dist/esm/icons/clock';
import CalendarDays from 'lucide-react/dist/esm/icons/calendar-days';
import Check from 'lucide-react/dist/esm/icons/check';
import Download from 'lucide-react/dist/esm/icons/download';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/atelier/dropdown-menu';
import { useToast } from '@/components/atelier/toast';
import { interviewIcsUrl } from '@/lib/api/scheduling';
import {
  flattenAgenda,
  useAgenda,
  useCancelAgendaItem,
  useSnoozeReminder,
  type AgendaItem,
} from '@/features/agenda/hooks';

type Bucket = 'Overdue' | 'Today' | 'This week' | 'Later';
type Filter = 'all' | 'reminder' | 'interview';

const SNOOZE_PRESETS: { label: string; preset: string }[] = [
  { label: 'In 1 day', preset: 'in_1_day' },
  { label: 'In 3 days', preset: 'in_3_days' },
  { label: 'Next week', preset: 'next_week' },
];

function bucketFor(whenIso: string, now: Date): Bucket {
  const when = new Date(whenIso);
  if (when.getTime() < now.getTime()) return 'Overdue';
  const endOfToday = new Date(now);
  endOfToday.setHours(23, 59, 59, 999);
  if (when.getTime() <= endOfToday.getTime()) return 'Today';
  const endOfWeek = new Date(endOfToday);
  endOfWeek.setDate(endOfWeek.getDate() + 7);
  if (when.getTime() <= endOfWeek.getTime()) return 'This week';
  return 'Later';
}

function formatWhen(whenIso: string, tz: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: tz || 'UTC',
    }).format(new Date(whenIso));
  } catch {
    return new Date(whenIso).toLocaleString();
  }
}

const BUCKET_ORDER: Bucket[] = ['Overdue', 'Today', 'This week', 'Later'];

export default function AgendaPage() {
  const { toast } = useToast();
  const query = useAgenda();
  const snooze = useSnoozeReminder();
  const cancel = useCancelAgendaItem();
  const [filter, setFilter] = React.useState<Filter>('all');

  const now = React.useMemo(() => new Date(), []);
  const items = flattenAgenda(query.data?.pages).filter((i) =>
    filter === 'all' ? true : i.kind === filter
  );

  const grouped = React.useMemo(() => {
    const map: Record<Bucket, AgendaItem[]> = {
      Overdue: [],
      Today: [],
      'This week': [],
      Later: [],
    };
    for (const item of items) map[bucketFor(item.when, now)].push(item);
    return map;
  }, [items, now]);

  async function onSnooze(item: AgendaItem, preset: string) {
    try {
      await snooze.mutateAsync({ applicationId: item.application_id, reminderId: item.id, preset });
      toast({ title: 'Reminder snoozed', variant: 'success' });
    } catch {
      toast({ title: 'Could not snooze', variant: 'error' });
    }
  }

  async function onDone(item: AgendaItem) {
    try {
      await cancel.mutateAsync({ item });
      toast({
        title: item.kind === 'reminder' ? 'Reminder cleared' : 'Interview cancelled',
        variant: 'success',
      });
    } catch {
      toast({ title: 'Could not update', variant: 'error' });
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Agenda</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Everything coming up across your applications.
          </p>
        </div>
        <div
          className="flex items-center gap-1 rounded-[var(--radius-at-md)] border border-[var(--border)] p-0.5"
          role="tablist"
          aria-label="Filter agenda"
        >
          {(['all', 'reminder', 'interview'] as Filter[]).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={
                'rounded-[var(--radius-at-sm)] px-3 py-1 text-sm capitalize transition-colors ' +
                (filter === f
                  ? 'bg-[var(--accent)] text-[var(--foreground)]'
                  : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]')
              }
            >
              {f === 'all' ? 'All' : `${f}s`}
            </button>
          ))}
        </div>
      </header>

      {query.isLoading ? (
        <LoadingSkeleton rows={4} />
      ) : query.isError ? (
        <ErrorState description="Could not load your agenda." onRetry={() => query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={CalendarClock}
          title="Nothing scheduled"
          description="Set a follow-up reminder or schedule an interview from an application to see it here."
          action={
            <Button asChild variant="outline">
              <Link href="/applications">Open applications</Link>
            </Button>
          }
        />
      ) : (
        <div className="space-y-6" aria-live="polite">
          {BUCKET_ORDER.map((bucket) =>
            grouped[bucket].length === 0 ? null : (
              <section key={bucket} className="space-y-2">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--muted-foreground)]">
                  {bucket === 'Overdue' ? (
                    <Clock className="h-4 w-4 text-[var(--destructive)]" />
                  ) : (
                    <CalendarDays className="h-4 w-4" />
                  )}
                  {bucket}
                  <span className="text-xs font-normal">({grouped[bucket].length})</span>
                </h2>
                <ul className="space-y-2">
                  {grouped[bucket].map((item) => (
                    <li key={`${item.kind}-${item.id}`}>
                      <AgendaRow
                        item={item}
                        overdue={bucket === 'Overdue'}
                        onSnooze={onSnooze}
                        onDone={onDone}
                        busy={snooze.isPending || cancel.isPending}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            )
          )}

          {query.hasNextPage && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                onClick={() => query.fetchNextPage()}
                loading={query.isFetchingNextPage}
              >
                Load more
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AgendaRow({
  item,
  overdue,
  onSnooze,
  onDone,
  busy,
}: {
  item: AgendaItem;
  overdue: boolean;
  onSnooze: (item: AgendaItem, preset: string) => void;
  onDone: (item: AgendaItem) => void;
  busy: boolean;
}) {
  const Icon = item.kind === 'interview' ? Users : Bell;
  return (
    <Card className="flex items-center gap-3 p-3.5">
      <span
        className={
          'flex h-9 w-9 shrink-0 items-center justify-center rounded-full ' +
          (item.kind === 'interview'
            ? 'bg-[var(--primary)]/12 text-[var(--primary)]'
            : 'bg-[var(--secondary)] text-[var(--muted-foreground)]')
        }
      >
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-[var(--foreground)]">{item.title}</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          <span className="capitalize">{item.kind}</span> - {formatWhen(item.when, item.tz)}
          {overdue && <span className="ml-1 text-[var(--destructive)]">- overdue</span>}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {item.kind === 'reminder' && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" disabled={busy} aria-label="Snooze reminder">
                Snooze
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {SNOOZE_PRESETS.map((p) => (
                <DropdownMenuItem key={p.preset} onClick={() => onSnooze(item, p.preset)}>
                  {p.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {item.kind === 'interview' && (
          <Button asChild variant="ghost" size="icon" aria-label="Download calendar invite">
            <a href={interviewIcsUrl(item.id)} target="_blank" rel="noopener noreferrer">
              <Download className="h-4 w-4" />
            </a>
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          disabled={busy}
          onClick={() => onDone(item)}
          aria-label={item.kind === 'reminder' ? 'Mark reminder done' : 'Cancel interview'}
        >
          <Check className="h-4 w-4" />
        </Button>
        <Button asChild variant="ghost" size="icon" aria-label="Open application">
          <Link href={`/applications/${item.application_id}`}>
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
      </div>
    </Card>
  );
}
