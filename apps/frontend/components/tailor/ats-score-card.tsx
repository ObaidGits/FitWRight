'use client';

import type { ATSScore } from '@/components/common/resume_previewer_context';

interface ATSScoreCardProps {
  atsScore: ATSScore;
}

const SUB_SCORE_LABELS: Record<string, string> = {
  keyword_match: 'Keyword Match',
  skills_coverage: 'Skills Coverage',
  section_completeness: 'Section Completeness',
};

function scoreColor(value: number): string {
  if (value >= 80) return 'text-[var(--at-success)]';
  if (value >= 60) return 'text-[var(--at-warning)]';
  return 'text-[var(--destructive)]';
}

function barColor(value: number): string {
  if (value >= 80) return 'bg-[var(--at-success)]';
  if (value >= 60) return 'bg-[var(--at-warning)]';
  return 'bg-[var(--destructive)]';
}

function clampWidth(value: number): number {
  return Number.isFinite(value) ? Math.min(Math.max(value, 0), 100) : 0;
}

function SubScoreRow({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm text-[var(--muted-foreground)]">{label}</span>
        <span className={`text-sm font-semibold tabular-nums ${scoreColor(value)}`}>
          {Number.isFinite(value) ? value.toFixed(1) : '—'}%
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-[var(--secondary)]">
        <div
          className={`h-1.5 rounded-full transition-all duration-500 ${barColor(value)}`}
          style={{ width: `${clampWidth(value)}%` }}
        />
      </div>
    </div>
  );
}

export function ATSScoreCard({ atsScore }: ATSScoreCardProps) {
  const { overall_score, sub_scores, missing_keywords, injectable_keywords, recommendations } =
    atsScore;

  return (
    <div className="space-y-5 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-5 shadow-[var(--shadow-at-e1)]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--foreground)]">ATS Score Breakdown</h3>
        <div className="flex items-end gap-1">
          <span className={`text-3xl font-bold tabular-nums ${scoreColor(overall_score)}`}>
            {overall_score.toFixed(1)}
          </span>
          <span className="mb-0.5 text-sm text-[var(--muted-foreground)]">/100</span>
        </div>
      </div>

      {/* Overall bar */}
      <div className="h-2 w-full rounded-full bg-[var(--secondary)]">
        <div
          className={`h-2 rounded-full transition-all duration-500 ${barColor(overall_score)}`}
          style={{ width: `${clampWidth(overall_score)}%` }}
        />
      </div>

      {/* Sub-score breakdown */}
      <div className="space-y-3">
        {Object.entries(sub_scores).map(([key, value]) => (
          <SubScoreRow key={key} label={SUB_SCORE_LABELS[key] ?? key} value={value} />
        ))}
      </div>

      {/* Missing keywords */}
      {missing_keywords.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            Missing Keywords
          </p>
          <div className="flex flex-wrap gap-1.5">
            {missing_keywords.map((kw, i) => (
              <span
                key={`missing-${i}-${kw}`}
                className="rounded-[var(--radius-at-sm)] border border-[var(--destructive)]/40 bg-[var(--destructive)]/10 px-2 py-0.5 text-xs text-[var(--destructive)]"
              >
                {kw}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Injectable keywords */}
      {injectable_keywords.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            Safe to Add (in your master resume)
          </p>
          <div className="flex flex-wrap gap-1.5">
            {injectable_keywords.map((kw, i) => (
              <span
                key={`injectable-${i}-${kw}`}
                className="rounded-[var(--radius-at-sm)] border border-[var(--primary)]/40 bg-[var(--primary)]/10 px-2 py-0.5 text-xs text-[var(--primary)]"
              >
                {kw}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            Recommendations
          </p>
          <ul className="space-y-1.5">
            {recommendations.map((tip, i) => (
              <li
                key={`rec-${i}-${tip.slice(0, 30)}`}
                className="flex gap-2 text-sm text-[var(--muted-foreground)]"
              >
                <span className="mt-0.5 shrink-0 text-[var(--primary)]">•</span>
                <span>{tip}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
