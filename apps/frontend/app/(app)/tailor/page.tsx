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
import { useQueryClient, useQuery } from '@tanstack/react-query';
import { invalidateApplicationLists, invalidateResumeLists, queryKeys } from '@/lib/query/client';
import { getProfile, type Profile } from '@/lib/api/profile';
import { AvatarUploader } from '@/components/profile/avatar-uploader';
import { DEFAULT_PHOTO_CONFIG } from '@/lib/types/photo';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import Target from 'lucide-react/dist/esm/icons/target';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import RotateCw from 'lucide-react/dist/esm/icons/rotate-cw';
import Download from 'lucide-react/dist/esm/icons/download';
import Eye from 'lucide-react/dist/esm/icons/eye';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import TrendingUp from 'lucide-react/dist/esm/icons/trending-up';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Mail from 'lucide-react/dist/esm/icons/mail';
import MessageSquareText from 'lucide-react/dist/esm/icons/message-square-text';
import CircleCheck from 'lucide-react/dist/esm/icons/circle-check';
import Copy from 'lucide-react/dist/esm/icons/copy';
import Check from 'lucide-react/dist/esm/icons/check';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/atelier/tabs';
import { ExportButton } from '@/components/resume/export-button';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { LoadingSkeleton, EmptyState } from '@/components/atelier/states';
import { AiProgress } from '@/components/ai/ai-progress';
import { ESTIMATE_MEDIUM } from '@/lib/ai-progress-copy';
import { useToast } from '@/components/atelier/toast';
import { Explain } from '@/components/ai/explain';
import { RecoveryBanner } from '@/components/resilience/recovery-banner';
import { useDraft } from '@/lib/hooks/use-draft';
import { useTailorResumes, usePromptOptions } from '@/features/tailor/hooks';
import { useSystemStatus } from '@/features/home/hooks';
import { deriveAiAvailability } from '@/lib/ai-availability';
import Key from 'lucide-react/dist/esm/icons/key-round';
import { fetchJdFromUrl, jdSourceLabel, type JdConfidence } from '@/lib/api/jd';
import {
  uploadJobDescriptions,
  previewImproveResume,
  streamImproveResume,
  recoverTailorPreview,
  cancelTailorStream,
  downloadResumePdf,
  updateResumeTemplateSettings,
  TailorStreamCancelled,
  TailorStreamError,
  confirmImproveResume,
  analyzeJob,
  fetchResume,
  generateCoverLetter,
  generateOutreachMessage,
  generateInterviewPrep,
  type JobAnalyzeResult,
  type TailorStageName,
} from '@/lib/api/resume';
import {
  extractKeywords,
  calculateMatchStats,
  buildResumeTextForMatch,
} from '@/lib/utils/keyword-matcher';
import type {
  ImprovedResult,
  ResumeFieldDiff,
  InterviewPrepData,
} from '@/components/common/resume_previewer_context';
import { ResumeDocument } from '@/components/resume/resume-document';
import type { ResumeData } from '@/components/dashboard/resume-component';
import { type TemplateSettings } from '@/lib/types/template-settings';
import {
  getPreferredTemplateSettings,
  getPreferredTemplateId,
} from '@/lib/resume/preferred-template';
import { buildResumeFilename } from '@/lib/resume/filename';
import {
  getTemplateById,
  templateToSettings,
  type ResumeTemplate,
} from '@/lib/resume/template-catalog';
import { TemplateGallery } from '@/components/resume/template-gallery';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/atelier/dialog';
import { ApiError, toMessage } from '@/lib/api/errors';
import {
  createErrorReport,
  type ErrorReportPayload,
  type ErrorReportReceipt,
} from '@/lib/api/error-reports';
import Link from 'next/link';

const MIN_JD = 50;
const TAILOR_ROUTES = {
  upload: '/jobs/upload',
  stream: '/resumes/improve/preview/stream',
  preview: '/resumes/improve/preview',
  recovery: '/resumes/improve/preview/result/{requestId}',
} as const;

type Phase = 'input' | 'generating' | 'review' | 'error';

function newClientId(prefix: string): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function safeReportMessage(message: string): string {
  const normalized = Array.from(message, (character) => {
    const code = character.charCodeAt(0);
    return code < 0x20 || code === 0x7f ? ' ' : character;
  })
    .join('')
    .trim();
  return (normalized || 'Resume tailoring did not complete.').slice(0, 500);
}

interface TailorFailure {
  message: string;
  retryable: boolean;
  report: ErrorReportPayload;
}

/** The full set of tailoring inputs persisted for crash/navigation recovery.
 *  Previously only the JD string was saved, so a restore silently dropped the
 *  Extra Instructions, tailoring style, source resume and template choice. */
interface TailorDraft {
  jd: string;
  customInstructions?: string;
  promptId?: string;
  resumeId?: string;
  templateId?: string;
  extras?: ExtrasState;
}

/** Which companion documents to also generate once the tailored resume is
 *  saved. Toggled by the user before/while reviewing - never auto-run, so
 *  every extra LLM call stays an explicit, cost-conscious choice. */
interface ExtrasState {
  coverLetter: boolean;
  outreach: boolean;
  interviewPrep: boolean;
  keywordMatch: boolean;
}

const DEFAULT_EXTRAS: ExtrasState = {
  coverLetter: false,
  outreach: false,
  interviewPrep: false,
  keywordMatch: false,
};

type ExtraKind = keyof ExtrasState;

/** Per-extra generation status, keyed the same as {@link ExtrasState}. */
type ExtraStatus = 'idle' | 'pending' | 'done' | 'error';

const EXTRA_META: Record<ExtraKind, { label: string; Icon: typeof FileText; hint: string }> = {
  coverLetter: {
    label: 'Cover letter',
    Icon: FileText,
    hint: 'A matching cover letter for this job.',
  },
  outreach: {
    label: 'Outreach message',
    Icon: Mail,
    hint: 'A short note for reaching out to a recruiter.',
  },
  interviewPrep: {
    label: 'Interview prep',
    Icon: MessageSquareText,
    hint: 'Likely questions, talking points, and skill-gap coaching.',
  },
  keywordMatch: {
    label: 'Keyword match',
    Icon: Target,
    hint: 'Check which of the job\u2019s keywords your tailored resume covers.',
  },
};

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

/** Coerce a tailored `resume_preview` into a fully-shaped, render-safe
 *  ResumeData. The pipeline always returns the full object, but normalizing
 *  guards the WYSIWYG renderer against a partial/legacy payload (missing
 *  arrays would otherwise throw inside the template). */
function toResumeData(preview: unknown): ResumeData {
  const p = (preview ?? {}) as Partial<ResumeData>;
  return {
    personalInfo: p.personalInfo ?? {},
    summary: p.summary ?? '',
    workExperience: Array.isArray(p.workExperience) ? p.workExperience : [],
    education: Array.isArray(p.education) ? p.education : [],
    personalProjects: Array.isArray(p.personalProjects) ? p.personalProjects : [],
    additional: p.additional ?? {},
    sectionMeta: p.sectionMeta,
    customSections: p.customSections,
  };
}

type ReviewWord = { text: string; changed: boolean };

const FIELD_LABELS: Record<ResumeFieldDiff['field_type'], string> = {
  skill: 'Technical skills',
  description: 'Description',
  summary: 'Professional summary',
  certification: 'Certifications',
  experience: 'Work experience',
  education: 'Education',
  project: 'Projects',
  language: 'Languages',
  award: 'Awards',
};

function formatChangeLocation(change: ResumeFieldDiff): string {
  if (change.field_path === 'summary') return 'Professional summary';
  const additionalLabels: Record<string, string> = {
    'additional.technicalSkills': 'Technical skills',
    'additional.certificationsTraining': 'Certifications and training',
    'additional.languages': 'Languages',
    'additional.awards': 'Awards',
  };
  if (additionalLabels[change.field_path]) return additionalLabels[change.field_path];

  const indexed = change.field_path.match(
    /^(workExperience|education|personalProjects)\[(\d+)\](?:\.(\w+))?$/
  );
  if (indexed) {
    const section =
      indexed[1] === 'workExperience'
        ? 'Work experience'
        : indexed[1] === 'personalProjects'
          ? 'Project'
          : 'Education';
    const rawField = indexed[3] ? indexed[3].replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase() : '';
    const field = rawField ? `${rawField.charAt(0).toUpperCase()}${rawField.slice(1)}` : '';
    return `${section} ${Number(indexed[2]) + 1}${field ? ` · ${field}` : ''}`;
  }
  return FIELD_LABELS[change.field_type];
}

