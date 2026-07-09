'use client';

/** Admin overview (Task 15.2) — stat cards + usage chart + activity (mock data). */
import * as React from 'react';
import Users from 'lucide-react/dist/esm/icons/users';
import UserCheck from 'lucide-react/dist/esm/icons/user-check';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Mail from 'lucide-react/dist/esm/icons/mail';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { LoadingSkeleton } from '@/components/atelier/states';
import { MiniAreaChart } from '@/components/admin/mini-chart';
import { useAdminStats, useUsageSeries } from '@/features/admin/hooks';

function Stat({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number | string;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2 text-[var(--muted-foreground)]">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className="mt-2 text-3xl font-semibold">{value}</p>
    </Card>
  );
}

export default function AdminOverviewPage() {
  const stats = useAdminStats();
  const signups = useUsageSeries('signups');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Overview</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Usage at a glance. <Badge variant="ai">Demo data</Badge>
        </p>
      </div>

      {stats.isLoading ? (
        <LoadingSkeleton rows={2} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat icon={Users} label="Total users" value={stats.data!.totalUsers} />
          <Stat icon={UserCheck} label="Active users" value={stats.data!.activeUsers} />
          <Stat icon={FileText} label="Resumes tailored" value={stats.data!.resumesTailored} />
          <Stat icon={Mail} label="Cover letters" value={stats.data!.coverLettersGenerated} />
        </div>
      )}

      <Card className="p-5">
        <h2 className="mb-3 text-sm font-semibold text-[var(--muted-foreground)]">
          Sign-ups (30 days)
        </h2>
        {signups.data ? <MiniAreaChart data={signups.data} /> : <LoadingSkeleton rows={1} />}
      </Card>
    </div>
  );
}
