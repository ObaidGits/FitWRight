'use client';

/**
 * AI operations (spec: ai-provider-admin, Phase 5).
 *
 * The page an operator opens when something feels wrong, and the one that tells them
 * something is wrong before it feels that way.
 *
 * Ordering is the design. Alerts come first because they are the reason to be here;
 * channel health second because it is usually the cause; reconciliation last because
 * it should be boring - a row of zeros - and the day it is not, it is the most
 * important thing on the screen.
 *
 * Nothing here has a "fix it" button, deliberately. Every signal is coarse enough that
 * automatic remediation would take a channel out of rotation because a provider had a
 * bad minute. The page's job is to make a human's decision well-informed and quick.
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
import { useAiAlerts, useChannelPerformance, useReconciliation } from '@/features/admin/hooks';
import ShieldCheck from 'lucide-react/dist/esm/icons/shield-check';

export default function AdminAiOpsPage() {
  const [days, setDays] = React.useState(7);
  const alerts = useAiAlerts(days);
  const performance = useChannelPerformance(days);
  const reconciliation = useReconciliation();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">AI operations</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Alerts, per-channel health, and the accounting checks that should always read zero.
          </p>
        </div>
        <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <SelectTrigger className="w-40" aria-label="Window">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="1">Last 24 hours</SelectItem>
            <SelectItem value="7">Last 7 days</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Alerts</h2>
        {alerts.isLoading ? (
          <LoadingSkeleton rows={2} />
        ) : alerts.isError ? (
          <ErrorState description="Could not load alerts." onRetry={() => alerts.refetch()} />
        ) : (alerts.data ?? []).length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="Nothing needs attention"
            description="No channel is failing, no cap is close, and no account is spending unusually."
          />
        ) : (
          <div className="space-y-2">
            {(alerts.data ?? []).map((a, i) => (
              <Card
                key={i}
                className={
                  a.severity === 'high'
                    ? 'border-[var(--destructive)]/40 bg-[var(--destructive)]/5 p-4'
                    : 'border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-4'
                }
              >
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  <Badge variant={a.severity === 'high' ? 'danger' : 'warning'}>{a.severity}</Badge>
                  {a.kind.replace(/_/g, ' ')}
                </p>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">{a.detail}</p>
                {a.note && (
                  <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">{a.note}</p>
                )}
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Channel health</h2>
        <p className="text-xs text-[var(--muted-foreground)]">
          P95 rather than an average: a channel that is usually quick and occasionally terrible
          looks fine on the mean, and users only remember the terrible calls.
        </p>
        {performance.isLoading ? (
          <LoadingSkeleton rows={2} />
        ) : performance.isError ? (
          <ErrorState
            description="Could not load channel health."
            onRetry={() => performance.refetch()}
          />
        ) : (performance.data ?? []).length === 0 ? (
          <p className="text-xs text-[var(--muted-foreground)]">
            No channel traffic in this window.
          </p>
        ) : (
          <Card className="p-4">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Success</TableHead>
                  <TableHead className="text-right">p95</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(performance.data ?? []).map((r) => {
                  const pct = r.success_rate === null ? null : Math.round(r.success_rate * 100);
                  return (
                    <TableRow key={r.channel_id}>
                      <TableCell className="max-w-[16rem] truncate font-medium">
                        {r.channel_id}
                      </TableCell>
                      <TableCell className="text-right">{r.calls.toLocaleString()}</TableCell>
                      <TableCell className="text-right">
                        {pct === null ? '-' : `${pct}%`}
                        {pct !== null && pct < 85 && (
                          <Badge variant="danger" className="ml-2">
                            low
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {r.p95_latency_ms === null ? '-' : `${r.p95_latency_ms}ms`}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Card>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Accounting checks</h2>
        {reconciliation.isLoading ? (
          <LoadingSkeleton rows={1} />
        ) : reconciliation.isError || !reconciliation.data ? (
          <ErrorState
            description="Could not run the checks."
            onRetry={() => reconciliation.refetch()}
          />
        ) : reconciliation.data.status === 'ok' ? (
          <Card className="flex items-center gap-2 p-4">
            <ShieldCheck className="h-5 w-5 text-[var(--at-success)]" />
            <p className="text-sm">Everything balances.</p>
          </Card>
        ) : (
          <Card className="border-[var(--destructive)]/40 bg-[var(--destructive)]/5 p-4">
            <p className="text-sm font-medium">These counts should all be zero</p>
            <ul className="mt-2 space-y-1">
              {Object.entries(reconciliation.data.findings ?? {})
                .filter(([, v]) => v > 0)
                .map(([k, v]) => (
                  <li key={k} className="flex items-center justify-between text-xs">
                    <span>{k.replace(/_/g, ' ')}</span>
                    <span className="font-semibold">{v}</span>
                  </li>
                ))}
            </ul>
            <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
              Nothing is repaired automatically. Each of these is evidence of how an assumption
              broke, and a silent fix would delete the evidence while leaving the cause in place.
            </p>
          </Card>
        )}
      </section>
    </div>
  );
}