function buildWordReview(
  before: string,
  after: string
): {
  before: ReviewWord[];
  after: ReviewWord[];
} {
  const left = before.trim().split(/\s+/).filter(Boolean);
  const right = after.trim().split(/\s+/).filter(Boolean);
  const lcs = Array.from({ length: left.length + 1 }, () =>
    Array<number>(right.length + 1).fill(0)
  );

  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      lcs[i][j] =
        left[i] === right[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const beforeWords: ReviewWord[] = [];
  const afterWords: ReviewWord[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      beforeWords.push({ text: left[i], changed: false });
      afterWords.push({ text: right[j], changed: false });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      beforeWords.push({ text: left[i], changed: true });
      i += 1;
    } else {
      afterWords.push({ text: right[j], changed: true });
      j += 1;
    }
  }
  while (i < left.length) {
    beforeWords.push({ text: left[i], changed: true });
    i += 1;
  }
  while (j < right.length) {
    afterWords.push({ text: right[j], changed: true });
    j += 1;
  }
  return { before: beforeWords, after: afterWords };
}

function ReviewText({ words, tone }: { words: ReviewWord[]; tone: 'before' | 'after' }) {
  return (
    <p className="text-sm leading-relaxed text-[var(--foreground)]">
      {words.map((word, index) => (
        <React.Fragment key={`${index}-${word.text}`}>
          <span
            className={
              word.changed
                ? tone === 'before'
                  ? 'rounded-[var(--radius-at-sm)] bg-[var(--destructive)]/12 px-0.5 text-[var(--destructive)] line-through decoration-[var(--destructive)]/60'
                  : 'rounded-[var(--radius-at-sm)] bg-[var(--at-success)]/18 px-0.5 font-medium'
                : undefined
            }
          >
            {word.text}
          </span>
          {index < words.length - 1 ? ' ' : null}
        </React.Fragment>
      ))}
    </p>
  );
}

