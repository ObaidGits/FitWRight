import type { ReactNode } from 'react';
import Bot from 'lucide-react/dist/esm/icons/bot';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Gauge from 'lucide-react/dist/esm/icons/gauge';
import ListChecks from 'lucide-react/dist/esm/icons/list-checks';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';

import { Button } from '@/components/atelier/button';
import { KanbanMock, TailorMock } from '@/components/marketing/mockups';
import { APP_ENTRY_HREF } from '@/lib/config/auth';

const PROOF_POINTS = [
  'Real product screens, not generic marketing art',
  'Tailoring, ATS analysis, and tracking in one flow',
  'Built to look calm, high-trust, and professional',
];

function Frame({
  title,
  subtitle,
  children,
  className = '',
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`min-w-0 overflow-hidden rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-at-e2)] ${className}`}
    >
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--at-surface-2)] px-3 py-2.5 md:px-4">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="min-w-0 text-right">
          <p className="truncate text-xs font-medium text-[var(--foreground)]">{title}</p>
          <p className="truncate text-[10px] text-[var(--muted-foreground)]">{subtitle}</p>
        </div>
      </div>
      <div className="min-w-0 p-3 md:p-4">{children}</div>
    </div>
  );
}

function BeforeAfter() {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-4">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
          Before
        </p>
        <p className="mt-2 text-sm text-[var(--foreground)]">Generic resume with no role fit</p>
      </div>
      <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
          After
        </p>
        <p className="mt-2 text-sm text-[var(--foreground)]">Tailored resume with ATS guidance</p>
      </div>
    </div>
  );
}

function QuickSteps() {
  const steps = [
    {
      title: 'Match',
      body: 'See fit and gaps before you touch the resume.',
    },
    {
      title: 'Edit',
      body: 'Rewrite only what needs changing, with reviewable AI.',
    },
    {
      title: 'Track',
      body: 'Keep each application and version in one place.',
    },
  ];

  return (
    <div className="grid gap-3 md:grid-cols-3">
      {steps.map((step) => (
        <div
          key={step.title}
          className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4 shadow-[var(--shadow-at-e1)]"
        >
          <p className="text-[11px] font-semibold uppercase tracking-wider text-[var(--at-ai)]">
            {step.title}
          </p>
          <p className="mt-2 text-sm text-[var(--foreground)]">{step.body}</p>
        </div>
      ))}
    </div>
  );
}

function CardNote({
  icon,
  title,
  body,
  points,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  points: string[];
}) {
  return (
    <div className="mb-3 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3">
      <div className="flex items-start gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--card)] text-[var(--at-ai)] shadow-[var(--shadow-at-e1)]">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-[var(--at-ai)]">
            {title}
          </p>
          <p className="mt-1 text-sm leading-snug text-[var(--foreground)]">{body}</p>
        </div>
      </div>

      <div className="mt-2.5 grid grid-cols-3 gap-1.5">
        {points.map((point, index) => (
          <div
            key={point}
            className="rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-[var(--card)] px-2 py-1"
          >
            <div className="flex items-center gap-1.5">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  index === 0
                    ? 'bg-[var(--primary)]'
                    : index === 1
                      ? 'bg-[var(--at-ai)]'
                      : 'bg-[var(--at-success)]'
                }`}
              />
              <p className="truncate text-[10px] font-medium text-[var(--muted-foreground)]">
                {point}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function HomepageProof() {
  return (
    <section
      id="proof"
      className="border-y border-[var(--border)] bg-[linear-gradient(180deg,var(--background)_0%,var(--at-surface-2)_30%,var(--background)_100%)]"
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-16 md:px-8 md:py-18">
        <div className="relative overflow-hidden rounded-[var(--radius-at-2xl)] border border-[var(--border)] bg-[radial-gradient(circle_at_top_left,var(--at-ai)_0%,transparent_26%),linear-gradient(180deg,var(--card)_0%,var(--at-surface-2)_100%)] px-4 py-6 shadow-[var(--shadow-at-e3)] sm:px-5 md:px-8 md:py-8">
          <div aria-hidden className="pointer-events-none absolute inset-0">
            <div className="absolute left-[-3rem] top-[-3rem] h-40 w-40 rounded-full bg-[var(--at-ai)]/10 blur-3xl" />
            <div className="absolute right-[-2rem] top-8 h-44 w-44 rounded-full bg-[var(--primary)]/10 blur-3xl" />
          </div>

          <div className="relative grid gap-8 lg:grid-cols-[1.02fr_0.98fr] lg:items-start">
            <div className="max-w-xl">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--at-ai)]/20 bg-[var(--at-ai)]/10 px-3 py-1 text-xs font-medium text-[var(--at-ai)]">
                <Sparkles className="h-3.5 w-3.5" /> See the product
              </span>
              <h2 className="mt-4 text-2xl font-semibold tracking-tight md:text-4xl">
                Looks like a real product because it is one
              </h2>
              <p className="mt-3 text-sm text-[var(--muted-foreground)] md:mt-4 md:text-base">
                Put product proof before more copy. Show the tailor, ATS score, and tracker right
                away.
              </p>

              <ul className="mt-6 space-y-3">
                {PROOF_POINTS.map((point) => (
                  <li key={point} className="flex items-start gap-3 text-sm">
                    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--primary)]/10 text-[var(--primary)]">
                      <ListChecks className="h-3.5 w-3.5" />
                    </span>
                    <span className="text-[var(--foreground)]">{point}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-6">
                <BeforeAfter />
              </div>

              <div className="mt-6">
                <QuickSteps />
              </div>

              <div className="mt-6 flex flex-wrap gap-3">
                <Button asChild>
                  <a href={APP_ENTRY_HREF}>
                    <Sparkles className="h-4 w-4" /> Try it now <ArrowRight className="h-4 w-4" />
                  </a>
                </Button>
                <Button asChild variant="outline">
                  <a href="#features">
                    <Gauge className="h-4 w-4" /> See features
                  </a>
                </Button>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Frame title="Tailor workspace" subtitle="Review before export">
                <CardNote
                  icon={<Sparkles className="h-4 w-4" />}
                  title="Job automation"
                  body="Auto-tailor resume, draft cover letter, and move each role into a tracked flow."
                  points={['Auto-tailor', 'Draft cover letter', 'Track follow-up']}
                />
                <div className="mx-auto w-full max-w-none">
                  <TailorMock />
                </div>
              </Frame>

              <Frame title="Application tracker" subtitle="All roles in one board">
                <CardNote
                  icon={<ListChecks className="h-4 w-4" />}
                  title="Job automation"
                  body="Every tailored version drops into one board so follow-ups never slip."
                  points={['One board', 'Version history', 'Follow-ups']}
                />
                <KanbanMock />
                <div className="pointer-events-none flex items-center justify-center py-5 text-[var(--at-ai)]/25 md:py-6">
                  <div className="flex h-32 w-32 items-center justify-center rounded-[var(--radius-at-sm)] border border-[var(--at-ai)]/15 bg-[var(--at-ai)]/8 shadow-[var(--shadow-at-e1)]">
                    <Bot className="h-16 w-16" />
                  </div>
                </div>
              </Frame>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
