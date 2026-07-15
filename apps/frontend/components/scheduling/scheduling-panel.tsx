'use client';

/**
 * SchedulingPanel (P3 §E/§F) — reminders + interviews for one application.
 *
 * Fully wired to the backend via `features/scheduling/hooks`: create / snooze /
 * cancel reminders (with presets + bounded recurrence) and create / cancel
 * interviews (UTC+IANA tz captured from the browser, lead-times, ICS download,
 * soft overlap warning). Explicit loading / empty / error states; times are
 * shown in the viewer's timezone. Keyboard- and touch-friendly.
 */
import * as React from 'react';
import Bell from 'lucide-react/dist/esm/icons/bell';
import Users from 'lucide-react/dist/esm/icons/users';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Download from 'lucide-react/dist/esm/icons/download';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { interviewIcsUrl } from '@/lib/api/scheduling';
import {
  browserTimezone,
  localInputToUtcIso,
  useCancelInterview,
  useCancelReminder,
  useCreateInterview,
  useCreateReminder,
  useInterviews,
  useReminders,
  useSnoozeReminderInApp,
} from '@/features/scheduling/hooks';

function fmt(iso: string, tz: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: tz || 'UTC',
    }).format(new Date(iso));
  } catch {
    return new Date(iso).toLocaleString();
  }
}