function ChangeReview({ changes }: { changes: ResumeFieldDiff[] }) {
  const counts = changes.reduce(
    (total, change) => ({ ...total, [change.change_type]: total[change.change_type] + 1 }),
    { added: 0, removed: 0, modified: 0 }
  );

  return (
    <div className="mt-4 space-y-3">
      <div className="flex flex-wrap gap-1.5" aria-label="Change summary">
        {counts.modified > 0 && <Badge variant="primary">{counts.modified} updated</Badge>}
        {counts.added > 0 && <Badge variant="success">{counts.added} added</Badge>}
        {counts.removed > 0 && <Badge variant="danger">{counts.removed} removed</Badge>}
      </div>
      <ul className="space-y-3">
        {changes.map((change, index) => {
          const isModified = change.change_type === 'modified';
          const wordReview =
            isModified && change.original_value && change.new_value
              ? buildWordReview(change.original_value, change.new_value)
              : null;
          const actionLabel = isModified
            ? 'Updated'
            : change.change_type === 'added'
              ? 'Added'
              : 'Removed';
          return (
            <li
              key={`${change.field_path}-${index}`}
              className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={
                    change.change_type === 'added'
                      ? 'success'
                      : change.change_type === 'removed'
                        ? 'danger'
                        : 'primary'
                  }
                >
                  {actionLabel}
                </Badge>
                <p className="text-sm font-medium text-[var(--foreground)]">
                  {formatChangeLocation(change)}
                </p>
              </div>

              {wordReview ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div className="rounded-[var(--radius-at-md)] border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-3">
                    <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                      Before
                    </p>
                    <ReviewText words={wordReview.before} tone="before" />
                  </div>
                  <div className="rounded-[var(--radius-at-md)] border border-[var(--at-success)]/25 bg-[var(--at-success)]/6 p-3">
                    <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--at-success)]">
                      After
                    </p>
                    <ReviewText words={wordReview.after} tone="after" />
                  </div>
                </div>
              ) : change.change_type === 'removed' && change.original_value ? (
                <div className="mt-3 rounded-[var(--radius-at-md)] border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 p-3">
                  <p className="text-sm leading-relaxed text-[var(--muted-foreground)] line-through">
                    {change.original_value}
                  </p>
                </div>
              ) : change.new_value ? (
                <div className="mt-3 rounded-[var(--radius-at-md)] border border-[var(--at-success)]/25 bg-[var(--at-success)]/6 p-3">
                  <p className="text-sm font-medium leading-relaxed text-[var(--foreground)]">
                    {change.new_value}
                  </p>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
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
  const aiAvailability = deriveAiAvailability(statusQuery);
  const aiUnconfigured = aiAvailability.state === 'unconfigured';
  const aiBlocked = !aiAvailability.canUseAi;
  const aiBlockTitle = aiUnconfigured
    ? 'Add an AI key in settings first'
    : aiBlocked
      ? 'Checking AI availability'
      : undefined;

  const [resumeId, setResumeId] = React.useState('');
  const [jd, setJd] = React.useState('');
  const [promptId, setPromptId] = React.useState<string>('');
  // Optional per-run steering (emphasis/ordering/tone, or real content the user
  // attests to). Sent as custom_instructions; the backend sanitizes it and keeps
  // it subordinate to the anti-fabrication rules.
  const [customInstructions, setCustomInstructions] = React.useState('');
  const CUSTOM_INSTRUCTIONS_MAX = 2000;

  // Companion-document toggles (Task: bring cover letter / outreach / interview
  // prep / keyword match into the tailor flow itself). Off by default - each
  // is an extra LLM call, so it's opt-in per the cost-consent principle.
  const [extras, setExtras] = React.useState<ExtrasState>(DEFAULT_EXTRAS);
  const [extraStatus, setExtraStatus] = React.useState<Record<ExtraKind, ExtraStatus>>({
    coverLetter: 'idle',
    outreach: 'idle',
    interviewPrep: 'idle',
    keywordMatch: 'idle',
  });
  const [keywordMatchJd, setKeywordMatchJd] = React.useState<string | null>(null);
  const [coverLetterText, setCoverLetterText] = React.useState<string | null>(null);
  const [outreachText, setOutreachText] = React.useState<string | null>(null);
  const [interviewPrepData, setInterviewPrepData] = React.useState<InterviewPrepData | null>(null);
  // The saved resume id + a "saved" sub-state (distinct from `phase`, which
  // stays 'review' so the preview pane keeps showing the tailored document).
  // Set once Accept & save completes when at least one extra was requested -
  // this is what keeps the user ON the tailor page to see the results instead
  // of bouncing to /applications.
  const [savedResumeId, setSavedResumeId] = React.useState<string | null>(null);
  const [activeExtraTab, setActiveExtraTab] = React.useState<ExtraKind>('coverLetter');

  function toggleExtra(kind: ExtraKind, value: boolean) {
    setExtras((prev) => {
      const next = { ...prev, [kind]: value };
      saveDraft({ extras: next });
      return next;
    });
  }

  /** Run every toggled-on extra against the newly saved tailored resume.
   *  Independent and best-effort per extra - one failing (e.g. rate limit)
   *  never blocks the others or the save the user already completed. */
  async function runSelectedExtras(newResumeId: string) {
    const jobs: Array<Promise<void>> = [];
    if (extras.coverLetter) {
      setExtraStatus((s) => ({ ...s, coverLetter: 'pending' }));
      jobs.push(
        generateCoverLetter(newResumeId)
          .then((content) => {
            setCoverLetterText(content);
            setExtraStatus((s) => ({ ...s, coverLetter: 'done' }));
          })
          .catch(() => setExtraStatus((s) => ({ ...s, coverLetter: 'error' })))
      );
    }
    if (extras.outreach) {
      setExtraStatus((s) => ({ ...s, outreach: 'pending' }));
      jobs.push(
        generateOutreachMessage(newResumeId)
          .then((content) => {
            setOutreachText(content);
            setExtraStatus((s) => ({ ...s, outreach: 'done' }));
          })
          .catch(() => setExtraStatus((s) => ({ ...s, outreach: 'error' })))
      );
    }
    if (extras.interviewPrep) {
      setExtraStatus((s) => ({ ...s, interviewPrep: 'pending' }));
      jobs.push(
        generateInterviewPrep(newResumeId)
          .then((data) => {
            setInterviewPrepData(data);
            setExtraStatus((s) => ({ ...s, interviewPrep: 'done' }));
          })
          .catch(() => setExtraStatus((s) => ({ ...s, interviewPrep: 'error' })))
      );
    }
    if (extras.keywordMatch) {
      // No LLM call needed - the job description we already hold is enough
      // to compute a client-side match against the tailored resume text.
      setKeywordMatchJd(jd);
      setExtraStatus((s) => ({ ...s, keywordMatch: 'done' }));
    }
    if (jobs.length) await Promise.allSettled(jobs);
  }

  /** Retry a single failed extra (e.g. after a transient rate-limit) without
   *  re-running the others. */
  async function retryExtra(kind: ExtraKind) {
    if (!savedResumeId) return;
    if (kind === 'keywordMatch') {
      setKeywordMatchJd(jd);
      setExtraStatus((s) => ({ ...s, keywordMatch: 'done' }));
      return;
    }
    setExtraStatus((s) => ({ ...s, [kind]: 'pending' }));
    try {
      if (kind === 'coverLetter') {
        setCoverLetterText(await generateCoverLetter(savedResumeId, true));
      } else if (kind === 'outreach') {
        setOutreachText(await generateOutreachMessage(savedResumeId, true));
      } else if (kind === 'interviewPrep') {
        setInterviewPrepData(await generateInterviewPrep(savedResumeId, true));
      }
      setExtraStatus((s) => ({ ...s, [kind]: 'done' }));
    } catch {
      setExtraStatus((s) => ({ ...s, [kind]: 'error' }));
    }
  }
  // Presentation template for the tailored result: drives the WYSIWYG preview,
  // the downloaded PDF, and is persisted on save. Seeded from the user's
  // preferred template so most users never need to touch it. Affects layout
  // only - never the tailored content. Chosen via the SAME visual gallery as
  // the /templates page (real-render cards), not a dropdown.
  const [templateSettings, setTemplateSettings] = React.useState<TemplateSettings>(() =>
    getPreferredTemplateSettings()
  );
  const [selectedTemplateId, setSelectedTemplateId] = React.useState<string | undefined>(
    () => getPreferredTemplateId() ?? undefined
  );
  const [templatePickerOpen, setTemplatePickerOpen] = React.useState(false);
  const selectedTemplateName = selectedTemplateId
    ? (getTemplateById(selectedTemplateId)?.name ?? 'Custom')
    : 'Default';

  const onPickTemplate = (t: ResumeTemplate) => {
    setSelectedTemplateId(t.id);
    setTemplateSettings(templateToSettings(t));
    setTemplatePickerOpen(false);
    // Persist the template choice so a later restore brings the look back too.
    saveDraft({ templateId: t.id });
  };

  // Profile photo (the canonical source for a photo template's header). Read
  // from the account master - the same source resumes resolve server-side.
  const profileQuery = useQuery<Profile>({ queryKey: queryKeys.profile, queryFn: getProfile });
  const profileAvatarUrl = profileQuery.data?.avatar_url ?? null;

  // The selected source resume's processed data, used to preview the user's
  // CURRENT resume in the right pane before they generate - so the split view
  // always shows a document (their resume -> becomes the tailored one), not an
  // empty box. Cheap: cached per resume and only the ready sources are listed.
  const sourceResumeQuery = useQuery({
    queryKey: ['resume', 'tailor-source', resumeId],
    queryFn: () => fetchResume(resumeId),
    enabled: Boolean(resumeId),
    staleTime: 60_000,
  });
  const sourceResumeData = sourceResumeQuery.data?.processed_resume
    ? toResumeData(sourceResumeQuery.data.processed_resume)
    : null;

  const selectedTemplate = selectedTemplateId ? getTemplateById(selectedTemplateId) : undefined;
  // A photo template should SHOW the profile photo by default (issue: results
  // used to ignore the template's photo slot). Only decide this when we know the
  // chosen template's photo support.
  const templateWantsPhoto = selectedTemplate
    ? selectedTemplate.photoSupport !== 'none'
    : undefined;

  // Apply the selected template's photo intent to resume data used for BOTH the
  // preview and the saved copy, so preview == PDF == stored:
  //  - photo template  -> show the header photo (canonical -> profile avatar).
  //  - no-photo template -> hide it.
  // Leaves data untouched when no template is explicitly selected (the backend
  // already resolved any canonical photo at generation time).
  const applyTemplatePhoto = React.useCallback(
    (data: ResumeData): ResumeData => {
      if (templateWantsPhoto === undefined) return data;
      const pi = { ...(data.personalInfo ?? {}) } as Record<string, unknown>;
      const currentPhoto = (pi.photo as Record<string, unknown>) ?? DEFAULT_PHOTO_CONFIG;
      if (templateWantsPhoto) {
        pi.photo = { ...currentPhoto, show: true, ref: 'canonical' };
        pi.avatarUrl = profileAvatarUrl ?? (pi.avatarUrl as string | null) ?? null;
      } else {
        pi.photo = { ...currentPhoto, show: false };
      }
      return { ...data, personalInfo: pi } as ResumeData;
    },
    [templateWantsPhoto, profileAvatarUrl]
  );

  const onProfilePhotoChange = React.useCallback(
    (url: string | null) => {
      qc.setQueryData<Profile>(queryKeys.profile, (old) =>
        old ? { ...old, avatar_url: url } : old
      );
      qc.invalidateQueries({ queryKey: queryKeys.profile });
    },
    [qc]
  );

  const [showOptions, setShowOptions] = React.useState(false);
  const [phase, setPhase] = React.useState<Phase>('input');
  const [result, setResult] = React.useState<ImprovedResult['data'] | null>(null);
  const [jobId, setJobId] = React.useState('');
  // Collapsed by default to keep the review step compact - the change count
  // and the "grounded in your resume" note are visible either way; the full
  // word-by-word diff is one click away via "Expand details".
  const [showDetail, setShowDetail] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [downloading, setDownloading] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  // Score of the PREVIOUS attempt, captured just before a Regenerate, so the
  // review can show an honest "82 -> 88" delta instead of silently discarding
  // the prior result. Null on the first generation.
  const [prevScore, setPrevScore] = React.useState<number | null>(null);
  // Snapshot of the pre-generation fit analysis at the moment Generate ran, so
  // the review can show how much tailoring closed the gap even if the live
  // `analysis` panel is later cleared. Null when the user skipped "Analyze fit".
  const [preFit, setPreFit] = React.useState<{ score: number | null; missing: number } | null>(
    null
  );
  // Graceful, structured failure surface. The attached report is a frozen,
  // privacy-minimized snapshot: it never reads live JD/resume/instruction state.
  const [failure, setFailure] = React.useState<TailorFailure | null>(null);
  const [reportStatus, setReportStatus] = React.useState<'idle' | 'pending' | 'reported'>('idle');
  const [reportReceipt, setReportReceipt] = React.useState<ErrorReportReceipt | null>(null);

  // Optional pre-generation fit analysis (Req 15 - explicit, cost-aware AI).
  // Never fires automatically: the user must click "Analyze fit" to spend a
  // keyword-extraction call before committing to a full tailor pass.
  const [analysis, setAnalysis] = React.useState<JobAnalyzeResult | null>(null);
  const [analyzing, setAnalyzing] = React.useState(false);
  // The exact inputs (resume + JD) the current analysis was computed for. When
  // the live inputs drift from this, the analysis is marked STALE (a subtle
  // banner) rather than silently wiped on every keystroke - so a one-character
  // edit no longer forces an immediate, costly re-analyze.
  const [analyzedKey, setAnalyzedKey] = React.useState<string | null>(null);
  // The FULL previous attempt, kept across a Regenerate so the user can compare
  // and restore the better of the two instead of losing a good result.
  const [prevResult, setPrevResult] = React.useState<ImprovedResult['data'] | null>(null);
  // Two-step Discard so an accidental click never throws away a tailored result.
  const [confirmingDiscard, setConfirmingDiscard] = React.useState(false);
  // A completed-but-unsaved preview from a prior visit, offered for recovery.
  const [recoverable, setRecoverable] = React.useState<{ requestId: string } | null>(null);
  const [recovering, setRecovering] = React.useState(false);

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

  /**
   * Read a posting through the extension when the server cannot.
   *
   * Returns true when it worked, so the caller can stop rather than also showing
   * the server's failure - which would be technically accurate and useless.
   */
  async function tryExtensionFetch(url: string): Promise<boolean> {
    const { detectExtension, requestJobDescription } =
      await import('@/features/discovery/extension-bridge');
    if (!(await detectExtension())) return false;

    toast({ title: 'Trying again through your browser…', variant: 'info' });
    const result = await requestJobDescription(url);
    if (!result.ok || !result.data.description.trim()) {
      return false;
    }

    setJd(result.data.description);
    saveDraft({ jd: result.data.description });
    setLowConfidence(false);
    setJdMeta({
      confidenceLevel: 'MEDIUM',
      source: 'extension',
      // Said plainly: this text came from the page in their browser, not a server
      // fetch, so verifying it is a reasonable ask.
      suggestions: ['Read through the text below before tailoring.'],
    });
    toast({
      title: `Job description read from your browser${
        result.data.company ? ` (${result.data.company})` : ''
      }`,
      variant: 'success',
    });
    return true;
  }

  async function importFromUrl() {
    const url = jdUrl.trim();
    if (!url) return;
    setFetchingUrl(true);
    setLowConfidence(false);
    setJdMeta(null);
    try {
      const res = await fetchJdFromUrl(url);
      if (!res.content) {
        // The server could not read it. Before giving up, try the browser: the
        // biggest boards disallow automated fetching in robots.txt and answer a
        // server with 403, and a posting shown in a modal has no page to fetch at
        // all - but the user can open all of them, and the extension runs there.
        const viaBrowser = await tryExtensionFetch(url);
        if (viaBrowser) return;

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
        saveDraft({ jd: res.content });
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

  // Draft persistence for the full input set (Task 18 / Req 30.1) - never lose
  // a long paste OR the accompanying Extra Instructions / style / template.
  const draft = useDraft<TailorDraft | string>('tailor-jd');

  // Persist the current inputs as one structured draft. `overrides` carries the
  // just-changed field so we don't race React's async state (the closure holds
  // the previous value for that field until the next render).
  const saveDraft = (overrides: Partial<TailorDraft> = {}) => {
    draft.save({
      jd,
      customInstructions: customInstructions || undefined,
      promptId: promptId || undefined,
      resumeId: resumeId || undefined,
      templateId: selectedTemplateId,
      extras,
      ...overrides,
    });
  };

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

  // A fit analysis is tied to the inputs it was computed for. Rather than wipe
  // it on every keystroke (which forced a costly re-analyze after a trivial
  // edit), we KEEP it and flag staleness when the live inputs drift, so the
  // user can still see the last result and decide whether to re-analyze.
  const currentInputKey = `${resumeId}::${jd.trim()}`;
  const analysisStale = analysis != null && analyzedKey !== currentInputKey;

  // Rough, honest cost/time expectations so users on slow/free models aren't
  // surprised. Analyze is a single keyword call; Generate runs the 5-stage
  // pipeline. Not a billing figure - just order-of-magnitude guidance.
  const GENERATE_COST_HINT = `About ${TAILOR_STAGES.length} AI steps · typically 20-60s`;
  const ANALYZE_COST_HINT = '1 AI call · a few seconds';

  // Offer to restore a completed-but-unsaved tailored resume from a prior visit
  // (the result otherwise lives only in React state and is lost on navigation).
  // We persist just the request id locally; the payload is re-fetched securely
  // from the server via recoverTailorPreview.
  const RECOVER_KEY = 'tailor-last-preview';
  React.useEffect(() => {
    if (phase !== 'input' || result) return;
    try {
      const raw = localStorage.getItem(RECOVER_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw) as { requestId?: string; savedAt?: number };
      // Only offer recent previews (24h) with a usable request id.
      if (saved.requestId && saved.savedAt && Date.now() - saved.savedAt < 24 * 60 * 60 * 1000) {
        setRecoverable({ requestId: saved.requestId });
      } else {
        localStorage.removeItem(RECOVER_KEY);
      }
    } catch {
      /* ignore malformed/unavailable storage */
    }
    // Run once on mount for the input phase.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function rememberPreview(requestId: string) {
    try {
      localStorage.setItem(RECOVER_KEY, JSON.stringify({ requestId, savedAt: Date.now() }));
    } catch {
      /* storage unavailable - recovery is best-effort */
    }
  }

  function forgetPreview() {
    try {
      localStorage.removeItem(RECOVER_KEY);
    } catch {
      /* ignore */
    }
    setRecoverable(null);
  }

  async function onRecoverPreview() {
    if (!recoverable) return;
    setRecovering(true);
    try {
      const res = await recoverTailorPreview(recoverable.requestId);
      if (!res) {
        toast({ title: 'That tailored resume is no longer available.', variant: 'info' });
        forgetPreview();
        return;
      }
      setResult(res.data);
      setJobId(res.data.job_id ?? '');
      setPrevResult(null);
      setPrevScore(null);
      setPreFit(null);
      setRecoverable(null);
      setPhase('review');
    } catch (e) {
      toast({
        title: toMessage(e, 'Could not restore your last tailored resume.'),
        variant: 'error',
      });
    } finally {
      setRecovering(false);
    }
  }

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
    if (aiBlocked || jd.trim().length < MIN_JD || !resumeId || analyzing) return;
    setAnalyzing(true);
    try {
      const res = await analyzeJob(jd.trim(), resumeId);
      setAnalysis(res);
      // Bind this analysis to the exact inputs it reflects (staleness anchor).
      setAnalyzedKey(`${resumeId}::${jd.trim()}`);
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Analysis failed', variant: 'error' });
    } finally {
      setAnalyzing(false);
    }
  }

  async function onGenerate() {
    if (aiBlocked || jd.trim().length < MIN_JD || !resumeId) return;
    // Remember the current attempt's score AND full result before it's
    // replaced, so a Regenerate can surface a before/after delta and let the
    // user restore the prior attempt if it was better (no silent loss).
    setPrevScore(result?.ats_score?.overall_score ?? null);
    setPrevResult(result ?? null);
    setPreFit(
      analysis
        ? {
            score: analysis.fit_score ?? null,
            missing: analysis.missing.length,
          }
        : null
    );
    setPhase('generating');
    setResult(null);
    setFailure(null);
    setReportStatus('idle');
    setReportReceipt(null);
    setStages(freshStages());

    // This client operation id is intentionally distinct from the backend's
    // X-Request-ID. It survives upload, stream, fallback, and recovery calls.
    const operationRequestId = newClientId('tailor-operation');
    requestIdRef.current = operationRequestId;
    let failedRoute: string = TAILOR_ROUTES.upload;
    let failedMethod: 'GET' | 'POST' = 'POST';
    let lastPipelineStage: TailorStageName | null = null;
    let lastStreamPhase: TailorStreamError['phase'] | null = null;
    let lastFallbackSafe: boolean | null = null;

    try {
      const jid = await uploadJobDescriptions([jd.trim()], resumeId);
      setJobId(jid);

      const controller = new AbortController();
      abortRef.current = controller;

      let data;
      try {
        failedRoute = TAILOR_ROUTES.stream;
        const res = await streamImproveResume(resumeId, jid, promptId || undefined, {
          requestId: operationRequestId,
          signal: controller.signal,
          customInstructions: customInstructions.trim() || undefined,
          onStage: (e) => {
            lastPipelineStage = e.stage;
            setStages((prev) => ({
              ...prev,
              [e.stage]: e.status === 'done' ? 'done' : 'active',
            }));
          },
        });
        data = res.data;
      } catch (streamErr) {
        if (streamErr instanceof TailorStreamCancelled) {
          setPhase('input');
          return;
        }
        if (!(streamErr instanceof TailorStreamError)) throw streamErr;
        lastStreamPhase = streamErr.phase;
        lastFallbackSafe = streamErr.fallbackSafe;
        if (!streamErr.fallbackSafe) {
          const canRecoverCompletedResult =
            streamErr.phase === 'after-event' &&
            ['stream_incomplete', 'stream_transport_error'].includes(streamErr.code);
          if (!canRecoverCompletedResult) throw streamErr;

          // Recovery has its own route. If no durable result exists, attribute
          // the failure to the original stream rather than the successful 404 poll.
          failedRoute = TAILOR_ROUTES.recovery;
          failedMethod = 'GET';
          const recovered = await recoverTailorPreview(operationRequestId);
          if (!recovered) {
            failedRoute = TAILOR_ROUTES.stream;
            failedMethod = 'POST';
            throw streamErr;
          }
          data = recovered.data;
        } else {
          failedRoute = TAILOR_ROUTES.preview;
          const res = await previewImproveResume(
            resumeId,
            jid,
            promptId || undefined,
            customInstructions.trim() || undefined
          );
          data = res.data;
        }
      }

      setResult(data);
      rememberPreview(operationRequestId);
      setRecoverable(null);
      setPhase('review');
    } catch (e) {
      const isApiErr = e instanceof ApiError;
      const isStreamErr = e instanceof TailorStreamError;
      const retryable = isApiErr ? [0, 408, 425, 429, 500, 502, 503, 504].includes(e.status) : true;
      const message = toMessage(
        e,
        'Resume tailoring is temporarily unavailable. Please try again in a moment.'
      );
      const report = Object.freeze<ErrorReportPayload>({
        clientReportId: newClientId('tailor-report'),
        issueType: 'tailor_generation_failed',
        message: safeReportMessage(message),
        errorCode: isApiErr ? e.code : null,
        httpStatus: isApiErr && e.status > 0 ? e.status : null,
        retryable,
        apiMethod: failedMethod,
        apiRoute: failedRoute,
        operationRequestId,
        apiRequestId: isApiErr ? (e.requestId ?? null) : null,
        pipelineStage: lastPipelineStage,
        streamPhase: isStreamErr ? e.phase : lastStreamPhase,
        fallbackSafe: isStreamErr ? e.fallbackSafe : lastFallbackSafe,
      });
      setFailure({ message, retryable, report });
      setPhase('error');
    } finally {
      abortRef.current = null;
    }
  }

  async function onReportError() {
    if (!failure || reportStatus !== 'idle') return;
    setReportStatus('pending');
    try {
      const receipt = await createErrorReport(failure.report);
      setReportReceipt(receipt);
      setReportStatus('reported');
    } catch {
      setReportStatus('idle');
      toast({
        title: 'Could not send the error report',
        description: 'Nothing sensitive was sent. Please try reporting again.',
        variant: 'error',
      });
    }
  }

  function onCancelGenerate() {
    abortRef.current?.abort();
    if (requestIdRef.current) void cancelTailorStream(requestIdRef.current);
  }

  /** Persist the tailored preview as a new resume variant. Returns the new
   *  resume id on success (or null on failure) so callers can chain a follow-up
   *  action such as a PDF download. Never throws. */
  async function confirmPreview(): Promise<string | null> {
    if (!result) return null;
    if (!result.preview_id) {
      toast({
        title: 'This preview can no longer be confirmed. Please generate it again.',
        variant: 'error',
      });
      return null;
    }
    const confirmed = await confirmImproveResume({
      preview_id: result.preview_id,
      resume_id: resumeId,
      job_id: jobId,
      // Send the preview UNMODIFIED so its integrity hash matches; the photo
      // intent for the chosen template is applied server-side after validation
      // (mutating it here would break the preview hash and reject the save).
      improved_data: result.resume_preview as unknown as ResumeData,
      improvements: (result.improvements ?? []).map((i) => ({
        suggestion: i.suggestion,
        lineNumber: typeof i.lineNumber === 'number' ? i.lineNumber : null,
      })),
      // Only send when a template is explicitly chosen (else leave as generated).
      ...(templateWantsPhoto === undefined ? {} : { include_photo: templateWantsPhoto }),
    });
    draft.clear();
    // The preview is now saved; drop the recovery breadcrumb.
    forgetPreview();
    const newResumeId = confirmed?.data?.resume_id ?? null;
    // Persist the chosen presentation template on the new tailored resume so it
    // matches the preview the user just approved (best-effort - never block the
    // save if only the appearance write fails).
    if (newResumeId) {
      try {
        await updateResumeTemplateSettings(newResumeId, templateSettings);
      } catch {
        /* appearance is non-critical; the resume is safely saved */
      }
    }
    // A confirmed tailor creates a NEW resume variant AND a new application
    // card - refresh both list surfaces so they're visible immediately.
    invalidateResumeLists(qc);
    invalidateApplicationLists(qc);
    return newResumeId;
  }

  async function onAccept() {
    if (!result) return;
    setConfirmingDiscard(false);
    setSaving(true);
    try {
      const newResumeId = await confirmPreview();
      const hasExtras = extras.coverLetter || extras.outreach || extras.interviewPrep;
      if (newResumeId && hasExtras) {
        // Stay on this page so the requested extras render here once ready,
        // instead of bouncing to /applications and hiding the result.
        setSavedResumeId(newResumeId);
        const firstOn = (['coverLetter', 'outreach', 'interviewPrep'] as ExtraKind[]).find(
          (k) => extras[k]
        );
        if (firstOn) setActiveExtraTab(firstOn);
        toast({ title: 'Tailored resume saved', variant: 'success' });
        void runSelectedExtras(newResumeId);
      } else {
        toast({ title: 'Tailored resume saved', variant: 'success' });
        router.push('/applications');
      }
    } catch (e) {
      toast({
        title: toMessage(e, 'Could not save your tailored resume. Please try again.'),
        variant: 'error',
      });
    } finally {
      setSaving(false);
    }
  }

  /** Save the tailored resume and open it in the full editor - the path for a
   *  user who wants to tweak a bullet before finalizing (the review surface is
   *  read-only by design). */
  async function onAcceptAndEdit() {
    if (!result) return;
    setConfirmingDiscard(false);
    setEditing(true);
    try {
      const newId = await confirmPreview();
      if (!newId) {
        toast({
          title: 'Could not save your tailored resume. Please try again.',
          variant: 'error',
        });
        return;
      }
      toast({ title: 'Saved - opening the editor', variant: 'success' });
      if (extras.coverLetter || extras.outreach || extras.interviewPrep) {
        // The editor page has its own cards for these - just kick generation
        // off in the background so it's already in progress once we land there.
        void runSelectedExtras(newId);
      }
      router.push(`/resumes/${newId}`);
    } catch (e) {
      toast({
        title: toMessage(e, 'Could not save your tailored resume. Please try again.'),
        variant: 'error',
      });
    } finally {
      setEditing(false);
    }
  }

  /** Two-step discard: first click arms a confirmation, second click discards.
   *  Prevents an accidental click from throwing away a tailored result. */
  function onDiscard() {
    if (!confirmingDiscard) {
      setConfirmingDiscard(true);
      return;
    }
    setConfirmingDiscard(false);
    forgetPreview();
    setPrevResult(null);
    setPrevScore(null);
    setPreFit(null);
    setPhase('input');
  }

  /** Restore the previous attempt as the current result (A/B: keep the better
   *  of two generations). The just-replaced attempt becomes the "previous" so
   *  the user can toggle back and forth. */
  function onRestorePrevious() {
    if (!prevResult) return;
    const current = result;
    setResult(prevResult);
    setPrevResult(current);
    setPrevScore(current?.ats_score?.overall_score ?? null);
  }

  /** Save the tailored resume, then immediately download its PDF - the fast
   *  path a user wants right after tailoring (no navigate-away, no hunting for
   *  the file). Falls back gracefully if the export fails after a successful
   *  save (the resume is still safely persisted). */
  async function onAcceptAndDownload() {
    if (!result) return;
    setConfirmingDiscard(false);
    setDownloading(true);
    try {
      const newId = await confirmPreview();
      if (!newId) {
        toast({
          title: 'Could not save your tailored resume. Please try again.',
          variant: 'error',
        });
        return;
      }
      if (extras.coverLetter || extras.outreach || extras.interviewPrep) {
        void runSelectedExtras(newId);
      }
      try {
        const blob = await downloadResumePdf(newId, templateSettings);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        // Meaningful name from the tailored resume's own header (name + role),
        // not the opaque UUID. Falls back to a short id-based name if empty.
        const pi = toResumeData(result.resume_preview).personalInfo ?? {};
        a.download = buildResumeFilename({
          name: (pi as { name?: string }).name,
          role: (pi as { title?: string }).title,
          id: newId,
        });
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast({ title: 'Saved and downloaded', variant: 'success' });
      } catch {
        // Saved fine; only the export leg failed. Point the user to the saved
        // copy rather than losing their work.
        toast({
          title: 'Saved. The PDF export failed - you can download it from Applications.',
          variant: 'info',
        });
      }
      router.push('/applications');
    } catch (e) {
      toast({
        title: toMessage(e, 'Could not save your tailored resume. Please try again.'),
        variant: 'error',
      });
    } finally {
      setDownloading(false);
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

  // The right-hand preview surface (split view). It always shows a document so
  // the reader never faces an empty pane, adapting to the phase:
  //  - review     -> the tailored resume (hero) + primary save/download actions
  //  - generating -> the honest per-stage streaming timeline
  //  - input/error-> the user's CURRENT source resume (or a gentle empty state)
  // Sticky on desktop so the resume stays in view while editing on the left.
  const previewTitle =
    phase === 'review' && result
      ? 'Tailored resume'
      : phase === 'generating'
        ? 'Tailoring your resume…'
        : 'Your current resume';

  const previewBody =
    phase === 'generating' ? (
      <div className="space-y-4 p-5">
        <AiProgress
          stages={TAILOR_STAGES}
          activeKey={liveActiveKey}
          doneKeys={liveDoneKeys}
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
      </div>
    ) : phase === 'review' && result ? (
      <div className="overflow-auto bg-[var(--at-surface-2)] p-3 lg:max-h-[calc(100vh-9rem)]">
        <ResumeDocument
          data={applyTemplatePhoto(toResumeData(result.resume_preview))}
          settings={templateSettings}
        />
      </div>
    ) : sourceResumeQuery.isLoading ? (
      <div className="p-5">
        <LoadingSkeleton rows={6} />
      </div>
    ) : sourceResumeData ? (
      <div className="overflow-auto bg-[var(--at-surface-2)] p-3 lg:max-h-[calc(100vh-9rem)]">
        <ResumeDocument data={applyTemplatePhoto(sourceResumeData)} settings={templateSettings} />
      </div>
    ) : (
      <div className="flex flex-col items-center justify-center gap-2 p-10 text-center">
        <Eye className="h-6 w-6 text-[var(--muted-foreground)]" />
        <p className="text-sm font-medium">Your tailored resume will appear here</p>
        <p className="max-w-xs text-xs text-[var(--muted-foreground)]">
          Pick a resume and paste a job description on the left, then Generate to see the tailored
          document side by side.
        </p>
      </div>
    );

  const previewPane = (
    <div className="lg:sticky lg:top-6">
      <Card className="flex flex-col overflow-hidden p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <Eye className="h-4 w-4" /> {previewTitle}
          </p>
          {phase === 'review' && result ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={onAcceptAndDownload}
                loading={downloading}
                disabled={saving || editing}
              >
                <Download className="h-4 w-4" /> Save &amp; download PDF
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={onAccept}
                loading={saving}
                disabled={downloading || editing}
              >
                Accept &amp; save
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={onAcceptAndEdit}
                loading={editing}
                disabled={saving || downloading}
              >
                Save &amp; edit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={onDiscard}
                disabled={saving || downloading || editing}
                title={
                  confirmingDiscard ? 'Click again to discard this tailored resume' : undefined
                }
              >
                {confirmingDiscard ? 'Click again to discard' : 'Discard'}
              </Button>
            </div>
          ) : (
            <span className="rounded-full bg-[var(--at-surface-2)] px-2 py-0.5 text-xs text-[var(--muted-foreground)]">
              {selectedTemplateName}
            </span>
          )}
        </div>
        {previewBody}
      </Card>
    </div>
  );

  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
      <div>
        <h1 className="text-2xl font-semibold">Tailor to a job</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Paste a job description and get a tailored resume - grounded in your real experience.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:items-start">
        {/* LEFT: inputs, controls, fit analysis + result metrics/changes. */}
        <div className="space-y-6">
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
              title="You have unsaved tailoring inputs from earlier. Restore them?"
              restoreLabel="Restore"
              onRestore={() => {
                const d = draft.recovered;
                // Backwards-compatible: older drafts stored just the JD string.
                if (typeof d === 'string') {
                  setJd(d);
                } else if (d) {
                  setJd(d.jd ?? '');
                  if (d.customInstructions != null) setCustomInstructions(d.customInstructions);
                  if (d.promptId != null) setPromptId(d.promptId);
                  // Only restore a resume that still exists in the current list.
                  if (d.resumeId && resumesQuery.data?.some((r) => r.resume_id === d.resumeId)) {
                    setResumeId(d.resumeId);
                  }
                  if (d.templateId) {
                    const t = getTemplateById(d.templateId);
                    if (t) {
                      setSelectedTemplateId(t.id);
                      setTemplateSettings(templateToSettings(t));
                    }
                  }
                  if (d.extras) setExtras(d.extras);
                  // Reveal the Options panel so restored Extra Instructions /
                  // style / template are actually visible (they live behind the
                  // disclosure, otherwise a restore looks like it did nothing).
                  if (d.customInstructions || d.promptId || d.templateId) setShowOptions(true);
                }
                draft.dismissRecovery();
              }}
              onDiscard={draft.clear}
            />
          )}

          {/* Recover a completed-but-unsaved tailored resume from a prior visit. */}
          {recoverable && phase === 'input' && (
            <RecoveryBanner
              savedAt={null}
              title="You tailored a resume last time but didn't save it. Restore it?"
              restoreLabel={recovering ? 'Restoring...' : 'Restore tailored resume'}
              onRestore={onRecoverPreview}
              onDiscard={forgetPreview}
            />
          )}

          {/* Source + JD (always visible top of the continuous surface) */}
          <Card className="space-y-4 p-5">
            <div className="space-y-1.5">
              <Label>Source resume</Label>
              <Select
                value={resumeId}
                onValueChange={(v) => {
                  setResumeId(v);
                  saveDraft({ resumeId: v });
                }}
                disabled={phase === 'generating'}
              >
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
                  saveDraft({ jd: e.target.value });
                }}
                onKeyDown={(e) => {
                  const ready =
                    (e.metaKey || e.ctrlKey) &&
                    e.key === 'Enter' &&
                    jd.trim().length >= MIN_JD &&
                    resumeId &&
                    phase !== 'generating' &&
                    !aiBlocked;
                  if (!ready) return;
                  e.preventDefault();
                  // Cmd/Ctrl+Shift+Enter runs the cheaper "Analyze fit"; plain
                  // Cmd/Ctrl+Enter generates. Same muscle memory as the wizard.
                  if (e.shiftKey) {
                    if (!analyzing) void onAnalyze();
                  } else {
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
                  : 'Looks good - press ⌘/Ctrl+Enter to generate, or ⌘/Ctrl+Shift+Enter to analyze fit.'}
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
                <div className="mt-3 space-y-4">
                  <div className="space-y-1.5">
                    <Label>Tailoring style</Label>
                    <Select
                      value={promptId}
                      onValueChange={(v) => {
                        setPromptId(v);
                        saveDraft({ promptId: v });
                      }}
                    >
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

                  <div className="space-y-1.5">
                    <Label>Resume template</Label>
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="text-sm">{selectedTemplateName}</span>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setTemplatePickerOpen(true)}
                      >
                        Change template
                      </Button>
                    </div>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      Sets the look of the tailored resume in the preview and PDF. Affects layout
                      only, not the content.
                    </p>
                  </div>

                  {/* Photo templates: use the profile photo by default; offer an
                  upload here so the user can add one if their profile has none.
                  If they add nothing, the resume simply renders without a photo. */}
                  {templateWantsPhoto && (
                    <div className="space-y-1.5">
                      <Label>Profile photo</Label>
                      <AvatarUploader
                        avatarUrl={profileAvatarUrl}
                        onUploaded={(r) => onProfilePhotoChange(r.avatar_url)}
                        onRemoved={() => onProfilePhotoChange(null)}
                        onError={(m) => toast({ title: m, variant: 'error' })}
                      />
                      <p className="text-xs text-[var(--muted-foreground)]">
                        This template shows a photo. Your profile photo is used by default. Upload
                        one here if you don&apos;t have a profile photo yet - or leave it empty to
                        render without a photo.
                      </p>
                    </div>
                  )}

                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <Label htmlFor="custom-instructions">Extra instructions (optional)</Label>
                      <span className="text-[11px] text-[var(--muted-foreground)]">
                        {customInstructions.length}/{CUSTOM_INSTRUCTIONS_MAX}
                      </span>
                    </div>
                    <Textarea
                      id="custom-instructions"
                      value={customInstructions}
                      maxLength={CUSTOM_INSTRUCTIONS_MAX}
                      onChange={(e) => {
                        const v = e.target.value.slice(0, CUSTOM_INSTRUCTIONS_MAX);
                        setCustomInstructions(v);
                        saveDraft({ customInstructions: v });
                      }}
                      placeholder={
                        'e.g. Emphasize backend over frontend. Prioritize Kubernetes and Postgres. ' +
                        'Add project KRIA: automates daily tasks, voice/desktop control. ' +
                        'I also know Rust - add it.'
                      }
                      className="min-h-24"
                      disabled={phase === 'generating'}
                    />
                    <p className="text-xs text-[var(--muted-foreground)]">
                      Steer emphasis, ordering, and tone - and add your own real content (a project,
                      role, or skill you actually have). It only adds what you explicitly provide;
                      the AI won&apos;t invent experience on its own.
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <Label>Also generate</Label>
                    <div className="grid gap-1.5 sm:grid-cols-2">
                      {(Object.keys(EXTRA_META) as ExtraKind[]).map((kind) => {
                        const meta = EXTRA_META[kind];
                        return (
                          <label
                            key={kind}
                            htmlFor={`extra-${kind}`}
                            title={meta.hint}
                            className="flex items-center justify-between gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] px-2.5 py-2 text-sm"
                          >
                            <span className="flex items-center gap-2">
                              <meta.Icon className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]" />
                              {meta.label}
                            </span>
                            <Switch
                              id={`extra-${kind}`}
                              checked={extras[kind]}
                              onCheckedChange={(v) => toggleExtra(kind, v)}
                              aria-label={`Also generate ${meta.label.toLowerCase()}`}
                            />
                          </label>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button
                onClick={onGenerate}
                loading={phase === 'generating'}
                disabled={jd.trim().length < MIN_JD || !resumeId || aiBlocked}
                title={aiBlocked ? aiBlockTitle : GENERATE_COST_HINT}
              >
                <Sparkles className="h-4 w-4" /> {phase === 'review' ? 'Regenerate' : 'Generate'}
              </Button>
              <Button
                variant="outline"
                onClick={onAnalyze}
                loading={analyzing}
                disabled={
                  jd.trim().length < MIN_JD || !resumeId || phase === 'generating' || aiBlocked
                }
                title={
                  aiBlocked
                    ? aiBlockTitle
                    : `See how your resume matches first · ${ANALYZE_COST_HINT}`
                }
              >
                <Target className="h-4 w-4" /> Analyze fit
              </Button>
            </div>
            {/* Honest cost/time expectation so users on slow/free models aren't
            surprised by a multi-step run. */}
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted-foreground)]">
              <span className="inline-flex items-center gap-1">
                <Sparkles className="h-3 w-3" /> Uses your configured AI provider
              </span>
              <span>Generate: {GENERATE_COST_HINT}</span>
              <span>Analyze fit: {ANALYZE_COST_HINT}</span>
            </p>
          </Card>

          {/* Pre-generation fit analysis (explicit action, cheaper than a full tailor) */}
          {analysis && phase !== 'generating' && (
            <Card className="space-y-4 p-5">
              {/* Inputs changed since this analysis ran - flag it as stale instead
              of wiping it, so the user keeps the reference and re-analyzes only
              if they want to. */}
              {analysisStale && (
                <div
                  role="status"
                  className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 px-3 py-2 text-xs text-[var(--foreground)]"
                >
                  <span>
                    Your resume or job description changed since this analysis. It may be out of
                    date.
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onAnalyze}
                    loading={analyzing}
                    disabled={jd.trim().length < MIN_JD || !resumeId || aiBlocked}
                  >
                    Re-analyze
                  </Button>
                </div>
              )}
              <div className="flex items-start gap-5">
                {analysis.fit_score != null && <ScoreRing score={analysis.fit_score} />}
                <div className="flex-1 space-y-1">
                  <p className="flex items-center gap-1.5 text-sm font-medium">
                    Fit analysis
                    <Explain label="What is fit analysis?">
                      A quick, pre-generation estimate of how many keywords from this job already
                      appear in your selected resume. Use it to decide whether to tailor - it does
                      not change your resume.
                    </Explain>
                  </p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {analysis.fit_score != null
                      ? `Your resume already covers ${analysis.matched.length} of ${
                          analysis.matched.length + analysis.missing.length
                        } key terms. Generate to close the gaps.`
                      : 'We could show the keyword breakdown, but not a fit score: this resume has no processed data yet (it may still be importing). Pick a ready resume, or generate anyway - tailoring will still work.'}
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
                  <p className="mb-1.5 text-xs font-medium text-[var(--foreground)]">
                    Already covered
                  </p>
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

          {/* Generating progress + the tailored/source resume render in the sticky
          preview pane (right column) - see `previewPane`. */}

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
                  <p className="text-sm font-medium">Resume tailoring did not complete</p>
                  <p className="text-sm text-[var(--muted-foreground)]">{failure.message}</p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Your job description and resume selection are saved - nothing was lost.
                  </p>
                  {(failure.report.apiRequestId || failure.report.operationRequestId) && (
                    <p className="pt-1 font-mono text-[11px] text-[var(--muted-foreground)]">
                      Reference: {failure.report.apiRequestId ?? failure.report.operationRequestId}
                    </p>
                  )}
                  <p className="pt-1 text-xs text-[var(--muted-foreground)]">
                    Reporting sends only technical context, never your resume or job description.
                  </p>
                  <div aria-live="polite">
                    {reportStatus === 'reported' && reportReceipt && (
                      <p className="pt-1 text-sm font-medium text-[var(--at-success)]">
                        Reported · Reference: {reportReceipt.reportId}
                      </p>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {failure.retryable && (
                  <Button size="sm" onClick={onGenerate} disabled={aiBlocked}>
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
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onReportError}
                  disabled={reportStatus !== 'idle'}
                >
                  {reportStatus === 'pending'
                    ? 'Reporting...'
                    : reportStatus === 'reported'
                      ? 'Reported'
                      : 'Report error'}
                </Button>
              </div>
            </Card>
          )}

          {/* Review - results render inline below */}
          {phase === 'review' && result && (
            <div className="space-y-3">
              {/* The tailored resume and its primary actions (save / download /
              edit / discard) live in the sticky preview pane on the right. Here
              on the left we surface the metrics and the change list. On narrow
              screens the pane stacks below this column. */}
              <Card className="flex items-center gap-2.5 p-3">
                <ShieldCheck className="h-4 w-4 shrink-0 text-[var(--at-success)]" />
                <p className="text-sm font-medium">
                  {savedResumeId
                    ? 'Saved - your requested extras are generating below'
                    : 'Your tailored resume is ready - review it in the preview, then save'}
                </p>
                {savedResumeId && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="ml-auto shrink-0"
                    onClick={() => router.push('/applications')}
                  >
                    Go to Applications
                  </Button>
                )}
              </Card>

              {(extras.coverLetter ||
                extras.outreach ||
                extras.interviewPrep ||
                extras.keywordMatch) && (
                <CompanionDocuments
                  extras={extras}
                  extraStatus={extraStatus}
                  coverLetterText={coverLetterText}
                  outreachText={outreachText}
                  interviewPrepData={interviewPrepData}
                  savedResumeId={savedResumeId}
                  personalInfo={
                    (toResumeData(result.resume_preview).personalInfo ?? {}) as {
                      name?: string;
                      title?: string;
                    }
                  }
                  keywordMatchJd={keywordMatchJd}
                  resumeDataForMatch={toResumeData(result.resume_preview)}
                  activeTab={activeExtraTab}
                  onTabChange={setActiveExtraTab}
                  onRetry={retryExtra}
                  saving={saving && !savedResumeId}
                />
              )}

              {/* A/B: a prior attempt is available (user regenerated). Let them
              restore the better one instead of losing it. */}
              {prevResult && (
                <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
                  <p className="text-sm text-[var(--muted-foreground)]">
                    You have a previous attempt
                    {prevResult.ats_score
                      ? ` (match ${Math.round(prevResult.ats_score.overall_score)})`
                      : ''}
                    . Prefer it?
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onRestorePrevious}
                    disabled={saving || downloading || editing}
                  >
                    <RotateCw className="h-4 w-4" /> Restore previous attempt
                  </Button>
                </Card>
              )}

              {/* Before -> after: quantify what tailoring changed. Shown when the
              user analyzed first (fit delta) or regenerated (score delta). */}
              {ats && (preFit || prevScore != null) && (
                <Card className="flex flex-wrap items-center gap-x-6 gap-y-2 p-3">
                  <p className="flex items-center gap-1.5 text-sm font-medium">
                    <TrendingUp className="h-4 w-4 text-[var(--at-success)]" />
                    {prevScore != null ? 'Since your last attempt' : 'What tailoring improved'}
                  </p>
                  {preFit?.score != null && (
                    <span className="inline-flex items-center gap-1.5 text-sm">
                      <span className="text-[var(--muted-foreground)]">Fit</span>
                      <span className="font-semibold">{Math.round(preFit.score)}</span>
                      <ArrowRight className="h-3.5 w-3.5 text-[var(--muted-foreground)]" />
                      <span className="font-semibold text-[var(--at-success)]">
                        {Math.round(ats.overall_score)}
                      </span>
                    </span>
                  )}
                  {prevScore != null && (
                    <span className="inline-flex items-center gap-1.5 text-sm">
                      <span className="text-[var(--muted-foreground)]">Match</span>
                      <span className="font-semibold">{Math.round(prevScore)}</span>
                      <ArrowRight className="h-3.5 w-3.5 text-[var(--muted-foreground)]" />
                      <span className="font-semibold text-[var(--at-success)]">
                        {Math.round(ats.overall_score)}
                      </span>
                    </span>
                  )}
                  {preFit && (
                    <span className="inline-flex items-center gap-1.5 text-sm">
                      <span className="text-[var(--muted-foreground)]">Missing terms</span>
                      <span className="font-semibold">{preFit.missing}</span>
                      <ArrowRight className="h-3.5 w-3.5 text-[var(--muted-foreground)]" />
                      <span className="font-semibold text-[var(--at-success)]">
                        {ats.missing_keywords.length}
                      </span>
                    </span>
                  )}
                </Card>
              )}

              {/* Instruction results / notices (e.g. "Added project KRIA",
              "Couldn't add X"). Surfaces ONLY what the Extra Instructions step
              did - never internal diff diagnostics - so an addition is never
              silently dropped and the message is always relevant. */}
              {result.instruction_notes && result.instruction_notes.length > 0 && (
                <Card className="space-y-1.5 border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-3">
                  <p className="text-sm font-medium">Notes on your instructions</p>
                  <ul className="list-disc space-y-0.5 pl-5 text-xs text-[var(--muted-foreground)]">
                    {result.instruction_notes.map((w, i) => (
                      <li key={`note-${i}`}>{w}</li>
                    ))}
                  </ul>
                </Card>
              )}

              {ats && (
                <Card className="space-y-3 p-4">
                  <div className="flex items-center gap-4">
                    <ScoreRing score={ats.overall_score} />
                    <div className="flex-1 space-y-1.5">
                      <p className="flex items-center gap-1.5 text-sm font-medium">
                        Match score
                        <Explain label="What is the match score?">
                          An estimate of how well this tailored resume aligns with the job
                          description, combining keyword match, skills coverage, and section
                          completeness. Higher is better - aim for 75+. It is guidance, not a
                          guarantee of how a specific ATS will parse your resume.
                        </Explain>
                      </p>
                      <div className="grid grid-cols-3 gap-1.5 text-xs">
                        <SubScore label="Keywords" value={ats.sub_scores.keyword_match} />
                        <SubScore label="Skills" value={ats.sub_scores.skills_coverage} />
                        <SubScore label="Sections" value={ats.sub_scores.section_completeness} />
                      </div>
                    </div>
                  </div>
                  {ats.missing_keywords.length > 0 && (
                    <div className="border-t border-[var(--border)] pt-3">
                      <p className="mb-1.5 text-xs font-medium text-[var(--foreground)]">
                        Missing keywords
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {ats.missing_keywords.slice(0, showDetail ? undefined : 8).map((k) => (
                          <Badge key={k} variant="warning">
                            {k}
                          </Badge>
                        ))}
                        {!showDetail && ats.missing_keywords.length > 8 && (
                          <button
                            onClick={() => setShowDetail(true)}
                            className="text-xs text-[var(--primary)] hover:underline"
                          >
                            +{ats.missing_keywords.length - 8} more
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </Card>
              )}

              {diff && (
                <Card className="p-4">
                  <div className="flex items-center justify-between">
                    <p className="flex items-center gap-1.5 text-sm font-medium">
                      {diff.total_changes} change{diff.total_changes === 1 ? '' : 's'} proposed
                      <Explain label="What are these changes?">
                        Each change rewrites or reorders content you already have to better match
                        the role - emphasising relevant skills and keywords. Nothing is invented;
                        expand the details to review every edit before you accept.
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
                    <ChangeReview changes={result.detailed_changes} />
                  )}
                </Card>
              )}
            </div>
          )}
        </div>
        {previewPane}
      </div>

      {/* Template picker - the SAME visual gallery as the /templates page
          (real-render cards, search/filter/preview), not a dropdown. */}
      <Dialog open={templatePickerOpen} onOpenChange={setTemplatePickerOpen}>
        <DialogContent className="max-w-6xl">
          <DialogHeader>
            <DialogTitle>Choose a template</DialogTitle>
            <DialogDescription>
              Pick the look for your tailored resume. Every preview is the real renderer, so what
              you see is exactly what you&apos;ll export.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[75vh] overflow-y-auto pr-1">
            <TemplateGallery
              selectedId={selectedTemplateId}
              onSelect={onPickTemplate}
              ctaLabel="Use this template"
            />
          </div>
        </DialogContent>
      </Dialog>
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

/** Small status pill shared by each companion-document tab trigger. */
function ExtraStatusDot({ status }: { status: ExtraStatus }) {
  if (status === 'pending') {
    return <Loader2 className="h-3 w-3 animate-spin text-[var(--muted-foreground)]" />;
  }
  if (status === 'done') {
    return <CircleCheck className="h-3 w-3 text-[var(--at-success)]" />;
  }
  if (status === 'error') {
    return <span className="h-1.5 w-1.5 rounded-full bg-[var(--destructive)]" />;
  }
  return null;
}

/** Placeholder shown inside a companion-document tab before its content has
 *  arrived: an idle "generates on save" note, a spinner while pending, or a
 *  retry affordance on error. Declared at module scope (not inline in
 *  {@link CompanionDocuments}) so it isn't recreated every render. */
function ExtraPending({
  kind,
  status,
  saving,
  onRetry,
}: {
  kind: ExtraKind;
  status: ExtraStatus;
  saving: boolean;
  onRetry: (kind: ExtraKind) => Promise<void> | void;
}) {
  if (!saving && status === 'idle') {
    return (
      <p className="py-4 text-center text-xs text-[var(--muted-foreground)]">
        Generates automatically once you save this resume.
      </p>
    );
  }
  if (status === 'pending' || (saving && status === 'idle')) {
    return (
      <div className="flex items-center justify-center gap-2 py-4 text-xs text-[var(--muted-foreground)]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Generating{' '}
        {EXTRA_META[kind].label.toLowerCase()}…
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className="flex items-center justify-between gap-2 py-3">
        <p className="text-xs text-[var(--destructive)]">Could not generate. Try again?</p>
        <Button variant="outline" size="sm" onClick={() => onRetry(kind)}>
          <RotateCw className="h-3.5 w-3.5" /> Retry
        </Button>
      </div>
    );
  }
  return null;
}

/**
 * Compact companion-documents panel for the tailor review step - cover letter,
 * outreach message, interview prep, and keyword match, all in one small
 * tabbed card instead of four separate full-height cards. Only the toggles
 * the user turned on get a tab. Before the resume is saved, each pending tab
 * shows a short "generates on save" notice instead of a spinner (nothing has
 * been requested from the backend yet).
 */
function CompanionDocuments({
  extras,
  extraStatus,
  coverLetterText,
  outreachText,
  interviewPrepData,
  savedResumeId,
  personalInfo,
  keywordMatchJd,
  resumeDataForMatch,
  activeTab,
  onTabChange,
  onRetry,
  saving,
}: {
  extras: ExtrasState;
  extraStatus: Record<ExtraKind, ExtraStatus>;
  coverLetterText: string | null;
  outreachText: string | null;
  interviewPrepData: InterviewPrepData | null;
  savedResumeId: string | null;
  personalInfo: { name?: string; title?: string };
  keywordMatchJd: string | null;
  resumeDataForMatch: ResumeData;
  activeTab: ExtraKind;
  onTabChange: (kind: ExtraKind) => void;
  onRetry: (kind: ExtraKind) => Promise<void> | void;
  saving: boolean;
}) {
  const { toast } = useToast();
  const [copiedKind, setCopiedKind] = React.useState<ExtraKind | null>(null);

  const enabledKinds = (Object.keys(EXTRA_META) as ExtraKind[]).filter((k) => extras[k]);
  if (enabledKinds.length === 0) return null;
  const tab: ExtraKind = enabledKinds.includes(activeTab) ? activeTab : enabledKinds[0];

  async function copyText(kind: ExtraKind, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKind(kind);
      window.setTimeout(() => setCopiedKind((k) => (k === kind ? null : k)), 1500);
    } catch {
      toast({ title: 'Could not copy to clipboard', variant: 'error' });
    }
  }

  return (
    <Card className="p-4">
      <Tabs value={tab} onValueChange={(v) => onTabChange(v as ExtraKind)}>
        <TabsList className="w-full flex-wrap justify-start">
          {enabledKinds.map((kind) => {
            const meta = EXTRA_META[kind];
            return (
              <TabsTrigger key={kind} value={kind} className="gap-1.5">
                <meta.Icon className="h-3.5 w-3.5" />
                {meta.label}
                <ExtraStatusDot status={extraStatus[kind]} />
              </TabsTrigger>
            );
          })}
        </TabsList>

        {extras.coverLetter && (
          <TabsContent value="coverLetter" className="mt-3">
            {coverLetterText ? (
              <div className="space-y-2">
                <div className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3 text-xs leading-relaxed text-[var(--foreground)]">
                  {coverLetterText}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => copyText('coverLetter', coverLetterText)}
                  >
                    {copiedKind === 'coverLetter' ? (
                      <Check className="h-3.5 w-3.5" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                    {copiedKind === 'coverLetter' ? 'Copied' : 'Copy'}
                  </Button>
                  {savedResumeId && (
                    <ExportButton
                      kind="cover-letter"
                      resumeId={savedResumeId}
                      label="Download PDF"
                      name={personalInfo.name}
                      role={personalInfo.title}
                    />
                  )}
                </div>
              </div>
            ) : (
              <ExtraPending
                kind="coverLetter"
                status={extraStatus.coverLetter}
                saving={saving}
                onRetry={onRetry}
              />
            )}
          </TabsContent>
        )}

        {extras.outreach && (
          <TabsContent value="outreach" className="mt-3">
            {outreachText ? (
              <div className="space-y-2">
                <div className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3 text-xs leading-relaxed text-[var(--foreground)]">
                  {outreachText}
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => copyText('outreach', outreachText)}
                >
                  {copiedKind === 'outreach' ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  {copiedKind === 'outreach' ? 'Copied' : 'Copy'}
                </Button>
              </div>
            ) : (
              <ExtraPending
                kind="outreach"
                status={extraStatus.outreach}
                saving={saving}
                onRetry={onRetry}
              />
            )}
          </TabsContent>
        )}

        {extras.interviewPrep && (
          <TabsContent value="interviewPrep" className="mt-3">
            {interviewPrepData ? (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
                  <span>{interviewPrepData.resume_questions.length} likely questions</span>
                  <span>{interviewPrepData.skill_gaps.length} skill gaps</span>
                  <span>{interviewPrepData.talking_points.length} talking points</span>
                </div>
                {savedResumeId && (
                  <ExportButton
                    kind="interview-prep"
                    resumeId={savedResumeId}
                    label="Download PDF"
                    name={personalInfo.name}
                    role={personalInfo.title}
                  />
                )}
              </div>
            ) : (
              <ExtraPending
                kind="interviewPrep"
                status={extraStatus.interviewPrep}
                saving={saving}
                onRetry={onRetry}
              />
            )}
          </TabsContent>
        )}

        {extras.keywordMatch && (
          <TabsContent value="keywordMatch" className="mt-3">
            {keywordMatchJd ? (
              <KeywordMatchSummary jd={keywordMatchJd} resumeData={resumeDataForMatch} />
            ) : (
              <ExtraPending
                kind="keywordMatch"
                status={extraStatus.keywordMatch}
                saving={saving}
                onRetry={onRetry}
              />
            )}
          </TabsContent>
        )}
      </Tabs>
    </Card>
  );
}

/** Inline keyword-match stats for the tailor review step - the same
 *  client-side comparison the resume page's JdMatchCard does, computed here
 *  against the freshly tailored preview so the user sees it without leaving
 *  the tailor flow. No LLM call. */
function KeywordMatchSummary({ jd, resumeData }: { jd: string; resumeData: ResumeData }) {
  const keywords = React.useMemo(() => extractKeywords(jd), [jd]);
  const resumeText = React.useMemo(() => buildResumeTextForMatch(resumeData), [resumeData]);
  const stats = React.useMemo(
    () => calculateMatchStats(resumeText, keywords),
    [resumeText, keywords]
  );
  const pct = stats.matchPercentage;
  const pctTone =
    pct >= 50
      ? 'text-[var(--at-success)]'
      : pct >= 30
        ? 'text-[var(--at-warning)]'
        : 'text-[var(--destructive)]';
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      <span className="inline-flex items-center gap-1.5">
        <Target className="h-4 w-4 text-[var(--primary)]" />
        {keywords.size} keywords
      </span>
      <span className="inline-flex items-center gap-1.5">
        <CircleCheck className="h-4 w-4 text-[var(--at-success)]" />
        {stats.matchCount} matched
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="text-[var(--muted-foreground)]">Match rate</span>
        <span className={`text-base font-bold ${pctTone}`}>{pct}%</span>
      </span>
    </div>
  );
}
