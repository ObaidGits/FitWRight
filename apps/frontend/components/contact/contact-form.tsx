'use client';

/**
 * Premium contact form (marketing). Client component: inline + live validation,
 * progressive disclosure (project/budget appear for hiring/collaboration),
 * character counter, draft persistence + recovery, honeypot + submit-timing
 * spam defenses, accessible errors, and an animated success state. Fully wired
 * to `POST /contact` via {@link submitContact}. Never resets user input on error.
 */
import * as React from 'react';
import Link from 'next/link';
import Send from 'lucide-react/dist/esm/icons/send';
import CircleCheck from 'lucide-react/dist/esm/icons/circle-check';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import Github from 'lucide-react/dist/esm/icons/github';
import Linkedin from 'lucide-react/dist/esm/icons/linkedin';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { useDraft } from '@/lib/hooks/use-draft';
import { submitContact, ContactError, type ContactResult } from '@/lib/api/contact';

const MESSAGE_MAX = 4000;
const MESSAGE_MIN = 10;

const PURPOSES = [
  { value: 'general', label: 'Just saying hello' },
  { value: 'hiring', label: 'Hiring / recruiting' },
  { value: 'job', label: 'Job opportunity' },
  { value: 'collaboration', label: 'Collaboration / project' },
  { value: 'business', label: 'Business inquiry' },
  { value: 'feedback', label: 'General feedback' },
  { value: 'bug', label: 'Bug report' },
  { value: 'feature', label: 'Feature request' },
  { value: 'improvement', label: 'Improvement suggestion' },
  { value: 'support', label: 'Product support' },
  { value: 'press', label: 'Press / speaking' },
  { value: 'other', label: 'Something else' },
] as const;

const PROJECT_TYPES = [
  'Full-stack web app',
  'AI / LLM feature',
  'Backend / API',
  'Data / infrastructure',
  'Consulting / advisory',
  'Something else',
];

const BUDGETS = [
  'Not sure yet',
  '< $5k',
  '$5k - $15k',
  '$15k - $50k',
  '$50k+',
  'Ongoing / retainer',
];

type Values = {
  name: string;
  email: string;
  subject: string;
  purpose: string;
  company: string;
  linkedin: string;
  projectType: string;
  budget: string;
  message: string;
};

function makeEmpty(purpose: string): Values {
  return {
    name: '',
    email: '',
    subject: '',
    purpose,
    company: '',
    linkedin: '',
    projectType: '',
    budget: '',
    message: '',
  };
}

// Purposes for which the project/budget fields are relevant.
const PROJECT_PURPOSES = new Set(['hiring', 'collaboration', 'business', 'job']);

type Errors = Partial<Record<keyof Values, string>>;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validate(v: Values): Errors {
  const e: Errors = {};
  if (!v.name.trim()) e.name = 'Please tell me your name.';
  else if (v.name.trim().length > 100) e.name = 'That name is a little too long.';
  if (!v.email.trim()) e.email = 'An email lets me reply.';
  else if (!EMAIL_RE.test(v.email.trim())) e.email = "That doesn't look like a valid email.";
  if (!v.subject.trim()) e.subject = 'A short subject helps me triage.';
  else if (v.subject.trim().length > 150)
    e.subject = 'Please keep the subject under 150 characters.';
  const msg = v.message.trim();
  if (!msg) e.message = "Your message can't be empty.";
  else if (msg.length < MESSAGE_MIN)
    e.message = `A little more detail helps (at least ${MESSAGE_MIN} characters).`;
  else if (msg.length > MESSAGE_MAX) e.message = `Please keep it under ${MESSAGE_MAX} characters.`;
  return e;
}

