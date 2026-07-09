'use client';

/**
 * Guided resume wizard (Task 7.3 / Req 8.5) — Atelier rebuild.
 *
 * A conversational, single-surface builder: a question card on the left with
 * step progress, an always-visible live preview on the right (reuses the
 * render engine → matches the PDF). Reuses the existing `/resume-wizard/*`
 * backend turn/finalize API unchanged. Answers persist as a local draft so an
 * accidental reload never loses progress.
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import Wand from 'lucide-react/dist/esm/icons/wand-sparkles';
import Check from 'lucide-react/dist/esm/icons/check';
import Link from 'next/link';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Textarea } from '@/components/atelier/input';
import { Badge } from '@/components/atelier/badge';
import { useToast } from '@/components/atelier/toast';
import { RenderTemplate } from '@/components/resume/render-template';
import { DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import type { ResumeData } from '@/components/dashboard/resume-component';
import {
  createInitialResumeWizardState,
  postResumeWizardTurn,
  finalizeResumeWizard,
  type ResumeWizardState,
  type ResumeWizardAction,
} from '@/lib/api/resume-wizard';

export default function WizardPage() {
  const router = useRouter();
  const { toast } = useToast();

  const [state, setState] = React.useState<ResumeWizardState>(() =>
    createInitialResumeWizardState()
  );
  const [answer, setAnswer] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [finalizing, setFinalizing] = React.useState(false);

  async function turn(action: ResumeWizardAction) {
    setBusy(true);
    try {
      const res = await postResumeWizardTurn({
        state,
        action,
        answer: action === 'answer' ? { text: answer.trim() } : undefined,
      });
      setState(res.state);
      setAnswer('');
    } catch (e) {
      toast({
        title: e instanceof Error ? e.message : 'The wizard could not continue',
        variant: 'error',
      });
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    setFinalizing(true);
    try {
      const res = await finalizeResumeWizard(state);
      toast({ title: 'Resume created', variant: 'success' });
      router.push(`/resumes/${res.resume_id}`);
    } catch (e) {
      toast({
        title: e instanceof Error ? e.message : 'Could not finish the resume',
        variant: 'error',
      });
    } finally {
      setFinalizing(false);
    }
  }

  const isIntro = state.step === 'intro';
  const isReview = state.step === 'review' || state.is_complete;
  const canAnswer = answer.trim().length > 0;
  const progressPct = state.progress.total
    ? Math.min(100, Math.round((state.progress.current / state.progress.total) * 100))
    : 0;

  return (
    <div className="space-y-4">
      <Link
        href="/import"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
      >
        <ArrowLeft className="h-4 w-4" /> Back to import
      </Link>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Question / conversation surface */}
        <div className="space-y-4">
          {/* Step progress */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
              <span className="inline-flex items-center gap-1.5">
                <Wand className="h-3.5 w-3.5 text-[var(--at-ai)]" /> Resume wizard
              </span>
              <span>
                {isReview ? 'Review' : `Step ${state.progress.current} of ${state.progress.total}`}
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--secondary)]">
              <div
                className="h-full rounded-full bg-[var(--primary)] transition-all"
                style={{ width: `${isReview ? 100 : progressPct}%` }}
              />
            </div>
          </div>

          {state.warnings.length > 0 && (
            <div className="rounded-[var(--radius-at-md)] bg-[var(--at-warning)]/10 px-3 py-2 text-xs text-[var(--at-warning)]">
              {state.warnings.join(' ')}
            </div>
          )}

          {isReview ? (
            <Card className="space-y-4 p-6 text-center">
              <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-[var(--at-success)]/15 text-[var(--at-success)]">
                <Check className="h-5 w-5" />
              </span>
              <div className="space-y-1">
                <h2 className="text-lg font-semibold">Your resume is ready to save</h2>
                <p className="text-sm text-[var(--muted-foreground)]">
                  Review the live preview. You can refine everything in the editor afterwards.
                </p>
              </div>
              <div className="flex justify-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => turn('back')}
                  disabled={busy || finalizing}
                >
                  Keep editing
                </Button>
                <Button onClick={finalize} loading={finalizing}>
                  Save resume
                </Button>
              </div>
            </Card>
          ) : (
            <Card className="space-y-4 p-6">
              <p className="text-base font-medium text-[var(--foreground)]">
                {state.current_question.text}
              </p>
              {state.current_question.section !== 'intro' && (
                <Badge variant="neutral" className="capitalize">
                  {state.current_question.section.replace(/([A-Z])/g, ' $1').trim()}
                </Badge>
              )}
              <Textarea
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Type your answer…"
                className="min-h-28"
                disabled={busy}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canAnswer)
                    void turn('answer');
                }}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex gap-2">
                  {state.history.length > 0 && (
                    <Button variant="ghost" size="sm" onClick={() => turn('back')} disabled={busy}>
                      Back
                    </Button>
                  )}
                  {!isIntro && (
                    <Button variant="ghost" size="sm" onClick={() => turn('skip')} disabled={busy}>
                      Skip
                    </Button>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {!isIntro && state.progress.current >= 3 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => turn('review')}
                      disabled={busy}
                    >
                      Review &amp; finish
                    </Button>
                  )}
                  <Button
                    size="sm"
                    onClick={() => turn('answer')}
                    loading={busy}
                    disabled={!canAnswer}
                  >
                    {isIntro ? 'Start' : 'Next'} <ArrowRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* Live preview */}
        <div className="lg:sticky lg:top-6 lg:self-start">
          <p className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">Live preview</p>
          <div className="overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-white">
            <div className="max-h-[70vh] overflow-y-auto p-4">
              <RenderTemplate
                data={state.resume_data as ResumeData}
                settings={DEFAULT_TEMPLATE_SETTINGS}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
