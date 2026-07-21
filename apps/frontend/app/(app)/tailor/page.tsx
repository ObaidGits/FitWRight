'use client';

/**
 * Tailor flow (Task 8 / Req 9,15,27) - AI-native core.
 * Internal state machine (input -> generating -> review -> saved) rendered as ONE
 * continuous surface (not a wizard). Analysis + score + diff are surfaced from
 * the pipeline result; generation is cost-aware and cancellable; input is
 * preserved across failures.
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateApplicationLists, invalidateResumeLists } from '@/lib/query/client';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import Target from 'lucide-react/dist/esm/icons/target';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import RotateCw from 'lucide-react/dist/esm/icons/rotate-cw';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { LoadingSkeleton, EmptyState } from '@/components/atelier/states';
import { AiProgress } from '@/components/ai/ai-progress';
import { TAILOR_MESSAGES, ESTIMATE_MEDIUM } from '@/lib/ai-progress-copy';
import { useToast } from '@/components/atelier/toast';
import { Explain } from '@/components/ai/explain';
import { RecoveryBanner } from '@/components/resilience/recovery-banner';
import { useDraft } from '@/lib/hooks/use-draft';
import { useTailorResumes, usePromptOptions } from '@/features/tailor/hooks';
import { useSystemStatus } from '@/features/home/hooks';
import Key from 'lucide-react/dist/esm/icons/key-round';
import { fetchJdFromUrl, jdSourceLabel, type JdConfidence } from '@/lib/api/jd';
import {
  uploadJobDescriptions,
  previewImproveResume,
  streamImproveResume,
  cancelTailorStream,
  TailorStreamCancelled,
  confirmImproveResume,
  analyzeJob,
  type JobAnalyzeResult,
  type TailorStageName,
} from '@/lib/api/resume';
import type { ImprovedResult } from '@/components/common/resume_previewer_context';
import type { ResumeData } from '@/components/dashboard/resume-component';
import { ApiError, toMessage } from '@/lib/api/errors';
import Link from 'next/link';

const MIN_JD = 50;
type Phase = 'input' | 'generating' | 'review' | 'error';

type StageStatus = 'pending' | 'active' | 'done';

// The real backend pipeline stages, in order - each maps 1:1 to a boundary the
// server emits, so progress is honest (never a fabricated timer).
const TAILOR_STAGES: { key: TailorStageName; label: string }[] = [
  { key: 'keywords', label: 'Analyzing the role' },
  { key: 'plan', label: 'Planning skill matches' },
  { key: 'rewrite', label: 'Rewriting your sections' },
  { key: 'refine', label: 'Refining and fact-checking' },
  { key: 'score', label: 'Scoring the match' },
];

function freshStages(): Record<TailorStageName, StageStatus> {
  return {
    keywords: 'pending',
    plan: 'pending',
    rewrite: 'pending',
    refine: 'pending',
    score: 'pending',
  };
}

/** Extraction-confidence badge with a "how it was extracted" tooltip (§31). */
function ConfidenceBadge({
  level,
  score,
  source,
}: {
  level: JdConfidence;
  score?: number;
  source?: string;
}) {
  const tone =
    level === 'HIGH'
      ? { bg: 'var(--at-success)', label: 'High confidence' }
      : level === 'MEDIUM'
        ? { bg: 'var(--at-warning)', label: 'Medium confidence' }
        : { bg: 'var(--destructive)', label: 'Low confidence' };
  const title = `Extracted from ${jdSourceLabel(source)}${
    typeof score === 'number' ? ` - score ${score}/100` : ''
  }. ${level === 'HIGH' ? 'Looks reliable.' : 'Please verify the text before tailoring.'}`;
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium"
      style={{
        borderColor: `${tone.bg}66`,
        color: 'var(--foreground)',
        background: `${tone.bg}1a`,
      }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: tone.bg }} aria-hidden />
      {tone.label}
    </span>
  );
}

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
  const qc = useQueryClient();
  const resumesQuery = useTailorResumes();
  const promptsQuery = usePromptOptions();
  const statusQuery = useSystemStatus();
  const aiUnconfigured = statusQuery.data && !statusQuery.data.llm_configured;

  const [resumeId, setResumeId] = React.useState('');
  const [jd, setJd] = React.useState('');
  const [promptId, setPromptId] = React.useState<string>('');
  const [showOptions, setShowOptions] = React.useState(false);
  const [phase, setPhase] = React.useState<Phase>('input');
  const [result, setResult] = React.useState<ImprovedResult['data'] | null>(null);
  const [jobId, setJobId] = React.useState('');
  // Default the change diff to EXPANDED: the list of edits is the core trust
  // artifact ("grounded in your resume - nothing invented"), so it should be
  // visible on arrival, not hidden behind a disclosure.
  const [showDetail, setShowDetail] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  // Graceful, structured failure surface (never raw HTML/error text). Input is
  // preserved so the user can retry or edit without re-entering anything.
  const [failure, setFailure] = React.useState<{
    message: string;
    requestId?: string;
    retryable: boolean;
  } | null>(null);

  // Optional pre-generation fit analysis (Req 15 - explicit, cost-aware AI).
  // Never fires automatically: the user must click "Analyze fit" to spend a
  // keyword-extraction call before committing to a full tailor pass.
  const [analysis, setAnalysis] = React.useState<JobAnalyzeResult | null>(null);
  const [analyzing, setAnalyzing] = React.useState(false);

  // Live stage progress for streamed tailoring + cancel machinery.
  const [stages, setStages] = React.useState<Record<TailorStageName, StageStatus>>(freshStages);
  const abortRef = React.useRef<AbortController | null>(null);
  const requestIdRef = React.useRef<string>('');

  // JD-from-URL import (Req 9).
  const [jdUrl, setJdUrl] = React.useState('');
  const [fetchingUrl, setFetchingUrl] = React.useState(false);
  const [lowConfidence, setLowConfidence] = React.useState(false);
  const [jdMeta, setJdMeta] = React.useState<{
    confidenceLevel?: JdConfidence;
    confidenceScore?: number;
    source?: string;
    partial?: boolean;
    suggestions?: string[];
    warnings?: string[];
  } | null>(null);

  async function importFromUrl() {
    const url = jdUrl.trim();
    if (!url) return;
    setFetchingUrl(true);
    setLowConfidence(false);
    setJdMeta(null);
    try {
      const res = await fetchJdFromUrl(url);
      if (!res.content) {
        // Surface the classified reason (e.g. robots blocked, unsupported PDF).
        const reason = res.warnings?.[0] || res.suggestions?.[0];
        toast({
          title: reason
            ? `Couldn't extract this posting. ${reason}`
            : 'That page had no readable job description. Paste it instead.',
          variant: 'error',
        });
        setJdMeta({
          confidenceLevel: res.confidenceLevel,
          source: res.source,
          suggestions: res.suggestions,
          warnings: res.warnings,
        });
      } else {
        setJd(res.content);
        draft.save(res.content);
        setLowConfidence(res.lowConfidence);
        setJdMeta({
          confidenceLevel: res.confidenceLevel,
          confidenceScore: res.confidenceScore,
          source: res.source,
          partial: res.partial,
          suggestions: res.suggestions,
          warnings: res.warnings,
        });
        toast({
          title: res.lowConfidence
            ? 'Imported - please verify the text below'
            : `Job description imported${res.source ? ` (via ${jdSourceLabel(res.source)})` : ''}`,
          variant: res.lowConfidence ? 'info' : 'success',
        });
      }
    } catch (e) {
      toast({
        title: e instanceof Error ? e.message : 'Could not import from URL',
        variant: 'error',
      });
    } finally {
      setFetchingUrl(false);
    }
  }

  // Draft persistence for the JD (Task 18 / Req 30.1) - never lose a long paste.
  const draft = useDraft<string>('tailor-jd');

  // ARIA live announcement for async AI results (Task 16 / Req 21.6).
  const activeStageLabel = TAILOR_STAGES.find((s) => stages[s.key] === 'active')?.label;

  // Map the streamed stage record -> the shared AiProgress live-mode props.
  const liveDoneKeys = TAILOR_STAGES.filter((s) => stages[s.key] === 'done').map((s) => s.key);
  const liveActiveKey =
    TAILOR_STAGES.find((s) => stages[s.key] === 'active')?.key ??
    TAILOR_STAGES.find((s) => stages[s.key] !== 'done')?.key ??
    TAILOR_STAGES[TAILOR_STAGES.length - 1].key;
  const announcement =
    phase === 'generating'
      ? activeStageLabel
        ? `Tailoring your resume. ${activeStageLabel}.`
        : 'Tailoring your resume. This may take a moment.'
      : phase === 'review' && result
        ? `Tailored resume ready. Match score ${Math.round(result.ats_score?.overall_score ?? 0)} out of 100.`
        : '';

  // A prior fit analysis becomes stale the moment the JD or source resume
  // changes - clear it so we never show a result that no longer matches inputs.
  React.useEffect(() => {
    setAnalysis(null);
  }, [jd, resumeId]);

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

  async function onAnalyze() {
    if (jd.trim().length < MIN_JD || !resumeId || analyzing) return;
    setAnalyzing(true);
    try {
      const res = await analyzeJob(jd.trim(), resumeId);
      setAnalysis(res);
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Analysis failed', variant: 'error' });
    } finally {
      setAnalyzing(false);
    }
  }

  async function onGenerate() {
    if (jd.trim().length < MIN_JD || !resumeId) return;
    setPhase('generating');
    setResult(null);
    setFailure(null);
    setStages(freshStages());
    try {
      const jid = await uploadJobDescriptions([jd.trim()], resumeId);
      setJobId(jid);

      const requestId =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : `tailor-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      requestIdRef.current = requestId;
      const controller = new AbortController();
      abortRef.current = controller;

      let data;
      try {
        const res = await streamImproveResume(resumeId, jid, promptId || undefined, {
          requestId,
          signal: controller.signal,
          onStage: (e) =>
            setStages((prev) => ({
              ...prev,
              [e.stage]: e.status === 'done' ? 'done' : 'active',
            })),
        });
        data = res.data;
      } catch (streamErr) {
        if (streamErr instanceof TailorStreamCancelled) {
          // User cancelled - preserve input, no error toast.
          setPhase('input');
          return;
        }
        // Stream unusable (flag off / unsupported / network) -> transparent
        // fallback to the non-stream path so the user still gets a result.
        const res = await previewImproveResume(resumeId, jid, promptId || undefined);
        data = res.data;
      }

      setResult(data);
      setPhase('review');
    } catch (e) {
      // Preserve input (jd/resume stay in state) and show a graceful, structured
      // failure surface. NEVER render raw error text - a 5xx from the Heroku
      // router is an HTML page, and `toUserMessage` guarantees a clean message.
      const isApiErr = e instanceof ApiError;
      // Retryable: network/timeout/5xx/429 could plausibly succeed on retry.
      const retryable = isApiErr ? [0, 408, 425, 429, 500, 502, 503, 504].includes(e.status) : true;
      setFailure({
        message: toMessage(
          e,
          'Resume tailoring is temporarily unavailable. Please try again in a moment.'
        ),
        requestId:
          isApiErr && typeof e.details === 'object' && e.details
            ? ((e.details as Record<string, unknown>).request_id as string | undefined)
            : undefined,
        retryable,
      });
      setPhase('error');
    } finally {
      abortRef.current = null;
    }
  }

  function onCancelGenerate() {
    abortRef.current?.abort();
    if (requestIdRef.current) void cancelTailorStream(requestIdRef.current);
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
      // A confirmed tailor creates a NEW resume variant AND a new application
      // card - refresh both list surfaces so they're visible immediately.
      invalidateResumeLists(qc);
      invalidateApplicationLists(qc);
      toast({ title: 'Tailored resume saved', variant: 'success' });
      router.push('/applications');
    } catch (e) {
      toast({
        title: toMessage(e, 'Could not save your tailored resume. Please try again.'),
        variant: 'error',
      });
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
          Paste a job description and get a tailored resume - grounded in your real experience.
        </p>
      </div>

      {aiUnconfigured && (
        <Card className="flex items-start gap-3 border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-4">
          <Key className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-warning)]" />
          <div className="flex-1">
            <p className="text-sm font-medium">Add an AI provider key to tailor</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              Tailoring needs a configured AI provider. Add a key in settings, then come back.
            </p>
          </div>
          <Button asChild size="sm" variant="outline">
            <Link href="/settings">Open settings</Link>
          </Button>
        </Card>
      )}

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
                  {r.title || r.filename || 'Untitled'} {r.is_master ? '- Master' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="jd-url">Import from a job link (optional)</Label>
          <div className="flex gap-2">
            <Input
              id="jd-url"
              type="url"
              inputMode="url"
              value={jdUrl}
              onChange={(e) => setJdUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  importFromUrl();
                }
              }}
              placeholder="https://company.com/careers/123"
              disabled={fetchingUrl || phase === 'generating'}
            />
            <Button
              type="button"
              variant="outline"
              onClick={importFromUrl}
              loading={fetchingUrl}
              disabled={!jdUrl.trim() || phase === 'generating'}
            >
              Import
            </Button>
          </div>
          <p className="text-xs text-[var(--muted-foreground)]">
            We fetch the page securely and extract the description. Review it before generating.
          </p>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="jd">Job description</Label>
            {jdMeta?.confidenceLevel && (
              <ConfidenceBadge
                level={jdMeta.confidenceLevel}
                score={jdMeta.confidenceScore}
                source={jdMeta.source}
              />
            )}
          </div>
          {lowConfidence && (
            <div
              role="alert"
              className="rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 px-3 py-2 text-xs text-[var(--foreground)]"
            >
              We couldn&apos;t confidently extract this posting - please check and edit the text
              below before tailoring.
            </div>
          )}
          {jdMeta?.partial && !lowConfidence && (
            <div
              role="status"
              className="rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 px-3 py-2 text-xs text-[var(--foreground)]"
            >
              Some sections may be missing - please verify the full description below.
            </div>
          )}
          {jdMeta?.warnings && jdMeta.warnings.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-[var(--muted-foreground)]">
              {jdMeta.warnings.map((w, i) => (
                <li key={`w-${i}`}>{w}</li>
              ))}
            </ul>
          )}
          {jdMeta?.suggestions && jdMeta.suggestions.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-[var(--muted-foreground)]">
              {jdMeta.suggestions.map((s, i) => (
                <li key={`s-${i}`}>{s}</li>
              ))}
            </ul>
          )}
          <Textarea
            id="jd"
            value={jd}
            onChange={(e) => {
              setJd(e.target.value);
              draft.save(e.target.value);
            }}
            onKeyDown={(e) => {
              // Cmd/Ctrl+Enter generates without reaching for the mouse - the
              // same shortcut the wizard uses, so muscle memory carries over.
              if (
                (e.metaKey || e.ctrlKey) &&
                e.key === 'Enter' &&
                jd.trim().length >= MIN_JD &&
                resumeId &&
                phase !== 'generating' &&
                !aiUnconfigured
              ) {
                e.preventDefault();
                void onGenerate();
              }
            }}
            placeholder="Paste the full job description here..."
            className="min-h-40"
            disabled={phase === 'generating'}
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            {jd.trim().length < MIN_JD
              ? `Add at least ${MIN_JD} characters (${jd.trim().length}/${MIN_JD}).`
              : 'Looks good - press ⌘/Ctrl+Enter to generate.'}
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

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={onGenerate}
            loading={phase === 'generating'}
            disabled={jd.trim().length < MIN_JD || !resumeId || Boolean(aiUnconfigured)}
            title={aiUnconfigured ? 'Add an AI key in settings first' : undefined}
          >
            <Sparkles className="h-4 w-4" /> {phase === 'review' ? 'Regenerate' : 'Generate'}
          </Button>
          <Button
            variant="outline"
            onClick={onAnalyze}
            loading={analyzing}
            disabled={
              jd.trim().length < MIN_JD ||
              !resumeId ||
              phase === 'generating' ||
              Boolean(aiUnconfigured)
            }
            title={
              aiUnconfigured
                ? 'Add an AI key in settings first'
                : 'See how your resume matches before generating'
            }
          >
            <Target className="h-4 w-4" /> Analyze fit
          </Button>
          <span className="inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
            <Sparkles className="h-3 w-3" /> Uses your configured AI provider
          </span>
        </div>
      </Card>

      {/* Pre-generation fit analysis (explicit action, cheaper than a full tailor) */}
      {analysis && phase !== 'generating' && (
        <Card className="space-y-4 p-5">
          <div className="flex items-start gap-5">
            {analysis.fit_score != null && <ScoreRing score={analysis.fit_score} />}
            <div className="flex-1 space-y-1">
              <p className="flex items-center gap-1.5 text-sm font-medium">
                Fit analysis
                <Explain label="What is fit analysis?">
                  A quick, pre-generation estimate of how many keywords from this job already appear
                  in your selected resume. Use it to decide whether to tailor - it does not change
                  your resume.
                </Explain>
              </p>
              <p className="text-xs text-[var(--muted-foreground)]">
                {analysis.fit_score != null
                  ? `Your resume already covers ${analysis.matched.length} of ${
                      analysis.matched.length + analysis.missing.length
                    } key terms. Generate to close the gaps.`
                  : 'Keyword breakdown for this role. Pick a resume with processed data for a fit score.'}
              </p>
            </div>
          </div>

          {analysis.missing.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-[var(--foreground)]">
                Missing from your resume
              </p>
              <div className="flex flex-wrap gap-1.5">
                {analysis.missing.map((k) => (
                  <Badge key={`miss-${k}`} variant="warning">
                    {k}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {analysis.matched.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-[var(--foreground)]">Already covered</p>
              <div className="flex flex-wrap gap-1.5">
                {analysis.matched.map((k) => (
                  <Badge key={`match-${k}`} variant="success">
                    {k}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Generating - honest, per-stage progress streamed from the backend
          (shared AiProgress in LIVE mode). */}
      {phase === 'generating' && (
        <Card className="space-y-4 p-5">
          <p className="text-sm font-medium">Tailoring your resume...</p>
          <AiProgress
            stages={TAILOR_STAGES}
            activeKey={liveActiveKey}
            doneKeys={liveDoneKeys}
            messages={TAILOR_MESSAGES}
            estimate={ESTIMATE_MEDIUM}
          />
          <div className="flex items-center justify-between gap-3 pt-1">
            <span className="text-xs text-[var(--muted-foreground)]">
              You can cancel anytime - nothing is saved until you accept.
            </span>
            <Button variant="outline" size="sm" onClick={onCancelGenerate}>
              Cancel
            </Button>
          </div>
        </Card>
      )}

      {/* Failure - graceful, structured surface. Never raw HTML/stack traces.
          Input is preserved so Retry re-runs with the same JD + resume. */}
      {phase === 'error' && failure && (
        <Card
          role="alert"
          className="space-y-4 border-[var(--destructive)]/40 bg-[var(--destructive)]/5 p-5"
        >
          <div className="flex items-start gap-3">
            <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-[var(--destructive)]" />
            <div className="flex-1 space-y-1">
              <p className="text-sm font-medium">Resume tailoring didn't complete</p>
              <p className="text-sm text-[var(--muted-foreground)]">{failure.message}</p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Your job description and resume selection are saved - nothing was lost.
              </p>
              {failure.requestId && (
                <p className="pt-1 font-mono text-[11px] text-[var(--muted-foreground)]">
                  Reference: {failure.requestId}
                </p>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {failure.retryable && (
              <Button size="sm" onClick={onGenerate} disabled={Boolean(aiUnconfigured)}>
                <RotateCw className="h-4 w-4" /> Try again
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setFailure(null);
                setPhase('input');
              }}
            >
              Back to editing
            </Button>
            <Button asChild size="sm" variant="ghost">
              <Link href="/contact?topic=bug">Report issue</Link>
            </Button>
          </div>
        </Card>
      )}

      {/* Review - results render inline below */}
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
                    better - aim for 75+. It is guidance, not a guarantee of how a specific ATS will
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
                    role - emphasising relevant skills and keywords. Nothing is invented; expand the
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
                <ShieldCheck className="h-3.5 w-3.5" /> Grounded in your resume - no invented
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
