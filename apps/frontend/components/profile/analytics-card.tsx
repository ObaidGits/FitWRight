'use client';

/**
 * Analytics card — a compact, privacy-respecting usage summary.
 *
 * Reads the per-user analytics snapshot (event-derived counters + completeness
 * gauge). Non-PII; purely informational. Hidden while loading/empty to avoid a
 * noisy zero-state.
 */
import * as React from 'react';
import BarChart3 from 'lucide-react/dist/esm/icons/bar-chart-3';

import { Card } from '@/components/atelier/card';
import { useProfileAnalytics } from '@/features/profile/hooks';

const LABELS: Record<string, string> = {
  resumes_generated: 'Resumes generated',
  imports: 'Imports',
  syncs: 'Syncs',
  ai_suggestions: 'AI assists',
  exports: 'Exports',
  public_views: 'Public views',
  shares: 'Shares',
};

export function AnalyticsCard() {
  const { data } = useProfileAnalytics();
  if (!data) return null;

  const rows = Object.entries(LABELS)
    .map(([key, label]) => ({ label, value: data.counters[key] ?? 0 }))
    .filter((r) => r.value > 0);

  if (rows.length === 0) return null;

  return (
    <Card className="p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
        <BarChart3 className="h-4 w-4" /> Activity
      </h2>
      <dl className="space-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between text-sm">
            <dt className="text-[var(--muted-foreground)]">{r.label}</dt>
            <dd className="font-medium tabular-nums">{r.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
