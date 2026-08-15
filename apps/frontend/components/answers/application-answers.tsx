'use client';

/**
 * Application Answers - the Settings home for everything job forms ask you.
 *
 * The page is inbox-first on purpose. A month of applying produces a long tail of
 * questions, and burying the two that need attention inside a list of two hundred
 * answered ones would make the feature useless. So anything the extension could
 * not answer sits at the top as a card you can clear in one action, and the full
 * set lives below in collapsed groups.
 *
 * Three details that carry weight:
 *  - Each editor matches the field's real type and offers the form's real options,
 *    because a free-text guess at a dropdown value will not match on submission.
 *  - Fields answered from your Profile are read-only here and say so. Editing them
 *    in two places is how stale answers happen.
 *  - Screening questions are badged. A wrong visa status or salary silently
 *    auto-rejects an application, so they are worth a second look.
 */
import * as React from 'react';

import { Button } from '@/components/atelier/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/atelier/card';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import {
  useApplicationFields,
  useDeleteApplicationField,
  useUpdateApplicationField,
} from '@/features/application-fields/hooks';
import {
  FIELD_GROUPS,
  groupForField,
  type ApplicationField,
  type FieldGroup,
} from '@/lib/api/application-fields';

/** Render the input a given field type deserves. */
function AnswerEditor({
  field,
  value,
  onChange,
  disabled,
}: {
  field: ApplicationField;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const id = `answer-${field.id}`;

  // A Profile-backed answer is displayed, not edited. Rendering it as a disabled
  // <select> looked empty whenever the Profile value was not one of the form's
  // options - "Indian citizen" against a Yes/No dropdown showed the placeholder,
  // which reads as "unanswered" when an answer exists.
  if (disabled) {
    const text = value.trim();
    const mismatched = text !== '' && field.options.length > 0 && !field.options.includes(text);
    return (
      <div id={id} className="text-sm text-[var(--foreground)]">
        {text === '' ? (
          <span className="text-[var(--muted-foreground)]">Not set in your Profile yet</span>
        ) : (
          <span>{text}</span>
        )}
        {mismatched && (
          <p className="mt-1 text-[11px] text-[var(--at-warning)]">
            This form only accepts {field.options.join(' or ')}, so your Profile answer will not
            match. Point this question at a different Profile field, or answer it here instead.
          </p>
        )}
      </div>
    );
  }

  if (field.options.length > 0) {
    return (
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-[var(--radius-at-md)] border border-[var(--input)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)] disabled:opacity-60"
      >
        <option value="">Select an answer…</option>
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  if (field.field_type === 'textarea') {
    return (
      <Textarea
        id={id}
        value={value}
        disabled={disabled}
        rows={3}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (field.field_type === 'checkbox') {
    return (
      <label className="flex items-center gap-2 text-sm text-[var(--foreground)]">
        <input
          id={id}
          type="checkbox"
          checked={value === 'true' || value === 'Yes'}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked ? 'true' : 'false')}
          className="rounded-sm border-[var(--input)] accent-[var(--primary)]"
        />
        Yes
      </label>
    );
  }

  return (
    <Input
      id={id}
      type={
        field.field_type === 'number' ? 'number' : field.field_type === 'date' ? 'date' : 'text'
      }
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function Badges({ field }: { field: ApplicationField }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
      {field.is_knockout && (
        <span
          className="rounded bg-[var(--at-warning)]/15 px-1.5 py-0.5 font-medium text-[var(--at-warning)]"
          title="A screening question - a wrong answer here can auto-reject your application"
        >
          Screening
        </span>
      )}
      {field.from_profile && (
        <span className="rounded bg-[var(--primary)]/10 px-1.5 py-0.5 font-medium text-[var(--primary)]">
          From your Profile
        </span>
      )}
      <span className="rounded bg-[var(--muted)] px-1.5 py-0.5 text-[var(--muted-foreground)]">
        {field.scope === 'company' && field.company ? field.company : 'Everywhere'}
      </span>
      {field.times_seen > 1 && (
        <span className="text-[var(--muted-foreground)]">seen {field.times_seen}×</span>
      )}
      {field.synonyms.length > 0 && (
        <span className="text-[var(--muted-foreground)]" title={field.synonyms.join(' · ')}>
          +{field.synonyms.length} wording{field.synonyms.length === 1 ? '' : 's'}
        </span>
      )}
    </div>
  );
}

/** One row in a group, or one card in the inbox. */
function FieldRow({ field, emphasise }: { field: ApplicationField; emphasise?: boolean }) {
  const initial = field.value == null ? '' : String(field.value);
  const [draft, setDraft] = React.useState(initial);
  const update = useUpdateApplicationField();
  const remove = useDeleteApplicationField();
  const { toast } = useToast();

  // Keep the editor in step when the list refetches (a merge or a Profile edit
  // can change the value under us), but never clobber an unsaved edit.
  React.useEffect(() => {
    setDraft((current) => (current === '' || current === initial ? initial : current));
  }, [initial]);

  const dirty = draft !== initial;
  const readOnly = field.from_profile;
  // Set when the user chooses to answer a Profile-backed question here instead.
  const [overriding, setOverriding] = React.useState(false);

  function save() {
    update.mutate(
      { id: field.id, patch: { value: draft === '' ? null : draft } },
      {
        onSuccess: () => toast({ title: 'Answer saved', variant: 'success' }),
        onError: (error) => toast({ title: error.message, variant: 'error' }),
      }
    );
  }

  return (
    <div
      className={`rounded-[var(--radius-at-md)] border p-3 ${
        emphasise
          ? 'border-[var(--primary)]/30 bg-[var(--accent)]'
          : 'border-[var(--border)] bg-[var(--card)]'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <Label htmlFor={`answer-${field.id}`} className="text-sm">
            {field.label}
          </Label>
          {emphasise && (field.last_seen_ats || field.company) && (
            <p className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">
              Asked on {field.company ?? 'an application'}
              {field.last_seen_ats ? ` · ${field.last_seen_ats}` : ''}
            </p>
          )}
        </div>
        <Badges field={field} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <div className="min-w-[12rem] flex-1">
          {/* Profile-backed answers stay DISPLAYED rather than edited by default -
              a disabled <select> showed its placeholder whenever the Profile value
              was not one of the form's options, which reads as "unanswered" when an
              answer exists. But display-only was a dead end: the mismatch warning
              already told users to "answer it here instead" and there was no way to,
              and a Profile with no matching input left them nowhere to go at all.
              The override makes that promise true while keeping the Profile as the
              default source, so the two cannot drift silently. */}
          <AnswerEditor
            field={field}
            value={draft}
            onChange={setDraft}
            disabled={readOnly && !overriding}
          />
        </div>

        {readOnly && !overriding ? (
          <Button size="sm" variant="outline" onClick={() => setOverriding(true)}>
            Answer here instead
          </Button>
        ) : (
          <>
            <Button size="sm" onClick={save} disabled={!dirty || update.isPending}>
              Save
            </Button>
            {overriding && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setOverriding(false);
                  setDraft(initial);
                }}
              >
                Cancel
              </Button>
            )}
            {emphasise && !overriding && (
              <Button
                size="sm"
                variant="outline"
                title="Stop asking me this question"
                onClick={() => update.mutate({ id: field.id, patch: { status: 'ignored' } })}
              >
                Never ask
              </Button>
            )}
            {!emphasise && !overriding && (
              <button
                onClick={() => remove.mutate(field.id)}
                className="text-[11px] text-[var(--muted-foreground)] hover:text-[var(--at-danger)] hover:underline"
              >
                Remove
              </button>
            )}
          </>
        )}
      </div>

      {readOnly && (
        <p className="mt-1.5 text-[11px] text-[var(--muted-foreground)]">
          {overriding
            ? 'Saving here answers this question on job forms only. Your Profile is left unchanged.'
            : 'Comes from your Profile - edit it there to change it everywhere.'}
        </p>
      )}
    </div>
  );
}

function Group({ name, fields }: { name: FieldGroup; fields: ApplicationField[] }) {
  // Eligibility is open by default: it holds the answers that decide whether an
  // application survives screening, so it is the one worth seeing unprompted.
  const [open, setOpen] = React.useState(name === 'Eligibility & Work Authorization');
  if (fields.length === 0) return null;

  return (
    <div className="rounded-[var(--radius-at-md)] border border-[var(--border)]">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-[var(--foreground)]"
      >
        <span>
          {name}{' '}
          <span className="font-normal text-[var(--muted-foreground)]">({fields.length})</span>
        </span>
        <span className="text-[var(--muted-foreground)]">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-[var(--border)] p-3">
          {fields.map((field) => (
            <FieldRow key={field.id} field={field} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ApplicationAnswers() {
  const { data, isLoading, isError, error, refetch } = useApplicationFields();

  const fields = data ?? [];
  const inbox = fields.filter((f) => f.status === 'needs_answer');
  const answered = fields.filter((f) => f.status === 'answered');

  // Not memoized: `answered` is a fresh array on every render, so a useMemo keyed
  // on it never hit its cache - it only claimed to. The compiler memoizes this
  // correctly on its own.
  const grouped = ((): Map<FieldGroup, ApplicationField[]> => {
    const map = new Map<FieldGroup, ApplicationField[]>();
    for (const field of answered) {
      const group = groupForField(field);
      const list = map.get(group) ?? [];
      list.push(field);
      map.set(group, list);
    }
    return map;
  })();

  if (isLoading) return <LoadingSkeleton rows={4} />;
  if (isError) {
    return (
      <ErrorState
        title="Could not load your answers"
        description={error?.message}
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Application answers</CardTitle>
          <CardDescription>
            What job application forms ask you, and how the extension should answer. Fill a question
            once here and every future form uses it.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {inbox.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-[var(--foreground)]">
                Needs your answer ({inbox.length})
              </h3>
              <p className="text-xs text-[var(--muted-foreground)]">
                The extension met these on a form and had nothing to fill them with.
              </p>
              {inbox.map((field) => (
                <FieldRow key={field.id} field={field} emphasise />
              ))}
            </div>
          ) : fields.length === 0 ? (
            <EmptyState
              title="Nothing asked yet"
              description="Once you autofill a job application with the FitWright extension, every question it meets shows up here - and anything it could not answer waits for you."
            />
          ) : (
            <p className="text-xs text-[var(--at-success)]">Every question so far has an answer.</p>
          )}

          {answered.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-[var(--foreground)]">
                Your answers ({answered.length})
              </h3>
              {FIELD_GROUPS.map((group) => (
                <Group key={group} name={group} fields={grouped.get(group) ?? []} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
