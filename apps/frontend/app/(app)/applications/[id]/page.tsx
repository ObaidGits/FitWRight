'use client';

/**
 * Application Workspace (Task 9.2-9.6 / Req 12,13,14,17). Overview + resource
 * sections (Cover Letter · Interview Prep · Outreach). Per-job deliverables
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

type Deliverable = { coverLetter: string | null; outreach: string | null; interviewPrep: unknown };

export default function ApplicationWorkspacePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const { data: app, isLoading, isError, refetch } = useApplicationDetail(id);
  const move = useMoveApplication();
  const notesMut = useUpdateApplicationNotes();
  const { toast } = useToast();
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

  // Task 9.6 — duplicate this application (same resume + JD) into a fresh card.
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
      toast({ title: 'Application duplicated', variant: 'success' });
      router.push(`/applications/${created.application_id}`);
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Could not duplicate', variant: 'error' });
    } finally {
      setDuplicating(false);
    }
  }

  // Task 9.6 — reuse the underlying resume to tailor for another job.
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
          <TabsTrigger value="cover">Cover Letter</TabsTrigger>
          <TabsTrigger value="prep">Interview Prep</TabsTrigger>
          <TabsTrigger value="outreach">Outreach</TabsTrigger>
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
                placeholder="Add notes…"
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

        <TabsContent value="cover">
          <DeliverablePanel
            label="cover letter"
            value={deliverable.coverLetter}
            busy={busy === 'cover'}
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

        <TabsContent value="prep">
          {deliverable.interviewPrep ? (
            <Card className="p-5">
              <pre className="max-h-[28rem] overflow-y-auto whitespace-pre-wrap text-sm">
                {JSON.stringify(deliverable.interviewPrep, null, 2)}
              </pre>
            </Card>
          ) : (
            <EmptyState
              icon={Sparkles}
              title="No interview prep yet"
              description="Generate resume-grounded questions and talking points for this role."
              action={
                <Button loading={busy === 'prep'} onClick={() => run('prep')}>
                  <Sparkles className="h-4 w-4" /> Generate interview prep
                </Button>
              }
            />
          )}
        </TabsContent>

        <TabsContent value="outreach">
          <DeliverablePanel
            label="outreach message"
            value={deliverable.outreach}
            busy={busy === 'outreach'}
            onGenerate={() => run('outreach')}
            onCopy={() =>
              deliverable.outreach && navigator.clipboard.writeText(deliverable.outreach)
            }
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function DeliverablePanel({
  label,
  value,
  busy,
  onGenerate,
  onCopy,
  exportSlot,
}: {
  label: string;
  value: string | null;
  busy: boolean;
  onGenerate: () => void;
  onCopy: () => void;
  exportSlot?: React.ReactNode;
}) {
  if (!value) {
    return (
      <EmptyState
        icon={Sparkles}
        title={`No ${label} yet`}
        description={`Generate a tailored ${label} grounded in your resume and the job.`}
        action={
          <Button loading={busy} onClick={onGenerate}>
            <Sparkles className="h-4 w-4" /> Generate {label}
          </Button>
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
          <Button size="sm" variant="outline" loading={busy} onClick={onGenerate}>
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