export function SchedulingPanel({ applicationId }: { applicationId: string }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <RemindersSection applicationId={applicationId} />
      <InterviewsSection applicationId={applicationId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reminders
// ---------------------------------------------------------------------------

function RemindersSection({ applicationId }: { applicationId: string }) {
  const { toast } = useToast();
  const q = useReminders(applicationId);
  const create = useCreateReminder(applicationId);
  const snooze = useSnoozeReminderInApp(applicationId);
  const cancel = useCancelReminder(applicationId);
  const [adding, setAdding] = React.useState(false);
  const [due, setDue] = React.useState('');
  const [note, setNote] = React.useState('');
  const [recurrence, setRecurrence] = React.useState('');

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!due) return;
    try {
      await create.mutateAsync({
        due_at: localInputToUtcIso(due),
        tz: browserTimezone(),
        note: note || null,
        recurrence: recurrence || null,
      });
      toast({ title: 'Reminder set', variant: 'success' });
      setAdding(false);
      setDue('');
      setNote('');
      setRecurrence('');
    } catch (err) {
      toast({
        title: err instanceof Error ? err.message : 'Could not set reminder',
        variant: 'error',
      });
    }
  }

  const reminders = (q.data ?? []).filter((r) => r.status !== 'cancelled' && r.status !== 'fired');

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--muted-foreground)]">
          <Bell className="h-4 w-4" /> Reminders
        </h2>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setAdding((v) => !v)}
          aria-expanded={adding}
        >
          <Plus className="h-4 w-4" /> Add
        </Button>
      </div>

      {adding && (
        <form
          onSubmit={submit}
          className="mb-3 space-y-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3"
        >
          <div>
            <Label htmlFor="rem-due" className="text-xs">
              When
            </Label>
            <Input
              id="rem-due"
              type="datetime-local"
              value={due}
              onChange={(e) => setDue(e.target.value)}
              required
            />
          </div>
          <div>
            <Label htmlFor="rem-note" className="text-xs">
              Note (optional)
            </Label>
            <Input
              id="rem-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Follow up with recruiter"
              maxLength={1000}
            />
          </div>
          <div>
            <Label htmlFor="rem-rec" className="text-xs">
              Repeat
            </Label>
            <Select
              value={recurrence || 'none'}
              onValueChange={(v) => setRecurrence(v === 'none' ? '' : v)}
            >
              <SelectTrigger id="rem-rec" aria-label="Repeat">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Does not repeat</SelectItem>
                <SelectItem value="daily">Daily</SelectItem>
                <SelectItem value="weekly">Weekly</SelectItem>
                <SelectItem value="every:2:weeks">Every 2 weeks</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={create.isPending}>
              Save
            </Button>
          </div>
        </form>
      )}

      {q.isLoading ? (
        <LoadingSkeleton rows={2} />
      ) : q.isError ? (
        <ErrorState description="Could not load reminders." onRetry={() => q.refetch()} />
      ) : reminders.length === 0 ? (
        <EmptyState
          icon={Bell}
          title="No reminders"
          description="Add a follow-up so you never miss a beat."
        />
      ) : (
        <ul className="space-y-2">
          {reminders.map((r) => (
            <li
              key={r.id}
              className="flex items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{r.note || 'Follow-up'}</p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {fmt(r.due_at, r.tz)}
                  {r.recurrence && <span className="ml-1">· repeats</span>}
                  {r.status === 'snoozed' && <span className="ml-1">· snoozed</span>}
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                disabled={snooze.isPending}
                onClick={() => snooze.mutate({ id: r.id, preset: 'in_3_days' })}
              >
                Snooze
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Delete reminder"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate(r.id)}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Interviews
// ---------------------------------------------------------------------------

function InterviewsSection({ applicationId }: { applicationId: string }) {
  const { toast } = useToast();
  const q = useInterviews(applicationId);
  const create = useCreateInterview(applicationId);
  const cancel = useCancelInterview(applicationId);
  const [adding, setAdding] = React.useState(false);
  const [start, setStart] = React.useState('');
  const [duration, setDuration] = React.useState(60);
  const [kind, setKind] = React.useState('screen');
  const [location, setLocation] = React.useState('');

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!start) return;
    try {
      const created = await create.mutateAsync({
        starts_at: localInputToUtcIso(start),
        tz: browserTimezone(),
        duration_min: duration,
        kind: kind as never,
        location: location || null,
        lead_times: [1440, 60],
      });
      if (created.overlaps.length > 0) {
        toast({
          title: 'Scheduled',
          description: 'Heads up — it overlaps another interview.',
          variant: 'info',
        });
      } else {
        toast({ title: 'Interview scheduled', variant: 'success' });
      }
      setAdding(false);
      setStart('');
      setLocation('');
    } catch (err) {
      toast({ title: err instanceof Error ? err.message : 'Could not schedule', variant: 'error' });
    }
  }

  const interviews = (q.data ?? []).filter((i) => i.status === 'scheduled');

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--muted-foreground)]">
          <Users className="h-4 w-4" /> Interviews
        </h2>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setAdding((v) => !v)}
          aria-expanded={adding}
        >
          <Plus className="h-4 w-4" /> Schedule
        </Button>
      </div>

      {adding && (
        <form
          onSubmit={submit}
          className="mb-3 space-y-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3"
        >
          <div>
            <Label htmlFor="iv-start" className="text-xs">
              Starts
            </Label>
            <Input
              id="iv-start"
              type="datetime-local"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label htmlFor="iv-dur" className="text-xs">
                Minutes
              </Label>
              <Input
                id="iv-dur"
                type="number"
                min={1}
                max={1440}
                value={duration}
                onChange={(e) =>
                  setDuration(Math.max(1, Math.min(1440, Number(e.target.value) || 60)))
                }
              />
            </div>
            <div>
              <Label htmlFor="iv-kind" className="text-xs">
                Type
              </Label>
              <Select value={kind} onValueChange={setKind}>
                <SelectTrigger id="iv-kind" aria-label="Interview type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="screen">Screen</SelectItem>
                  <SelectItem value="technical">Technical</SelectItem>
                  <SelectItem value="onsite">Onsite</SelectItem>
                  <SelectItem value="behavioral">Behavioral</SelectItem>
                  <SelectItem value="final">Final</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <Label htmlFor="iv-loc" className="text-xs">
              Location / link (optional)
            </Label>
            <Input
              id="iv-loc"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Zoom link or address"
              maxLength={500}
            />
          </div>
          <p className="text-xs text-[var(--muted-foreground)]">
            Reminders 1 day and 1 hour before ({browserTimezone()}).
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={create.isPending}>
              Schedule
            </Button>
          </div>
        </form>
      )}

      {q.isLoading ? (
        <LoadingSkeleton rows={2} />
      ) : q.isError ? (
        <ErrorState description="Could not load interviews." onRetry={() => q.refetch()} />
      ) : interviews.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No interviews"
          description="Schedule one to get timezone-correct reminders and a calendar invite."
        />
      ) : (
        <ul className="space-y-2">
          {interviews.map((i) => (
            <li
              key={i.id}
              className="flex items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm capitalize">
                  {i.kind} interview <Badge variant="neutral">{i.duration_min}m</Badge>
                </p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {fmt(i.starts_at, i.tz)}
                  {i.location && <span className="ml-1 truncate">· {i.location}</span>}
                </p>
              </div>
              <Button asChild size="icon" variant="ghost" aria-label="Download calendar invite">
                <a href={interviewIcsUrl(i.id)} target="_blank" rel="noopener noreferrer">
                  <Download className="h-4 w-4" />
                </a>
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Cancel interview"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate(i.id)}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
