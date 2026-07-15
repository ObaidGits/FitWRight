'use client';

/**
 * Review form (Connect page). An accessible star rating (radiogroup, keyboard-
 * operable) plus title/body with a counter, optional attribution + anonymous
 * mode, honeypot + submit-timing spam fields, draft persistence, and an animated
 * success state. Wired to `POST /reviews` via {@link submitReview}. Reviews are
 * moderated before appearing publicly (communicated in the copy).
 */
import * as React from 'react';
import Star from 'lucide-react/dist/esm/icons/star';
import Heart from 'lucide-react/dist/esm/icons/heart';
import CircleCheck from 'lucide-react/dist/esm/icons/circle-check';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import { useDraft } from '@/lib/hooks/use-draft';
import { submitReview, ReviewError } from '@/lib/api/reviews';

const BODY_MAX = 2000;
const BODY_MIN = 10;
const RATING_LABELS = ['', 'Poor', 'Fair', 'Good', 'Great', 'Excellent'];

type Draft = { rating: number; title: string; body: string; name: string; anonymous: boolean };

export function ReviewForm() {
  const draft = useDraft<Draft>('connect-review');
  const [rating, setRating] = React.useState(0);
  const [hover, setHover] = React.useState(0);
  const [title, setTitle] = React.useState('');
  const [body, setBody] = React.useState('');
  const [name, setName] = React.useState('');
  const [anonymous, setAnonymous] = React.useState(false);
  const [errors, setErrors] = React.useState<{ rating?: string; title?: string; body?: string }>(
    {}
  );
  const [submitting, setSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);

  const [hp, setHp] = React.useState('');
  const mountedAt = React.useRef(Date.now());
  const errorRef = React.useRef<HTMLDivElement | null>(null);

  function persist(next: Partial<Draft>) {
    draft.save({ rating, title, body, name, anonymous, ...next });
  }

  function validate() {
    const e: typeof errors = {};
    if (rating < 1) e.rating = 'Please pick a rating.';
    if (!title.trim()) e.title = 'A short headline helps.';
    else if (title.trim().length > 120) e.title = 'Keep the headline under 120 characters.';
    const b = body.trim();
    if (!b) e.body = 'Tell us a little about your experience.';
    else if (b.length < BODY_MIN)
      e.body = `A bit more detail helps (at least ${BODY_MIN} characters).`;
    else if (b.length > BODY_MAX) e.body = `Please keep it under ${BODY_MAX} characters.`;
    return e;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const found = validate();
    setErrors(found);
    if (Object.values(found).some(Boolean)) return;

    setSubmitting(true);
    try {
      await submitReview({
        rating,
        title: title.trim(),
        body: body.trim(),
        name: anonymous || !name.trim() ? undefined : name.trim(),
        company_website: hp,
        elapsed_ms: Date.now() - mountedAt.current,
      });
      draft.clear();
      setDone(true);
    } catch (err) {
      setFormError(err instanceof ReviewError ? err.message : 'Could not submit your review.');
      requestAnimationFrame(() => errorRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <Card className="p-6 text-center" role="status" aria-live="polite">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--at-success)]/15 text-[var(--at-success)] motion-safe:animate-in motion-safe:zoom-in-50">
          <CircleCheck className="h-6 w-6" />
        </span>
        <h3 className="mt-4 text-lg font-semibold">Thank you for the review</h3>
        <p className="mx-auto mt-1.5 max-w-sm text-sm text-[var(--muted-foreground)]">
          It means a lot and genuinely shapes what gets built next. Reviews are checked before they
          appear publicly.
        </p>
      </Card>
    );
  }

  const bodyLen = body.trim().length;
  const displayRating = hover || rating;

  return (
    <Card className="relative overflow-hidden p-6 md:p-7">
      <div className="mb-4 flex items-center gap-2">
        <Heart className="h-5 w-5 text-[var(--at-ai)]" />
        <h3 className="text-lg font-semibold">Leave a review</h3>
      </div>

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

        {/* Accessible star rating: a radiogroup of 5 buttons, keyboard-operable. */}
        <div className="space-y-1.5">
          <Label id="review-rating-label">Your rating</Label>
          <div
            role="radiogroup"
            aria-labelledby="review-rating-label"
            aria-describedby={errors.rating ? 'review-rating-err' : undefined}
            className="flex items-center gap-1"
            onMouseLeave={() => setHover(0)}
          >
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={rating === n}
                aria-label={`${n} star${n > 1 ? 's' : ''} — ${RATING_LABELS[n]}`}
                onClick={() => {
                  setRating(n);
                  persist({ rating: n });
                  setErrors((p) => ({ ...p, rating: undefined }));
                }}
                onMouseEnter={() => setHover(n)}
                onFocus={() => setHover(n)}
                onBlur={() => setHover(0)}
                className="rounded-[var(--radius-at-sm)] p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              >
                <Star
                  className={`h-7 w-7 transition-transform hover:scale-110 ${
                    n <= displayRating
                      ? 'fill-[var(--at-warning)] text-[var(--at-warning)]'
                      : 'text-[var(--muted-foreground)]'
                  }`}
                />
              </button>
            ))}
            <span className="ml-2 text-sm text-[var(--muted-foreground)]" aria-live="polite">
              {displayRating ? RATING_LABELS[displayRating] : ''}
            </span>
          </div>
          {errors.rating && (
            <p id="review-rating-err" role="alert" className="text-xs text-[var(--destructive)]">
              {errors.rating}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="review-title">Headline</Label>
          <Input
            id="review-title"
            value={title}
            maxLength={120}
            placeholder="Sum it up in a few words"
            aria-invalid={Boolean(errors.title)}
            aria-describedby={errors.title ? 'review-title-err' : undefined}
            onChange={(e) => {
              setTitle(e.target.value);
              persist({ title: e.target.value });
            }}
          />
          {errors.title && (
            <p id="review-title-err" role="alert" className="text-xs text-[var(--destructive)]">
              {errors.title}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="review-body">Your review</Label>
            <span
              className={`text-xs tabular-nums ${
                bodyLen > BODY_MAX ? 'text-[var(--destructive)]' : 'text-[var(--muted-foreground)]'
              }`}
            >
              {bodyLen}/{BODY_MAX}
            </span>
          </div>
          <Textarea
            id="review-body"
            value={body}
            maxLength={BODY_MAX + 100}
            placeholder="What did you like? What could be better?"
            className="min-h-32"
            aria-invalid={Boolean(errors.body)}
            aria-describedby={errors.body ? 'review-body-err' : undefined}
            onChange={(e) => {
              setBody(e.target.value);
              persist({ body: e.target.value });
            }}
          />
          {errors.body && (
            <p id="review-body-err" role="alert" className="text-xs text-[var(--destructive)]">
              {errors.body}
            </p>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2 sm:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="review-name">Your name (optional)</Label>
            <Input
              id="review-name"
              value={name}
              maxLength={100}
              autoComplete="name"
              disabled={anonymous}
              placeholder={anonymous ? 'Posting anonymously' : 'How to credit you'}
              onChange={(e) => {
                setName(e.target.value);
                persist({ name: e.target.value });
              }}
            />
          </div>
          <label className="flex items-center gap-2.5 pb-2 text-sm">
            <Switch
              checked={anonymous}
              onCheckedChange={(c) => {
                setAnonymous(c);
                persist({ anonymous: c });
              }}
              aria-label="Post anonymously"
            />
            Post anonymously
          </label>
        </div>

        {/* Honeypot */}
        <div aria-hidden className="absolute left-[-9999px] top-[-9999px] h-0 w-0 overflow-hidden">
          <label htmlFor="review-company-website">Company website (leave blank)</label>
          <input
            id="review-company-website"
            type="text"
            tabIndex={-1}
            autoComplete="off"
            value={hp}
            onChange={(e) => setHp(e.target.value)}
          />
        </div>

        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--muted-foreground)]">
            Checked before appearing publicly.
          </p>
          <Button type="submit" loading={submitting} disabled={submitting}>
            <Star className="h-4 w-4" /> Submit review
          </Button>
        </div>
      </form>
    </Card>
  );
}
