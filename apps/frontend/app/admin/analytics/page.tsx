'use client';

/** Admin analytics (Task 15.4) — time-series charts (mock data). */
import * as React from 'react';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { LoadingSkeleton } from '@/components/atelier/states';
import { MiniAreaChart } from '@/components/admin/mini-chart';
import { useUsageSeries } from '@/features/admin/hooks';

function ChartCard({
  title,
  metric,
}: {
  title: string;
  metric: 'signups' | 'active' | 'tailored';
}) {
  const { data } = useUsageSeries(metric);
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">{title}</h2>
      {data ? <MiniAreaChart data={data} /> : <LoadingSkeleton rows={1} />}
    </Card>
  );
}

export default function AdminAnalyticsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Analytics</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Usage over the last 30 days. <Badge variant="ai">Demo data</Badge>
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <ChartCard title="Sign-ups" metric="signups" />
        <ChartCard title="Active users" metric="active" />
        <ChartCard title="Resumes tailored" metric="tailored" />
      </div>
    </div>
  );
}
