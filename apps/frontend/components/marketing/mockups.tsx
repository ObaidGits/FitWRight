/**
 * Landing-page product mockups (homepage enhancement).
 *
 * Realistic FitWright UI rendered natively with Atelier tokens + inline SVG -
 * no external images, no network requests, and truthful (these mirror the real
 * product surfaces). Pure presentational; safe in server or client trees.
 */
import * as React from 'react';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';
import Mail from 'lucide-react/dist/esm/icons/mail';
import CircleCheck from 'lucide-react/dist/esm/icons/circle-check';
import { cn } from '@/lib/utils';

/* --------------------------------- ATS ring -------------------------------- */
export function AtsRing({ score = 92, size = 92 }: { score?: number; size?: number }) {
  const stroke = 8;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.min(100, Math.max(0, score)) / 100);
  const tone =
    score >= 75 ? 'var(--at-success)' : score >= 50 ? 'var(--at-warning)' : 'var(--destructive)';
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--secondary)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          className="at-ring-track"
          style={{ ['--ring-circumference' as string]: `${c}` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-semibold text-[var(--foreground)]">{score}</span>
        <span className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
          Match
        </span>
      </div>
    </div>
  );
}

function Chip({ label, tone }: { label: string; tone: 'match' | 'miss' }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium',
        tone === 'match'
          ? 'bg-[var(--at-success)]/15 text-[var(--at-success)]'
          : 'bg-[var(--at-warning)]/15 text-[var(--at-warning)]'
      )}
    >
      {tone === 'match' && <CircleCheck className="h-3 w-3" />}
      {label}
    </span>
  );
}

function Bar({ label, pct }: { label: string; pct: number }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px] text-[var(--muted-foreground)]">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--secondary)]">
        <div className="h-full rounded-full bg-[var(--primary)]" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ------------------------------- Hero centerpiece -------------------------- */
export function TailorMock() {
  return (
    <div className="w-full max-w-md rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] p-5 shadow-[var(--shadow-at-e3)]">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-[var(--foreground)]">Senior Backend Engineer</p>
          <p className="text-xs text-[var(--muted-foreground)]">Tailored - just now</p>
        </div>
        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--at-ai-surface)] px-2 py-1 text-[10px] font-medium text-[var(--at-ai)]">
          <Sparkles className="h-3 w-3" /> AI
        </span>
      </div>

      <div className="flex items-center gap-4">
        <AtsRing score={92} />
        <div className="flex-1 space-y-2">
          <Bar label="Keywords" pct={94} />
          <Bar label="Skills" pct={88} />
          <Bar label="Sections" pct={100} />
        </div>
      </div>

      <div className="mt-4 space-y-2">
        <p className="text-[10px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
          Keyword coverage
        </p>
        <div className="flex flex-wrap gap-1.5">
          <Chip tone="match" label="FastAPI" />
          <Chip tone="match" label="PostgreSQL" />
          <Chip tone="match" label="Docker" />
          <Chip tone="match" label="CI/CD" />
          <Chip tone="miss" label="Kubernetes" />
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2 rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] p-2.5">
        <ShieldCheck className="h-4 w-4 shrink-0 text-[var(--at-success)]" />
        <span className="text-[11px] text-[var(--muted-foreground)]">
          Grounded in your real experience - nothing invented.
        </span>
      </div>
    </div>
  );
}

/* -------------------------- Floating AI suggestion ------------------------- */
export function AiSuggestionCard({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'at-glass w-56 rounded-[var(--radius-at-lg)] border border-[var(--border)] p-3 shadow-[var(--shadow-at-e2)]',
        className
      )}
    >
      <div className="mb-1.5 flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5 text-[var(--at-ai)]" />
        <span className="text-[11px] font-medium text-[var(--at-ai)]">Suggestion</span>
      </div>
      <p className="text-[11px] leading-relaxed text-[var(--foreground)]">
        Quantify impact: "Cut API latency <span className="font-semibold">40%</span> by adding Redis
        caching."
      </p>
    </div>
  );
}

