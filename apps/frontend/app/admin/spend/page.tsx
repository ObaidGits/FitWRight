'use client';

/**
 * Operator spend (spec: ai-provider-admin, Phase 2/5).
 *
 * Answers "what is this costing me, and am I making anything on it?" - which is the
 * question that decides whether hosted AI is a business or a hobby.
 *
 * Two honesty rules the layout enforces:
 *
 * 1. Margin is shown ONLY when the rate table covered every call. A single unpriced
 *    call means provider cost is understated, so a margin figure derived from it
 *    would read as authoritative while being wrong. When that happens the page says
 *    so and names the number of unpriced calls instead of quietly rounding.
 * 2. Credits and money are never mixed in one column. Credits are the user's unit and
 *    deliberately stable; micros are real provider money. Adding them would produce a
 *    figure that means nothing.
 */
import * as React from 'react';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/atelier/table';
import { LoadingSkeleton, EmptyState, ErrorState } from '@/components/atelier/states';
import { useAiSpend } from '@/features/admin/hooks';
import type { SpendBucket } from '@/lib/api/admin';
import Coins from 'lucide-react/dist/esm/icons/coins';

/** Micros are millionths of a currency unit. Shown to cents, which is the smallest
 *  unit an operator reconciles against an invoice. */
function money(micros: number): string {
  return (micros / 1_000_000).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function AdminSpendPage() {
  const [days, setDays] = React.useState(30);
  const { data, isLoading, isError, refetch } = useAiSpend(days);

  const complete = (data?.unpriced_calls ?? 0) === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">AI spend</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            What your users consumed, and what the providers charged you for it.
          </p>
        </div>
        <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <SelectTrigger className="w-40" aria-label="Window">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">Last 7 days</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
            <SelectItem value="90">Last 90 days</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={4} />
      ) : isError || !data ? (
        <ErrorState description="Could not load spend." onRetry={() => refetch()} />
      ) : data.calls === 0 ? (
        <EmptyState
          icon={Coins}
          title="No AI usage yet"
          description="Once your users start generating, cost and credit totals appear here."
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="AI calls" value={data.calls.toLocaleString()} />
            <Metric label="Provider cost" value={money(data.provider_cost_micros)} />
            <Metric label="Credits charged" value={data.credits_charged.toLocaleString()} />
            <Metric
              label="Failed calls"
              value={data.failed_calls.toLocaleString()}
              hint="Not charged to users"
            />
          </div>

          {/* The honesty rule: an incomplete rate table must not masquerade as a
              complete cost picture. */}
          {!complete && (
            <Card className="border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-4">
              <p className="text-sm font-medium">
                {data.unpriced_calls.toLocaleString()} calls have no price
              </p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Those models are missing from the rate table, so the cost above is lower than what
                you were actually billed. Add them via AI_RATE_OVERRIDES to make this figure
                trustworthy.
              </p>
            </Card>
          )}

          <Card className="p-4">
            <h2 className="mb-3 text-sm font-semibold">By feature</h2>
            <BucketTable rows={data.by_feature} keyOf={(r) => r.feature ?? '-'} label="Feature" />
          </Card>

          <Card className="p-4">
            <h2 className="mb-3 text-sm font-semibold">By channel</h2>
            <BucketTable
              rows={data.by_channel}
              keyOf={(r) => r.channel_id ?? 'no channel (own key or fallback)'}
              label="Channel"
              showCredits={false}
            />
          </Card>

          <Card className="p-4">
            <h2 className="mb-3 text-sm font-semibold">Heaviest users</h2>
            <p className="mb-3 text-xs text-[var(--muted-foreground)]">
              Ordered by cost to you. A user far above the rest is worth understanding before they
              are worth limiting.
            </p>
            <BucketTable rows={data.top_users} keyOf={(r) => r.user_id ?? '-'} label="User" />
          </Card>
        </>
      )}
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-2xl font-semibold">{value}</p>
      <p className="text-xs text-[var(--muted-foreground)]">{label}</p>
      {hint && <p className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">{hint}</p>}
    </Card>
  );
}

function BucketTable({
  rows,
  keyOf,
  label,
  showCredits = true,
}: {
  rows: SpendBucket[];
  keyOf: (r: SpendBucket) => string;
  label: string;
  showCredits?: boolean;
}) {
  if (rows.length === 0) {
    return <p className="text-xs text-[var(--muted-foreground)]">Nothing in this window.</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{label}</TableHead>
          <TableHead className="text-right">Calls</TableHead>
          {showCredits && <TableHead className="text-right">Credits</TableHead>}
          <TableHead className="text-right">Cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={keyOf(r)}>
            <TableCell className="max-w-[18rem] truncate font-medium">
              {keyOf(r)}
              {r.cost_micros === 0 && r.calls > 0 && (
                <Badge variant="neutral" className="ml-2">
                  unpriced
                </Badge>
              )}
            </TableCell>
            <TableCell className="text-right">{r.calls.toLocaleString()}</TableCell>
            {showCredits && (
              <TableCell className="text-right">{(r.credits ?? 0).toLocaleString()}</TableCell>
            )}
            <TableCell className="text-right">{money(r.cost_micros)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
