'use client';

/**
 * Application Workspace (Task 9.2-9.6 / Req 12,13,14,17). Overview + resource
 * sections (Cover Letter - Interview Prep - Outreach). Per-job deliverables
 * live here (not in the Resume Editor). Generation reuses existing APIs and is
 * cost-aware (only on explicit action).
 */
import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import PenLine from 'lucide-react/dist/esm/icons/pen-line';
import Copy from 'lucide-react/dist/esm/icons/copy';
import CopyPlus from 'lucide-react/dist/esm/icons/copy-plus';
import Repeat from 'lucide-react/dist/esm/icons/repeat';
import Ellipsis from 'lucide-react/dist/esm/icons/ellipsis';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Textarea } from '@/components/atelier/input';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/atelier/tabs';
import { LoadingSkeleton, ErrorState, EmptyState } from '@/components/atelier/states';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/atelier/dropdown-menu';
import { useToast } from '@/components/atelier/toast';
import { ExportButton } from '@/components/resume/export-button';
import { AiProgress } from '@/components/ai/ai-progress';
import {
  COVER_LETTER_STAGES,
  COVER_LETTER_MESSAGES,
  OUTREACH_STAGES,
  OUTREACH_MESSAGES,
  INTERVIEW_PREP_STAGES,
  INTERVIEW_PREP_MESSAGES,
  ESTIMATE_MEDIUM,
} from '@/lib/ai-progress-copy';
import { SchedulingPanel } from '@/components/scheduling/scheduling-panel';
import {
  APPLICATION_STATUS_ORDER,
  createApplication,
  type ApplicationStatus,
} from '@/lib/api/tracker';
import {
  useApplicationDetail,
  useMoveApplication,
  useUpdateApplicationNotes,
  STATUS_LABELS,
} from '@/features/applications/hooks';
import {
  generateCoverLetter,
  generateOutreachMessage,
  generateInterviewPrep,
  fetchResume,
} from '@/lib/api/resume';
import { useFeatureConfig } from '@/features/settings/hooks';
import { useSystemStatus } from '@/features/home/hooks';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateApplicationLists, queryKeys } from '@/lib/query/client';
import type { InterviewPrepData } from '@/components/common/resume_previewer_context';

type Deliverable = {
  coverLetter: string | null;
  outreach: string | null;
  interviewPrep: InterviewPrepData | null;
};