/* ------------------------- Cover-letter mini preview ----------------------- */
export function CoverLetterMock({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'at-glass w-52 rounded-[var(--radius-at-lg)] border border-[var(--border)] p-3 shadow-[var(--shadow-at-e2)]',
        className
      )}
    >
      <div className="mb-2 flex items-center gap-1.5">
        <Mail className="h-3.5 w-3.5 text-[var(--primary)]" />
        <span className="text-[11px] font-medium text-[var(--foreground)]">Cover letter</span>
      </div>
      <div className="space-y-1.5">
        {[92, 80, 96, 72, 88].map((w, i) => (
          <div
            key={i}
            className="h-1.5 rounded-full bg-[var(--secondary)]"
            style={{ width: `${w}%` }}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------ Analysis panel ----------------------------- */
export function AnalysisMock() {
  return (
    <div className="rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] p-5 shadow-[var(--shadow-at-e2)]">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm font-semibold">Job analysis</p>
        <span className="text-[11px] text-[var(--muted-foreground)]">Backend Engineer</span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Matched
          </p>
          <div className="flex flex-wrap gap-1.5">
            {['Python', 'FastAPI', 'REST APIs', 'PostgreSQL', 'Docker'].map((k) => (
              <Chip key={k} tone="match" label={k} />
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Missing
          </p>
          <div className="flex flex-wrap gap-1.5">
            {['Kubernetes', 'Terraform', 'gRPC'].map((k) => (
              <Chip key={k} tone="miss" label={k} />
            ))}
          </div>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-3">
        <Bar label="Keyword" pct={94} />
        <Bar label="Skills" pct={81} />
        <Bar label="Sections" pct={100} />
      </div>
    </div>
  );
}

/* ------------------------------- Kanban mock ------------------------------- */
export function KanbanMock() {
  const columns: { title: string; cards: { role: string; co: string }[] }[] = [
    { title: 'Applied', cards: [{ role: 'Backend Engineer', co: 'Nimbus' }] },
    {
      title: 'Interviewing',
      cards: [
        { role: 'Platform Engineer', co: 'Aster' },
        { role: 'API Developer', co: 'Loop' },
      ],
    },
    { title: 'Offer', cards: [{ role: 'Senior SWE', co: 'Vertex' }] },
  ];
  return (
    <div className="rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--card)] p-4 shadow-[var(--shadow-at-e2)]">
      <div className="grid grid-cols-3 gap-3">
        {columns.map((col) => (
          <div key={col.title} className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-[var(--foreground)]">{col.title}</span>
              <span className="rounded-full bg-[var(--secondary)] px-1.5 text-[10px] text-[var(--muted-foreground)]">
                {col.cards.length}
              </span>
            </div>
            <div className="space-y-2 rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] p-2">
              {col.cards.map((c) => (
                <div
                  key={c.role}
                  className="rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-[var(--card)] p-2"
                >
                  <p className="truncate text-[11px] font-medium text-[var(--foreground)]">
                    {c.role}
                  </p>
                  <p className="truncate text-[10px] text-[var(--muted-foreground)]">{c.co}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------- Resume doc skeleton -------------------------- */
export function ResumeDocMock({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'aspect-[1/1.3] w-40 overflow-hidden rounded-[var(--radius-at-md)] border border-[var(--border)] bg-white p-3 shadow-[var(--shadow-at-e2)]',
        className
      )}
    >
      <div className="mb-2 h-2.5 w-2/3 rounded bg-neutral-800" />
      <div className="mb-3 h-1.5 w-1/2 rounded bg-neutral-300" />
      {[100, 92, 96, 70].map((w, i) => (
        <div
          key={`a${i}`}
          className="mb-1.5 h-1.5 rounded bg-neutral-200"
          style={{ width: `${w}%` }}
        />
      ))}
      <div className="my-2 h-1.5 w-1/3 rounded bg-[var(--primary)]/60" />
      {[96, 88, 100, 64].map((w, i) => (
        <div
          key={`b${i}`}
          className="mb-1.5 h-1.5 rounded bg-neutral-200"
          style={{ width: `${w}%` }}
        />
      ))}
    </div>
  );
}