export function ContactForm({ defaultPurpose = 'general' }: { defaultPurpose?: string } = {}) {
  const [values, setValues] = React.useState<Values>(() => makeEmpty(defaultPurpose));
  const [errors, setErrors] = React.useState<Errors>({});
  const [touched, setTouched] = React.useState<Partial<Record<keyof Values, boolean>>>({});
  const [submitting, setSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<ContactResult | null>(null);

  // Honeypot + timing (spam defenses). `hp` must stay empty; mount time gives
  // an elapsed-ms signal the backend uses to catch instant bot submissions.
  const [hp, setHp] = React.useState('');
  const mountedAt = React.useRef<number>(Date.now());
  const errorRef = React.useRef<HTMLDivElement | null>(null);

  // Draft persistence so a long message is never lost on reload.
  const draft = useDraft<Values>('contact-form');

  const showProjectFields = PROJECT_PURPOSES.has(values.purpose);
  const messageLen = values.message.trim().length;

  function set<K extends keyof Values>(key: K, value: string) {
    setValues((prev) => {
      const next = { ...prev, [key]: value };
      draft.save(next);
      return next;
    });
    // Live-clear an error as the user fixes it.
    if (touched[key]) {
      setErrors((prev) => {
        const nextErr = validate({ ...values, [key]: value });
        return { ...prev, [key]: nextErr[key] };
      });
    }
  }

  function onBlur(key: keyof Values) {
    setTouched((prev) => ({ ...prev, [key]: true }));
    setErrors((prev) => ({ ...prev, [key]: validate(values)[key] }));
  }

  function restoreDraft() {
    if (draft.recovered) setValues(draft.recovered);
    draft.dismissRecovery();
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const found = validate(values);
    setErrors(found);
    setTouched({ name: true, email: true, subject: true, message: true });
    if (Object.values(found).some(Boolean)) {
      // Move focus to the first invalid field for keyboard/AT users.
      const firstKey = (['name', 'email', 'subject', 'message'] as const).find((k) => found[k]);
      if (firstKey) document.getElementById(`contact-${firstKey}`)?.focus();
      return;
    }

    setSubmitting(true);
    try {
      const res = await submitContact({
        name: values.name.trim(),
        email: values.email.trim(),
        subject: values.subject.trim(),
        message: values.message.trim(),
        purpose: values.purpose,
        company: values.company.trim() || undefined,
        linkedin: values.linkedin.trim() || undefined,
        project_type: showProjectFields && values.projectType ? values.projectType : undefined,
        budget: showProjectFields && values.budget ? values.budget : undefined,
        company_website: hp,
        elapsed_ms: Date.now() - mountedAt.current,
      });
      draft.clear();
      setResult(res);
    } catch (err) {
      const message =
        err instanceof ContactError
          ? err.message
          : 'Could not send your message. Please try again.';
      setFormError(message);
      // Surface the error to screen readers + scroll it into view.
      requestAnimationFrame(() => errorRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <SuccessCard
        result={result}
        onReset={() => {
          setResult(null);
          setValues(makeEmpty(defaultPurpose));
          setTouched({});
          setErrors({});
          mountedAt.current = Date.now();
        }}
      />
    );
  }

  return (
    <Card className="relative overflow-hidden p-6 md:p-8">
      {draft.recovered && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] px-3 py-2 text-sm">
          <span className="text-[var(--muted-foreground)]">
            You have an unsent draft from earlier.
          </span>
          <span className="flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={restoreDraft}>
              Restore
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={draft.clear}>
              Discard
            </Button>
          </span>
        </div>
      )}

      <form onSubmit={onSubmit} noValidate className="space-y-5">
        {formError && (
          <div
            ref={errorRef}
            tabIndex={-1}
            role="alert"
            className="flex items-start gap-2.5 rounded-[var(--radius-at-md)] border border-[var(--destructive)]/40 bg-[var(--destructive)]/8 p-3 text-sm text-[var(--destructive)] focus:outline-none"
          >
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{formError}</span>
          </div>
        )}

        <div className="grid gap-5 sm:grid-cols-2">
          <Field id="contact-name" label="Your name" error={touched.name ? errors.name : undefined}>
            <Input
              id="contact-name"
              value={values.name}
              autoComplete="name"
              maxLength={100}
              placeholder="Ada Lovelace"
              aria-invalid={Boolean(touched.name && errors.name)}
              aria-describedby={touched.name && errors.name ? 'contact-name-err' : undefined}
              onChange={(e) => set('name', e.target.value)}
              onBlur={() => onBlur('name')}
            />
          </Field>
          <Field id="contact-email" label="Email" error={touched.email ? errors.email : undefined}>
            <Input
              id="contact-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              maxLength={320}
              placeholder="you@company.com"
              value={values.email}
              aria-invalid={Boolean(touched.email && errors.email)}
              aria-describedby={touched.email && errors.email ? 'contact-email-err' : undefined}
              onChange={(e) => set('email', e.target.value)}
              onBlur={() => onBlur('email')}
            />
          </Field>
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="contact-purpose">What's this about?</Label>
            <Select value={values.purpose} onValueChange={(v) => set('purpose', v)}>
              <SelectTrigger id="contact-purpose" aria-label="Purpose of your message">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PURPOSES.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Field
            id="contact-subject"
            label="Subject"
            error={touched.subject ? errors.subject : undefined}
          >
            <Input
              id="contact-subject"
              value={values.subject}
              maxLength={150}
              placeholder="A short headline"
              aria-invalid={Boolean(touched.subject && errors.subject)}
              aria-describedby={
                touched.subject && errors.subject ? 'contact-subject-err' : undefined
              }
              onChange={(e) => set('subject', e.target.value)}
              onBlur={() => onBlur('subject')}
            />
          </Field>
        </div>

        {/* Progressive disclosure - only relevant for hiring/collaboration. */}
        {showProjectFields && (
          <div className="grid gap-5 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)]/50 p-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="contact-company">Company / org (optional)</Label>
              <Input
                id="contact-company"
                value={values.company}
                maxLength={120}
                autoComplete="organization"
                placeholder="Where you're reaching out from"
                onChange={(e) => set('company', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="contact-linkedin">LinkedIn / site (optional)</Label>
              <Input
                id="contact-linkedin"
                value={values.linkedin}
                maxLength={200}
                inputMode="url"
                placeholder="linkedin.com/in/..."
                onChange={(e) => set('linkedin', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="contact-project">Project type</Label>
              <Select value={values.projectType} onValueChange={(v) => set('projectType', v)}>
                <SelectTrigger id="contact-project" aria-label="Project type">
                  <SelectValue placeholder="Choose one" />
                </SelectTrigger>
                <SelectContent>
                  {PROJECT_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="contact-budget">Budget (optional)</Label>
              <Select value={values.budget} onValueChange={(v) => set('budget', v)}>
                <SelectTrigger id="contact-budget" aria-label="Budget">
                  <SelectValue placeholder="Rough range" />
                </SelectTrigger>
                <SelectContent>
                  {BUDGETS.map((b) => (
                    <SelectItem key={b} value={b}>
                      {b}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        <Field
          id="contact-message"
          label="Message"
          error={touched.message ? errors.message : undefined}
          hint={
            <span
              className={
                messageLen > MESSAGE_MAX
                  ? 'text-[var(--destructive)]'
                  : 'text-[var(--muted-foreground)]'
              }
            >
              {messageLen}/{MESSAGE_MAX}
            </span>
          }
        >
          <Textarea
            id="contact-message"
            value={values.message}
            maxLength={MESSAGE_MAX + 100}
            placeholder="Tell me what you're thinking about - the more context, the better."
            className="min-h-40"
            aria-invalid={Boolean(touched.message && errors.message)}
            aria-describedby={touched.message && errors.message ? 'contact-message-err' : undefined}
            onChange={(e) => set('message', e.target.value)}
            onBlur={() => onBlur('message')}
          />
        </Field>

        {/* Honeypot: hidden from humans + assistive tech; bots that fill it are dropped. */}
        <div aria-hidden className="absolute left-[-9999px] top-[-9999px] h-0 w-0 overflow-hidden">
          <label htmlFor="contact-company-website">Company website (leave blank)</label>
          <input
            id="contact-company-website"
            type="text"
            tabIndex={-1}
            autoComplete="off"
            value={hp}
            onChange={(e) => setHp(e.target.value)}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
          <p className="text-xs text-[var(--muted-foreground)]">
            I'll never share your details. Typically reply within 1-2 business days.
          </p>
          <Button type="submit" size="lg" loading={submitting} disabled={submitting}>
            <Send className="h-4 w-4" /> Send message
          </Button>
        </div>
      </form>
    </Card>
  );
}

/** A labelled field wrapper with an accessible inline error + optional hint. */
function Field({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{label}</Label>
        {hint && <span className="text-xs tabular-nums">{hint}</span>}
      </div>
      {children}
      {error && (
        <p id={`${id}-err`} role="alert" className="text-xs text-[var(--destructive)]">
          {error}
        </p>
      )}
    </div>
  );
}

/** Elegant post-submit success experience. */
function SuccessCard({ result, onReset }: { result: ContactResult; onReset: () => void }) {
  const headingRef = React.useRef<HTMLHeadingElement | null>(null);
  // Move focus to the confirmation so keyboard/screen-reader users are taken to
  // the result rather than left on the now-unmounted form (focus + live region).
  React.useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <Card className="relative overflow-hidden p-8 text-center" role="status" aria-live="polite">
      <div aria-hidden className="pointer-events-none absolute inset-0 opacity-70">
        <div
          className="at-blob left-1/2 top-0 h-40 w-40 -translate-x-1/2"
          style={{ background: 'var(--at-success)' }}
        />
      </div>
      <div className="relative">
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-[var(--at-success)]/15 text-[var(--at-success)] motion-safe:animate-in motion-safe:zoom-in-50">
          <CircleCheck className="h-7 w-7" />
        </span>
        <h2
          ref={headingRef}
          tabIndex={-1}
          className="mt-5 text-2xl font-semibold tracking-tight focus:outline-none"
        >
          Message sent - thank you
        </h2>
        <p className="mx-auto mt-2 max-w-md text-[var(--muted-foreground)]">
          Your note landed safely. I read every message and reply {result.estimated_response}. If
          it's time-sensitive, just reply to the confirmation email.
        </p>
        <p className="mt-4 inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--at-surface-2)] px-3 py-1 text-xs text-[var(--muted-foreground)]">
          Reference <span className="font-mono text-[var(--foreground)]">{result.reference}</span>
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button asChild>
            <Link href="/">
              Back to home <ArrowRight className="h-4 w-4" />
            </Link>
          </Button>
          <Button type="button" variant="outline" onClick={onReset}>
            Send another
          </Button>
        </div>
        <div className="mt-6 flex items-center justify-center gap-4 text-sm text-[var(--muted-foreground)]">
          <a
            href="https://www.linkedin.com/in/obaidullah-zeeshan/"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 hover:text-[var(--foreground)]"
          >
            <Linkedin className="h-4 w-4" /> LinkedIn
          </a>
          <a
            href="https://github.com/ObaidGits"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 hover:text-[var(--foreground)]"
          >
            <Github className="h-4 w-4" /> GitHub
          </a>
        </div>
      </div>
    </Card>
  );
}