export default function ApplicationWorkspacePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const { data: app, isLoading, isError, refetch } = useApplicationDetail(id);
  const move = useMoveApplication();
  const notesMut = useUpdateApplicationNotes();
  const features = useFeatureConfig();
  const statusQuery = useSystemStatus();
  const aiUnconfigured = statusQuery.data && !statusQuery.data.llm_configured;
  const { toast } = useToast();
  const qc = useQueryClient();

  const coverEnabled = features.data?.enable_cover_letter ?? true;
  const outreachEnabled = features.data?.enable_outreach_message ?? true;
  const prepEnabled = features.data?.enable_interview_prep ?? true;
  const [duplicating, setDuplicating] = React.useState(false);

  const [notes, setNotes] = React.useState('');
  const [deliverable, setDeliverable] = React.useState<Deliverable>({
    coverLetter: null,
    outreach: null,
    interviewPrep: null,
  });
  const [busy, setBusy] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (app) setNotes(app.notes ?? '');
  }, [app]);

  // Load existing deliverables from the tailored resume record.
  React.useEffect(() => {
    if (!app?.resume_id) return;
    let active = true;
    fetchResume(app.resume_id)
      .then((r) => {
        if (!active) return;
        setDeliverable({
          coverLetter: r.cover_letter ?? null,
          outreach: r.outreach_message ?? null,
          interviewPrep: r.interview_prep ?? null,
        });
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [app?.resume_id]);

  async function run(kind: 'cover' | 'outreach' | 'prep') {
    if (!app?.resume_id) return;
    setBusy(kind);
    try {
      if (kind === 'cover') {
        const content = await generateCoverLetter(app.resume_id);
        setDeliverable((d) => ({ ...d, coverLetter: content }));
      } else if (kind === 'outreach') {
        const content = await generateOutreachMessage(app.resume_id);
        setDeliverable((d) => ({ ...d, outreach: content }));
      } else {
        const prep = await generateInterviewPrep(app.resume_id);
        setDeliverable((d) => ({ ...d, interviewPrep: prep }));
      }
      // These persist onto the resume record - refresh its cache so the Resume
      // Editor (if open elsewhere) reflects the new deliverable on next view.
      qc.invalidateQueries({ queryKey: queryKeys.resume(app.resume_id) });
      toast({ title: 'Generated', variant: 'success' });
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Generation failed', variant: 'error' });
    } finally {
      setBusy(null);
    }
  }

  async function onMove(status: ApplicationStatus) {
    try {
      await move.mutateAsync({ id, status });
      toast({ title: `Moved to ${STATUS_LABELS[status]}`, variant: 'success' });
      refetch();
    } catch {
      toast({ title: 'Could not update stage', variant: 'error' });
    }
  }

  async function saveNotes() {
    try {
      await notesMut.mutateAsync({ id, notes });
      toast({ title: 'Notes saved', variant: 'success' });
    } catch {
      toast({ title: 'Could not save notes', variant: 'error' });
    }
  }

  // Task 9.6 - duplicate this application (same resume + JD) into a fresh card.
  async function duplicateApplication() {
    if (!app?.resume_id) return;
    setDuplicating(true);
    try {
      const created = await createApplication({
        resume_id: app.resume_id,
        job_description: app.job_content || '',
        company: app.company ?? undefined,
        role: app.role ?? undefined,
        status: 'saved',
        notes: app.notes ?? undefined,
      });
      invalidateApplicationLists(qc);
      toast({ title: 'Application duplicated', variant: 'success' });
      router.push(`/applications/${created.application_id}`);
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Could not duplicate', variant: 'error' });
    } finally {
      setDuplicating(false);
    }
  }

  // Task 9.6 - reuse the underlying resume to tailor for another job.
  function reuseResume() {
    if (!app) return;
    const source = app.master_resume_id || app.resume_id;
    router.push(`/tailor?resume=${source}`);
  }

  if (isLoading) return <LoadingSkeleton rows={4} />;
  if (isError || !app)
    return <ErrorState description="Could not load this application." onRetry={() => refetch()} />;

  return (
    <div className="space-y-6">
      <Link
        href="/applications"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
      >
        <ArrowLeft className="h-4 w-4" /> Applications
      </Link>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{app.role || 'Untitled role'}</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            {app.company || 'Unknown company'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">{STATUS_LABELS[app.status]}</Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Move to stage</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {APPLICATION_STATUS_ORDER.map((s) => (
                <DropdownMenuItem key={s} onClick={() => onMove(s)}>
                  {STATUS_LABELS[s]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          {app.resume_id && (
            <Button asChild variant="outline">
              <Link href={`/resumes/${app.resume_id}`}>
                <PenLine className="h-4 w-4" /> Edit resume
              </Link>
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More actions" loading={duplicating}>
                <Ellipsis className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={duplicateApplication} disabled={!app.resume_id}>
                <CopyPlus className="h-4 w-4" /> Duplicate application
              </DropdownMenuItem>
              <DropdownMenuItem onClick={reuseResume}>
                <Repeat className="h-4 w-4" /> Reuse resume for another job
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="schedule">Schedule</TabsTrigger>
          {coverEnabled && <TabsTrigger value="cover">Cover Letter</TabsTrigger>}
          {prepEnabled && <TabsTrigger value="prep">Interview Prep</TabsTrigger>}
          {outreachEnabled && <TabsTrigger value="outreach">Outreach</TabsTrigger>}
        </TabsList>

        <TabsContent value="overview">
          <div className="grid gap-4 md:grid-cols-2">
            <Card className="p-5">
              <h2 className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">
                Job description
              </h2>
              <p className="max-h-72 overflow-y-auto whitespace-pre-wrap text-sm text-[var(--foreground)]">
                {app.job_content || 'No job description saved.'}
              </p>
            </Card>
            <Card className="p-5">
              <h2 className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">Notes</h2>
              <Textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Add notes..."
              />
              <div className="mt-2 flex justify-end">
                <Button
                  size="sm"
                  variant="outline"
                  loading={notesMut.isPending}
                  onClick={saveNotes}
                >
                  Save notes
                </Button>
              </div>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="schedule">
          <SchedulingPanel applicationId={id} />
        </TabsContent>

        {coverEnabled && (
          <TabsContent value="cover">
            <DeliverablePanel
              label="cover letter"
              value={deliverable.coverLetter}
              busy={busy === 'cover'}
              disabled={Boolean(aiUnconfigured)}
              onGenerate={() => run('cover')}
              onCopy={() =>
                deliverable.coverLetter && navigator.clipboard.writeText(deliverable.coverLetter)
              }
              exportSlot={
                app.resume_id ? (
                  <ExportButton kind="cover-letter" resumeId={app.resume_id} label="Export PDF" />
                ) : null
              }
            />
          </TabsContent>
        )}

        {prepEnabled && (
          <TabsContent value="prep">
            {busy === 'prep' && !deliverable.interviewPrep ? (
              <Card className="p-5">
                <AiProgress
                  stages={INTERVIEW_PREP_STAGES}
                  active
                  messages={INTERVIEW_PREP_MESSAGES}
                  estimate={ESTIMATE_MEDIUM}
                />
              </Card>
            ) : deliverable.interviewPrep ? (
              <div className="space-y-3">
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="outline"
                    loading={busy === 'prep'}
                    disabled={Boolean(aiUnconfigured)}
                    onClick={() => run('prep')}
                  >
                    Regenerate
                  </Button>
                </div>
                <InterviewPrep data={deliverable.interviewPrep} />
              </div>
            ) : (
              <EmptyState
                icon={Sparkles}
                title="No interview prep yet"
                description={
                  aiUnconfigured
                    ? 'Add an AI provider key in settings to generate interview prep.'
                    : 'Generate resume-grounded questions and talking points for this role.'
                }
                action={
                  aiUnconfigured ? (
                    <Button asChild variant="outline">
                      <Link href="/settings">Open settings</Link>
                    </Button>
                  ) : (
                    <Button loading={busy === 'prep'} onClick={() => run('prep')}>
                      <Sparkles className="h-4 w-4" /> Generate interview prep
                    </Button>
                  )
                }
              />
            )}
          </TabsContent>
        )}

        {outreachEnabled && (
          <TabsContent value="outreach">
            <DeliverablePanel
              label="outreach message"
              value={deliverable.outreach}
              busy={busy === 'outreach'}
              disabled={Boolean(aiUnconfigured)}
              onGenerate={() => run('outreach')}
              onCopy={() =>
                deliverable.outreach && navigator.clipboard.writeText(deliverable.outreach)
              }
            />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}

function DeliverablePanel({
  label,
  value,
  busy,
  disabled = false,
  onGenerate,
  onCopy,
  exportSlot,
}: {
  label: string;
  value: string | null;
  busy: boolean;
  disabled?: boolean;
  onGenerate: () => void;
  onCopy: () => void;
  exportSlot?: React.ReactNode;
}) {
  // While the FIRST draft generates, show the honest stage timeline instead of a
  // bare spinner (regenerating keeps the existing value visible).
  if (busy && !value) {
    const progress =
      label === 'outreach message'
        ? { stages: OUTREACH_STAGES, messages: OUTREACH_MESSAGES }
        : { stages: COVER_LETTER_STAGES, messages: COVER_LETTER_MESSAGES };
    return (
      <Card className="p-5">
        <AiProgress
          stages={progress.stages}
          active
          messages={progress.messages}
          estimate={ESTIMATE_MEDIUM}
        />
      </Card>
    );
  }
  if (!value) {
    return (
      <EmptyState
        icon={Sparkles}
        title={`No ${label} yet`}
        description={
          disabled
            ? `Add an AI provider key in settings to generate a ${label}.`
            : `Generate a tailored ${label} grounded in your resume and the job.`
        }
        action={
          disabled ? (
            <Button asChild variant="outline">
              <Link href="/settings">Open settings</Link>
            </Button>
          ) : (
            <Button loading={busy} onClick={onGenerate}>
              <Sparkles className="h-4 w-4" /> Generate {label}
            </Button>
          )
        }
      />
    );
  }
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold capitalize text-[var(--muted-foreground)]">{label}</h2>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={onCopy}>
            <Copy className="h-4 w-4" /> Copy
          </Button>
          {exportSlot}
          <Button
            size="sm"
            variant="outline"
            loading={busy}
            disabled={disabled}
            onClick={onGenerate}
          >
            Regenerate
          </Button>
        </div>
      </div>
      <p className="max-h-[28rem] overflow-y-auto whitespace-pre-wrap text-sm text-[var(--foreground)]">
        {value}
      </p>
    </Card>
  );
}

/** Atelier-styled interview-prep renderer (replaces raw JSON dump). */
function InterviewPrep({ data }: { data: InterviewPrepData }) {
  const questionBlock = (title: string, items: InterviewPrepData['resume_questions']) =>
    items.length > 0 && (
      <Card className="space-y-3 p-5">
        <h3 className="text-sm font-semibold text-[var(--muted-foreground)]">{title}</h3>
        <ul className="space-y-3">
          {items.map((q, i) => (
            <li key={i} className="space-y-1">
              <p className="text-sm font-medium text-[var(--foreground)]">{q.question}</p>
              {q.focus_area && (
                <p className="text-xs text-[var(--muted-foreground)]">Focus: {q.focus_area}</p>
              )}
              {q.suggested_answer_points.length > 0 && (
                <ul className="ml-4 list-disc space-y-0.5 text-xs text-[var(--muted-foreground)]">
                  {q.suggested_answer_points.map((p, j) => (
                    <li key={j}>{p}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Card>
    );

  return (
    <div className="space-y-4">
      {data.role_fit_analysis.length > 0 && (
        <Card className="space-y-2 p-5">
          <h3 className="text-sm font-semibold text-[var(--muted-foreground)]">Role fit</h3>
          <ul className="ml-4 list-disc space-y-1 text-sm text-[var(--foreground)]">
            {data.role_fit_analysis.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </Card>
      )}
      {questionBlock('Likely questions', data.resume_questions)}
      {questionBlock('Project follow-ups', data.project_follow_ups)}
      {data.skill_gaps.length > 0 && (
        <Card className="space-y-3 p-5">
          <h3 className="text-sm font-semibold text-[var(--muted-foreground)]">
            Skill gaps to prepare
          </h3>
          <ul className="space-y-3">
            {data.skill_gaps.map((g, i) => (
              <li key={i} className="space-y-0.5">
                <p className="text-sm font-medium text-[var(--foreground)]">{g.skill}</p>
                <p className="text-xs text-[var(--muted-foreground)]">{g.why_it_matters}</p>
                <p className="text-xs text-[var(--at-ai)]">{g.preparation_suggestion}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {data.talking_points.length > 0 && (
        <Card className="space-y-2 p-5">
          <h3 className="text-sm font-semibold text-[var(--muted-foreground)]">Talking points</h3>
          <ul className="ml-4 list-disc space-y-1 text-sm text-[var(--foreground)]">
            {data.talking_points.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
