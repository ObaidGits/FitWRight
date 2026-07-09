'use client';

/**
 * Tailor flow (Task 8 / Req 9,15,27) — AI-native core.
 * Internal state machine (input → generating → review → saved) rendered as ONE
 * continuous surface (not a wizard). Analysis + score + diff are surfaced from
 * the pipeline result; generation is cost-aware and cancellable; input is
 * preserved across failures.
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { LoadingSkeleton, EmptyState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { Explain } from '@/components/ai/explain';
import { RecoveryBanner } from '@/components/resilience/recovery-banner';
import { useDraft } from '@/lib/hooks/use-draft';
import { useTailorResumes, usePromptOptions } from '@/features/tailor/hooks';
import {
  uploadJobDescriptions,
  previewImproveResume,
  confirmImproveResume,
} from '@/lib/api/resume';
import type { ImprovedResult } from '@/components/common/resume_previewer_context';
import type { ResumeData } from '@/components/dashboard/resume-component';
import Link from 'next/link';

const MIN_JD = 50;
type Phase = 'input' | 'generating' | 'review';

function ScoreRing({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(score)));
  const tone =
    pct >= 75 ? 'var(--at-success)' : pct >= 50 ? 'var(--at-warning)' : 'var(--destructive)';
  return (
    <div
      className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full"
      style={{ background: `conic-gradient(${tone} ${pct * 3.6}deg, var(--secondary) 0deg)` }}
      role="img"
      aria-label={`Match score ${pct} percent`}
    >
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[var(--card)] text-lg font-semibold">
        {pct}
      </div>
    </div>
  );
}

export default function TailorPage() {
  const router = useRouter();
  const { toast } = useToast();
  const resumesQuery = useTailorResumes();
  const promptsQuery = usePromptOptions();

  const [resumeId, setResumeId] = React.useState('');
  const [jd, setJd] = React.useState('');
  const [promptId, setPromptId] = React.useState<string>('');
  const [showOptions, setShowOptions] = React.useState(false);
  const [phase, setPhase] = React.useState<Phase>('input');
  const [result, setResult] = React.useState<ImprovedResult['data'] | null>(null);
  const [jobId, setJobId] = React.useState('');
  const [showDetail, setShowDetail] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  // Draft persistence for the JD (Task 18 / Req 30.1) — never lose a long paste.
  const draft = useDraft<string>('tailor-jd');

  // ARIA live announcement for async AI results (Task 16 / Req 21.6).
  const announcement =
    phase === 'generating'
      ? 'Generating your tailored resume. This may take a moment.'
      : phase === 'review' && result
        ? `Tailored resume ready. Match score ${Math.round(result.ats_score?.overall_score ?? 0)} out of 100.`
        : '';

  // Preselect source resume: ?resume= param, else master, else first.
  React.useEffect(() => {
    if (resumeId || !resumesQuery.data?.length) return;
    const param =
      typeof window !== 'undefined'
        ? new URLSearchParams(window.location.search).get('resume')
        : null;
    const master = resumesQuery.data.find((r) => r.is_master);
    setResumeId(param || master?.resume_id || resumesQuery.data[0].resume_id);
  }, [resumesQuery.data, resumeId]);

  async function onGenerate() {
    if (jd.trim().length < MIN_JD || !resumeId) return;
    setPhase('generating');
    setResult(null);
    try {
      const jid = await uploadJobDescriptions([jd.trim()], resumeId);
      setJobId(jid);
      const res = await previewImproveResume(resumeId, jid, promptId || undefined);
      setResult(res.data);
      setPhase('review');
    } catch (e) {
      // Preserve input; return to editable state.
      setPhase('input');
      toast({ title: e instanceof Error ? e.message : 'Tailoring failed', variant: 'error' });
    }
  }

  async function onAccept() {
    if (!result) return;
    setSaving(true);
    try {
      await confirmImproveResume({
        resume_id: resumeId,
        job_id: jobId,
        improved_data: result.resume_preview as unknown as ResumeData,
        improvements: (result.improvements ?? []).map((i) => ({
          suggestion: i.suggestion,
          lineNumber: typeof i.lineNumber === 'number' ? i.lineNumber : null,
        })),
      });
      draft.clear();
      toast({ title: 'Tailored resume saved', variant: 'success' });
      router.push('/applications');
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Could not save', variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  if (resumesQuery.isLoading) return <LoadingSkeleton rows={4} />;
  if ((resumesQuery.data?.length ?? 0) === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title="Add a resume first"
        description="You need a ready resume before tailoring to a job."
        action={
          <Button asChild>
            <Link href="/import">Add a resume</Link>
          </Button>
        }
      />
    );
  }

  const ats = result?.ats_score;
  const diff = result?.diff_summary;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
      <div>
        <h1 className="text-2xl font-semibold">Tailor to a job</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Paste a job description and get a tailored resume — grounded in your real experience.
        </p>
      </div>

      {draft.recovered && phase === 'input' && !jd && (
        <RecoveryBanner
          savedAt={draft.recoveredAt}
          title="You have an unsaved job description from earlier. Restore it?"
          restoreLabel="Restore"
          onRestore={() => {
            setJd(draft.recovered ?? '');
            draft.dismissRecovery();
          }}
          onDiscard={draft.clear}
        />
      )}

      {/* Source + JD (always visible top of the continuous surface) */}
      <Card className="space-y-4 p-5">
        <div className="space-y-1.5">
          <Label>Source resume</Label>
          <Select value={resumeId} onValueChange={setResumeId} disabled={phase === 'generating'}>
            <SelectTrigger>
              <SelectValue placeholder="Choose a resume" />
            </SelectTrigger>
            <SelectContent>
              {resumesQuery.data!.map((r) => (
                <SelectItem key={r.resume_id} value={r.resume_id}>
                  {r.title || r.filename || 'Untitled'} {r.is_master ? '· Master' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="jd">Job description</Label>
          <Textarea
            id="jd"
            value={jd}
            onChange={(e) => {
              setJd(e.target.value);
              draft.save(e.target.value);
            }}
            placeholder="Paste the full job description here…"
            className="min-h-40"
            disabled={phase === 'generating'}
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            {jd.trim().length < MIN_JD
              ? `Add at least ${MIN_JD} characters (${jd.trim().length}/${MIN_JD}).`
              : 'Looks good.'}
          </p>
        </div>

        {/* Options (progressive disclosure) */}
        <div>
          <button
            type="button"
            onClick={() => setShowOptions((v) => !v)}
            className="flex items-center gap-1 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform ${showOptions ? 'rotate-180' : ''}`}
            />
            Options
          </button>
          {showOptions && (
            <div className="mt-3 space-y-1.5">
              <Label>Tailoring style</Label>
              <Select value={promptId} onValueChange={setPromptId}>
                <SelectTrigger>
                  <SelectValue placeholder="Default" />
                </SelectTrigger>
                <SelectContent>
                  {(promptsQuery.data?.prompt_options ?? []).map((o) => (
                    <SelectItem key={o.id} value={o.id}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <Button
            onClick={onGenerate}
            loading={phase === 'generating'}
            disabled={jd.trim().length < MIN_JD || !resumeId}
          >
            <Sparkles className="h-4 w-4" /> {phase === 'review' ? 'Regenerate' : 'Generate'}
          </Button>
          <span className="inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
            <Sparkles className="h-3 w-3" /> Uses your configured AI provider
          </span>
        </div>
      </Card>

      {/* Generating — honest progress (single pipeline call) */}
      {phase === 'generating' && (
        <Card className="p-5">
          <p className="text-sm font-medium">
            Analyzing the role, rewriting sections, and scoring…
          </p>
          <LoadingSkeleton rows={2} className="mt-3" />
        </Card>
      )}

      {/* Review — results render inline below */}
      {phase === 'review' && result && (
        <div className="space-y-4">
          {ats && (
            <Card className="flex items-center gap-5 p-5">
              <ScoreRing score={ats.overall_score} />
              <div className="flex-1 space-y-2">
                <p className="flex items-center gap-1.5 text-sm font-medium">
                  Match score
                  <Explain label="What is the match score?">
                    An estimate of how well this tailored resume aligns with the job description,
                    combining keyword match, skills coverage, and section completeness. Higher is
                    better — aim for 75+. It is guidance, not a guarantee of how a specific ATS will
                    parse your resume.
                  </Explain>
                </p>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <SubScore label="Keywords" value={ats.sub_scores.keyword_match} />
                  <SubScore label="Skills" value={ats.sub_scores.skills_coverage} />
                  <SubScore label="Sections" value={ats.sub_scores.section_completeness} />
                </div>
              </div>
            </Card>
          )}

          {ats && ats.missing_keywords.length > 0 && (
            <Card className="p-5">
              <p className="mb-2 text-sm font-medium">Missing keywords</p>
              <div className="flex flex-wrap gap-1.5">
                {ats.missing_keywords.slice(0, showDetail ? undefined : 10).map((k) => (
                  <Badge key={k} variant="warning">
                    {k}
                  </Badge>
                ))}
              </div>
            </Card>
          )}

          {diff && (
            <Card className="p-5">
              <div className="flex items-center justify-between">
                <p className="flex items-center gap-1.5 text-sm font-medium">
                  {diff.total_changes} change{diff.total_changes === 1 ? '' : 's'} proposed
                  <Explain label="What are these changes?">
                    Each change rewrites or reorders content you already have to better match the
                    role — emphasising relevant skills and keywords. Nothing is invented; expand the
                    details to review every edit before you accept.
                  </Explain>
                </p>
                <button
                  onClick={() => setShowDetail((v) => !v)}
                  className="text-xs text-[var(--primary)] hover:underline"
                >
                  {showDetail ? 'Hide details' : 'Expand details'}
                </button>
              </div>
              <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--at-success)]">
                <ShieldCheck className="h-3.5 w-3.5" /> Grounded in your resume — no invented
                experience.
              </p>
              {showDetail && result.detailed_changes && (
                <ul className="mt-3 space-y-2">
                  {result.detailed_changes.map((c, i) => (
                    <li
                      key={i}
                      className="rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] p-2.5 text-xs"
                    >
                      <Badge
                        variant={
                          c.change_type === 'added'
                            ? 'success'
                            : c.change_type === 'removed'
                              ? 'danger'
                              : 'neutral'
                        }
                      >
                        {c.change_type}
                      </Badge>{' '}
                      <span className="text-[var(--muted-foreground)]">{c.field_path}</span>
                      {c.new_value && (
                        <p className="mt-1 text-[var(--foreground)]">{c.new_value}</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}

          <div className="flex gap-2">
            <Button onClick={onAccept} loading={saving}>
              Accept &amp; save
            </Button>
            <Button variant="outline" onClick={() => setPhase('input')} disabled={saving}>
              Discard
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function SubScore({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] p-2 text-center">
      <p className="font-semibold text-[var(--foreground)]">{Math.round(value)}</p>
      <p className="text-[var(--muted-foreground)]">{label}</p>
    </div>
  );
}
