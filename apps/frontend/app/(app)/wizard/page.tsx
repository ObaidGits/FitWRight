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
import { useQueryClient } from '@tanstack/react-query';
import { invalidateResumeLists } from '@/lib/query/client';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import Wand from 'lucide-react/dist/esm/icons/wand-sparkles';
import Check from 'lucide-react/dist/esm/icons/check';
import Link from 'next/link';

import Key from 'lucide-react/dist/esm/icons/key-round';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Textarea } from '@/components/atelier/input';
import { Badge } from '@/components/atelier/badge';
import { useToast } from '@/components/atelier/toast';
import { useSystemStatus } from '@/features/home/hooks';
import { RenderTemplate } from '@/components/resume/render-template';
import { UnsavedChangesGuard } from '@/components/common/unsaved-changes-guard';
import { useDraft } from '@/lib/hooks/use-draft';
import { Skeleton } from '@/components/atelier/skeleton';
import { type TemplateSettings, DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import { getPreferredTemplateSettings } from '@/lib/resume/preferred-template';
import { updateResumeTemplateSettings } from '@/lib/api/resume';
import type { ResumeData } from '@/components/dashboard/resume-component';
import {
  createInitialResumeWizardState,
  postResumeWizardTurn,
  prefillResumeWizard,
  finalizeResumeWizard,
  type ResumeWizardState,
  type ResumeWizardAction,
  type ResumeWizardStructuredUpdate,
} from '@/lib/api/resume-wizard';
import {
  ContactFields,
  EducationCard,
  ExperienceCard,
  IdentityFields,
  ProjectCard,
  SkillsChips,
  applyStructuredToResume,
  isStructuredSection,
} from './structured-sections';

export default function WizardPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { toast } = useToast();
  const statusQuery = useSystemStatus();
  const aiUnconfigured = statusQuery.data && !statusQuery.data.llm_configured;

  const [state, setState] = React.useState<ResumeWizardState>(() =>
    createInitialResumeWizardState()
  );
  const [answer, setAnswer] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [finalizing, setFinalizing] = React.useState(false);
  // Live-preview template. Defaults to the standard template on the server and
  // adopts the user's gallery choice (localStorage) after mount, so the wizard
  // opens rendered in whatever template they picked — without a hydration
  // mismatch (server + first client render are identical).
  const [previewSettings, setPreviewSettings] =
    React.useState<TemplateSettings>(DEFAULT_TEMPLATE_SETTINGS);
  React.useEffect(() => {
    setPreviewSettings(getPreferredTemplateSettings());
  }, []);
  // Pending structured-section edits (W-P1.1) + whether they pass validation.
  const [structured, setStructured] = React.useState<ResumeWizardStructuredUpdate | null>(null);
  const [structuredValid, setStructuredValid] = React.useState(false);
  // Once the resume is saved we stop guarding — the intentional redirect to the
  // new resume must not be treated as "leaving with unsaved work".
  const [saved, setSaved] = React.useState(false);

  // Master-resume choice. A user with no master can opt to set this as their
  // master (default on); a user who already has one saves a regular resume
  // (we never silently replace an existing master).
  const hasMaster = !!statusQuery.data?.has_master_resume;
  const [makeMaster, setMakeMaster] = React.useState(true);
  React.useEffect(() => {
    if (hasMaster) setMakeMaster(false);
  }, [hasMaster]);

  // Real draft persistence (W-P0.2). The full wizard state is serialisable, so
  // persist it to localStorage on every change and rehydrate on mount — a reload
  // or accidental navigation no longer loses progress. Cleared on finalize.
  const draft = useDraft<ResumeWizardState>('resume-wizard');
  const { save: saveDraft, clear: clearDraft, recovered } = draft;
  const consumedRecoveryRef = React.useRef(false);

  React.useEffect(() => {
    if (consumedRecoveryRef.current) return;
    if (recovered) {
      consumedRecoveryRef.current = true;
      setState(recovered);
    }
  }, [recovered]);

  // Prefill from the user's profile on first load (W-P3.2). Best-effort and
  // non-blocking: the empty initial state renders instantly; if the server
  // returns a profile-prefilled state we adopt it — but only while the wizard is
  // still pristine (no typing, no recovered draft), so we never clobber work.
  const prefillTriedRef = React.useRef(false);
  React.useEffect(() => {
    if (prefillTriedRef.current) return;
    prefillTriedRef.current = true;
    let cancelled = false;
    void (async () => {
      const prefilled = await prefillResumeWizard();
      if (cancelled || !prefilled || consumedRecoveryRef.current) return;
      const meaningful =
        !!prefilled.resume_data.personalInfo?.name?.trim() ||
        (prefilled.resume_data.workExperience?.length ?? 0) > 0 ||
        (prefilled.resume_data.education?.length ?? 0) > 0 ||
        (prefilled.resume_data.additional?.technicalSkills?.length ?? 0) > 0;
      if (!meaningful) return;
      setState((current) => {
        const pristine =
          current.step === 'intro' &&
          current.history.length === 0 &&
          !current.resume_data.personalInfo?.name?.trim();
        return pristine ? prefilled : current;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  React.useEffect(() => {
    if (saved) return;
    // Don't persist the pristine initial state — only real progress.
    const pristine =
      state.step === 'intro' &&
      state.history.length === 0 &&
      !state.resume_data.personalInfo?.name?.trim();
    if (pristine) return;
    saveDraft(state);
  }, [state, saved, saveDraft]);

  async function turn(
    action: ResumeWizardAction,
    structuredPayload?: ResumeWizardStructuredUpdate
  ) {
    setBusy(true);
    try {
      const res = await postResumeWizardTurn({
        state,
        action,
        answer: action === 'answer' ? { text: answer.trim() } : undefined,
        structured: action === 'structured' ? (structuredPayload ?? {}) : undefined,
      });
      setState(res.state);
      // On Back, repopulate the input with the restored answer so the user can
      // edit it (W-P0.1); every other action starts from a clean input.
      setAnswer(action === 'back' ? (res.state.restored_answer ?? '') : '');
      setStructured(null);
      setStructuredValid(false);
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
      const res = await finalizeResumeWizard(state, makeMaster);
      invalidateResumeLists(qc);
      setSaved(true);
      clearDraft();
      // Persist the template the user picked (shown in the live preview) onto
      // the newly created resume so it opens in that design — best-effort, never
      // blocks navigation.
      void updateResumeTemplateSettings(res.resume_id, previewSettings).catch(() => {
        /* best-effort */
      });
      toast({ title: res.message || 'Resume saved', variant: 'success' });
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
  const section = state.current_question.section;
  const isStructured = !isReview && isStructuredSection(section);
  const canAnswer = answer.trim().length > 0;
  // The name is the one hard requirement to finalize; once it's captured the
  // user may review/finish at any time rather than at a fixed step count
  // (W-P0.5, replaces the old `progress.current >= 3` magic gate).
  const canReview = !isIntro && !!state.resume_data.personalInfo?.name?.trim();

  // Has the user changed a structured field vs. what's already stored? Used both
  // to gate the unsaved-changes guard and to drive the optimistic preview.
  const structuredDirty = React.useMemo(() => {
    if (!structured) return false;
    const info = (state.resume_data.personalInfo ?? {}) as Record<string, string>;
    for (const [key, value] of Object.entries(structured.personal_info ?? {})) {
      if ((info[key] ?? '') !== value) return true;
    }
    if (structured.technical_skills) {
      const current = state.resume_data.additional?.technicalSkills ?? [];
      if (structured.technical_skills.length !== current.length) return true;
      if (structured.technical_skills.some((s, i) => s !== current[i])) return true;
    }
    if (structured.education?.institution?.trim() || structured.education?.degree?.trim()) {
      return true;
    }
    if (structured.experiences?.some((e) => e.title?.trim() || e.company?.trim())) return true;
    if (structured.projects?.some((p) => p.name?.trim())) return true;
    return false;
  }, [structured, state.resume_data]);

  // Optimistic preview (W-P1.3): reflect structured edits instantly.
  const previewData = React.useMemo(
    () =>
      isStructured ? applyStructuredToResume(state.resume_data, structured) : state.resume_data,
    [isStructured, state.resume_data, structured]
  );
  const progressPct = state.progress.total
    ? Math.min(100, Math.round((state.progress.current / state.progress.total) * 100))
    : 0;

  // Dirty once the user has invested any answers (moved past intro or typed a
  // pending answer) and hasn't saved. Protects a partially-built resume from an
  // accidental reload, back button, or sidebar click.
  const dirty =
    !saved && (state.history.length > 0 || state.step !== 'intro' || canAnswer || structuredDirty);

  // The wizard is entirely AI-driven — block it up front (rather than letting
  // the first turn fail) when no provider is configured.
  if (aiUnconfigured) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-6">
        <Link
          href="/import"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-4 w-4" /> Back to import
        </Link>
        <Card className="space-y-4 p-6 text-center">
          <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-[var(--at-warning)]/15 text-[var(--at-warning)]">
            <Key className="h-5 w-5" />
          </span>
          <div className="space-y-1">
            <h2 className="text-lg font-semibold">Add an AI provider first</h2>
            <p className="text-sm text-[var(--muted-foreground)]">
              The wizard uses AI to draft your resume. Add a provider key in settings, then start
              the wizard.
            </p>
          </div>
          <div className="flex justify-center gap-2">
            <Button asChild>
              <Link href="/settings">Open settings</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/import">Upload instead</Link>
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <UnsavedChangesGuard
        when={dirty}
        title="Leave the wizard?"
        description="Your answers so far haven’t been saved into a resume. If you leave now, they’ll be lost."
        confirmLabel="Leave wizard"
        cancelLabel="Keep building"
      />
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
                {isReview
                  ? 'Review'
                  : `${state.progress.current} of ${state.progress.total} sections`}
              </span>
            </div>
            <div
              role="progressbar"
              aria-label="Resume completion"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={isReview ? 100 : progressPct}
              className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--secondary)]"
            >
              <div
                className="h-full rounded-full bg-[var(--primary)] transition-all"
                style={{ width: `${isReview ? 100 : progressPct}%` }}
              />
            </div>
            {!isIntro && state.scores && (
              <div
                className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-0.5 text-xs text-[var(--muted-foreground)]"
                aria-live="polite"
              >
                <span>
                  Quality{' '}
                  <strong className="text-[var(--foreground)]">{state.scores.completeness}%</strong>
                </span>
                <span aria-hidden>·</span>
                <span>
                  ATS <strong className="text-[var(--foreground)]">{state.scores.ats}%</strong>
                </span>
              </div>
            )}
          </div>

          {state.warnings.length > 0 && (
            <ul
              className="space-y-1 rounded-[var(--radius-at-md)] bg-[var(--at-warning)]/10 px-3 py-2 text-xs text-[var(--at-warning)]"
              aria-live="polite"
            >
              {state.warnings.map((warning) => (
                <li key={warning} className="flex gap-1.5">
                  <span aria-hidden>•</span>
                  <span>{warning}</span>
                </li>
              ))}
            </ul>
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
              {hasMaster ? (
                <p className="text-xs text-[var(--muted-foreground)]">
                  You already have a master resume, so this will be saved as a separate resume. You
                  can make it your master later from the resume editor.
                </p>
              ) : (
                <label className="flex items-center justify-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={makeMaster}
                    onChange={(e) => setMakeMaster(e.target.checked)}
                    disabled={finalizing}
                  />
                  Set as my master resume
                </label>
              )}
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
              {isStructured ? (
                section === 'skills' ? (
                  <SkillsChips
                    data={state.resume_data}
                    suggestions={state.inferred_skills}
                    onChange={setStructured}
                    onValidityChange={setStructuredValid}
                  />
                ) : section === 'contact' ? (
                  <ContactFields
                    data={state.resume_data}
                    onChange={setStructured}
                    onValidityChange={setStructuredValid}
                  />
                ) : section === 'education' ? (
                  <EducationCard
                    data={state.resume_data}
                    onChange={setStructured}
                    onValidityChange={setStructuredValid}
                  />
                ) : section === 'personalProjects' ? (
                  <ProjectCard
                    data={state.resume_data}
                    onChange={setStructured}
                    onValidityChange={setStructuredValid}
                  />
                ) : section === 'workExperience' || section === 'internships' ? (
                  <ExperienceCard
                    section={section}
                    data={state.resume_data}
                    onChange={setStructured}
                    onValidityChange={setStructuredValid}
                  />
                ) : (
                  <IdentityFields
                    data={state.resume_data}
                    onChange={setStructured}
                    onValidityChange={setStructuredValid}
                  />
                )
              ) : busy ? (
                <div className="space-y-2" role="status" aria-live="polite" aria-busy>
                  <p className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                    <Wand className="h-3.5 w-3.5 text-[var(--at-ai)]" /> Working on your resume…
                  </p>
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-5/6" />
                </div>
              ) : (
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
              )}
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
                  {canReview && (
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
                    onClick={() =>
                      isStructured ? turn('structured', structured ?? {}) : turn('answer')
                    }
                    loading={busy}
                    disabled={isStructured ? !structuredValid : !canAnswer}
                  >
                    {isIntro ? 'Start' : isStructured ? 'Continue' : 'Next'}{' '}
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* Live preview */}
        <div className="lg:sticky lg:top-6 lg:self-start">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-[var(--muted-foreground)]">Live preview</p>
            <Link
              href="/templates"
              className="text-xs font-medium text-[var(--at-ai)] hover:underline"
            >
              Change template
            </Link>
          </div>
          <div className="overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-white">
            {/* scrollbar-gutter:stable reserves the scrollbar track so toggling
                it (as content height crosses the max-height) can't change the
                preview's available width and re-trigger a fit-to-width rescale. */}
            <div className="max-h-[70vh] overflow-y-auto p-4 [scrollbar-gutter:stable]">
              <RenderTemplate data={previewData as ResumeData} settings={previewSettings} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
