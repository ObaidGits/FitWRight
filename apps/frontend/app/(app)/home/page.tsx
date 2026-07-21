'use client';

/**
 * Home - lightweight launchpad (Task 6 / Req 6, 7).
 * Priority order: primary action -> continue where you left off -> needs
 * attention -> recent. No dense dashboard; links out to destinations.
 */
import * as React from 'react';
import Link from 'next/link';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Upload from 'lucide-react/dist/esm/icons/upload';
import Wand from 'lucide-react/dist/esm/icons/wand-sparkles';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Layers from 'lucide-react/dist/esm/icons/layers';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';
import Key from 'lucide-react/dist/esm/icons/key-round';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { EmptyState, LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import {
  useResumes,
  useApplications,
  useSetupStatus,
  shouldShowFirstRun,
  flattenApplications,
} from '@/features/home/hooks';
import { useAgenda, flattenAgenda } from '@/features/agenda/hooks';
import CalendarClock from 'lucide-react/dist/esm/icons/calendar-clock';
import CircleCheck from 'lucide-react/dist/esm/icons/circle-check';
import Circle from 'lucide-react/dist/esm/icons/circle';

/**
 * Onboarding checklist that reflects live setup state: add an AI key -> add a
 * resume -> tailor to a job. Completed steps show a filled check; the current
 * step is emphasized. Replaces the previous first-run dead-end.
 */
function FirstRunChecklist({
  aiUnconfigured,
  hasResume,
}: {
  aiUnconfigured: boolean;
  hasResume: boolean;
}) {
  const steps = [
    { label: 'Add an AI provider key', done: !aiUnconfigured },
    { label: 'Add your first resume', done: hasResume },
    { label: 'Tailor it to a job', done: false },
  ];
  // Emphasize the first incomplete step so a new user always knows what's next
  // (goal-gradient / recognition-over-recall).
  const currentStep = steps.findIndex((s) => !s.done);
  return (
    <Card className="p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
        Get set up
      </p>
      <ol className="space-y-2">
        {steps.map((s, i) => {
          const isCurrent = i === currentStep;
          return (
            <li key={s.label} className="flex items-center gap-2.5 text-sm">
              {s.done ? (
                <CircleCheck className="h-4 w-4 shrink-0 text-[var(--at-success)]" />
              ) : (
                <Circle
                  className={`h-4 w-4 shrink-0 ${isCurrent ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]'}`}
                />
              )}
              <span
                className={
                  s.done
                    ? 'text-[var(--muted-foreground)] line-through'
                    : isCurrent
                      ? 'font-medium text-[var(--foreground)]'
                      : 'text-[var(--muted-foreground)]'
                }
              >
                {s.label}
              </span>
              {isCurrent && (
                <span className="ml-auto text-[11px] font-medium uppercase tracking-wide text-[var(--primary)]">
                  Next
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function statusBadge(status: string) {
  if (status === 'ready') return <Badge variant="success">Ready</Badge>;
  if (status === 'failed') return <Badge variant="danger">Failed</Badge>;
  if (status === 'processing' || status === 'pending')
    return <Badge variant="warning">Processing</Badge>;
  return <Badge>{status}</Badge>;
}

export default function HomePage() {
  const resumesQuery = useResumes();
  const appsQuery = useApplications();
  const setupQuery = useSetupStatus();
  const agendaQuery = useAgenda();
  const upcoming = flattenAgenda(agendaQuery.data?.pages).slice(0, 3);

  const resumes = resumesQuery.data ?? [];
  const apps = flattenApplications(appsQuery.data);
  const setup = setupQuery.data;
  const aiUnconfigured = setup ? !setup.llm_configured : false;
  const failed = resumes.filter((r) => r.processing_status === 'failed');
  const recent = [...resumes]
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
    .slice(0, 4);
  const mostRecent = recent[0];

  if (resumesQuery.isLoading || setupQuery.isLoading) {
    return (
      <div className="space-y-6">
        <div className="h-9 w-56 animate-pulse rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)]" />
        <LoadingSkeleton rows={3} />
      </div>
    );
  }

  if (resumesQuery.isError || setupQuery.isError) {
    return (
      <ErrorState
        description="Could not load your workspace."
        onRetry={() => {
          void resumesQuery.refetch();
          void setupQuery.refetch();
        }}
      />
    );
  }

  // First-run/setup is determined by the backend's persisted user-scoped facts,
  // never by the visible resume list. The list intentionally excludes masters,
  // which previously sent users whose only resume was their master back through
  // onboarding. Provider health is also excluded: an outage is not first-time setup.
  if (shouldShowFirstRun(setup)) {
    return (
      <div className="mx-auto max-w-2xl space-y-6 py-6">
        <div className="space-y-1 text-center">
          <h1 className="text-2xl font-semibold">Welcome to FitWright</h1>
          <p className="text-[var(--muted-foreground)]">
            {setup.has_master_resume
              ? 'Your resume is ready. Finish connecting an AI provider to start tailoring.'
              : 'Start with your master resume - upload one or build it with the wizard.'}
          </p>
        </div>

        {/* Getting-started checklist - turns the dead-end into a guided path.
            Step 1 (AI key) is what a brand-new user is otherwise never told. */}
        <FirstRunChecklist aiUnconfigured={aiUnconfigured} hasResume={setup.has_master_resume} />

        {/* First-run AI-key guidance: without a key, upload parsing and the
            wizard will fail silently. Tell the user up front. */}
        {aiUnconfigured && (
          <Card className="flex items-start gap-3 border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-4">
            <Key className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-warning)]" />
            <div className="flex-1">
              <p className="text-sm font-medium">Add an AI provider key first</p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Parsing, the wizard, and tailoring all need an AI key. Add one in settings, then
                come back here.
              </p>
            </div>
            <Button asChild size="sm" variant="outline">
              <Link href="/settings">Open settings</Link>
            </Button>
          </Card>
        )}

        {!setup.has_master_resume && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Card className="p-6">
              <Upload className="mb-3 h-6 w-6 text-[var(--primary)]" />
              <h2 className="mb-1 text-base font-semibold">Upload a resume</h2>
              <p className="mb-4 text-sm text-[var(--muted-foreground)]">
                Start from a PDF or DOCX and we&apos;ll parse it into your master profile.
              </p>
              <Button asChild className="w-full">
                <Link href="/import">Upload resume</Link>
              </Button>
            </Card>
            <Card className="p-6">
              <Wand className="mb-3 h-6 w-6 text-[var(--at-ai)]" />
              <h2 className="mb-1 text-base font-semibold">Build with the wizard</h2>
              <p className="mb-4 text-sm text-[var(--muted-foreground)]">
                Answer a few questions and let AI assemble a strong first draft.
              </p>
              {aiUnconfigured ? (
                <Button variant="outline" className="w-full" disabled title="Add an AI key first">
                  Add an AI key to use the wizard
                </Button>
              ) : (
                <Button asChild variant="outline" className="w-full">
                  <Link href="/wizard">Start wizard</Link>
                </Button>
              )}
            </Card>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header + primary action */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Welcome back</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Pick up where you left off, or tailor for a new role.
          </p>
        </div>
        <Button asChild size="lg">
          <Link href="/tailor">
            <Sparkles className="h-4 w-4" /> Tailor to a job
          </Link>
        </Button>
      </div>

      {/* Continue where you left off */}
      {mostRecent && (
        <Card className="flex items-center justify-between gap-4 p-5">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              Continue where you left off
            </p>
            <p className="mt-1 truncate text-base font-medium">
              {mostRecent.title || mostRecent.filename || 'Untitled resume'}
            </p>
          </div>
          <Button asChild variant="outline">
            <Link href={`/resumes/${mostRecent.resume_id}`}>
              Open <ArrowRight className="h-4 w-4" />
            </Link>
          </Button>
        </Card>
      )}

      {/* Needs attention */}
      {(aiUnconfigured || failed.length > 0) && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Needs attention</h2>
          <div className="space-y-2">
            {aiUnconfigured && (
              <Card className="flex items-center gap-3 p-4">
                <Key className="h-5 w-5 text-[var(--at-warning)]" />
                <div className="flex-1">
                  <p className="text-sm font-medium">Configure your AI provider</p>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Add an API key to enable tailoring.
                  </p>
                </div>
                <Button asChild size="sm" variant="outline">
                  <Link href="/settings">Open settings</Link>
                </Button>
              </Card>
            )}
            {failed.map((r) => (
              <Card key={r.resume_id} className="flex items-center gap-3 p-4">
                <TriangleAlert className="h-5 w-5 text-[var(--destructive)]" />
                <div className="flex-1 min-w-0">
                  <p className="truncate text-sm font-medium">
                    {r.title || r.filename || 'Resume'} failed to process
                  </p>
                </div>
                <Button asChild size="sm" variant="outline">
                  <Link href={`/resumes/${r.resume_id}`}>Review</Link>
                </Button>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Coming up (agenda snapshot) */}
      {upcoming.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Coming up</h2>
            <Link href="/agenda" className="text-xs text-[var(--primary)] hover:underline">
              View agenda
            </Link>
          </div>
          <div className="space-y-2">
            {upcoming.map((item) => (
              <Link key={`${item.kind}-${item.id}`} href={`/applications/${item.application_id}`}>
                <Card className="flex items-center gap-3 p-3.5 transition-shadow hover:shadow-[var(--shadow-at-e2)]">
                  <CalendarClock className="h-4 w-4 shrink-0 text-[var(--muted-foreground)]" />
                  <span className="min-w-0 flex-1 truncate text-sm">{item.title}</span>
                  <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                    {new Date(item.when).toLocaleDateString(undefined, {
                      month: 'short',
                      day: 'numeric',
                    })}
                  </span>
                  <Badge variant={item.kind === 'interview' ? 'primary' : 'neutral'}>
                    {item.kind}
                  </Badge>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* Recent + pipeline snapshot link */}
      <div className="grid gap-6 md:grid-cols-2">
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Recent resumes</h2>
            <Link href="/resumes" className="text-xs text-[var(--primary)] hover:underline">
              View all
            </Link>
          </div>
          <div className="space-y-3">
            {recent.map((r) => (
              <Link
                key={r.resume_id}
                href={`/resumes/${r.resume_id}`}
                className="block mb-3 last:mb-0"
              >
                <Card className="flex items-center justify-between gap-3 p-3.5 transition-shadow hover:shadow-[var(--shadow-at-e2)]">
                  <span className="flex min-w-0 items-center gap-2">
                    <FileText className="h-4 w-4 shrink-0 text-[var(--muted-foreground)]" />
                    <span className="truncate text-sm">
                      {r.title || r.filename || 'Untitled resume'}
                    </span>
                    {r.is_master && <Badge variant="primary">Master</Badge>}
                  </span>
                  {statusBadge(r.processing_status)}
                </Card>
              </Link>
            ))}
          </div>
        </section>

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Applications</h2>
            <Link href="/applications" className="text-xs text-[var(--primary)] hover:underline">
              Open pipeline
            </Link>
          </div>
          {appsQuery.isLoading ? (
            <LoadingSkeleton rows={1} />
          ) : apps.length === 0 ? (
            <EmptyState
              icon={Layers}
              title="No applications yet"
              description="Tailor a resume to a job to start tracking."
            />
          ) : (
            <Card className="flex items-center justify-between p-5">
              <div>
                <p className="text-2xl font-semibold">{apps.length}</p>
                <p className="text-sm text-[var(--muted-foreground)]">tracked applications</p>
              </div>
              <Button asChild variant="outline">
                <Link href="/applications">
                  <Layers className="h-4 w-4" /> View pipeline
                </Link>
              </Button>
            </Card>
          )}
        </section>
      </div>
    </div>
  );
}
